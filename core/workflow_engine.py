"""
Advanced Workflow Engine — runs multi-step task chains with:
  • Parallel step execution (steps that don't depend on each other)
  • Per-step retry with exponential back-off
  • State persistence  (resume an interrupted workflow)
  • Conditional branching  (run step only if previous succeeded/failed)
  • Full execution history  (every step outcome saved to memory)

No AI, no LLM.  Pure deterministic orchestration.

YAML workflow definition (config/workflow.yaml):

  my_workflow:
    description: "..."
    on_error: notify   # ignore | notify | abort
    steps:
      - task: install_app
        args: {app_name: VLC}
        retries: 2
        retry_wait: 5
        parallel: false       # run alone (default)
        depends_on: []        # step names this step waits for
        run_if: success       # success | failure | always (default: always)

  parallel example:
      - task: youtube_automation
        args: {query: "rrr naatu naatu"}
        parallel: true        # can run alongside other parallel:true steps
      - task: system_screenshot
        parallel: true

Usage:
    from core.workflow_engine import WorkflowEngine
    engine = WorkflowEngine()
    engine.run("install_essentials", "config/workflow.yaml")
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from core.memory import memory
from core.task_contract import TaskResult


# ─────────────────────────────────────────────────────────────────────────────
# STEP RESULT
# ─────────────────────────────────────────────────────────────────────────────
class StepResult:
    def __init__(self, step_name: str) -> None:
        self.step_name = step_name
        self.success:   bool  = False
        self.skipped:   bool  = False
        self.duration:  float = 0.0
        self.error:     str   = ""
        self.evidence:  list[dict[str, Any]] = []
        self.outcome:   TaskResult | None = None
        self.aliases:   tuple[str, ...] = (step_name,)

    def __repr__(self) -> str:
        status = "SKIP" if self.skipped else ("OK" if self.success else "FAIL")
        return f"StepResult({self.step_name!r} {status} {self.duration:.1f}s)"


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class WorkflowEngine:
    """
    Loads and executes workflow definitions from YAML.
    Learns from past runs: which steps tend to fail → escalate retries next time.
    """

    def __init__(self, max_workers: int = 4, *, approve_all: bool = False) -> None:
        self._max_workers = max_workers
        self._approve_all = approve_all
        self._run_stack: list[str] = []

    # ── YAML loading ──────────────────────────────────────────────────────────

    def _load_yaml(self, path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Workflow file not found: {path}")
        with open(p) as f:
            return yaml.safe_load(f) or {}

    # ── Memory helpers ────────────────────────────────────────────────────────

    def _record_step(self, wf: str, step: str, success: bool, duration: float) -> None:
        ns = f"workflow:{wf}:{step}"
        ok  = memory.get(ns, "ok")  or 0
        err = memory.get(ns, "err") or 0
        ok  = ok  if isinstance(ok,  (int, float)) else 0
        err = err if isinstance(err, (int, float)) else 0
        if success:
            memory.set(ns, "ok",       ok + 1)
        else:
            memory.set(ns, "err",      err + 1)
        memory.set(ns, "last_duration", duration)

    def _step_failure_rate(self, wf: str, step: str) -> float:
        ns  = f"workflow:{wf}:{step}"
        ok  = memory.get(ns, "ok")  or 0
        err = memory.get(ns, "err") or 0
        ok  = ok  if isinstance(ok,  (int, float)) else 0
        err = err if isinstance(err, (int, float)) else 0
        total = ok + err
        return err / total if total > 0 else 0.0

    def _adaptive_retries(self, wf: str, step: str, base: int) -> int:
        """
        Automatically raise retries for steps that have a high historical failure rate.
        This is the learning-without-AI: steps that fail often get more chances.
        """
        rate = self._step_failure_rate(wf, step)
        if rate >= 0.5:
            extra = 2
        elif rate >= 0.25:
            extra = 1
        else:
            extra = 0
        adapted = base + extra
        if extra:
            logger.info(
                f"  Adaptive retry: '{step}' failure rate={rate:.0%} → retries {base}→{adapted}"
            )
        return adapted

    # ── Single step execution ─────────────────────────────────────────────────

    @staticmethod
    def _step_aliases(step_cfg: dict[str, Any], fallback: str) -> tuple[str, ...]:
        """Return stable user-visible identifiers for dependency references."""
        aliases: list[str] = []
        for value in (step_cfg.get("key"), step_cfg.get("name"), step_cfg.get("task"), fallback):
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)
        return tuple(aliases)

    @staticmethod
    def _dependencies(step_cfg: dict[str, Any]) -> list[str]:
        declared = step_cfg.get("depends_on", [])
        if declared is None:
            return []
        if isinstance(declared, str):
            return [declared]
        if isinstance(declared, (list, tuple)):
            return [str(item) for item in declared]
        return [str(declared)]

    @staticmethod
    def _find_prior_result(
        reference: str,
        previous_results: dict[str, StepResult],
    ) -> StepResult | None:
        """Resolve an exact result key or the latest matching prior step alias."""
        if reference in previous_results:
            return previous_results[reference]
        for result_key, result in reversed(list(previous_results.items())):
            if reference == result_key.rsplit("#", 1)[0] or reference in result.aliases:
                return result
        return None

    def _dependency_error(
        self,
        step_cfg: dict[str, Any],
        previous_results: dict[str, StepResult],
    ) -> str:
        for dependency in self._dependencies(step_cfg):
            prior = self._find_prior_result(dependency, previous_results)
            if prior is None:
                return f"Missing dependency {dependency!r}"
            if not prior.success and not prior.skipped:
                detail = f": {prior.error}" if prior.error else ""
                return f"Dependency {dependency!r} failed{detail}"
        return ""

    @staticmethod
    def _run_condition(step_cfg: dict[str, Any]) -> tuple[str, str]:
        """Read legacy scalar and optional mapping forms without changing either API."""
        configured = step_cfg.get("run_if", "always")
        reference = ""
        if isinstance(configured, dict):
            condition = configured.get("condition", configured.get("status", "always"))
            reference = str(
                configured.get("depends_on", configured.get("step", configured.get("key", "")))
                or ""
            )
        else:
            condition = configured
        explicit = step_cfg.get(
            "run_if_on",
            step_cfg.get("condition_dependency", step_cfg.get("condition_step", "")),
        )
        if explicit:
            reference = str(explicit)
        return str(condition).lower(), reference

    @staticmethod
    def _must_not_retry(outcome: TaskResult) -> bool:
        if outcome.state == "uncertain":
            return True
        return (
            outcome.dispatch_status in ("dispatched", "unknown")
            and outcome.side_effect in ("external", "destructive")
        )

    def _run_step(
        self,
        step_cfg: dict[str, Any],
        wf_name: str,
        shared_resources: dict[str, Any],
        previous_results: dict[str, StepResult],
    ) -> StepResult:
        """Execute one workflow step with retries."""

        task_name = str(step_cfg.get("task", ""))
        nested_workflow = str(step_cfg.get("workflow", "")).strip()
        step_label = task_name or (f"workflow:{nested_workflow}" if nested_workflow else "step")
        result = StepResult(step_label)
        result.aliases = self._step_aliases(step_cfg, step_label)

        dependency_error = self._dependency_error(step_cfg, previous_results)
        if dependency_error:
            result.error = dependency_error
            logger.error(f"  Step '{step_label}' blocked: {dependency_error}")
            return result

        run_if, condition_reference = self._run_condition(step_cfg)
        if run_if != "always" and (previous_results or condition_reference):
            condition_result = (
                self._find_prior_result(condition_reference, previous_results)
                if condition_reference
                else list(previous_results.values())[-1]
            )
            if condition_result is None:
                result.error = f"Missing condition dependency {condition_reference!r}"
                logger.error(f"  Step '{step_label}' blocked: {result.error}")
                return result
            if run_if == "success" and not condition_result.success:
                logger.info(f"  Skipping '{step_label}' (run_if=success, condition not successful)")
                result.skipped = True
                return result
            if run_if == "failure" and (condition_result.success or condition_result.skipped):
                logger.info(f"  Skipping '{step_label}' (run_if=failure, condition completed)")
                result.skipped = True
                return result

        if nested_workflow:
            nested_file = str(step_cfg.get("workflow_file", "")).strip()
            if not nested_file:
                result.error = "nested workflow requires workflow_file"
                return result
            t0 = time.time()
            result.success = self.run(nested_workflow, nested_file, shared_resources)
            result.duration = time.time() - t0
            if not result.success:
                result.error = f"Nested workflow {nested_workflow!r} failed"
            return result

        args = dict(step_cfg.get("args", {}) or {})
        base_retry = int(step_cfg.get("retries", 0))
        retry_wait = float(step_cfg.get("retry_wait", 2.0))

        from core.action_policy import requires_approval
        if requires_approval(task_name, args) and not self._approve_all:
            result.error = f"Task {task_name!r} requires explicit approval"
            logger.error(result.error)
            return result
        retries = self._adaptive_retries(wf_name, task_name, base_retry)
        t0 = time.time()

        for attempt in range(retries + 1):
            if attempt > 0:
                wait = retry_wait * (2 ** (attempt - 1))  # exponential back-off
                logger.info(f"  Retry {attempt}/{retries} in {wait:.1f}s …")
                time.sleep(wait)

            try:
                from core.automation_service import ExecutionRequest, automation_service

                outcome = automation_service.execute(
                    ExecutionRequest(
                        task=task_name,
                        params=args,
                        approved=self._approve_all,
                        source=f"workflow:{wf_name}",
                        execution_id=f"workflow:{wf_name}:{task_name}:{attempt}",
                    ),
                    resources=shared_resources,
                )
                result.outcome = outcome
                result.success = outcome.success
                result.evidence = list(outcome.evidence)
                if outcome.error:
                    result.error = outcome.error
                elif outcome.success:
                    result.error = ""
                else:
                    result.error = f"Task outcome: {outcome.state}"
                if result.success or self._must_not_retry(outcome):
                    break
            except Exception as exc:
                logger.warning(f"  Step '{task_name}' attempt {attempt+1} raised: {exc}")
                result.error = str(exc)

        result.duration = time.time() - t0
        self._record_step(wf_name, task_name, result.success, result.duration)
        return result

    # ── Parallel batch ────────────────────────────────────────────────────────

    def _run_parallel_batch(
        self,
        steps: list[dict[str, Any]],
        wf_name: str,
        shared_resources: dict[str, Any],
        previous_results: dict[str, StepResult],
        start_ordinal: int = 0,
    ) -> dict[str, StepResult]:
        # Keyed by "{task_name}#{ordinal}", NOT bare task_name -- a workflow
        # that runs the same task more than once (e.g. visiting two URLs both
        # via open_browser_and_visit) would otherwise silently collide in
        # this dict, with the later result overwriting the earlier one and
        # the run's final ok/fail summary undercounting real, successfully
        # executed steps. Verified live: a 3-step workflow with a repeated
        # task name reported ok=2 instead of ok=3 despite all 3 actions
        # genuinely succeeding (confirmed independently via the browser's
        # own tab list).
        results: dict[str, StepResult] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            futures = {
                ex.submit(
                    self._run_step, step, wf_name, shared_resources, previous_results
                ): f"{step.get('task', f'step_{i}')}#{start_ordinal + i}"
                for i, step in enumerate(steps)
            }
            # Futures still execute concurrently, but results are inserted in
            # declared order so later run_if evaluation cannot depend on timing.
            for fut, result_key in futures.items():
                try:
                    results[result_key] = fut.result()
                except Exception as exc:
                    r = StepResult(result_key)
                    r.error = str(exc)
                    results[result_key] = r
        return results

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(
        self,
        workflow_name: str,
        workflow_file: str = "config/workflow.yaml",
        shared_resources: dict[str, Any] | None = None,
        *,
        resume: bool = False,
        state_file: str | None = None,
    ) -> bool:
        """Run a workflow while always unwinding nested-workflow cycle state."""
        if workflow_name in self._run_stack:
            cycle = " -> ".join([*self._run_stack, workflow_name])
            logger.error(f"Nested workflow cycle rejected: {cycle}")
            return False
        self._run_stack.append(workflow_name)
        try:
            return self._run(
                workflow_name,
                workflow_file,
                shared_resources,
                resume=resume,
                state_file=state_file,
            )
        except Exception as exc:
            logger.exception(f"Workflow {workflow_name!r} failed unexpectedly: {exc}")
            return False
        finally:
            self._run_stack.pop()

    def _run(
        self,
        workflow_name: str,
        workflow_file: str = "config/workflow.yaml",
        shared_resources: dict[str, Any] | None = None,
        *,
        resume: bool = False,
        state_file: str | None = None,
    ) -> bool:
        """Load and execute a named workflow after recursion state is registered."""
        from config.config_loader import load_config
        from core.gui_controller import GUIController
        from core.vision_engine import VisionEngine

        wf_id = str(uuid.uuid4())[:8]
        logger.info(f"╔══ Workflow '{workflow_name}' [{wf_id}] ══╗")

        try:
            all_wf = self._load_yaml(workflow_file)
        except FileNotFoundError as exc:
            logger.error(str(exc))
            return False

        if workflow_name not in all_wf:
            logger.error(f"Workflow '{workflow_name}' not found in {workflow_file}")
            logger.info(f"Available: {list(all_wf)}")
            return False

        wf_cfg   = all_wf[workflow_name]
        on_error = wf_cfg.get("on_error", "notify").lower()
        timeout  = wf_cfg.get("timeout", None)
        # Accept both 'steps:' and 'tasks:' as the YAML key (both are common)
        steps    = wf_cfg.get("steps") or wf_cfg.get("tasks") or []

        fingerprint = hashlib.sha256(
            json.dumps(wf_cfg, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        checkpoint_path = Path(state_file) if state_file else None
        completed_indices: set[int] = set()
        if resume and checkpoint_path and checkpoint_path.is_file():
            try:
                saved = json.loads(checkpoint_path.read_text())
                if (
                    saved.get("workflow") == workflow_name
                    and saved.get("fingerprint") == fingerprint
                ):
                    completed_indices = {
                        int(index) for index in saved.get("completed_indices", [])
                    }
                    logger.info(
                        f"  Resuming from checkpoint: {len(completed_indices)} completed step(s)"
                    )
                else:
                    logger.warning("Checkpoint does not match current workflow; starting fresh")
            except (OSError, ValueError, TypeError) as exc:
                logger.warning(f"Could not read checkpoint; starting fresh: {exc}")

        def save_checkpoint(complete: bool = False) -> None:
            if checkpoint_path is None:
                return
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "workflow": workflow_name,
                "fingerprint": fingerprint,
                "completed_indices": sorted(completed_indices),
                "complete": complete,
            }
            temporary = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, sort_keys=True))
            os.replace(temporary, checkpoint_path)

        logger.info(f"  Description : {wf_cfg.get('description', '')}")
        logger.info(f"  Steps       : {len(steps)}")
        logger.info(f"  On error    : {on_error}")

        # Shared resources (created once, reused across all steps)
        if shared_resources is None:
            try:
                cfg  = load_config()
                shared_resources = {
                    "gui":    GUIController(safe_mode=cfg.get("gui", {}).get("safe_mode", True)),
                    "vision": VisionEngine(),
                    "config": cfg,
                }
            except Exception as exc:
                logger.warning(f"Could not create shared resources: {exc}")
                shared_resources = {}

        all_results:   dict[str, StepResult] = {}
        wf_start = time.time()
        overall_ok = True

        # Group steps: collect consecutive parallel steps, run serially otherwise
        i = 0
        while i < len(steps):
            # Check workflow timeout
            if timeout and (time.time() - wf_start) > timeout:
                logger.warning("Workflow timeout reached — stopping")
                overall_ok = False
                break

            step = steps[i]

            if i in completed_indices:
                task_label = step.get("task") or (
                    f"workflow:{step['workflow']}" if step.get("workflow") else f"step_{i}"
                )
                resumed = StepResult(str(task_label))
                resumed.success = True
                resumed.skipped = True
                resumed.aliases = self._step_aliases(step, str(task_label))
                all_results[f"{task_label}#{i}"] = resumed
                logger.info(f"  ↪ [{i+1}/{len(steps)}] {task_label} restored from checkpoint")
                i += 1
                continue

            if step.get("parallel", False):
                # Collect all consecutive parallel steps
                batch: list[dict[str, Any]] = []
                batch_aliases: set[str] = set()
                while i < len(steps) and steps[i].get("parallel", False):
                    candidate = steps[i]
                    # A parallel step may depend on an earlier parallel step. End
                    # this wave before it so that dependency has a real result.
                    if batch and any(
                        dependency in batch_aliases
                        for dependency in self._dependencies(candidate)
                    ):
                        break
                    batch.append(candidate)
                    aliases = self._step_aliases(
                        candidate,
                        str(candidate.get("task") or f"step_{i}"),
                    )
                    batch_aliases.update(aliases)
                    batch_aliases.update(f"{alias}#{i}" for alias in aliases)
                    i += 1
                logger.info(f"  \u26a1 Running {len(batch)} steps in parallel \u2026")
                batch_results = self._run_parallel_batch(
                    batch, workflow_name, shared_resources, all_results, start_ordinal=i - len(batch)
                )
                all_results.update(batch_results)
                for result_key, r in batch_results.items():
                    if r.success or r.skipped:
                        try:
                            completed_indices.add(int(result_key.rsplit("#", 1)[1]))
                        except (IndexError, ValueError):
                            logger.warning(f"Could not checkpoint parallel result {result_key!r}")
                    elif not r.skipped:
                        overall_ok = False
                save_checkpoint()
            else:
                task_label = step.get("task") or (
                    f"workflow:{step['workflow']}" if step.get("workflow") else f"step_{i}"
                )
                logger.info(f"\n  \u2192 [{i+1}/{len(steps)}] {task_label}")
                r = self._run_step(step, workflow_name, shared_resources, all_results)
                step_index = i
                all_results[f"{task_label}#{i}"] = r
                i += 1
                if r.success or r.skipped:
                    completed_indices.add(step_index)
                save_checkpoint()

                status = "✅ OK" if r.success else ("⏭ skipped" if r.skipped else "❌ FAILED")
                logger.info(f"     {status} ({r.duration:.1f}s)")

                if not r.success and not r.skipped:
                    overall_ok = False
                    if on_error == "abort":
                        logger.error("on_error=abort — stopping workflow")
                        break
                    if on_error == "notify":
                        try:
                            from core.logger import notify
                            notify(f"Workflow step failed: {task_label}", critical=True)
                        except Exception as exc:
                            logger.debug(f"workflow notify failed: {exc}")

        workflow_complete = len(completed_indices) == len(steps) and overall_ok
        save_checkpoint(complete=workflow_complete)

        # Summary
        ok_count  = sum(1 for r in all_results.values() if r.success)
        fail_count = sum(1 for r in all_results.values() if not r.success and not r.skipped)
        skip_count = sum(1 for r in all_results.values() if r.skipped)
        total_time = time.time() - wf_start

        logger.info(
            f"\n╚══ Workflow '{workflow_name}' done in {total_time:.1f}s — "
            f"ok={ok_count} fail={fail_count} skip={skip_count} ══╝"
        )

        # Save workflow run to memory for later analysis
        memory.set(f"workflow_run:{workflow_name}", wf_id, {
            "ok": ok_count, "fail": fail_count, "skip": skip_count,
            "duration": total_time, "overall": overall_ok,
        })

        return workflow_complete

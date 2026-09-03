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

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from core.memory import memory


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

    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers

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

    def _run_step(
        self,
        step_cfg: dict[str, Any],
        wf_name: str,
        shared_resources: dict[str, Any],
        previous_results: dict[str, StepResult],
    ) -> StepResult:
        """Execute one workflow step with retries."""
        from tasks import get_task

        task_name  = step_cfg.get("task", "")
        args       = step_cfg.get("args", {}) or {}
        base_retry = int(step_cfg.get("retries", 0))
        retry_wait = float(step_cfg.get("retry_wait", 2.0))
        run_if     = step_cfg.get("run_if", "always").lower()

        result = StepResult(task_name)

        # Conditional execution: check previous step outcome
        if run_if != "always" and previous_results:
            last = list(previous_results.values())[-1]
            if run_if == "success" and not last.success:
                logger.info(f"  Skipping '{task_name}' (run_if=success, prev failed)")
                result.skipped = True
                return result
            if run_if == "failure" and last.success:
                logger.info(f"  Skipping '{task_name}' (run_if=failure, prev succeeded)")
                result.skipped = True
                return result

        mod = get_task(task_name)
        if not mod:
            logger.error(f"Task not found: {task_name}")
            result.error = f"Task '{task_name}' not found"
            return result

        retries = self._adaptive_retries(wf_name, task_name, base_retry)
        t0 = time.time()

        for attempt in range(retries + 1):
            if attempt > 0:
                wait = retry_wait * (2 ** (attempt - 1))  # exponential back-off
                logger.info(f"  Retry {attempt}/{retries} in {wait:.1f}s …")
                time.sleep(wait)

            try:
                task_res = mod.setup()
                all_res  = {**shared_resources, **task_res}
                ok       = mod.execute(args, all_res)
                mod.cleanup(all_res)
                if ok:
                    result.success = True
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
    ) -> dict[str, StepResult]:
        results: dict[str, StepResult] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            futures = {
                ex.submit(
                    self._run_step, step, wf_name, shared_resources, previous_results
                ): step.get("task", f"step_{i}")
                for i, step in enumerate(steps)
            }
            for fut in as_completed(futures):
                task_name = futures[fut]
                try:
                    results[task_name] = fut.result()
                except Exception as exc:
                    r = StepResult(task_name)
                    r.error = str(exc)
                    results[task_name] = r
        return results

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(
        self,
        workflow_name: str,
        workflow_file: str = "config/workflow.yaml",
        shared_resources: dict[str, Any] | None = None,
    ) -> bool:
        """
        Load and execute a named workflow.

        Returns True if all non-skipped steps succeeded.
        """
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
                break

            step = steps[i]

            if step.get("parallel", False):
                # Collect all consecutive parallel steps
                batch: list[dict] = []
                while i < len(steps) and steps[i].get("parallel", False):
                    batch.append(steps[i])
                    i += 1
                logger.info(f"  ⚡ Running {len(batch)} steps in parallel …")
                batch_results = self._run_parallel_batch(
                    batch, workflow_name, shared_resources, all_results
                )
                all_results.update(batch_results)
                for r in batch_results.values():
                    if not r.success and not r.skipped:
                        overall_ok = False
            else:
                task_label = step.get("task", f"step_{i}")
                logger.info(f"\n  → [{i+1}/{len(steps)}] {task_label}")
                r = self._run_step(step, workflow_name, shared_resources, all_results)
                all_results[task_label] = r
                i += 1

                status = "✅ OK" if r.success else ("⏭ skipped" if r.skipped else "❌ FAILED")
                logger.info(f"     {status} ({r.duration:.1f}s)")

                if not r.success and not r.skipped:
                    overall_ok = False
                    if on_error == "abort":
                        logger.error(f"on_error=abort — stopping workflow")
                        break
                    if on_error == "notify":
                        try:
                            from core.logger import notify
                            notify(f"Workflow step failed: {task_label}", critical=True)
                        except Exception:
                            pass

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

        return overall_ok

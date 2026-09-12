"""Bridges existing deterministic plans into the authoritative task runtime."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.capability_registry import CapabilityRegistry, StructuredCapabilities
from core.structured_automation import (
    PlanStep,
    StructuredExecutor,
    StructuredPlan,
    plan_fingerprint,
    validate_plan,
)
from core.task_runtime import (
    RuntimeAction,
    RuntimeDecision,
    RuntimeResult,
    RuntimeState,
    RuntimeSubgoalSpec,
    TaskRuntime,
    load_runtime_state,
)

RuntimeObservation = Callable[[RuntimeState], dict[str, Any]]


def build_runtime_registry(
    *,
    executor: StructuredExecutor | None = None,
    page: Any = None,
    goal: str = "",
    approve_all: bool = False,
    approval_callback: Callable[[StructuredPlan], bool] | None = None,
    capability_groups: list[str] | None = None,
) -> CapabilityRegistry:
    """Single source of truth for runtime capabilities.

    Merges three layers into one registry:
      1. capabilities/ — the OS-level universal capability set (fs, calc,
         sheet, browser, verify, wait, ...) from capabilities.build_registry().
      2. structured.* — the proven StructuredExecutor browser/desktop/vision
         primitives (kept as one executor for browser ownership and repair).
      3. task:* — registered task modules for legacy compatibility.

    The result is what TaskRuntime and planners see: ONE registry where the
    universal capabilities are finally reachable.
    """
    from capabilities import build_registry as _build_universal

    registry = _build_universal(groups=capability_groups, approve_all=approve_all)

    capabilities = StructuredCapabilities(
        executor=executor,
        page=page,
        goal=goal,
        approval_callback=approval_callback,
        approve_all=approve_all,
    )
    capabilities.install(registry)

    task_caps = RegisteredTaskCapabilitiesAdapter(approved=approve_all)
    task_caps.install(registry)
    return registry


class RegisteredTaskCapabilitiesAdapter:
    """Register task:* capabilities under the 'task:' prefix (thin wrapper
    over core.capability_registry.RegisteredTaskCapabilities)."""

    def __init__(self, *, approved: bool = False) -> None:
        from core.capability_registry import RegisteredTaskCapabilities

        self._inner = RegisteredTaskCapabilities(approved=approved)

    def install(self, registry: CapabilityRegistry) -> None:
        self._inner.install(registry)


def execute_structured_plan_runtime(
    plan: StructuredPlan,
    goal: str,
    *,
    executor: StructuredExecutor | None = None,
    page: Any = None,
    observe: RuntimeObservation | None = None,
    approve_all: bool = False,
    approval_callback: Callable[[StructuredPlan], bool] | None = None,
    checkpoint_path: str | Path | None = None,
    state: RuntimeState | None = None,
    max_steps: int | None = None,
) -> RuntimeResult:
    """Execute a validated fixed plan through the shared stateful runtime.

    The structured executor remains the single implementation of browser,
    AT-SPI, vision and local primitives.  This adapter gives a fixed plan the
    same durable subgoals, verified facts, safe uncertainty boundary, and
    resume semantics as an adaptive plan.  Universal ``cap`` steps dispatch
    through the same merged registry the adaptive loop uses, so every action
    in one plan shares one approval policy and evidence contract.
    """
    registry = build_runtime_registry(
        executor=executor,
        page=page,
        goal=goal,
        approve_all=approve_all,
        approval_callback=approval_callback,
    )

    declarations = tuple(
        RuntimeSubgoalSpec(
            id=_step_id(index),
            description=_step_description(step),
            depends_on=(_step_id(index - 1),) if index > 1 else (),
        )
        for index, step in enumerate(plan.steps, 1)
    )

    def decide(runtime: RuntimeState, _observation: dict[str, Any]) -> RuntimeDecision:
        next_step = int(runtime.verified_facts.get("structured_next_step", 0)) + 1
        if next_step > len(plan.steps):
            return RuntimeDecision(complete=True, message=plan.summary, subgoals=declarations)
        step = plan.steps[next_step - 1]
        args = dict(step.args)
        if step.expect:
            args["expect"] = dict(step.expect)
        if step.when:
            args["when"] = dict(step.when)
        # Every step — including universal "cap" steps — dispatches through
        # the structured wrapper so one executor owns condition evaluation,
        # evidence numbering, ${step.N.field} resolution, the approval gate,
        # and failed-step repair.  structured.cap then delegates to the
        # universal registry inside _execute_capability_step.
        return RuntimeDecision(
            action=RuntimeAction(
                capability=f"structured.{step.action}",
                args=args,
                subgoal=_step_id(next_step),
            ),
            subgoals=declarations,
        )

    def verify(
        action: RuntimeAction,
        outcome: Any,
        _before: dict[str, Any],
        _after: dict[str, Any],
        runtime: RuntimeState,
    ) -> tuple[bool, dict[str, Any]]:
        index = _step_index(action.subgoal)
        skipped = outcome.state == "skipped" and outcome.data.get("condition_met") is False
        if not outcome.success and not skipped:
            return False, {}
        prior = list(runtime.verified_facts.get("structured_evidence", ()))
        if outcome.evidence:
            # ``StructuredExecutor.execute`` numbers evidence entries before
            # resolving ${step.N.field}.  The runtime invokes one step at a
            # time, so retain that same public evidence contract here.
            prior.extend({"step": index, **item} for item in outcome.evidence)
        elif skipped:
            prior.append({"step": index, "action": plan.steps[index - 1].action, "skipped": True})
        return True, {"structured_next_step": index, "structured_evidence": prior}

    def final_verify(runtime: RuntimeState, _observation: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        complete = int(runtime.verified_facts.get("structured_next_step", 0)) == len(plan.steps)
        return complete, {"structured_plan_summary": plan.summary} if complete else {}

    serialized_plan = _plan_payload(plan)
    fingerprint = plan_fingerprint(plan)
    if state is None:
        state = RuntimeState(
            goal=goal,
            verified_facts={
                "structured_plan": serialized_plan,
                "structured_plan_fingerprint": fingerprint,
            },
        )
    elif state.verified_facts.get("structured_plan_fingerprint") not in {None, fingerprint}:
        state.status = "failed"
        state.message = "Runtime checkpoint plan fingerprint does not match"
        return RuntimeResult(state)

    runtime = TaskRuntime.with_capabilities(
        capabilities=registry,
        observe=observe or (lambda _runtime: {"plan_steps": len(plan.steps)}),
        decide=decide,
        verify=verify,
        final_verify=final_verify,
        checkpoint_path=checkpoint_path,
        max_steps=max_steps if max_steps is not None else max(1, len(plan.steps) * 2 + 1),
    )
    if state.status == "paused_for_human":
        return runtime.resume(state)
    return runtime.run(goal, state=state)


def resume_structured_plan_runtime(
    goal: str,
    checkpoint_path: str | Path,
    **kwargs: Any,
) -> RuntimeResult:
    """Resume the exact fixed plan stored in a runtime checkpoint."""
    state = load_runtime_state(checkpoint_path)
    if state.goal != goal:
        raise TypeError("Runtime checkpoint belongs to a different goal")
    raw_plan = state.verified_facts.get("structured_plan")
    if not isinstance(raw_plan, dict):
        raise TypeError("Runtime checkpoint has no resumable structured plan")
    return execute_structured_plan_runtime(
        validate_plan(raw_plan),
        goal,
        checkpoint_path=checkpoint_path,
        state=state,
        **kwargs,
    )


def _step_id(index: int) -> str:
    return f"structured-step:{index}"


def _step_index(identifier: str) -> int:
    prefix, raw_index = identifier.rsplit(":", 1)
    if prefix != "structured-step":
        raise ValueError(f"Invalid structured runtime subgoal {identifier!r}")
    return int(raw_index)


def _step_description(step: PlanStep) -> str:
    return f"Structured {step.action}"


def _plan_payload(plan: StructuredPlan) -> dict[str, Any]:
    return {
        "summary": plan.summary,
        "steps": [
            {"action": step.action, "args": step.args, "expect": step.expect, "when": step.when}
            for step in plan.steps
        ],
    }

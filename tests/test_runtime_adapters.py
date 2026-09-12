from __future__ import annotations

from pathlib import Path

from core.runtime_adapters import execute_structured_plan_runtime, resume_structured_plan_runtime
from core.structured_automation import PlanStep, StructuredPlan
from core.task_contract import ActionOutcome
from core.task_runtime import load_runtime_state


class FakeStructuredExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[PlanStep, list[dict[str, object]]]] = []

    def execute_step(self, step, *, evidence=None, **_kwargs):
        previous = list(evidence or ())
        self.calls.append((step, previous))
        if step.when.get("enabled") is False:
            return ActionOutcome(
                state="skipped",
                data={"condition_met": False},
                dispatch_status="not_attempted",
                side_effect="read",
            )
        item = {"action": step.action, "value": step.args.get("value", "done")}
        return ActionOutcome(
            state="succeeded",
            data=item,
            evidence=[item],
            dispatch_status="confirmed",
            side_effect="read",
        )


def test_structured_plan_uses_authoritative_runtime_and_preserves_evidence(tmp_path: Path) -> None:
    executor = FakeStructuredExecutor()
    plan = StructuredPlan(
        "complete local sequence",
        (
            PlanStep("extract_text", {"value": "first"}),
            PlanStep("wait", {}, when={"enabled": False}),
            PlanStep("assert_value", {"value": "third"}),
        ),
    )
    checkpoint = tmp_path / "runtime.json"

    result = execute_structured_plan_runtime(
        plan,
        "complete local sequence",
        executor=executor,
        checkpoint_path=checkpoint,
    )

    assert result.success
    assert [subgoal.status for subgoal in result.state.subgoals.values()] == ["verified"] * 3
    assert result.state.verified_facts["structured_next_step"] == 3
    assert executor.calls[2][1][0]["value"] == "first"
    assert executor.calls[2][1][0]["step"] == 1
    assert any(item.get("skipped") for item in result.state.verified_facts["structured_evidence"])
    assert load_runtime_state(checkpoint).status == "completed"


def test_structured_plan_resume_continues_after_verified_step_without_replay(tmp_path: Path) -> None:
    executor = FakeStructuredExecutor()
    plan = StructuredPlan(
        "resume a local sequence",
        (
            PlanStep("extract_text", {"target": "first"}),
            PlanStep("assert_value", {"value": "second", "operator": "equals", "expected": "second"}),
        ),
    )
    checkpoint = tmp_path / "resume.json"

    bounded = execute_structured_plan_runtime(
        plan,
        "resume a local sequence",
        executor=executor,
        checkpoint_path=checkpoint,
        max_steps=1,
    )
    assert bounded.state.status == "running"
    assert [call[0].action for call in executor.calls] == ["extract_text"]

    resumed = resume_structured_plan_runtime(
        "resume a local sequence",
        checkpoint,
        executor=executor,
    )

    assert resumed.success
    assert [call[0].action for call in executor.calls] == ["extract_text", "assert_value"]

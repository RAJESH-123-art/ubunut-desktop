from __future__ import annotations

from pathlib import Path

from core.task_contract import ActionOutcome
from core.task_runtime import (
    RuntimeAction,
    RuntimeDecision,
    RuntimeState,
    RuntimeSubgoalSpec,
    TaskRuntime,
    load_runtime_state,
)


def _success() -> ActionOutcome:
    return ActionOutcome(
        state="succeeded", dispatch_status="confirmed", side_effect="read"
    )


def test_runtime_verifies_transitions_and_final_goal(tmp_path: Path) -> None:
    state = {"clicked": False}
    checkpoint = tmp_path / "runtime.json"

    runtime = TaskRuntime(
        observe=lambda _runtime: {"clicked": state["clicked"]},
        decide=lambda _runtime, observed: (
            RuntimeDecision(complete=True, message="complete")
            if observed["clicked"]
            else RuntimeDecision(action=RuntimeAction("click", {"text": "Complete"}, subgoal="complete local task"))
        ),
        act=lambda _action, _runtime: (state.__setitem__("clicked", True) or _success()),
        verify=lambda _action, _outcome, _before, after, _runtime: (after["clicked"], {"clicked": True}),
        final_verify=lambda _runtime, observed: (observed["clicked"], {"final": "proven"}),
        checkpoint_path=checkpoint,
    )

    result = runtime.run("complete a local task")

    assert result.success
    assert result.state.verified_facts == {"clicked": True, "final": "proven"}
    assert result.state.subgoals["complete local task"].status == "verified"
    restored = load_runtime_state(checkpoint)
    assert restored.status == "completed"
    assert restored.subgoals["complete local task"].verified_facts == {"clicked": True}
    assert any(event.phase == "final_verification" for event in restored.events)


def test_runtime_uses_safe_recovery_without_replaying_failed_action() -> None:
    calls: list[str] = []
    state = {"recovered": False}

    def act(action: RuntimeAction, _runtime: object) -> ActionOutcome:
        calls.append(action.capability)
        if action.capability == "stale_click":
            return ActionOutcome(
                state="failed", error="control changed", dispatch_status="not_dispatched", side_effect="read"
            )
        state["recovered"] = True
        return _success()

    runtime = TaskRuntime(
        observe=lambda _runtime: dict(state),
        decide=lambda _runtime, observed: (
            RuntimeDecision(complete=True)
            if observed["recovered"]
            else RuntimeDecision(action=RuntimeAction("stale_click"))
        ),
        act=act,
        verify=lambda _action, outcome, _before, after, _runtime: (outcome.success and after["recovered"], {}),
        final_verify=lambda _runtime, observed: observed["recovered"],
        recover=lambda *_args: RuntimeDecision(
            action=RuntimeAction("fresh_click", safe_to_retry=True), message="use fresh control"
        ),
    )

    result = runtime.run("complete changed page")

    assert result.success
    assert calls == ["stale_click", "fresh_click"]
    assert any(event.phase == "diagnosis" for event in result.state.events)
    assert result.state.subgoals["stale_click"].status == "recovering"
    assert result.state.subgoals["fresh_click"].status == "verified"


def test_uncertain_action_is_preserved_when_no_safe_diagnosis_exists() -> None:
    runtime = TaskRuntime(
        observe=lambda _runtime: {"screen": "unknown"},
        decide=lambda _runtime, _observed: RuntimeDecision(action=RuntimeAction("save")),
        act=lambda _action, _runtime: ActionOutcome(
            state="uncertain", error="save may have been dispatched", dispatch_status="unknown", side_effect="external"
        ),
        verify=lambda *_args: False,
    )

    result = runtime.run("save document")

    assert result.state.status == "uncertain"
    assert "may have been dispatched" in result.state.message
    assert result.state.subgoals["save"].status == "uncertain"
    action = next(event for event in result.state.events if event.phase == "action")
    assert action.outcome["state"] == "uncertain"
    assert action.outcome["dispatch_status"] == "unknown"


def test_uncertain_recovery_rejects_an_unmarked_retry() -> None:
    runtime = TaskRuntime(
        observe=lambda _runtime: {"screen": "unknown"},
        decide=lambda _runtime, _observed: RuntimeDecision(action=RuntimeAction("send")),
        act=lambda _action, _runtime: ActionOutcome(
            state="uncertain", dispatch_status="unknown", side_effect="external"
        ),
        verify=lambda *_args: False,
        recover=lambda *_args: RuntimeDecision(action=RuntimeAction("send")),
    )

    result = runtime.run("send a message")

    assert result.state.status == "blocked"
    assert "explicitly safe" in result.state.message


def test_runtime_refuses_to_replay_a_checkpointed_inflight_action() -> None:
    calls = 0

    def act(*_args):
        nonlocal calls
        calls += 1
        return _success()

    runtime = TaskRuntime(
        observe=lambda _runtime: {"screen": "same"},
        decide=lambda _runtime, _observed: RuntimeDecision(action=RuntimeAction("save")),
        act=act,
        verify=lambda *_args: True,
    )
    interrupted = RuntimeState(
        goal="save document",
        pending_action={"capability": "save", "subgoal": "save"},
    )

    result = runtime.resume(interrupted)

    assert result.state.status == "uncertain"
    assert "automatic replay refused" in result.state.message
    assert calls == 0


def test_replan_preserves_verified_facts_and_creates_a_new_plan_version() -> None:
    calls: list[str] = []

    def act(action: RuntimeAction, _runtime: object) -> ActionOutcome:
        calls.append(action.capability)
        if action.capability == "obsolete":
            return ActionOutcome(
                state="failed", error="page changed", dispatch_status="not_dispatched", side_effect="read"
            )
        return _success()

    runtime = TaskRuntime(
        observe=lambda _runtime: {"replacement_done": calls == ["obsolete", "replacement"]},
        decide=lambda _runtime, observed: (
            RuntimeDecision(complete=True)
            if observed["replacement_done"]
            else RuntimeDecision(action=RuntimeAction("obsolete", subgoal="continue checkout"))
        ),
        act=act,
        verify=lambda _action, outcome, _before, _after, _runtime: outcome.success,
        final_verify=lambda _runtime, observed: observed["replacement_done"],
        replan=lambda *_args: RuntimeDecision(
            action=RuntimeAction("replacement", subgoal="continue checkout", safe_to_retry=True),
            message="new page control discovered",
        ),
    )

    result = runtime.run(
        "continue checkout",
        state=RuntimeState(goal="continue checkout", verified_facts={"cart": "verified"}),
    )

    assert result.success
    assert calls == ["obsolete", "replacement"]
    assert result.state.plan_version == 2
    assert result.state.verified_facts["cart"] == "verified"
    assert any(event.phase == "replan" for event in result.state.events)


def test_runtime_retains_interfaces_reported_by_real_observation() -> None:
    runtime = TaskRuntime(
        observe=lambda _runtime: {"interfaces": ["browser_dom", "filesystem"]},
        decide=lambda _runtime, _observed: RuntimeDecision(complete=True),
        act=lambda *_args: _success(),
        verify=lambda *_args: True,
    )

    result = runtime.run("inspect available interfaces")

    assert result.success
    assert result.state.available_interfaces == {"browser_dom", "filesystem"}


def test_runtime_accepts_dynamic_subgoals_and_enforces_verified_dependencies() -> None:
    completed: list[str] = []

    def decide(_runtime, _observed):
        if not completed:
            return RuntimeDecision(
                action=RuntimeAction("extract", subgoal="discover"),
                subgoals=(
                    RuntimeSubgoalSpec("discover", "Discover source data"),
                    RuntimeSubgoalSpec("save", "Save discovered data", ("discover",)),
                ),
            )
        if completed == ["extract"]:
            return RuntimeDecision(action=RuntimeAction("write", subgoal="save"))
        return RuntimeDecision(complete=True)

    runtime = TaskRuntime(
        observe=lambda _runtime: {"completed": list(completed)},
        decide=decide,
        act=lambda action, _runtime: (completed.append(action.capability) or _success()),
        verify=lambda _action, outcome, _before, _after, _runtime: outcome.success,
        final_verify=lambda _runtime, observed: observed["completed"] == ["extract", "write"],
    )

    result = runtime.run("discover then save")

    assert result.success
    assert result.state.subgoals["discover"].status == "verified"
    assert result.state.subgoals["save"].status == "verified"


def test_runtime_blocks_dynamic_subgoal_when_dependency_is_not_verified() -> None:
    runtime = TaskRuntime(
        observe=lambda _runtime: {},
        decide=lambda _runtime, _observed: RuntimeDecision(
            action=RuntimeAction("write", subgoal="save"),
            subgoals=(
                RuntimeSubgoalSpec("discover", "Discover data"),
                RuntimeSubgoalSpec("save", "Save data", ("discover",)),
            ),
        ),
        act=lambda *_args: _success(),
        verify=lambda *_args: True,
    )

    result = runtime.run("save without discovery")

    assert result.state.status == "blocked"
    assert "discover" in result.state.message


def test_resume_continues_verified_checkpoint_without_replaying_prior_action(tmp_path: Path) -> None:
    calls: list[str] = []
    checkpoint = tmp_path / "resume.json"

    def decide(_runtime, observed):
        if not observed["first"]:
            return RuntimeDecision(action=RuntimeAction("first", subgoal="first"))
        if not observed["second"]:
            return RuntimeDecision(action=RuntimeAction("second", subgoal="second"))
        return RuntimeDecision(complete=True)

    runtime = TaskRuntime(
        observe=lambda _runtime: {"first": "first" in calls, "second": "second" in calls},
        decide=decide,
        act=lambda action, _runtime: (calls.append(action.capability) or _success()),
        verify=lambda _action, outcome, _before, _after, _runtime: outcome.success,
        final_verify=lambda _runtime, observed: observed["second"],
        checkpoint_path=checkpoint,
        max_steps=1,
    )
    first = runtime.run("two steps")
    restored = load_runtime_state(checkpoint)
    resumed = TaskRuntime(
        observe=lambda _runtime: {"first": "first" in calls, "second": "second" in calls},
        decide=decide,
        act=lambda action, _runtime: (calls.append(action.capability) or _success()),
        verify=lambda _action, outcome, _before, _after, _runtime: outcome.success,
        final_verify=lambda _runtime, observed: observed["second"],
        checkpoint_path=checkpoint,
        max_steps=2,
    ).resume(restored)

    assert first.state.status == "running"
    assert resumed.success
    assert calls == ["first", "second"]


def test_uncertain_checkpoint_is_never_automatically_resumed(tmp_path: Path) -> None:
    checkpoint = tmp_path / "uncertain.json"
    runtime = TaskRuntime(
        observe=lambda _runtime: {},
        decide=lambda _runtime, _observed: RuntimeDecision(action=RuntimeAction("send")),
        act=lambda *_args: ActionOutcome(
            state="uncertain", error="delivery unknown", dispatch_status="unknown", side_effect="external"
        ),
        verify=lambda *_args: False,
        checkpoint_path=checkpoint,
    )
    original = runtime.run("send message")
    restored = load_runtime_state(checkpoint)
    resumed = runtime.resume(restored)

    assert original.state.status == "uncertain"
    assert resumed.state.status == "uncertain"
    assert resumed.state.message == "delivery unknown"

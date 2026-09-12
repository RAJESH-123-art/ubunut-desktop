"""One stateful execution loop for dynamic desktop goals.

The runtime deliberately does not know how to click, type, or inspect a
particular application.  Existing browser, AT-SPI, visual, and task adapters
provide those operations.  This module owns the part that must be identical
for every adapter: observe real state, choose one action, verify the state
transition, preserve uncertainty, and recover without replaying an action
whose effect is unknown.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from core.atomic_write import atomic_write_json
from core.task_contract import ActionOutcome

RuntimeStatus = Literal[
    "running", "completed", "failed", "blocked", "uncertain", "paused_for_human", "cancelled"
]
SubgoalStatus = Literal[
    "planned", "running", "verified", "recovering", "blocked", "uncertain", "failed"
]


@dataclass(frozen=True)
class RuntimeAction:
    """One capability invocation chosen for the current observed state."""

    capability: str
    args: dict[str, Any] = field(default_factory=dict)
    subgoal: str = ""
    # A recovery action may be retried only when this is explicitly true.
    safe_to_retry: bool = False


@dataclass(frozen=True)
class RuntimeSubgoalSpec:
    """A planner-created subgoal that may be added while a goal is running."""

    id: str
    description: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.description.strip():
            raise ValueError("Runtime subgoals require an id and description")


@dataclass(frozen=True)
class RuntimeDecision:
    """The next transition selected by a local rule or an AI planner."""

    action: RuntimeAction | None = None
    complete: bool = False
    message: str = ""
    subgoals: tuple[RuntimeSubgoalSpec, ...] = ()

    def __post_init__(self) -> None:
        if self.complete and self.action is not None:
            raise ValueError("A runtime decision cannot be complete and contain an action")
        if not self.complete and self.action is None:
            raise ValueError("A running runtime decision requires an action")


@dataclass
class RuntimeEvent:
    """A durable, JSON-safe record of one observed action transition."""

    index: int
    phase: str
    timestamp: float
    observation: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "phase": self.phase,
            "timestamp": self.timestamp,
            "observation": self.observation,
            "action": self.action,
            "outcome": self.outcome,
            "message": self.message,
        }


@dataclass
class RuntimeSubgoal:
    """A dynamically discovered unit of verified work inside a user goal."""

    id: str
    description: str
    status: SubgoalStatus = "planned"
    verified_facts: dict[str, Any] = field(default_factory=dict)
    action_count: int = 0
    last_error: str = ""
    depends_on: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "verified_facts": self.verified_facts,
            "action_count": self.action_count,
            "last_error": self.last_error,
            "depends_on": list(self.depends_on),
        }


@dataclass
class RuntimeState:
    """The authoritative state for a single dynamic user goal."""

    goal: str
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: RuntimeStatus = "running"
    message: str = ""
    verified_facts: dict[str, Any] = field(default_factory=dict)
    subgoals: dict[str, RuntimeSubgoal] = field(default_factory=dict)
    plan_version: int = 1
    available_interfaces: set[str] = field(default_factory=set)
    # Written before dispatch.  If the process dies while an action is in
    # flight, a later invocation must not silently replay it.
    pending_action: dict[str, Any] = field(default_factory=dict)
    events: list[RuntimeEvent] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "goal": self.goal,
            "execution_id": self.execution_id,
            "status": self.status,
            "message": self.message,
            "verified_facts": self.verified_facts,
            "subgoals": {name: subgoal.payload() for name, subgoal in self.subgoals.items()},
            "plan_version": self.plan_version,
            "available_interfaces": sorted(self.available_interfaces),
            "pending_action": self.pending_action,
            "events": [event.payload() for event in self.events],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RuntimeResult:
    state: RuntimeState

    @property
    def success(self) -> bool:
        return self.state.status == "completed"


Observer = Callable[[RuntimeState], dict[str, Any]]
Decider = Callable[[RuntimeState, dict[str, Any]], RuntimeDecision]
Actor = Callable[[RuntimeAction, RuntimeState], ActionOutcome]
Verifier = Callable[
    [RuntimeAction, ActionOutcome, dict[str, Any], dict[str, Any], RuntimeState],
    bool | tuple[bool, dict[str, Any]],
]
Recovery = Callable[
    [RuntimeState, RuntimeAction, ActionOutcome, dict[str, Any], dict[str, Any]],
    RuntimeDecision | None,
]
Replanner = Callable[
    [RuntimeState, RuntimeAction, ActionOutcome, dict[str, Any], dict[str, Any]],
    RuntimeDecision | None,
]
FinalVerifier = Callable[[RuntimeState, dict[str, Any]], bool | tuple[bool, dict[str, Any]]]


class TaskRuntime:
    """Run a goal through one observable, recoverable execution authority.

    ``decide`` may be backed by cheap local rules first and an LLM only for
    ambiguous state.  The runtime itself remains model-agnostic, which keeps
    safety and completion semantics consistent across every control surface.
    """

    def __init__(
        self,
        *,
        observe: Observer,
        decide: Decider,
        act: Actor,
        verify: Verifier,
        final_verify: FinalVerifier | None = None,
        recover: Recovery | None = None,
        replan: Replanner | None = None,
        checkpoint_path: str | Path | None = None,
        max_steps: int = 50,
    ) -> None:
        self.observe = observe
        self.decide = decide
        self.act = act
        self.verify = verify
        self.final_verify = final_verify
        self.recover = recover
        self.replan = replan
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.max_steps = max(1, max_steps)

    @classmethod
    def with_capabilities(
        cls,
        *,
        capabilities: Any,
        observe: Observer,
        decide: Decider,
        verify: Verifier,
        final_verify: FinalVerifier | None = None,
        recover: Recovery | None = None,
        replan: Replanner | None = None,
        checkpoint_path: str | Path | None = None,
        max_steps: int = 50,
    ) -> TaskRuntime:
        """Construct a runtime that dispatches reusable named capabilities."""
        return cls(
            observe=observe,
            decide=decide,
            act=capabilities.execute,
            verify=verify,
            final_verify=final_verify,
            recover=recover,
            replan=replan,
            checkpoint_path=checkpoint_path,
            max_steps=max_steps,
        )

    def run(self, goal: str, *, state: RuntimeState | None = None) -> RuntimeResult:
        runtime = state or RuntimeState(goal=goal)
        if runtime.goal != goal:
            raise ValueError("Runtime state belongs to a different goal")
        if runtime.status != "running":
            return RuntimeResult(runtime)
        if runtime.pending_action:
            action_name = str(runtime.pending_action.get("capability", "action"))
            return self._finish(
                runtime,
                "uncertain",
                f"Interrupted action {action_name!r} may have been dispatched; automatic replay refused",
            )

        observation = self._observe(runtime, phase="initial")
        pending_recovery: RuntimeDecision | None = None
        for index in range(self.max_steps):
            decision = pending_recovery or self.decide(runtime, observation)
            pending_recovery = None
            self._apply_subgoals(runtime, decision.subgoals)
            if decision.complete:
                return self._finish_decision(runtime, observation, decision, index)

            assert decision.action is not None
            action = decision.action
            subgoal = self._subgoal(runtime, action)
            unmet = [
                identifier for identifier in subgoal.depends_on
                if runtime.subgoals.get(identifier) is None
                or runtime.subgoals[identifier].status != "verified"
            ]
            if unmet:
                subgoal.status = "blocked"
                return self._finish(
                    runtime,
                    "blocked",
                    f"Subgoal {subgoal.id!r} is waiting for verified dependencies: {', '.join(unmet)}",
                )
            subgoal.status = "running"
            subgoal.action_count += 1
            before = observation
            runtime.pending_action = {
                "capability": action.capability,
                "args": dict(action.args),
                "subgoal": action.subgoal,
                "safe_to_retry": action.safe_to_retry,
            }
            self._record(runtime, index, "prepared", before, action, None)
            outcome = self.act(action, runtime)
            runtime.pending_action = {}
            after = self._observe(runtime, phase="post_action")
            self._record(runtime, index, "action", before, action, outcome)

            if outcome.state == "paused_for_human":
                subgoal.status = "blocked"
                return self.pause_for_human(runtime, outcome.error or "Human intervention required")

            if outcome.state == "uncertain":
                recovery = self._recover(runtime, action, outcome, before, after, index)
                if recovery is None:
                    subgoal.status = "uncertain"
                    subgoal.last_error = outcome.error or "Action outcome is uncertain"
                    return self._finish(runtime, "uncertain", outcome.error or "Action outcome is uncertain")
                if recovery.action is not None and not recovery.action.safe_to_retry:
                    subgoal.status = "blocked"
                    return self._finish(
                        runtime,
                        "blocked",
                        "Recovery after an uncertain action must use an explicitly safe action",
                    )
                self._record(runtime, index, "recovery", after, recovery.action, None, recovery.message)
                if recovery.complete:
                    return self._finish_decision(runtime, after, recovery, index)
                pending_recovery = recovery
                observation = after
                continue

            verified, facts = self._verification(self.verify(action, outcome, before, after, runtime))
            # A skipped action is a verified transition only when the adapter
            # explicitly proves that its declared condition was false.  This
            # lets conditional plans advance without pretending an action ran.
            completed_without_dispatch = (
                outcome.state == "skipped"
                and outcome.dispatch_status in {"not_attempted", "not_dispatched"}
                and bool(outcome.data.get("condition_met") is False)
            )
            if (outcome.success or completed_without_dispatch) and verified:
                runtime.verified_facts.update(facts)
                subgoal.status = "verified"
                subgoal.verified_facts.update(facts)
                self._record(runtime, index, "verified", after, action, outcome)
                observation = after
                self._checkpoint(runtime)
                continue

            recovery = self._recover(runtime, action, outcome, before, after, index)
            if recovery is None:
                status: RuntimeStatus = "failed" if outcome.state == "failed" else "blocked"
                subgoal.status = "failed" if status == "failed" else "blocked"
                subgoal.last_error = outcome.error or "Action did not produce its verified state"
                return self._finish(runtime, status, outcome.error or "Action did not produce its verified state")
            if recovery.action is not None and not recovery.action.safe_to_retry:
                subgoal.status = "blocked"
                return self._finish(runtime, "blocked", "Recovery action was not explicitly marked safe to retry")
            subgoal.status = "recovering"
            self._record(runtime, index, "recovery", after, recovery.action, outcome, recovery.message)
            if recovery.complete:
                return self._finish_decision(runtime, after, recovery, index)
            pending_recovery = recovery
            observation = after

        # A bounded invocation did not prove the goal failed.  Preserve the
        # verified tree as running so a later resume can continue from the
        # next observation without replaying completed actions.
        runtime.message = f"Step safety limit ({self.max_steps}) reached; checkpoint is resumable"
        runtime.updated_at = time.time()
        self._checkpoint(runtime)
        return RuntimeResult(runtime)

    def resume(self, state: RuntimeState) -> RuntimeResult:
        """Resume only a checkpoint that has no unresolved uncertain action."""
        if state.status == "uncertain":
            return RuntimeResult(state)
        if state.status == "paused_for_human":
            state.status = "running"
            state.message = "Resumed after human intervention"
        return self.run(state.goal, state=state)

    @staticmethod
    def discover_capabilities(runtime: RuntimeState, capabilities: Any) -> list[Any]:
        """Expose only capabilities supported by live observed interfaces."""
        return capabilities.discover(runtime.available_interfaces)

    def _finish_decision(
        self,
        runtime: RuntimeState,
        observation: dict[str, Any],
        decision: RuntimeDecision,
        index: int,
    ) -> RuntimeResult:
        verified, facts = self._verification(
            self.final_verify(runtime, observation) if self.final_verify else (True, {})
        )
        self._record(runtime, index, "final_verification", observation, None, None, decision.message)
        if not verified:
            return self._finish(runtime, "failed", "Final goal verification failed")
        runtime.verified_facts.update(facts)
        return self._finish(runtime, "completed", decision.message or "Final goal verified")

    def _recover(
        self,
        runtime: RuntimeState,
        action: RuntimeAction,
        outcome: ActionOutcome,
        before: dict[str, Any],
        after: dict[str, Any],
        index: int,
    ) -> RuntimeDecision | None:
        if self.recover is None:
            recovery = None
        else:
            recovery = self.recover(runtime, action, outcome, before, after)
            if recovery is not None:
                self._record(runtime, index, "diagnosis", after, action, outcome, recovery.message)
                return recovery
        if self.replan is None:
            return None
        replacement = self.replan(runtime, action, outcome, before, after)
        if replacement is not None:
            runtime.plan_version += 1
            self._record(runtime, index, "replan", after, action, outcome, replacement.message)
        return replacement

    @staticmethod
    def _subgoal(runtime: RuntimeState, action: RuntimeAction) -> RuntimeSubgoal:
        identifier = action.subgoal.strip() or action.capability
        subgoal = runtime.subgoals.get(identifier)
        if subgoal is None:
            subgoal = RuntimeSubgoal(id=identifier, description=action.subgoal.strip() or action.capability)
            runtime.subgoals[identifier] = subgoal
        return subgoal

    @staticmethod
    def _apply_subgoals(runtime: RuntimeState, definitions: tuple[RuntimeSubgoalSpec, ...]) -> None:
        for definition in definitions:
            existing = runtime.subgoals.get(definition.id)
            if existing is None:
                runtime.subgoals[definition.id] = RuntimeSubgoal(
                    id=definition.id,
                    description=definition.description,
                    depends_on=definition.depends_on,
                )
                continue
            if (
                existing.description != definition.description
                or existing.depends_on != definition.depends_on
            ):
                raise ValueError(f"Dynamic subgoal {definition.id!r} changed after it was declared")

    def _observe(self, runtime: RuntimeState, *, phase: str) -> dict[str, Any]:
        try:
            observation = self.observe(runtime)
        except Exception as exc:
            return {"observation_error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(observation, dict):
            return {"observation_error": "Observer must return an object"}
        interfaces = observation.get("interfaces")
        if isinstance(interfaces, (list, tuple, set)) and all(isinstance(item, str) for item in interfaces):
            runtime.available_interfaces.update(interfaces)
        self._record(runtime, len(runtime.events), phase, observation, None, None)
        return observation

    @staticmethod
    def _verification(value: bool | tuple[bool, dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
        if isinstance(value, tuple):
            passed, facts = value
            return bool(passed), dict(facts)
        return bool(value), {}

    def _record(
        self,
        runtime: RuntimeState,
        index: int,
        phase: str,
        observation: dict[str, Any],
        action: RuntimeAction | None,
        outcome: ActionOutcome | None,
        message: str = "",
    ) -> None:
        runtime.events.append(RuntimeEvent(
            index=index,
            phase=phase,
            timestamp=time.time(),
            observation=dict(observation),
            action=(
                {"capability": action.capability, "args": action.args, "subgoal": action.subgoal,
                 "safe_to_retry": action.safe_to_retry}
                if action else {}
            ),
            outcome=(
                {"state": outcome.state, "error": outcome.error,
                 "dispatch_status": outcome.dispatch_status, "uncertainty": outcome.uncertainty,
                 "evidence": outcome.evidence, "metadata": outcome.metadata}
                if outcome is not None else {}
            ),
            message=message,
        ))
        runtime.updated_at = time.time()
        self._checkpoint(runtime)

    def _finish(self, runtime: RuntimeState, status: RuntimeStatus, message: str) -> RuntimeResult:
        runtime.status = status
        runtime.message = message
        runtime.updated_at = time.time()
        self._checkpoint(runtime)
        return RuntimeResult(runtime)

    def pause_for_human(self, runtime: RuntimeState, reason: str) -> RuntimeResult:
        """Persist a human-only boundary without treating it as failure."""
        return self._finish(runtime, "paused_for_human", reason)

    def _checkpoint(self, runtime: RuntimeState) -> None:
        if self.checkpoint_path is not None:
            atomic_write_json(self.checkpoint_path, runtime.payload())


def load_runtime_state(path: str | Path) -> RuntimeState:
    """Restore a durable runtime checkpoint without replaying any action."""
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported runtime checkpoint")
    events = [RuntimeEvent(**event) for event in payload.get("events", [])]
    raw_subgoals = payload.get("subgoals", {})
    subgoals = {
        str(name): RuntimeSubgoal(
            **{**value, "depends_on": tuple(value.get("depends_on", ()))},
        )
        for name, value in raw_subgoals.items()
        if isinstance(value, dict)
    }
    return RuntimeState(
        goal=str(payload["goal"]),
        execution_id=str(payload["execution_id"]),
        status=str(payload.get("status", "running")),
        message=str(payload.get("message", "")),
        verified_facts=dict(payload.get("verified_facts", {})),
        subgoals=subgoals,
        plan_version=int(payload.get("plan_version", 1)),
        available_interfaces={str(item) for item in payload.get("available_interfaces", []) if isinstance(item, str)},
        pending_action=dict(payload.get("pending_action", {})),
        events=events,
        created_at=float(payload.get("created_at", time.time())),
        updated_at=float(payload.get("updated_at", time.time())),
    )

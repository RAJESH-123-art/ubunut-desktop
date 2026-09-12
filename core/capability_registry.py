"""Explicit reusable capabilities for the authoritative task runtime.

Existing task modules remain useful adapters, but they no longer need to be
the planning model.  A capability declares what it accepts, how risky it is,
what can prove its effect, and hints the runtime can use for recovery.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from core.automation_service import (
    AutomationService,
    ExecutionRequest,
    automation_service,
)
from core.task_contract import (
    ActionOutcome,
    SideEffect,
    normalize_task_result,
)
from core.task_runtime import RuntimeAction, RuntimeState

CapabilityExecutor = Callable[[dict[str, Any], RuntimeState], object]


@dataclass(frozen=True)
class CapabilityContract:
    """The planner-visible, safety-relevant contract for one capability."""

    name: str
    description: str
    side_effect: SideEffect = "read"
    inputs: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    observable_outcomes: tuple[str, ...] = ()
    recovery_hints: tuple[str, ...] = ()
    requires_confirmation: bool = False
    interfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capability:
    contract: CapabilityContract
    execute: CapabilityExecutor = field(repr=False, compare=False)


class CapabilityRegistry:
    """A small registry that dispatches named capabilities with one contract."""

    def __init__(self) -> None:
        self._items: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        name = capability.contract.name.strip()
        if not name:
            raise ValueError("Capability name cannot be empty")
        if name in self._items:
            raise ValueError(f"Capability {name!r} is already registered")
        self._items[name] = capability

    def get(self, name: str) -> Capability | None:
        return self._items.get(name)

    def contracts(self) -> list[CapabilityContract]:
        return [self._items[name].contract for name in sorted(self._items)]

    def discover(self, interfaces: Iterable[str]) -> list[CapabilityContract]:
        """Return capabilities supported by currently observed interfaces."""
        available = set(interfaces)
        return [
            contract for contract in self.contracts()
            if not contract.interfaces or set(contract.interfaces).issubset(available)
        ]

    def execute(self, action: RuntimeAction, state: RuntimeState) -> ActionOutcome:
        capability = self.get(action.capability)
        if capability is None:
            return ActionOutcome(
                state="failed",
                error=f"Unknown capability {action.capability!r}",
                dispatch_status="not_attempted",
                side_effect="read",
            )
        required = set(capability.contract.interfaces)
        if required and state.available_interfaces and not required.issubset(state.available_interfaces):
            return ActionOutcome(
                state="blocked",
                error=(
                    f"Capability {action.capability!r} requires live interfaces "
                    f"{', '.join(sorted(required))}; observed interfaces are "
                    f"{', '.join(sorted(state.available_interfaces)) or 'none'}"
                ),
                dispatch_status="not_attempted",
                side_effect=capability.contract.side_effect,
                metadata={"capability": action.capability, "missing_interfaces": sorted(required - state.available_interfaces)},
            )
        try:
            raw = capability.execute(dict(action.args), state)
            result = normalize_task_result(raw, side_effect=capability.contract.side_effect)
        except Exception as exc:
            return ActionOutcome(
                state="failed",
                error=f"Capability {action.capability!r} raised {type(exc).__name__}: {exc}",
                dispatch_status="unknown",
                side_effect=capability.contract.side_effect,
            )
        return ActionOutcome(
            state=result.state,
            data=result.data,
            evidence=result.evidence,
            confidence=result.confidence,
            uncertainty=result.uncertainty,
            dispatch_status=result.dispatch_status,
            side_effect=result.side_effect,
            warnings=result.warnings,
            error=result.error,
            metadata={**result.metadata, "capability": action.capability},
        )


class RegisteredTaskCapabilities:
    """Expose existing registered task modules as runtime capabilities.

    This adapter is intentionally dynamic: when a task module is registered,
    it becomes available without a new runtime-specific Python file.
    """

    PREFIX = "task:"

    def __init__(
        self,
        *,
        service: AutomationService = automation_service,
        approved: bool = False,
        source: str = "task_runtime",
        resources: dict[str, Any] | None = None,
    ) -> None:
        self.service = service
        self.approved = approved
        self.source = source
        self.resources = dict(resources or {})

    def install(self, registry: CapabilityRegistry, task_names: Iterable[str] | None = None) -> None:
        from tasks import get_task_spec, list_tasks

        for task_name in task_names or list_tasks():
            spec = get_task_spec(task_name)
            if spec is None:
                continue
            registry.register(Capability(
                CapabilityContract(
                    name=f"{self.PREFIX}{task_name}",
                    description=spec.description or f"Execute registered task {task_name}",
                    side_effect=spec.side_effect,
                    inputs=tuple(parameter.name for parameter in spec.parameters),
                    observable_outcomes=("TaskResult evidence",),
                    recovery_hints=("Observe current state before retrying consequential actions",),
                    requires_confirmation=spec.requires_confirmation,
                    interfaces=("registered_task",),
                ),
                execute=lambda args, _state, name=task_name: self.service.execute(
                    ExecutionRequest(
                        task=name,
                        params=dict(args.get("params", args)),
                        approved=self.approved,
                        source=self.source,
                    ),
                    self.resources,
                ),
            ))


class StructuredCapabilities:
    """Expose the proven structured browser, desktop, vision and file actions.

    Every entry here maps to an existing generic ``StructuredExecutor``
    primitive.  This is an adapter layer, not a collection of new task
    scripts; it keeps one executor alive so browser ownership, repair, and
    evidence rules remain consistent across a runtime goal.
    """

    PREFIX = "structured."
    _GROUPS: dict[str, tuple[str, ...]] = {  # noqa: RUF012 — class-level constant registry
        "browser": (
            "navigate", "click", "select_option", "fill", "press", "scroll",
            "drag_drop", "wait_for", "extract_text", "read_field", "check",
            "upload_file", "open_tab", "switch_tab", "close_tab", "download",
        ),
        "desktop": (
            "launch_app", "focus_app", "click_ui", "type_ui", "copy_ui",
            "paste_ui", "hotkey_ui", "wait_ui", "read_ui",
        ),
        "vision": ("visual_click", "visual_drag"),
        "local": ("transform_text", "assert_value", "wait", "verify_file"),
        "task": ("task",),
        "universal": ("cap",),
    }

    def __init__(
        self,
        *,
        executor: Any | None = None,
        page: Any = None,
        goal: str = "",
        approval_callback: Callable[[Any], bool] | None = None,
        approve_all: bool = False,
    ) -> None:
        if executor is None:
            from core.structured_automation import StructuredExecutor

            executor = StructuredExecutor(
                approve_all=approve_all,
                repair_approval_callback=approval_callback,
            )
        self.executor = executor
        self.page = page
        self.goal = goal
        self.approval_callback = approval_callback

    def install(self, registry: CapabilityRegistry) -> None:

        for group, actions in self._GROUPS.items():
            for action_name in actions:
                capability_name = f"{self.PREFIX}{action_name}"
                registry.register(Capability(
                    CapabilityContract(
                        name=capability_name,
                        description=f"{group} capability: {action_name}",
                        side_effect=self._side_effect(action_name),
                        inputs=("action arguments",),
                        preconditions=("Use the strongest available semantic interface",),
                        observable_outcomes=("Structured executor evidence",),
                        recovery_hints=("Refresh observation and use structured failed-step repair",),
                        requires_confirmation=self._side_effect(action_name) != "read",
                        interfaces=self._interfaces_for(group, action_name),
                    ),
                    execute=lambda args, state, name=action_name: self._execute(name, args, state),
                ))

    def _execute(self, action_name: str, args: dict[str, Any], state: RuntimeState) -> object:
        """Resolve prior runtime evidence before validating an isolated step."""
        from core.structured_automation import PlanStep, _resolve_step_references

        evidence = list(state.verified_facts.get("structured_evidence", ()))
        step = PlanStep(
            action_name,
            {key: value for key, value in args.items() if key not in {"expect", "when"}},
            dict(args.get("expect", {})),
            dict(args.get("when", {})),
        )
        resolved = PlanStep(
            step.action,
            _resolve_step_references(step.args, evidence),
            _resolve_step_references(step.expect, evidence),
            _resolve_step_references(step.when, evidence),
        )
        return self.executor.execute_step(
            resolved,
            goal=self.goal,
            evidence=evidence,
            approval_callback=self.approval_callback,
            page=self.page,
        )

    def close(self) -> None:
        self.executor.close()

    @staticmethod
    def _side_effect(action: str) -> SideEffect:
        if action in {"wait", "wait_for", "extract_text", "read_field", "read_ui", "verify_file", "transform_text", "assert_value"}:
            return "read"
        if action == "download":
            return "local_write"
        # "cap" delegates to per-capability contracts; the worst case is
        # external, and the executor resolves per-name side effects.
        return "external"

    @staticmethod
    def _interfaces_for(group: str, action: str) -> tuple[str, ...]:
        if group == "local":
            return ("filesystem",) if action == "verify_file" else ()
        if group == "universal":
            return ()
        if group == "task":
            # Registered tasks dispatch through the shared task boundary,
            # which enforces its own dynamic approval policy per action.
            return ()
        return ({
            "browser": "browser_dom",
            "desktop": "atspi",
            "vision": "vision",
        }[group],)

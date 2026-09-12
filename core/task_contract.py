"""Typed metadata and compatibility adapters for executable tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SideEffect = Literal["read", "local_write", "external", "destructive"]
ParameterKind = Literal["string", "number", "integer", "boolean", "object", "array"]
OutcomeState = Literal[
    "succeeded",
    "failed",
    "skipped",
    "blocked",
    "paused_for_human",
    "cancelled",
    "uncertain",
]
DispatchStatus = Literal[
    "not_attempted",
    "not_dispatched",
    "dispatched",
    "confirmed",
    "unknown",
]

_OUTCOME_STATES = {
    "succeeded", "failed", "skipped", "blocked", "paused_for_human", "cancelled", "uncertain",
}
_DISPATCH_STATUSES = {
    "not_attempted", "not_dispatched", "dispatched", "confirmed", "unknown",
}
_SIDE_EFFECTS = {"read", "local_write", "external", "destructive"}
_UNSET = object()


@dataclass(frozen=True)
class TaskParameter:
    """One planner-visible task argument."""

    name: str
    kind: ParameterKind = "string"
    required: bool = False
    choices: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class TaskSpec:
    """Static execution properties used by policy and orchestration layers."""

    name: str
    side_effect: SideEffect = "external"
    requires_confirmation: bool = True
    thread_safe: bool = False
    description: str = ""
    parameters: tuple[TaskParameter, ...] = ()
    allow_extra_parameters: bool = False


@dataclass
class ActionOutcome:
    """Canonical outcome for one action or dispatch attempt."""

    state: OutcomeState
    data: Any = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    uncertainty: list[str] = field(default_factory=list)
    dispatch_status: DispatchStatus = "unknown"
    side_effect: SideEffect = "external"
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in _OUTCOME_STATES:
            raise ValueError(f"Invalid outcome state: {self.state!r}")
        if self.dispatch_status not in _DISPATCH_STATUSES:
            raise ValueError(f"Invalid dispatch status: {self.dispatch_status!r}")
        if self.side_effect not in _SIDE_EFFECTS:
            raise ValueError(f"Invalid side-effect classification: {self.side_effect!r}")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, (int, float)
            ):
                raise TypeError("confidence must be a number between 0.0 and 1.0")
            if not 0.0 <= float(self.confidence) <= 1.0:
                raise ValueError("confidence must be between 0.0 and 1.0")
            self.confidence = float(self.confidence)
        if not all(isinstance(reason, str) for reason in self.uncertainty):
            raise TypeError("uncertainty entries must be strings")

    @property
    def success(self) -> bool:
        return self.state == "succeeded"

    @property
    def ok(self) -> bool:
        return self.success

    def __bool__(self) -> bool:
        return self.success


@dataclass(init=False)
class TaskResult(ActionOutcome):
    """Aggregate task result with legacy ``success`` and ``ok`` constructors."""

    actions: list[ActionOutcome] = field(default_factory=list)

    def __init__(
        self,
        success: bool | None = None,
        data: Any = _UNSET,
        evidence: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
        error: str | None = None,
        *,
        ok: bool | None = None,
        state: OutcomeState | None = None,
        confidence: float | None = None,
        uncertainty: list[str] | None = None,
        dispatch_status: DispatchStatus = "unknown",
        side_effect: SideEffect = "external",
        metadata: dict[str, Any] | None = None,
        actions: list[ActionOutcome] | None = None,
    ) -> None:
        if success is not None and not isinstance(success, bool):
            raise TypeError("success must be boolean")
        if ok is not None and not isinstance(ok, bool):
            raise TypeError("ok must be boolean")
        if success is not None and ok is not None and success != ok:
            raise ValueError("success and ok cannot disagree")

        legacy_success = success if success is not None else ok
        if state is None:
            if legacy_success is None:
                raise TypeError("TaskResult requires success, ok, or state")
            state = "succeeded" if legacy_success else "failed"
        elif legacy_success is not None and legacy_success != (state == "succeeded"):
            raise ValueError("state cannot disagree with success or ok")

        if data is _UNSET:
            data = None if success is None and ok is not None else {}

        ActionOutcome.__init__(
            self,
            state=state,
            data=data,
            evidence=list(evidence or ()),
            confidence=confidence,
            uncertainty=list(uncertainty or ()),
            dispatch_status=dispatch_status,
            side_effect=side_effect,
            warnings=list(warnings or ()),
            error=error,
            metadata=dict(metadata or {}),
        )
        self.actions = list(actions or ())
        if not all(isinstance(action, ActionOutcome) for action in self.actions):
            raise TypeError("actions entries must be ActionOutcome instances")


def validate_task_params(spec: TaskSpec, params: object) -> dict[str, Any]:
    """Validate untrusted task parameters against a registered contract."""
    if not isinstance(params, dict):
        raise TypeError(f"Task {spec.name!r} params must be an object")
    allowed = {parameter.name: parameter for parameter in spec.parameters}
    if not spec.allow_extra_parameters:
        unknown = sorted(set(params) - set(allowed))
        if unknown:
            raise ValueError(f"Task {spec.name!r} has unknown params: {unknown}")
    missing = sorted(
        parameter.name
        for parameter in spec.parameters
        if parameter.required and parameter.name not in params
    )
    if missing:
        raise ValueError(f"Task {spec.name!r} is missing required params: {missing}")

    kind_types: dict[ParameterKind, type | tuple[type, ...]] = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    validated = dict(params)
    for name, value in validated.items():
        parameter = allowed.get(name)
        if parameter is None:
            continue
        if not isinstance(value, kind_types[parameter.kind]):
            raise TypeError(
                f"Task {spec.name!r} param {name!r} must be {parameter.kind}"
            )
        if parameter.kind in {"number", "integer"} and isinstance(value, bool):
            raise TypeError(
                f"Task {spec.name!r} param {name!r} must be {parameter.kind}, not boolean"
            )
        if parameter.choices and str(value).lower() not in {
            choice.lower() for choice in parameter.choices
        }:
            raise ValueError(
                f"Task {spec.name!r} param {name!r} must be one of {parameter.choices}"
            )
    return validated


def normalize_task_result(
    value: object,
    *,
    side_effect: SideEffect | None = None,
    dispatch_status: DispatchStatus | None = None,
) -> TaskResult:
    """Adapt legacy and action-level values into the unified task contract."""
    if isinstance(value, TaskResult):
        if side_effect is None and dispatch_status is None:
            return value
        return TaskResult(
            state=value.state,
            data=value.data,
            evidence=value.evidence,
            confidence=value.confidence,
            uncertainty=value.uncertainty,
            dispatch_status=dispatch_status or value.dispatch_status,
            side_effect=side_effect or value.side_effect,
            warnings=value.warnings,
            error=value.error,
            metadata=value.metadata,
            actions=value.actions,
        )

    if isinstance(value, ActionOutcome):
        return TaskResult(
            state=value.state,
            data=value.data,
            evidence=value.evidence,
            confidence=value.confidence,
            uncertainty=value.uncertainty,
            dispatch_status=dispatch_status or value.dispatch_status,
            side_effect=side_effect or value.side_effect,
            warnings=value.warnings,
            error=value.error,
            metadata=value.metadata,
            actions=[value],
        )

    resolved_side_effect = side_effect or "external"
    resolved_dispatch = dispatch_status or "unknown"
    if isinstance(value, bool):
        return TaskResult(
            value,
            confidence=None,
            uncertainty=["legacy_boolean_result"],
            dispatch_status=resolved_dispatch,
            side_effect=resolved_side_effect,
        )
    if value is None:
        return TaskResult(
            False,
            error="Task returned no result",
            confidence=None,
            uncertainty=["legacy_none_result"],
            dispatch_status=resolved_dispatch,
            side_effect=resolved_side_effect,
        )

    legacy_status = getattr(value, "success", None)
    if not isinstance(legacy_status, bool):
        legacy_status = getattr(value, "ok", None)
    if isinstance(legacy_status, bool):
        raw_metadata = getattr(value, "metadata", {})
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_evidence = getattr(value, "evidence", [])
        evidence = raw_evidence if isinstance(raw_evidence, list) else []
        raw_warnings = getattr(value, "warnings", [])
        warnings = raw_warnings if isinstance(raw_warnings, list) else []
        return TaskResult(
            legacy_status,
            data=getattr(value, "data", None),
            evidence=evidence,
            warnings=warnings,
            error=getattr(value, "error", None),
            confidence=None,
            uncertainty=["legacy_result_adapter"],
            dispatch_status=resolved_dispatch,
            side_effect=resolved_side_effect,
            metadata=metadata,
        )

    return TaskResult(
        bool(value),
        confidence=None,
        uncertainty=["result_truthiness_adapter"],
        dispatch_status=resolved_dispatch,
        side_effect=resolved_side_effect,
        metadata={"legacy_type": type(value).__name__},
    )

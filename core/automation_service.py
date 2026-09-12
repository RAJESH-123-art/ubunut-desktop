"""Unified execution boundary for every registered desktop task.

This module is deliberately small: planners decide *what* should happen and
task modules know *how* to do it.  The service owns the cross-cutting rules
that must be identical regardless of the entry point: parameter validation,
approval, resource cleanup, result normalization, and a privacy-conscious
local execution history.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.action_policy import requires_approval
from core.atomic_write import atomic_write_json
from core.task_contract import TaskResult, normalize_task_result, validate_task_params

_DEFAULT_HISTORY = Path.home() / ".config" / "desktop_automation" / "execution_history.json"
_SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "message", "text"}


def _redact(value: Any, *, key: str = "") -> Any:
    """Return JSON-safe history data without retaining user-provided secrets."""
    if key.lower() in _SENSITIVE_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {str(name): _redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class ExecutionRequest:
    """One concrete task invocation after planning and before execution."""

    task: str
    params: dict[str, Any] = field(default_factory=dict)
    approved: bool = False
    source: str = "direct"
    execution_id: str = ""


@dataclass
class ExecutionRecord:
    """Small durable audit record suitable for a user-facing activity history."""

    id: str
    task: str
    source: str
    params: dict[str, Any]
    started_at: float
    finished_at: float
    state: str
    error: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "source": self.source,
            "params": self.params,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "state": self.state,
            "error": self.error,
        }


class AutomationService:
    """Execute registered tasks consistently from CLI, workflow, or planners."""

    def __init__(self, history_path: Path = _DEFAULT_HISTORY, history_limit: int = 200) -> None:
        self.history_path = history_path
        self.history_limit = max(1, history_limit)

    def execute(self, request: ExecutionRequest, resources: dict[str, Any] | None = None) -> TaskResult:
        from tasks import get_task, get_task_spec

        started_at = time.time()
        record_id = request.execution_id or uuid.uuid4().hex
        params = dict(request.params)
        spec = get_task_spec(request.task)
        if spec is None:
            return self._finish(
                record_id, request, params, started_at,
                TaskResult(False, error=f"Unknown registered task {request.task!r}", dispatch_status="not_attempted"),
            )

        try:
            params = validate_task_params(spec, params)
        except (TypeError, ValueError) as exc:
            return self._finish(
                record_id, request, params, started_at,
                TaskResult(False, error=str(exc), dispatch_status="not_attempted", side_effect=spec.side_effect),
            )

        if requires_approval(request.task, params) and not request.approved:
            return self._finish(
                record_id, request, params, started_at,
                TaskResult(False, state="blocked", error="This exact action requires approval", dispatch_status="not_attempted", side_effect=spec.side_effect),
            )

        # These flags are implementation guards inside legacy task modules.
        # They are derived only after the policy check, never accepted from a
        # planner or a caller as a way to bypass approval.
        if request.task == "run_command":
            params["authorized"] = request.approved
        elif request.task == "system_power":
            params["confirm"] = request.approved

        module = get_task(request.task)
        if module is None:
            return self._finish(
                record_id, request, params, started_at,
                TaskResult(False, error=f"Task module {request.task!r} could not be loaded", dispatch_status="not_attempted", side_effect=spec.side_effect),
            )

        task_resources = dict(resources or {})
        created_resources: dict[str, Any] | None = None
        try:
            created_resources = module.setup()
            task_resources = {**task_resources, **created_resources}
            raw = module.execute(params, task_resources)
            result = normalize_task_result(raw, side_effect=spec.side_effect)
        except Exception as exc:
            logger.exception(f"Task {request.task!r} failed")
            result = TaskResult(False, error=str(exc), dispatch_status="unknown", side_effect=spec.side_effect)
        finally:
            if created_resources is not None:
                try:
                    module.cleanup(task_resources)
                except Exception as exc:
                    logger.warning(f"Task {request.task!r} cleanup failed: {exc}")
                    if result.success:
                        result = TaskResult(False, error=f"cleanup failed: {exc}", dispatch_status=result.dispatch_status, side_effect=spec.side_effect)

        return self._finish(record_id, request, params, started_at, result)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.history_path.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload[-max(0, limit):] if isinstance(item, dict)][::-1]

    def _finish(self, record_id: str, request: ExecutionRequest, params: dict[str, Any], started_at: float, result: TaskResult) -> TaskResult:
        record = ExecutionRecord(
            id=record_id,
            task=request.task,
            source=request.source,
            params=_redact(params),
            started_at=started_at,
            finished_at=time.time(),
            state=result.state,
            error=result.error or "",
        )
        self._append_record(record)
        return result

    def _append_record(self, record: ExecutionRecord) -> None:
        try:
            existing = list(reversed(self.recent(self.history_limit)))
            existing.append(record.payload())
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.history_path, existing[-self.history_limit:])
        except OSError as exc:
            logger.warning(f"Could not save execution history: {exc}")


automation_service = AutomationService()

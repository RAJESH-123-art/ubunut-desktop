"""Inspection and lifecycle controls for structured execution checkpoints."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.atomic_write import atomic_write_json
from core.structured_automation import (
    ExecutionResult,
    StructuredExecutor,
    StructuredPlan,
    StructuredPlanner,
    describe_plan,
    plan_fingerprint,
    validate_plan,
)

_SESSION_ID_RE = re.compile(r"^[a-f0-9]{20}$")
_DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "logs" / "structured_sessions"


def _session_path(session_id: str, directory: Path) -> Path:
    normalized = session_id.removesuffix(".json").lower()
    if not _SESSION_ID_RE.fullmatch(normalized):
        raise ValueError("Session ID must be the 20-character hexadecimal goal hash")
    return directory / f"{normalized}.json"


def load_session(session_id: str, *, directory: Path = _DEFAULT_DIRECTORY) -> dict[str, Any]:
    path = _session_path(session_id, directory)
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        raise FileNotFoundError(f"Structured session {session_id!r} was not found") from exc
    if not isinstance(payload, dict):
        raise TypeError("Structured session payload must be an object")
    plan = validate_plan(payload.get("plan"))
    payload["session_id"] = path.stem
    payload["plan_description"] = describe_plan(plan)
    return payload


def list_sessions(
    *,
    directory: Path = _DEFAULT_DIRECTORY,
    include_completed: bool = False,
) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    sessions: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if not _SESSION_ID_RE.fullmatch(path.stem):
            continue
        try:
            payload = load_session(path.stem, directory=directory)
        except (OSError, TypeError, ValueError):
            continue
        if payload.get("complete") and not include_completed:
            continue
        plan = payload.get("plan", {})
        steps = plan.get("steps", []) if isinstance(plan, dict) else []
        sessions.append({
            "session_id": path.stem,
            "goal": str(payload.get("goal", "")),
            "summary": str(plan.get("summary", "")) if isinstance(plan, dict) else "",
            "completed_steps": int(payload.get("completed_steps", 0)),
            "total_steps": len(steps) if isinstance(steps, list) else 0,
            "in_progress": payload.get("in_progress"),
            "phase": str(payload.get("phase", "")),
            "uncertain": bool(payload.get("uncertain", False)),
            "complete": bool(payload.get("complete", False)),
            "abandoned": bool(payload.get("abandoned", False)),
            "error": str(payload.get("error", "")),
            "updated_at": float(payload.get("updated_at", 0.0)),
        })
    return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)


def resume_session(
    session_id: str,
    *,
    directory: Path = _DEFAULT_DIRECTORY,
    approve_all: bool = False,
    approval_callback: Callable[[StructuredPlan], bool] | None = None,
) -> ExecutionResult:
    """Resume the exact stored plan without generating a replacement plan."""
    path = _session_path(session_id, directory)
    payload = load_session(session_id, directory=directory)
    if payload.get("complete"):
        return ExecutionResult(False, "Session is already complete or abandoned")
    if payload.get("uncertain"):
        return ExecutionResult(
            False,
            "Interrupted consequential step has uncertain outcome; automatic replay refused",
            int(payload.get("completed_steps", 0)),
            list(payload.get("evidence", [])),
        )
    goal = str(payload.get("goal", ""))
    if not goal:
        return ExecutionResult(False, "Session has no resumable goal")
    plan = validate_plan(payload.get("plan"))
    executor = StructuredExecutor(
        approve_all=approve_all,
        execution_id=str(payload.get("execution_id") or "") or None,
        repair_approval_callback=approval_callback,
    )
    approval_error = executor._approval_error(plan)
    if approval_error and not approve_all:
        if approval_callback is None:
            return ExecutionResult(False, approval_error)
        fingerprint = plan_fingerprint(plan)
        if not approval_callback(plan):
            return ExecutionResult(False, "Structured plan was not approved")
        if plan_fingerprint(plan) != fingerprint:
            return ExecutionResult(False, "Structured plan changed after approval")
        executor.approve_all = True
    planner = StructuredPlanner()
    if planner.available():
        executor.repair_callback = planner.repair_step
    return executor.execute(
        plan,
        checkpoint_path=path,
        resume=True,
        goal=goal,
    )


def abandon_session(session_id: str, *, directory: Path = _DEFAULT_DIRECTORY) -> dict[str, Any]:
    payload = load_session(session_id, directory=directory)
    payload.pop("session_id", None)
    payload.pop("plan_description", None)
    payload.update({
        "complete": True,
        "abandoned": True,
        "in_progress": None,
        "updated_at": time.time(),
    })
    atomic_write_json(_session_path(session_id, directory), payload)
    return payload

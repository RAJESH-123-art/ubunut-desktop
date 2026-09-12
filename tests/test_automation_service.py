from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.automation_service import AutomationService, ExecutionRequest
from core.task_contract import TaskResult, TaskSpec


def test_service_validates_then_executes_and_records_redacted_history(monkeypatch, tmp_path: Path) -> None:
    calls: list[object] = []

    def setup() -> dict[str, object]:
        calls.append("setup")
        return {"created": True}

    def execute(params: dict[str, object], resources: dict[str, object]) -> TaskResult:
        calls.append((params, resources["created"]))
        return TaskResult(True, data={"saved": True})

    def cleanup(resources: dict[str, object]) -> None:
        calls.append(("cleanup", resources["created"]))

    module = SimpleNamespace(setup=setup, execute=execute, cleanup=cleanup)
    spec = TaskSpec(name="demo", side_effect="local_write", parameters=(), allow_extra_parameters=True)
    monkeypatch.setattr("tasks.get_task", lambda name: module)
    monkeypatch.setattr("tasks.get_task_spec", lambda name: spec)

    service = AutomationService(tmp_path / "history.json")
    result = service.execute(
        ExecutionRequest("demo", {"text": "private value"}, approved=True, source="test")
    )

    assert result.success
    assert calls == ["setup", ({"text": "private value"}, True), ("cleanup", True)]
    history = service.recent()
    assert history[0]["task"] == "demo"
    assert history[0]["params"] == {"text": "<redacted>"}


def test_service_blocks_consequential_task_without_approval(monkeypatch, tmp_path: Path) -> None:
    spec = TaskSpec(name="file_operations", side_effect="destructive", parameters=(), allow_extra_parameters=True)
    monkeypatch.setattr("tasks.get_task_spec", lambda name: spec)

    result = AutomationService(tmp_path / "history.json").execute(
        ExecutionRequest("file_operations", {"operation": "delete"})
    )

    assert result.state == "blocked"
    assert result.dispatch_status == "not_attempted"

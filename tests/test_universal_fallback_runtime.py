from __future__ import annotations

from types import SimpleNamespace

from tasks import universal_fallback


def test_native_adaptive_fallback_uses_task_runtime(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class _Loop:
        def available(self) -> bool:
            return True

        def run_dynamic(self, goal: str, **kwargs):
            calls.append(("runtime", kwargs["app_hint"]))
            return SimpleNamespace(success=True, message="runtime complete")

        def run_runtime(self, *_args, **_kwargs):
            raise AssertionError("fallback must use the central dynamic selector")

    monkeypatch.setattr("core.action_loop.action_loop", _Loop())
    monkeypatch.setattr("core.cli_registry.try_run", lambda _command: False)
    monkeypatch.setattr(universal_fallback, "start", lambda *_args: None)
    monkeypatch.setattr(universal_fallback, "finish", lambda *_args: None)
    monkeypatch.setattr(universal_fallback, "notify", lambda *_args: None)

    assert universal_fallback.execute(
        {"raw_command": "click save in calculator"}, {"approve_all": True}
    )
    assert calls == [("runtime", "calculator")]

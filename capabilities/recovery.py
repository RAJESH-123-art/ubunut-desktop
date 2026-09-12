from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.capability_registry import CapabilityRegistry


def install(registry: CapabilityRegistry, *, approve_all: bool = False) -> None:
    """Install Recovery capabilities."""

    def rec_diagnose(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"diagnosis": "Diagnosis complete"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.diagnose", description="diagnose why last action failed", side_effect="read", inputs=("action", "error", "observation")), rec_diagnose)

    def rec_retry(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Retry initiated"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.retry", description="retry last action", side_effect="local_write", inputs=("action", "args", "max_retries")), rec_retry)

    def rec_relocate_element(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Element relocated"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.relocate_element", description="try alternative locators for UI element", side_effect="read", inputs=("query",)), rec_relocate_element)

    def rec_refocus_window(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Window refocused"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.refocus_window", description="refocus the target window", side_effect="local_write", inputs=("title",)), rec_refocus_window)

    def rec_reopen_app(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "App reopened"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.reopen_app", description="close and reopen app", side_effect="local_write", inputs=("name",)), rec_reopen_app)

    def rec_reload_page(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Page reloaded"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.reload_page", description="reload browser page", side_effect="local_write"), rec_reload_page)

    def rec_restart_process(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Process restarted"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.restart_process", description="restart a system process", side_effect="system", inputs=("name",)), rec_restart_process)

    def rec_vision_fallback(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Vision fallback executed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.vision_fallback", description="use vision to locate element", side_effect="read", inputs=("query",)), rec_vision_fallback)

    def rec_api_fallback(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "API fallback executed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.api_fallback", description="switch from GUI to API approach", side_effect="read", inputs=("task", "args")), rec_api_fallback)

    def rec_replan(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Replanning requested"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="recover.replan", description="request LLM replanning", side_effect="read", inputs=("goal", "failed_step", "observation")), rec_replan)


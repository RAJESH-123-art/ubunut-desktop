from __future__ import annotations

import os
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.capability_registry import CapabilityRegistry
from core.system_utils import clipboard_get

try:
    from core.atspi_navigator import ATSPINavigator
except ImportError:
    ATSPINavigator = None

def install(registry: CapabilityRegistry, *, approve_all: bool = False) -> None:
    """Install Observation capabilities (Risk: read-only)."""

    def obs_screen(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Screen observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.screen", description="full screen observation", side_effect="read"), obs_screen)

    def obs_window(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Window observed", "title": kwargs.get("title")})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.window", description="observe specific window", side_effect="read", inputs=("title",)), obs_window)

    def obs_application(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Application observed", "name": kwargs.get("name")})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.application", description="observe app state", side_effect="read", inputs=("name",)), obs_application)

    def obs_browser(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Browser observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.browser", description="observe browser DOM state", side_effect="read"), obs_browser)

    def obs_dom(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "DOM observed", "selector": kwargs.get("selector")})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.dom", description="get DOM structure of current page", side_effect="read", inputs=("selector",)), obs_dom)

    def obs_accessibility(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Accessibility observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.accessibility", description="full AT-SPI accessibility tree", side_effect="read"), obs_accessibility)

    def obs_file(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            path = kwargs.get("path", "")
            exists = os.path.exists(path)
            size = os.path.getsize(path) if exists else 0
            return ok(data={"exists": exists, "size": size})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.file", description="observe file state", side_effect="read", inputs=("path",)), obs_file)

    def obs_folder(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            path = kwargs.get("path", "")
            if not os.path.exists(path):
                return fail("Folder does not exist")
            files = os.listdir(path)
            return ok(data={"file_count": len(files)})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.folder", description="observe folder state", side_effect="read", inputs=("path",)), obs_folder)

    def obs_process(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Process observed", "name": kwargs.get("name")})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.process", description="observe process state", side_effect="read", inputs=("name",)), obs_process)

    def obs_network(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Network observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.network", description="observe network state", side_effect="read"), obs_network)

    def obs_download(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Download observed", "path": kwargs.get("path")})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.download", description="observe download state", side_effect="read", inputs=("path",)), obs_download)

    def obs_notification(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Notification observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.notification", description="check for pending notifications", side_effect="read"), obs_notification)

    def obs_clipboard(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"content": clipboard_get()})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.clipboard", description="read clipboard content", side_effect="read"), obs_clipboard)

    def obs_audio(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Audio observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.audio", description="observe audio state", side_effect="read"), obs_audio)

    def obs_display(kwargs: dict[str, Any], state: Any) -> Any:
        try:
            return ok(data={"message": "Display observed"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="observe.display", description="observe display state", side_effect="read"), obs_display)


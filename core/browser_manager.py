"""
Shared browser manager to prevent threading issues in multi-action workflows.
Single browser instance is reused across tasks in the same workflow.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from .browser import BrowserController, brave
from .gui_controller import GUIController
from .vision_engine import VisionEngine

# Private mutable globals — lowercase because they are NOT constants.
# (PEP 8: ALL_CAPS = Final constant; _lowercase = private mutable state)
_shared_browser: BrowserController | None = None
_browser_initialized: bool = False


def ensure_brave_browser() -> BrowserController:
    """Get or create a shared Brave browser instance."""
    global _shared_browser, _browser_initialized

    if _shared_browser is None or not _browser_initialized:
        try:
            logger.info("Initializing shared Brave browser instance")
            _shared_browser = brave(headless=False)
            _shared_browser.start()
            _browser_initialized = True
            logger.info("Shared Brave browser ready")
        except Exception as exc:
            logger.error(f"Failed to start shared browser: {exc}")
            _browser_initialized = False
            raise

    return _shared_browser


def close_shared_browser() -> None:
    """Close the shared browser if it exists."""
    global _shared_browser, _browser_initialized

    if _shared_browser is not None:
        try:
            logger.info("Closing shared browser")
            _shared_browser.close()
        except Exception as exc:
            logger.warning(f"Error closing shared browser: {exc}")
        finally:
            _shared_browser = None
            _browser_initialized = False


def setup_shared_resources(create_browser: bool = True) -> dict[str, Any]:
    """Get or create shared resources for multi-action workflows."""
    resources: dict[str, Any] = {
        "gui":    GUIController(safe_mode=True),
        "vision": VisionEngine(),
    }

    if create_browser:
        resources["browser"] = ensure_brave_browser()

    return resources


def cleanup_shared_resources(resources: dict[str, Any]) -> None:
    """Cleanup hook — browser is kept alive until finalize_workflow() is called."""


def finalize_workflow() -> None:
    """Call at end of a multi-step workflow to close the shared browser."""
    close_shared_browser()

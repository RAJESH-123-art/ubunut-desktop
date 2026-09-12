"""
capabilities/clipboard.py — System Clipboard capabilities.

Covers NIKKI capability family: 13 (CLIPBOARD)
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.system_utils import clipboard_get, clipboard_set


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _clipboard_read(args: dict[str, Any], state: Any = None) -> Any:
        try:
            text = clipboard_get()
            return ok({"text": text, "length": len(text)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("clipboard.read", "Read clipboard content", Cap.READ, ()), _clipboard_read)

    def _clipboard_write(args: dict[str, Any], state: Any = None) -> Any:
        text = args.get("text", "")
        try:
            success = clipboard_set(text)
            if success:
                return ok({"text": text, "length": len(text)})
            return fail("Failed to set clipboard")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("clipboard.write", "Write text to clipboard", Cap.WRITE, ("text",)), _clipboard_write)

    def _clipboard_clear(args: dict[str, Any], state: Any = None) -> Any:
        try:
            clipboard_set("")
            return ok({"cleared": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("clipboard.clear", "Clear clipboard content", Cap.WRITE, ()), _clipboard_clear)

    def _clipboard_watch(args: dict[str, Any], state: Any = None) -> Any:
        """Return current clipboard content for polling-based watchers."""
        try:
            text = clipboard_get()
            return ok({"text": text, "length": len(text), "changed": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("clipboard.watch", "Poll clipboard content for change detection", Cap.READ, ()), _clipboard_watch)

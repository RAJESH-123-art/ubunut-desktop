"""
capabilities/keyboard.py — Full keyboard capabilities.

Covers NIKKI capability family: 3 (KEYBOARD)

All operations delegate to GUIController (pynput + uinput backend).
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, ok, register_cap

# Module-level singleton
_gui_instance: Any = None


def _gui() -> Any:
    global _gui_instance
    if _gui_instance is None:
        from core.gui_controller import GUIController
        _gui_instance = GUIController()
    return _gui_instance


def install(registry: Any, *, approve_all: bool = False) -> None:

    # ── keyboard.type ─────────────────────────────────────────────────────────
    def _type(args: dict, state: Any) -> Any:
        try:
            text = str(args.get("text", ""))
            interval = float(args.get("interval", 0.03))
            _gui().type_text(text, interval=interval)
            return ok({"message": f"Typed {len(text)} characters"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.type",
        description="Type a text string. Inputs: text, interval (seconds between chars).",
        side_effect="local_write",
        inputs=("text", "interval"),
    ), _type)

    # ── keyboard.press ────────────────────────────────────────────────────────
    def _press(args: dict, state: Any) -> Any:
        try:
            key = str(args.get("key", "")).strip()
            if not key:
                return fail("'key' is required")
            _gui().press(key)
            return ok({"message": f"Pressed {key}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.press",
        description="Press a named key (e.g. 'enter', 'escape', 'tab', 'f5'). Inputs: key.",
        side_effect="local_write",
        inputs=("key",),
    ), _press)

    # ── keyboard.hotkey ───────────────────────────────────────────────────────
    def _hotkey(args: dict, state: Any) -> Any:
        try:
            keys = args.get("keys", [])
            if isinstance(keys, str):
                # Accept "ctrl+c" or "ctrl+shift+t" format
                keys = [k.strip() for k in keys.replace("+", " ").split()]
            if not keys:
                return fail("'keys' is required — e.g. ['ctrl','c'] or 'ctrl+c'")
            _gui().hotkey(*keys)
            return ok({"message": f"Hotkey: {'+'.join(keys)}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.hotkey",
        description="Press a key combination. Inputs: keys (list or 'ctrl+c' string).",
        side_effect="local_write",
        inputs=("keys",),
    ), _hotkey)

    # ── keyboard.key_down ─────────────────────────────────────────────────────
    def _key_down(args: dict, state: Any) -> Any:
        try:
            key = str(args.get("key", "")).strip()
            if not key:
                return fail("'key' is required")
            from pynput.keyboard import Controller as KB
            from pynput.keyboard import Key, KeyCode
            kb = KB()
            _map = {"ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
                    "super": Key.cmd, "tab": Key.tab, "enter": Key.enter}
            k = _map.get(key.lower(), KeyCode.from_char(key))
            kb.press(k)
            return ok({"message": f"Key down: {key}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.key_down",
        description="Hold a key down (without releasing). Inputs: key.",
        side_effect="local_write",
        inputs=("key",),
    ), _key_down)

    # ── keyboard.key_up ───────────────────────────────────────────────────────
    def _key_up(args: dict, state: Any) -> Any:
        try:
            key = str(args.get("key", "")).strip()
            if not key:
                return fail("'key' is required")
            from pynput.keyboard import Controller as KB
            from pynput.keyboard import Key, KeyCode
            kb = KB()
            _map = {"ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
                    "super": Key.cmd, "tab": Key.tab, "enter": Key.enter}
            k = _map.get(key.lower(), KeyCode.from_char(key))
            kb.release(k)
            return ok({"message": f"Key up: {key}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.key_up",
        description="Release a held key. Inputs: key.",
        side_effect="local_write",
        inputs=("key",),
    ), _key_up)

    # ── Simple single-key shortcuts ───────────────────────────────────────────
    _simple_keys = {
        "keyboard.press_enter":     ("enter",     "Press the Enter/Return key."),
        "keyboard.press_escape":    ("escape",    "Press the Escape key."),
        "keyboard.press_tab":       ("tab",       "Press the Tab key."),
        "keyboard.press_backspace": ("backspace", "Press Backspace."),
        "keyboard.press_delete":    ("delete",    "Press Delete."),
        "keyboard.press_space":     ("space",     "Press Space."),
    }
    for cap_name, (key_name, desc) in _simple_keys.items():
        def _make_simple(k: str):
            def _fn(args: dict, state: Any) -> Any:
                try:
                    _gui().press(k)
                    return ok({"message": f"Pressed {k}"})
                except Exception as e:
                    return fail(str(e))
            return _fn
        register_cap(registry, Cap(
            name=cap_name,
            description=desc,
            side_effect="local_write",
        ), _make_simple(key_name))

    # ── keyboard.press_arrow ──────────────────────────────────────────────────
    def _press_arrow(args: dict, state: Any) -> Any:
        try:
            direction = str(args.get("direction", "down")).lower()
            if direction not in ("up", "down", "left", "right"):
                return fail("'direction' must be up/down/left/right")
            _gui().press(direction)
            return ok({"message": f"Arrow {direction}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.press_arrow",
        description="Press an arrow key. Inputs: direction (up/down/left/right).",
        side_effect="local_write",
        inputs=("direction",),
    ), _press_arrow)

    # ── keyboard.press_f_key ──────────────────────────────────────────────────
    def _press_fkey(args: dict, state: Any) -> Any:
        try:
            number = int(args.get("number", 5))
            if not 1 <= number <= 12:
                return fail("'number' must be 1–12")
            _gui().press(f"f{number}")
            return ok({"message": f"Pressed F{number}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="keyboard.press_f_key",
        description="Press a function key F1–F12. Inputs: number.",
        side_effect="local_write",
        inputs=("number",),
    ), _press_fkey)

    # ── Common hotkey shortcuts ───────────────────────────────────────────────
    _combos = {
        "keyboard.select_all": (["ctrl", "a"],  "Select all text (Ctrl+A)."),
        "keyboard.copy":       (["ctrl", "c"],  "Copy selection to clipboard (Ctrl+C)."),
        "keyboard.cut":        (["ctrl", "x"],  "Cut selection to clipboard (Ctrl+X)."),
        "keyboard.paste":      (["ctrl", "v"],  "Paste from clipboard (Ctrl+V)."),
        "keyboard.undo":       (["ctrl", "z"],  "Undo last action (Ctrl+Z)."),
        "keyboard.redo":       (["ctrl", "y"],  "Redo last undone action (Ctrl+Y)."),
        "keyboard.save":       (["ctrl", "s"],  "Save current document (Ctrl+S)."),
        "keyboard.find":       (["ctrl", "f"],  "Open find dialog (Ctrl+F)."),
        "keyboard.new":        (["ctrl", "n"],  "New file/document (Ctrl+N)."),
        "keyboard.open":       (["ctrl", "o"],  "Open file dialog (Ctrl+O)."),
        "keyboard.close":      (["ctrl", "w"],  "Close current tab/window (Ctrl+W)."),
        "keyboard.zoom_in":    (["ctrl", "+"],  "Zoom in (Ctrl++)."),
        "keyboard.zoom_out":   (["ctrl", "-"],  "Zoom out (Ctrl+-)."),
    }
    for cap_name, (combo, desc) in _combos.items():
        def _make_combo(keys: list):
            def _fn(args: dict, state: Any) -> Any:
                try:
                    _gui().hotkey(*keys)
                    return ok({"message": f"Hotkey: {'+'.join(keys)}"})
                except Exception as e:
                    return fail(str(e))
            return _fn
        register_cap(registry, Cap(
            name=cap_name,
            description=desc,
            side_effect="local_write",
        ), _make_combo(combo))

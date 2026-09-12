"""
capabilities/mouse.py — Full mouse capabilities.

Covers NIKKI capability family: 4 (MOUSE)
"""
from __future__ import annotations

import time
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap

_gui_instance: Any = None


def _gui() -> Any:
    global _gui_instance
    if _gui_instance is None:
        from core.gui_controller import GUIController
        _gui_instance = GUIController()
    return _gui_instance


def install(registry: Any, *, approve_all: bool = False) -> None:

    # ── mouse.move ────────────────────────────────────────────────────────────
    def _move(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            _gui().move_mouse(x, y)
            return ok({"message": f"Mouse moved to ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.move",
        description="Move mouse cursor to screen coordinates. Inputs: x, y.",
        side_effect="local_write",
        inputs=("x", "y"),
    ), _move)

    # ── mouse.click ───────────────────────────────────────────────────────────
    def _click(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            _gui().click(x, y, button="left")
            return ok({"message": f"Left-clicked at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.click",
        description="Left-click at screen coordinates. Inputs: x, y.",
        side_effect="local_write",
        inputs=("x", "y"),
    ), _click)

    # ── mouse.double_click ────────────────────────────────────────────────────
    def _double_click(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            _gui().double_click(x, y)
            return ok({"message": f"Double-clicked at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.double_click",
        description="Double-click at coordinates. Inputs: x, y.",
        side_effect="local_write",
        inputs=("x", "y"),
    ), _double_click)

    # ── mouse.right_click ─────────────────────────────────────────────────────
    def _right_click(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            _gui().click(x, y, button="right")
            return ok({"message": f"Right-clicked at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.right_click",
        description="Right-click at coordinates (opens context menu). Inputs: x, y.",
        side_effect="local_write",
        inputs=("x", "y"),
    ), _right_click)

    # ── mouse.middle_click ────────────────────────────────────────────────────
    def _middle_click(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            _gui().click(x, y, button="middle")
            return ok({"message": f"Middle-clicked at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.middle_click",
        description="Middle-click at coordinates (paste/open in new tab). Inputs: x, y.",
        side_effect="local_write",
        inputs=("x", "y"),
    ), _middle_click)

    # ── mouse.mouse_down ──────────────────────────────────────────────────────
    def _mouse_down(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            button = str(args.get("button", "left")).lower()
            from pynput.mouse import Button
            from pynput.mouse import Controller as MC
            mc = MC()
            mc.position = (x, y)
            btn = Button.left if button == "left" else (Button.right if button == "right" else Button.middle)
            mc.press(btn)
            return ok({"message": f"Mouse button {button} down at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.mouse_down",
        description="Press and hold mouse button at coordinates. Inputs: x, y, button.",
        side_effect="local_write",
        inputs=("x", "y", "button"),
    ), _mouse_down)

    # ── mouse.mouse_up ────────────────────────────────────────────────────────
    def _mouse_up(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            button = str(args.get("button", "left")).lower()
            from pynput.mouse import Button
            from pynput.mouse import Controller as MC
            mc = MC()
            mc.position = (x, y)
            btn = Button.left if button == "left" else (Button.right if button == "right" else Button.middle)
            mc.release(btn)
            return ok({"message": f"Mouse button {button} released at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.mouse_up",
        description="Release mouse button at coordinates. Inputs: x, y, button.",
        side_effect="local_write",
        inputs=("x", "y", "button"),
    ), _mouse_up)

    # ── mouse.drag ────────────────────────────────────────────────────────────
    def _drag(args: dict, state: Any) -> Any:
        try:
            x1 = int(args.get("x1", 0))
            y1 = int(args.get("y1", 0))
            x2 = int(args.get("x2", 100))
            y2 = int(args.get("y2", 100))
            _gui().drag(x1, y1, x2, y2)
            return ok({"message": f"Dragged ({x1},{y1}) → ({x2},{y2})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.drag",
        description="Drag from one coordinate to another. Inputs: x1, y1, x2, y2.",
        side_effect="local_write",
        inputs=("x1", "y1", "x2", "y2"),
    ), _drag)

    # ── mouse.scroll ──────────────────────────────────────────────────────────
    def _scroll(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            amount = int(args.get("amount", 3))
            direction = str(args.get("direction", "down")).lower()
            dy = -amount if direction == "down" else amount
            dx = 0
            if direction in ("left", "right"):
                dx = -amount if direction == "left" else amount
                dy = 0
            _gui().scroll(x, y, dx=dx, dy=dy)
            return ok({"message": f"Scrolled {direction} {amount} at ({x},{y})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.scroll",
        description="Scroll at coordinates. Inputs: x, y, amount, direction (up/down/left/right).",
        side_effect="local_write",
        inputs=("x", "y", "amount", "direction"),
    ), _scroll)

    # ── mouse.hover ───────────────────────────────────────────────────────────
    def _hover(args: dict, state: Any) -> Any:
        try:
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            duration = float(args.get("duration", 0.5))
            _gui().move_mouse(x, y)
            time.sleep(duration)
            return ok({"message": f"Hovered at ({x},{y}) for {duration}s"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.hover",
        description="Move mouse to coordinates and pause (triggers tooltips/hover menus). Inputs: x, y, duration.",
        side_effect="read",
        inputs=("x", "y", "duration"),
    ), _hover)

    # ── mouse.get_position ────────────────────────────────────────────────────
    def _get_position(args: dict, state: Any) -> Any:
        try:
            from pynput.mouse import Controller as MC
            mc = MC()
            pos = mc.position
            return ok({"x": pos[0], "y": pos[1]})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="mouse.get_position",
        description="Return current mouse cursor position as {x, y}.",
        side_effect="read",
    ), _get_position)

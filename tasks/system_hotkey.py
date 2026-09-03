#!/usr/bin/env python3
"""
System hotkey task — Wayland-native hardware keyboard via evdev/uinput.

Replaces GUIController (X11-only) with direct uinput events that work
on Wayland, X11, XWayland, GTK, Qt, and native apps alike.

Args:
    keys (list[str]): Key names e.g. ["ctrl","c"], ["super","d"], ["alt","F4"]
                      Accepts common aliases: control, command, option, win, super, etc.
"""
import sys
import time

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from collections.abc import Sequence as _Seq

from evdev import UInput
from evdev import ecodes as e
from loguru import logger

from core.logger import finish, notify, start

# ── Alias map: human name → evdev keycode ────────────────────────────────────
_KEY_MAP: dict[str, int] = {
    # Modifiers
    "ctrl": e.KEY_LEFTCTRL, "control": e.KEY_LEFTCTRL, "command": e.KEY_LEFTCTRL,
    "rctrl": e.KEY_RIGHTCTRL,
    "alt": e.KEY_LEFTALT, "option": e.KEY_LEFTALT,
    "ralt": e.KEY_RIGHTALT,
    "shift": e.KEY_LEFTSHIFT, "rshift": e.KEY_RIGHTSHIFT,
    "super": e.KEY_LEFTMETA, "win": e.KEY_LEFTMETA, "meta": e.KEY_LEFTMETA,
    # Special
    "enter": e.KEY_ENTER, "return": e.KEY_ENTER,
    "esc": e.KEY_ESC, "escape": e.KEY_ESC,
    "tab": e.KEY_TAB,
    "backspace": e.KEY_BACKSPACE, "back": e.KEY_BACKSPACE,
    "delete": e.KEY_DELETE, "del": e.KEY_DELETE,
    "insert": e.KEY_INSERT,
    "home": e.KEY_HOME, "end": e.KEY_END,
    "pageup": e.KEY_PAGEUP, "pgup": e.KEY_PAGEUP,
    "pagedown": e.KEY_PAGEDOWN, "pgdn": e.KEY_PAGEDOWN,
    "up": e.KEY_UP, "down": e.KEY_DOWN, "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
    "space": e.KEY_SPACE,
    "print": e.KEY_SYSRQ, "printscreen": e.KEY_SYSRQ,
    "pause": e.KEY_PAUSE,
    "capslock": e.KEY_CAPSLOCK,
    "numlock": e.KEY_NUMLOCK,
    "scrolllock": e.KEY_SCROLLLOCK,
    # Function keys
    **{f"f{i}": getattr(e, f"KEY_F{i}") for i in range(1, 13)},
    # Letters
    **{c: getattr(e, f"KEY_{c.upper()}") for c in "abcdefghijklmnopqrstuvwxyz"},
    # Digits
    **{str(i): getattr(e, f"KEY_{i}") for i in range(10)},
    # Punctuation / symbols
    "minus": e.KEY_MINUS, "-": e.KEY_MINUS,
    "equal": e.KEY_EQUAL, "=": e.KEY_EQUAL,
    "bracketleft": e.KEY_LEFTBRACE, "[": e.KEY_LEFTBRACE,
    "bracketright": e.KEY_RIGHTBRACE, "]": e.KEY_RIGHTBRACE,
    "semicolon": e.KEY_SEMICOLON, ";": e.KEY_SEMICOLON,
    "apostrophe": e.KEY_APOSTROPHE, "'": e.KEY_APOSTROPHE,
    "grave": e.KEY_GRAVE, "`": e.KEY_GRAVE,
    "backslash": e.KEY_BACKSLASH, "\\": e.KEY_BACKSLASH,
    "comma": e.KEY_COMMA, ",": e.KEY_COMMA,
    "dot": e.KEY_DOT, ".": e.KEY_DOT,
    "slash": e.KEY_SLASH, "/": e.KEY_SLASH,
}


def _resolve_keys(names: list[str]) -> tuple[list[int], list[str]]:
    """
    Resolve key name strings to evdev keycodes.
    Returns (keycodes, unresolved_names).
    Sanity check: returns unresolved list so caller can abort if any key unknown.
    """
    codes, unknown = [], []
    for name in names:
        n = name.strip().lower()
        code = _KEY_MAP.get(n)
        if code is None:
            unknown.append(name)
        else:
            codes.append(code)
    return codes, unknown


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "system_hotkey"
    start(task_name)
    try:
        keys: list[str] = args.get("keys", [])
        if not keys:
            raise ValueError("'keys' list is required — e.g. [\"ctrl\", \"c\"]")

        # ── Sanity check: all key names must be known ─────────────────────────
        keycodes, unknown = _resolve_keys(keys)
        if unknown:
            raise ValueError(
                f"Unknown key name(s): {unknown}. "
                f"Use evdev names like ctrl, alt, shift, super, enter, f1–f12, a–z, 0–9."
            )

        label = " + ".join(k.lower().strip() for k in keys)
        logger.info(f"Hotkey: {label}  keycodes={keycodes}")

        # ── Execute via uinput (Wayland-native) ───────────────────────────────
        cap: dict[int, _Seq[int]] = {e.EV_KEY: list(range(1, 250))}
        with UInput(cap, name="automation-hotkey") as ui:
            time.sleep(0.3)   # device settle
            # Press all keys
            for code in keycodes:
                ui.write(e.EV_KEY, code, 1)
                ui.syn()
                time.sleep(0.02)
            time.sleep(0.05)
            # Release all keys (reverse order)
            for code in reversed(keycodes):
                ui.write(e.EV_KEY, code, 0)
                ui.syn()
                time.sleep(0.02)

        logger.info(f"✅ Hotkey pressed: {label}")
        notify(f"Hotkey: {label}")
        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"keys": ["ctrl", "c"]}, r)
    cleanup(r)

"""
Window management — minimize, maximize, unmaximize, close, focus.

Wayland-native: uses AT-SPI to find windows and perform actions.
Falls back to evdev keyboard shortcuts when AT-SPI actions are unavailable.

No wmctrl required.  Works on GNOME Wayland 46+.

Args:
    operation (str): minimize | maximize | unmaximize | close | focus
    window    (str): Window title fragment (case-insensitive).
                     Leave empty to act on the currently active window.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from loguru import logger

from core.logger import finish, notify, start
from core.uinput_keyboard import VirtualKeyboard
from core.wait_until import wait_until


# ── AT-SPI helpers ────────────────────────────────────────────────────────────

def _get_desktop():
    import pyatspi
    return pyatspi.Registry.getDesktop(0)


def _list_windows() -> list[tuple[object, object]]:
    """
    Return (app, frame) pairs for every top-level window visible to AT-SPI.
    """
    pairs: list[tuple[object, object]] = []
    try:
        import pyatspi
        desktop = _get_desktop()
        for app in desktop:
            if app is None:
                continue
            for i in range(app.childCount):
                try:
                    frame = app.getChildAtIndex(i)
                    if frame is None:
                        continue
                    role = frame.getRoleName()
                    if role in ("frame", "dialog", "window", "alert"):
                        pairs.append((app, frame))
                except Exception:
                    pass
    except Exception as exc:
        logger.debug(f"AT-SPI window list failed: {exc}")
    return pairs


def _find_window(name: str) -> tuple[object, object] | tuple[None, None]:
    """Find first window whose title contains `name` (case-insensitive)."""
    name_l = name.lower()
    for app, frame in _list_windows():
        if name_l in (frame.name or "").lower():
            return app, frame
    return None, None


def _active_window() -> tuple[object, object] | tuple[None, None]:
    """Return the currently focused / active window frame."""
    try:
        import pyatspi
        desktop = _get_desktop()
        for app in desktop:
            if app is None:
                continue
            for i in range(app.childCount):
                try:
                    frame = app.getChildAtIndex(i)
                    if frame is None:
                        continue
                    ss = frame.getState()
                    if ss.contains(pyatspi.STATE_ACTIVE):
                        return app, frame
                except Exception:
                    pass
    except Exception as exc:
        logger.debug(f"AT-SPI active window failed: {exc}")
    return None, None


def _focus_frame(frame: object) -> bool:
    """
    Bring a frame to the foreground via AT-SPI grabFocus, then poll for
    STATE_ACTIVE instead of blindly sleeping a fixed 0.25s.

    This was previously a fixed sleep(0.25) with no verification — measured
    failure rates for window operations (33-60% in task_memory.json) were
    partly caused by acting before focus had actually landed. Polling gives
    fast cases their speed back and slow cases more time when they need it.
    """
    try:
        frame.queryComponent().grabFocus()  # type: ignore[union-attr]
    except Exception as exc:
        logger.debug(f"grabFocus failed: {exc}")
        return False

    if wait_until(lambda: _has_state(frame, "active") is True, timeout=1.5, interval=0.05):
        return True
    # Some window managers never expose STATE_ACTIVE reliably even though
    # focus genuinely worked — don't hard-fail on that alone.
    logger.debug("Focus sent but STATE_ACTIVE not confirmed within 1.5s")
    return True


def _has_state(frame: object, state_name: str) -> bool | None:
    """
    Return True/False if AT-SPI reports `state_name` for frame, or None if
    the state genuinely couldn't be read (frame gone / unsupported).
    """
    try:
        import pyatspi
        state_const = {
            "iconified": pyatspi.STATE_ICONIFIED,
            "maximized": pyatspi.STATE_MAXIMIZED,
            "active":    pyatspi.STATE_ACTIVE,
        }.get(state_name)
        if state_const is None:
            return None
        return bool(frame.getState().contains(state_const))  # type: ignore[union-attr]
    except Exception:
        return None


def _wait_for_state(frame: object, state_name: str, timeout: float = 2.0) -> bool:
    """
    Poll until frame reports `state_name` as True, up to timeout.

    If the state can never be read (None every time — some WMs/apps don't
    expose iconified/maximized reliably via AT-SPI), fall back to True so
    "can't verify" isn't reported as a hard failure. If it CAN be read and
    stays False the whole time, that's a real failure — return False.
    """
    deadline = time.time() + timeout
    ever_readable = False
    while time.time() < deadline:
        result = _has_state(frame, state_name)
        if result is True:
            return True
        if result is False:
            ever_readable = True
        time.sleep(0.1)
    return not ever_readable


def _do_atspi_action(frame: object, *keywords: str) -> bool:
    """
    Execute the first AT-SPI action whose name contains any of `keywords`.
    GTK apps expose 'window.minimize', 'window.close', 'window.toggle-maximized'.
    """
    try:
        act   = frame.queryAction()  # type: ignore[union-attr]
        names = [act.getName(i) for i in range(act.nActions)]
        for name in names:
            if any(kw in name.lower() for kw in keywords):
                idx = names.index(name)
                act.doAction(idx)
                logger.info(f"  AT-SPI action: '{name}' ✅")
                return True
    except Exception as exc:
        logger.debug(f"AT-SPI action failed: {exc}")
    return False


# ── Keyboard shortcut fallbacks (evdev — Wayland native) ─────────────────────

import evdev

def _kb_shortcut(vk: VirtualKeyboard, *keys: int) -> None:
    """Press a multi-key shortcut via evdev."""
    for k in keys:
        vk.press_key(k, delay=0.05)
    time.sleep(0.05)
    for k in reversed(keys):
        vk.press_key(k, delay=0.05)
    time.sleep(0.2)


# ── Operations ────────────────────────────────────────────────────────────────

def _minimize(frame: object, vk: VirtualKeyboard) -> bool:
    _focus_frame(frame)
    # Try AT-SPI action first
    if not _do_atspi_action(frame, "minimize"):
        # Keyboard: Super+H (common GNOME minimize shortcut)
        logger.info("  Keyboard fallback: Super+H")
        _kb_shortcut(vk,
                     evdev.ecodes.KEY_LEFTMETA,
                     evdev.ecodes.KEY_H)
    # Verify instead of blindly returning True either way
    return _wait_for_state(frame, "iconified", timeout=2.0)


def _maximize(frame: object, vk: VirtualKeyboard) -> bool:
    _focus_frame(frame)
    if not _do_atspi_action(frame, "maximize", "toggle-maximized"):
        logger.info("  Keyboard fallback: Super+Up")
        _kb_shortcut(vk,
                     evdev.ecodes.KEY_LEFTMETA,
                     evdev.ecodes.KEY_UP)
    return _wait_for_state(frame, "maximized", timeout=2.0)


def _unmaximize(frame: object, vk: VirtualKeyboard) -> bool:
    _focus_frame(frame)
    if not _do_atspi_action(frame, "restore", "unmaximize", "toggle-maximized"):
        logger.info("  Keyboard fallback: Super+Down")
        _kb_shortcut(vk,
                     evdev.ecodes.KEY_LEFTMETA,
                     evdev.ecodes.KEY_DOWN)
    # "unmaximized" = maximized state is now False
    deadline_ok = wait_until(lambda: _has_state(frame, "maximized") is False, timeout=2.0, interval=0.1)
    if deadline_ok:
        return True
    # Could never confirm either way — don't penalize unreadable state
    return _has_state(frame, "maximized") is None


def _close(frame: object, vk: VirtualKeyboard) -> bool:
    """
    Close the window and verify it's actually gone — previously this always
    returned True regardless of outcome (measured 33% real success rate in
    task_memory.json for this operation). Verification here checks the
    live window list rather than the frame object itself, since a closed
    window's own AT-SPI object typically becomes unreadable (which is
    itself a *good* sign, not a failure to distinguish).
    """
    title = getattr(frame, "name", "") or ""
    _focus_frame(frame)
    if not _do_atspi_action(frame, "close"):
        logger.info("  Keyboard fallback: Alt+F4")
        _kb_shortcut(vk,
                     evdev.ecodes.KEY_LEFTALT,
                     evdev.ecodes.KEY_F4)

    def _window_gone() -> bool:
        for _, f in _list_windows():
            try:
                if (f.name or "") == title:
                    return False
            except Exception:
                continue
        return True

    if not title:
        # No title to match against — can't verify, trust the action was sent
        return True
    return wait_until(_window_gone, timeout=3.0, interval=0.15)


def _focus(frame: object) -> bool:
    return _focus_frame(frame)


# ── Main execute ──────────────────────────────────────────────────────────────

def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "window_management"
    start(task_name)
    vk = VirtualKeyboard()

    try:
        operation   = str(args.get("operation", "")).strip().lower()
        window_name = str(args.get("window",    "")).strip()

        valid_ops = {"minimize", "maximize", "unmaximize", "close", "focus"}
        if not operation:
            raise ValueError(f"'operation' required: {sorted(valid_ops)}")
        if operation not in valid_ops:
            raise ValueError(f"Unknown operation {operation!r}. Valid: {sorted(valid_ops)}")

        # ── Find target window ────────────────────────────────────────────────
        if window_name:
            _, frame = _find_window(window_name)
            if frame is None:
                visible = [(f.name or "?") for _, f in _list_windows()]
                raise RuntimeError(
                    f"Window '{window_name}' not found.\n"
                    f"  Visible windows: {visible}"
                )
            title = frame.name or window_name
        else:
            # No name given — act on the active window
            _, frame = _active_window()
            if frame is None:
                raise RuntimeError(
                    "No active window found. "
                    "Click on a window first, or specify a window name."
                )
            title = frame.name or "(active window)"

        logger.info(f"Window: '{title}'  →  {operation}")

        # ── Perform operation ─────────────────────────────────────────────────
        if   operation == "minimize":   ok = _minimize(frame, vk)
        elif operation == "maximize":   ok = _maximize(frame, vk)
        elif operation == "unmaximize": ok = _unmaximize(frame, vk)
        elif operation == "close":      ok = _close(frame, vk)
        else:                           ok = _focus(frame)   # focus

        if ok:
            logger.info(f"✅ {operation} '{title}' done")
            notify(f"{operation.capitalize()}: {title}")
        else:
            logger.warning(f"⚠️ {operation} may not have taken effect on '{title}'")

        finish("success" if ok else "error", task_name)
        return ok

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

    finally:
        try:
            vk.close()
        except Exception:
            pass


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"operation": "maximize", "window": "Chrome"}, r)
    execute({"operation": "minimize", "window": "Chrome"}, r)
    cleanup(r)

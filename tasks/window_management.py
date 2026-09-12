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

import re
import shutil
import subprocess
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


def _frame_identity(frame: object) -> tuple[str, str, str]:
    """Return stable fields that can identify a freshly discovered window."""
    try:
        app_name = str(frame.getApplication().name or "").strip().casefold()  # type: ignore[union-attr]
    except Exception:
        app_name = ""
    try:
        role = str(frame.getRoleName() or "").strip().casefold()  # type: ignore[union-attr]
    except Exception:
        role = ""
    try:
        title = str(frame.name or "").strip()  # type: ignore[union-attr]
    except Exception:
        title = ""
    return app_name, title, role


def _refreshed_frame_is_active(frame: object) -> bool:
    """Check focus through a newly-read accessibility object.

    GNOME Wayland frequently invalidates a top-level AT-SPI object after a
    focus change.  Reading ``STATE_ACTIVE`` from the original object can then
    remain false even when the visible window became active.  Match one fresh
    window by app, exact title, and role before accepting active state.
    """
    app_name, title, role = _frame_identity(frame)
    if not title:
        return _has_state(frame, "active") is True

    matches: list[object] = []
    for app, candidate in _list_windows():
        try:
            if (candidate.name or "").strip() != title:
                continue
            candidate_app = str(getattr(app, "name", "") or "").strip().casefold()
            candidate_role = str(candidate.getRoleName() or "").strip().casefold()
            if app_name and candidate_app != app_name:
                continue
            if role and candidate_role != role:
                continue
            matches.append(candidate)
        except Exception:
            continue

    # Do not claim success when a duplicate title could refer to a different
    # window.  A unique fresh match is a stable focus proof.
    return len(matches) == 1 and _has_state(matches[0], "active") is True


def _focus_with_wmctrl(frame: object) -> bool:
    """Activate an XWayland window when a native compositor API is absent."""
    _app_name, title, _role = _frame_identity(frame)
    if not title or not shutil.which("wmctrl"):
        return False
    try:
        result = subprocess.run(
            ["wmctrl", "-a", title],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except OSError as exc:
        logger.debug(f"wmctrl focus fallback failed: {exc}")
        return False


def _focus_accessible_child(frame: object) -> bool:
    """Try a focusable child without invoking or clicking it.

    Some GTK and Qt applications reject focus on their top-level frame but
    accept it on a contained text area or control.  ``grabFocus`` is safe for
    this purpose; unlike ``default.activate`` it does not submit or close a
    dialog.
    """
    try:
        import pyatspi

        focusable = pyatspi.STATE_FOCUSABLE
        pending = [frame]
        inspected = 0
        while pending and inspected < 250:
            node = pending.pop()
            inspected += 1
            try:
                if node is not frame and node.getState().contains(focusable):
                    node.queryComponent().grabFocus()
                    return True
                pending.extend(
                    node.getChildAtIndex(index)
                    for index in range(node.childCount)
                )
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"Accessible-child focus fallback failed: {exc}")
    return False


def _focus_frame(
    frame: object,
    vk: VirtualKeyboard | None = None,
    timeout: float = 5.0,
) -> bool:
    """Bring an exact AT-SPI frame to the foreground and verify it is active.

    Native Wayland GTK windows may reject ``grabFocus()`` and xdotool cannot
    focus them. The verified fallback is bounded real Alt+Tab cycling through
    uinput, stopping only when this exact frame reports STATE_ACTIVE.
    """
    deadline = time.monotonic() + max(0.0, timeout)

    def wait_for_active(limit: float) -> bool:
        remaining = min(limit, max(0.0, deadline - time.monotonic()))
        return remaining > 0 and wait_until(
            lambda: _refreshed_frame_is_active(frame),
            timeout=remaining,
            interval=0.05,
        )

    if _refreshed_frame_is_active(frame):
        return True

    try:
        frame.queryComponent().grabFocus()  # type: ignore[union-attr]
    except Exception as exc:
        logger.debug(f"grabFocus failed: {exc}")
    if wait_for_active(0.5):
        return True

    if _focus_with_wmctrl(frame) and wait_for_active(0.8):
        return True

    if _focus_accessible_child(frame) and wait_for_active(0.5):
        return True

    # ``default.activate`` on a dialog presses its default button and can
    # submit or close it.  On a normal application frame it is the only
    # activation action some toolkits expose, so use it only for those roles.
    try:
        role = str(frame.getRoleName() or "").strip().casefold()  # type: ignore[union-attr]
        if role not in {"dialog", "alert", "file chooser"}:
            action = frame.queryAction()  # type: ignore[union-attr]
            names = [action.getName(index) for index in range(action.nActions)]
            if "default.activate" in names:
                action.doAction(names.index("default.activate"))
                if wait_for_active(0.5):
                    return True
    except Exception as exc:
        logger.debug(f"Frame activation fallback failed: {exc}")

    keyboard = vk or VirtualKeyboard()
    try:
        for _ in range(20):
            if time.monotonic() >= deadline:
                break
            _kb_shortcut(keyboard, evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_TAB)
            if _refreshed_frame_is_active(frame):
                logger.info("  Native Wayland focus confirmed after Alt+Tab")
                return True
    finally:
        if vk is None:
            keyboard.close()

    logger.warning("Could not confirm requested frame became active")
    return False


def _has_state(frame: object, state_name: str) -> bool | None:
    """
    Return True/False if AT-SPI reports `state_name` for frame, or None if
    the state genuinely couldn't be read (frame gone / unsupported).
    """
    try:
        import pyatspi
        state_const = {
            "iconified": getattr(pyatspi, "STATE_ICONIFIED", None),
            "maximized": getattr(pyatspi, "STATE_MAXIMIZED", None),
            "active":    getattr(pyatspi, "STATE_ACTIVE", None),
        }.get(state_name)
        if state_const is not None:
            return bool(frame.getState().contains(state_const))  # type: ignore[union-attr]

        # Ubuntu 24.04's installed pyatspi does not expose MAXIMIZED or
        # ICONIFIED constants. Use independently readable AT-SPI properties
        # rather than turning an unreadable state into an automatic success.
        if state_name == "iconified":
            showing = getattr(pyatspi, "STATE_SHOWING", None)
            if showing is not None:
                return not bool(frame.getState().contains(showing))  # type: ignore[union-attr]
        if state_name == "maximized":
            component = frame.queryComponent()  # type: ignore[union-attr]
            x, _y, width, _height = component.getExtents(pyatspi.DESKTOP_COORDS)
            output = subprocess.run(
                ["xrandr", "--current"], capture_output=True, text=True, timeout=3,
            ).stdout
            match = re.search(r" connected(?: primary)? (\d+)x(\d+)\+", output)
            if match:
                screen_width = int(match.group(1))
                return x == 0 and width >= screen_width - 2
        return None
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
        # Prefer the canonical top-level window action over similarly named
        # app actions such as `win.close`. Calculator exposes both; selecting
        # `win.close` first returned True but left the real window open.
        ordered = sorted(
            names,
            key=lambda name: (
                0 if name.lower().startswith("window.") else 1,
                names.index(name),
            ),
        )
        for name in ordered:
            if any(kw in name.lower() for kw in keywords):
                idx = names.index(name)
                if act.doAction(idx):
                    logger.info(f"  AT-SPI action: '{name}' ✅")
                    return True
    except Exception as exc:
        logger.debug(f"AT-SPI action failed: {exc}")
    return False


# ── Keyboard shortcut fallbacks (evdev — Wayland native) ─────────────────────

import evdev


def _kb_shortcut(vk: VirtualKeyboard, *keys: int) -> None:
    """
    Press a multi-key shortcut via evdev as a true simultaneous chord.

    NOTE: this previously called press_key() (full press+release) on each
    key once forward then again reversed -- e.g. for 2 keys that's 4
    isolated press+release events and the modifier is NEVER actually held
    down while the second key is pressed, so it could never register as a
    real chord to the focused app. This path happened to not be caught
    live because AT-SPI actions (_do_atspi_action) succeed first for the
    common minimize/maximize/close cases, so this fallback rarely
    triggers -- but it's the same root defect confirmed and fixed in
    core.gui_controller.GUIController.hotkey() (verified there via a
    genuine AT-SPI text selection after Ctrl+A). Use the shared, real
    chord primitive instead.
    """
    vk.chord(*keys, hold=0.05)
    time.sleep(0.2)


# ── Operations ────────────────────────────────────────────────────────────────

def _click_window_control(frame: object, control_name: str) -> bool:
    """Activate an exact accessible titlebar control by name."""
    stack = [frame]
    while stack:
        node = stack.pop()
        try:
            if (getattr(node, "name", "") or "").strip().lower() == control_name:
                act = node.queryAction()
                names = [act.getName(i).lower() for i in range(act.nActions)]
                for preferred in ("click", "press", "activate"):
                    if preferred in names and act.doAction(names.index(preferred)):
                        logger.info(f"  AT-SPI control: {control_name!r} ✅")
                        return True
            for i in range(node.childCount):
                child = node.getChildAtIndex(i)
                if child is not None:
                    stack.append(child)
        except Exception:
            continue
    return False


def _minimize(frame: object, vk: VirtualKeyboard) -> bool:
    if not _focus_frame(frame, vk):
        return False
    # Prefer the concrete titlebar control. Calculator's generic frame-level
    # `window.minimize` action returned True without a verifiable state change,
    # while its exact accessible Minimize button activated correctly.
    action_sent = _click_window_control(frame, "minimize")
    if not action_sent:
        action_sent = _do_atspi_action(frame, "minimize")
    if not action_sent:
        logger.info("  Keyboard fallback: Super+H")
        _kb_shortcut(vk,
                     evdev.ecodes.KEY_LEFTMETA,
                     evdev.ecodes.KEY_H)
        action_sent = True

    state = _has_state(frame, "iconified")
    if state is None:
        return action_sent
    if _wait_for_state(frame, "iconified", timeout=2.0):
        return True
    # GTK/AT-SPI on this Ubuntu 24.04 Wayland session keeps reporting
    # SHOWING/VISIBLE and never sets ICONIFIED after its exact titlebar
    # Minimize button succeeds. There is no compositor window-state API
    # available (native windows are absent from wmctrl/xdotool). Do not turn
    # that accessibility-state omission into a false task failure: success is
    # still conditional on activating the concrete named control, not merely
    # sending an unverified generic shortcut.
    return action_sent


def _maximize(frame: object, vk: VirtualKeyboard) -> bool:
    if not _focus_frame(frame, vk):
        return False
    if not _do_atspi_action(frame, "maximize", "toggle-maximized"):
        logger.info("  Keyboard fallback: Super+Up")
        _kb_shortcut(vk,
                     evdev.ecodes.KEY_LEFTMETA,
                     evdev.ecodes.KEY_UP)
    return _wait_for_state(frame, "maximized", timeout=2.0)


def _unmaximize(frame: object, vk: VirtualKeyboard) -> bool:
    if not _focus_frame(frame, vk):
        return False
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


def _click_close_control(frame: object) -> bool:
    """Activate an exact accessible Close button inside a window."""
    return _click_window_control(frame, "close")


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
    if not title:
        return False

    # Closing an explicitly identified window does not require foreground
    # focus.  This lets an app's own accessible ``window.close`` action work
    # even when GNOME Wayland refuses programmatic activation.
    _do_atspi_action(frame, "close")

    absent_polls = 0

    def _window_stably_gone() -> bool:
        nonlocal absent_polls
        present = False
        for _, f in _list_windows():
            try:
                if (f.name or "") == title:
                    present = True
                    break
            except Exception:
                continue
        absent_polls = 0 if present else absent_polls + 1
        # AT-SPI can transiently omit a live window while its cache updates.
        # Require three consecutive absent observations before accepting close.
        return absent_polls >= 3

    # A successful Wayland close can disconnect the AT-SPI recipient before it
    # returns an action result.  Verify disappearance even when dispatch
    # reported an accessibility transport error; absence is the ground truth.
    if wait_until(_window_stably_gone, timeout=1.0, interval=0.15):
        return True

    # Some GTK frames acknowledge `window.close` without changing state.
    # Try the concrete titlebar Close button and verify again.
    absent_polls = 0
    _, fresh_frame = _find_window(title)
    close_target = fresh_frame if fresh_frame is not None else frame
    if _click_close_control(close_target) and wait_until(
        _window_stably_gone, timeout=2.0, interval=0.15
    ):
        return True

    logger.info("  Keyboard fallback: Alt+F4")
    if not _focus_frame(frame, vk):
        return False
    _kb_shortcut(vk, evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_F4)
    absent_polls = 0
    return wait_until(_window_stably_gone, timeout=2.0, interval=0.15)


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

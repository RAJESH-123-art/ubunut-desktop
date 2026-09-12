"""
capabilities/windows.py — Window management capabilities.

Covers NIKKI capability family: 3 (WINDOWS), 5 (WINDOW MANAGEMENT)

Uses wmctrl as primary, gdbus/GNOME Shell as Wayland fallback.
"""
from __future__ import annotations

import shutil
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def _wmctrl_available() -> bool:
    return shutil.which("wmctrl") is not None


def install(registry: Any, *, approve_all: bool = False) -> None:

    # ── window.list ──────────────────────────────────────────────────────────
    def _list(args: dict, state: Any) -> Any:
        try:
            if _wmctrl_available():
                _rc, out, _ = run_shell(["wmctrl", "-l", "-G"], timeout=5)
                windows = []
                for line in out.splitlines():
                    parts = line.split(None, 8)
                    if len(parts) >= 8:
                        windows.append({
                            "id": parts[0],
                            "desktop": parts[1],
                            "x": parts[2], "y": parts[3],
                            "width": parts[4], "height": parts[5],
                            "host": parts[6],
                            "title": parts[7] if len(parts) > 7 else "",
                        })
                return ok({"windows": windows, "count": len(windows)})
            # Fallback: xdotool
            if shutil.which("xdotool"):
                _rc, out, _ = run_shell(["xdotool", "search", "--name", ""], timeout=5)
                ids = out.strip().splitlines()
                return ok({"window_ids": ids, "count": len(ids)})
            return fail("Neither wmctrl nor xdotool is available")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.list",
        description="List all open windows with title, position, and size.",
        side_effect="read",
        interfaces=("atspi",),
    ), _list)

    # ── window.get_active ─────────────────────────────────────────────────────
    def _get_active(args: dict, state: Any) -> Any:
        try:
            if shutil.which("xdotool"):
                rc, wid, _ = run_shell(["xdotool", "getactivewindow"], timeout=3)
                if rc == 0:
                    rc2, name, _ = run_shell(["xdotool", "getwindowname", wid.strip()], timeout=3)
                    return ok({"id": wid.strip(), "title": name.strip() if rc2 == 0 else ""})
            if _wmctrl_available():
                rc, out, _ = run_shell(["wmctrl", "-l"], timeout=5)
                # Return first non-desktop window as heuristic
                for line in out.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) == 4 and parts[1] != "-1":
                        return ok({"id": parts[0], "title": parts[3]})
            # Wayland-safe fallback: AT-SPI active/focused frame state.
            # xdotool can only see XWayland clients, so on native Wayland it
            # returns garbage or fails — AT-SPI tracks focus for all apps.
            try:
                from core.atspi_navigator import ATSPINavigator
                info = ATSPINavigator().get_active_window()
                if info:
                    return ok({"id": None, "title": info["title"], "app": info["app"], "method": "atspi"})
            except Exception as atspi_err:
                import sys
                print(f"atspi fallback failed: {atspi_err}", file=sys.stderr)
            return fail("Could not detect active window — install xdotool")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.get_active",
        description="Return the currently focused window title and ID.",
        side_effect="read",
    ), _get_active)

    # ── window.focus ──────────────────────────────────────────────────────────
    def _focus(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            pid = str(args.get("pid", "")).strip()
            if not title and not pid:
                return fail("'title' or 'pid' is required")
            if _wmctrl_available():
                if title:
                    rc, _, _err = run_shell(["wmctrl", "-a", title], timeout=5)
                    if rc == 0:
                        return ok({"message": f"Focused: {title}"})
                if pid:
                    rc, _, _err = run_shell(["wmctrl", "-ip", pid], timeout=5)
                    if rc == 0:
                        return ok({"message": f"Focused PID: {pid}"})
            if shutil.which("xdotool") and pid:
                rc, _, _ = run_shell(["xdotool", "windowfocus", "--sync", pid], timeout=5)
                if rc == 0:
                    return ok({"message": f"Focused window {pid}"})
            return fail(f"Could not focus window: {title or pid}")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.focus",
        description="Focus (raise) a window by title or PID. Inputs: title, pid.",
        side_effect="local_write",
        inputs=("title", "pid"),
    ), _focus)

    # ── window.move ───────────────────────────────────────────────────────────
    def _move(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            if not title:
                return fail("'title' is required")
            if _wmctrl_available():
                rc, _, err = run_shell(
                    ["wmctrl", "-r", title, "-e", f"0,{x},{y},-1,-1"],
                    timeout=5,
                )
                if rc == 0:
                    return ok({"message": f"Moved {title} to ({x},{y})"})
                return fail(f"wmctrl move failed: {err}")
            return fail("wmctrl is required for window.move")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.move",
        description="Move a window to position (x, y). Inputs: title, x, y.",
        side_effect="local_write",
        inputs=("title", "x", "y"),
    ), _move)

    # ── window.resize ─────────────────────────────────────────────────────────
    def _resize(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            w = int(args.get("width", 800))
            h = int(args.get("height", 600))
            if not title:
                return fail("'title' is required")
            if _wmctrl_available():
                rc, _, err = run_shell(
                    ["wmctrl", "-r", title, "-e", f"0,-1,-1,{w},{h}"],
                    timeout=5,
                )
                return ok({"message": f"Resized {title} to {w}x{h}"}) if rc == 0 else fail(err)
            return fail("wmctrl is required for window.resize")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.resize",
        description="Resize a window. Inputs: title, width, height.",
        side_effect="local_write",
        inputs=("title", "width", "height"),
    ), _resize)

    # ── window.maximize ───────────────────────────────────────────────────────
    def _maximize(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            target = title if title else ":ACTIVE:"
            if _wmctrl_available():
                rc, _, err = run_shell(["wmctrl", "-r", target, "-b", "add,maximized_vert,maximized_horz"], timeout=5)
                return ok({"message": "Maximized"}) if rc == 0 else fail(err)
            # Keyboard fallback: Super+Up on GNOME
            from core.gui_controller import GUIController
            GUIController().hotkey("super", "up")
            return ok({"message": "Maximized via Super+Up"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.maximize",
        description="Maximize a window. Inputs: title (optional, defaults to active).",
        side_effect="local_write",
        inputs=("title",),
    ), _maximize)

    # ── window.minimize ───────────────────────────────────────────────────────
    def _minimize(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            target = title if title else ":ACTIVE:"
            if _wmctrl_available():
                rc, _, _err = run_shell(["wmctrl", "-r", target, "-b", "add,hidden"], timeout=5)
                if rc == 0:
                    return ok({"message": "Minimized"})
            if shutil.which("xdotool"):
                if title:
                    rc, wid, _ = run_shell(["xdotool", "search", "--name", title], timeout=3)
                    wid = wid.strip().splitlines()[0] if rc == 0 and wid.strip() else ""
                else:
                    rc, wid, _ = run_shell(["xdotool", "getactivewindow"], timeout=3)
                    wid = wid.strip()
                if wid:
                    run_shell(["xdotool", "windowminimize", wid], timeout=5)
                    return ok({"message": "Minimized"})
            return fail("Could not minimize — install wmctrl or xdotool")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.minimize",
        description="Minimize a window. Inputs: title (optional).",
        side_effect="local_write",
        inputs=("title",),
    ), _minimize)

    # ── window.restore ────────────────────────────────────────────────────────
    def _restore(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            target = title if title else ":ACTIVE:"
            if _wmctrl_available():
                rc, _, err = run_shell(
                    ["wmctrl", "-r", target, "-b", "remove,maximized_vert,maximized_horz,hidden"],
                    timeout=5,
                )
                return ok({"message": "Restored"}) if rc == 0 else fail(err)
            return fail("wmctrl required for window.restore")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.restore",
        description="Restore a window from minimized/maximized state. Inputs: title (optional).",
        side_effect="local_write",
        inputs=("title",),
    ), _restore)

    # ── window.close ──────────────────────────────────────────────────────────
    def _close(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            if title and _wmctrl_available():
                rc, _, err = run_shell(["wmctrl", "-c", title], timeout=5)
                return ok({"message": f"Closed {title}"}) if rc == 0 else fail(err)
            # Keyboard fallback: Alt+F4
            from core.gui_controller import GUIController
            GUIController().hotkey("alt", "f4")
            return ok({"message": "Closed via Alt+F4"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.close",
        description="Close a window gracefully. Inputs: title (optional).",
        side_effect="local_write",
        inputs=("title",),
    ), _close)

    # ── window.fullscreen ─────────────────────────────────────────────────────
    def _fullscreen(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            target = title if title else ":ACTIVE:"
            if _wmctrl_available():
                rc, _, err = run_shell(
                    ["wmctrl", "-r", target, "-b", "add,fullscreen"],
                    timeout=5,
                )
                return ok({"message": "Fullscreen enabled"}) if rc == 0 else fail(err)
            from core.gui_controller import GUIController
            GUIController().press("f11")
            return ok({"message": "Fullscreen toggled via F11"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.fullscreen",
        description="Set window to fullscreen. Inputs: title (optional).",
        side_effect="local_write",
        inputs=("title",),
    ), _fullscreen)

    # ── window.exit_fullscreen ────────────────────────────────────────────────
    def _exit_fullscreen(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            target = title if title else ":ACTIVE:"
            if _wmctrl_available():
                rc, _, err = run_shell(
                    ["wmctrl", "-r", target, "-b", "remove,fullscreen"],
                    timeout=5,
                )
                return ok({"message": "Exited fullscreen"}) if rc == 0 else fail(err)
            from core.gui_controller import GUIController
            GUIController().press("f11")
            return ok({"message": "Fullscreen toggled via F11"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.exit_fullscreen",
        description="Exit fullscreen mode. Inputs: title (optional).",
        side_effect="local_write",
        inputs=("title",),
    ), _exit_fullscreen)

    # ── window.get_geometry ───────────────────────────────────────────────────
    def _get_geometry(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            if _wmctrl_available():
                _rc, out, _ = run_shell(["wmctrl", "-l", "-G"], timeout=5)
                for line in out.splitlines():
                    if title.lower() in line.lower():
                        parts = line.split(None, 8)
                        if len(parts) >= 7:
                            return ok({
                                "x": int(parts[2]), "y": int(parts[3]),
                                "width": int(parts[4]), "height": int(parts[5]),
                                "title": parts[7] if len(parts) > 7 else "",
                            })
            return fail(f"Window '{title}' not found or wmctrl unavailable")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.get_geometry",
        description="Return window position and size. Inputs: title.",
        side_effect="read",
        inputs=("title",),
    ), _get_geometry)

    # ── window.switch ─────────────────────────────────────────────────────────
    def _switch(args: dict, state: Any) -> Any:
        try:
            title = str(args.get("title", "")).strip()
            index = int(args.get("index", -1))
            if title:
                return _focus({"title": title}, state)
            # Alt+Tab N times
            from core.gui_controller import GUIController
            gui = GUIController()
            times = max(1, index) if index > 0 else 1
            import time
            gui.hotkey("alt", "tab")
            for _ in range(times - 1):
                time.sleep(0.1)
                gui.press("tab")
            return ok({"message": "Switched window"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="window.switch",
        description="Switch to window by title or index. Inputs: title, index.",
        side_effect="local_write",
        inputs=("title", "index"),
    ), _switch)

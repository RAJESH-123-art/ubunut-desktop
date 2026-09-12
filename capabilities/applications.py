"""
capabilities/applications.py — App launch, close, focus, find capabilities.

Covers NIKKI capability family: 2 (APPLICATIONS)

Risk levels:
    READ          — is_running, find, list_running
    LOW           — launch, open, wait_for, focus, switch
    WRITE         — close
    DESTRUCTIVE   — kill (requires_confirmation)
    SYSTEM_CHANGE — restart
"""
from __future__ import annotations

import shlex
import shutil
import signal
import subprocess
import time
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def install(registry: Any, *, approve_all: bool = False) -> None:

    # ── app.launch ────────────────────────────────────────────────────────────
    def _launch(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            from core.app_finder import find_app
            app = find_app(name)
            if app:
                cmd = shlex.split(app.launch_command())
                subprocess.Popen(cmd, start_new_session=True)
                return ok({"message": f"Launched {app.name}", "command": app.launch_command()})
            # Fallback: try running name as command directly
            if shutil.which(name):
                subprocess.Popen([name], start_new_session=True)
                return ok({"message": f"Launched {name} (direct command)"})
            # Fallback: xdg-open
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", name], start_new_session=True)
                return ok({"message": f"Opened {name} via xdg-open"})
            return fail(f"Could not find application: {name}")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.launch",
        description="Launch any installed application by name. Inputs: name.",
        side_effect="local_write",
        inputs=("name",),
        interfaces=("atspi", "filesystem"),
    ), _launch)

    # ── app.open ──────────────────────────────────────────────────────────────
    def _open(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            # Check if already running
            rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=3)
            if rc == 0 and out.strip():
                # Bring to focus instead
                return _focus({"name": name}, state)
            # Launch fresh
            result = _launch(args, state)
            if result.state != "succeeded":
                return result
            # Wait up to 3s for window
            time.sleep(2.0)
            return result
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.open",
        description="Open an application (launches if not running, focuses if already open). Inputs: name.",
        side_effect="local_write",
        inputs=("name",),
    ), _open)

    # ── app.close ─────────────────────────────────────────────────────────────
    def _close(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=3)
            if rc != 0 or not out.strip():
                return ok({"message": f"{name} is not running"})
            pids = out.strip().splitlines()
            for pid_str in pids:
                try:
                    import os
                    os.kill(int(pid_str.strip()), signal.SIGTERM)
                except (ValueError, ProcessLookupError):
                    pass
            return ok({"message": f"Sent SIGTERM to {name} (PIDs: {', '.join(pids)})"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.close",
        description="Close an application gracefully (SIGTERM). Inputs: name.",
        side_effect="local_write",
        inputs=("name",),
    ), _close)

    # ── app.kill ──────────────────────────────────────────────────────────────
    def _kill(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            rc, _, err = run_shell(["pkill", "-9", "-fi", name], timeout=5)
            if rc not in (0, 1):  # 1 = no process matched (not an error)
                return fail(f"pkill failed: {err}")
            return ok({"message": f"Force-killed {name}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.kill",
        description="Force-kill an application (SIGKILL). Inputs: name.",
        side_effect="local_write",
        inputs=("name",),
        requires_confirmation=True,
    ), _kill)

    # ── app.restart ───────────────────────────────────────────────────────────
    def _restart(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            _close(args, state)
            time.sleep(1.5)
            return _launch(args, state)
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.restart",
        description="Close then re-launch an application. Inputs: name.",
        side_effect="local_write",
        inputs=("name",),
    ), _restart)

    # ── app.is_running ────────────────────────────────────────────────────────
    def _is_running(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=3)
            running = rc == 0 and bool(out.strip())
            return ok({"running": running, "name": name})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.is_running",
        description="Check if an application is currently running. Inputs: name.",
        side_effect="read",
        inputs=("name",),
    ), _is_running)

    # ── app.find ──────────────────────────────────────────────────────────────
    def _find(args: dict, state: Any) -> Any:
        try:
            query = str(args.get("name", args.get("query", ""))).strip()
            if not query:
                return fail("'name' or 'query' is required")
            from core.app_finder import find_app
            app = find_app(query)
            if app:
                return ok({
                    "found": True,
                    "apps": [{"name": app.name, "id": app.id, "command": app.launch_command()}],
                })
            return ok({"found": False, "apps": []})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.find",
        description="Find an installed application by name pattern. Inputs: name.",
        side_effect="read",
        inputs=("name",),
    ), _find)

    # ── app.list_running ──────────────────────────────────────────────────────
    def _list_running(args: dict, state: Any) -> Any:
        try:
            _rc, out, _ = run_shell(
                ["ps", "-eo", "pid,comm,args", "--no-headers"],
                timeout=5,
            )
            apps = []
            seen: set = set()
            for line in out.splitlines():
                parts = line.strip().split(None, 2)
                if len(parts) >= 2:
                    pid, name = parts[0], parts[1]
                    if name not in seen and not name.startswith("["):
                        seen.add(name)
                        apps.append({"pid": int(pid), "name": name})
            return ok({"processes": apps[:50], "total": len(apps)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.list_running",
        description="List all currently running applications and processes.",
        side_effect="read",
    ), _list_running)

    # ── app.wait_for ──────────────────────────────────────────────────────────
    def _wait_for(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            timeout = float(args.get("timeout", 10.0))
            if not name:
                return fail("'name' is required")
            from core.wait_until import wait_until
            def check() -> bool:
                rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=2)
                return rc == 0 and bool(out.strip())
            found = wait_until(check, timeout=timeout, interval=0.5)
            return ok({"running": found, "name": name})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.wait_for",
        description="Wait up to timeout seconds for an app to start. Inputs: name, timeout.",
        side_effect="read",
        inputs=("name", "timeout"),
    ), _wait_for)

    # ── app.focus ─────────────────────────────────────────────────────────────
    def _focus(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if not name:
                return fail("'name' is required")
            if shutil.which("wmctrl"):
                rc, _, _ = run_shell(["wmctrl", "-a", name], timeout=5)
                if rc == 0:
                    return ok({"message": f"Focused window: {name}"})
            # GNOME Shell DBus fallback
            rc, _out, _ = run_shell(
                ["gdbus", "call", "--session",
                 "--dest", "org.gnome.Shell",
                 "--object-path", "/org/gnome/Shell",
                 "--method", "org.gnome.Shell.Eval",
                 f"global.get_window_actors().find(a=>a.meta_window.get_title().toLowerCase().includes('{name.lower()}')).meta_window.activate(global.get_current_time())"],
                timeout=5,
            )
            return ok({"message": f"Focused {name}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.focus",
        description="Bring an application's window to the foreground. Inputs: name.",
        side_effect="local_write",
        inputs=("name",),
        interfaces=("atspi",),
    ), _focus)

    # ── app.switch ────────────────────────────────────────────────────────────
    def _switch(args: dict, state: Any) -> Any:
        try:
            name = str(args.get("name", "")).strip()
            if name:
                return _focus({"name": name}, state)
            # Generic Alt+Tab
            from core.gui_controller import GUIController
            gui = GUIController()
            gui.hotkey("alt", "tab")
            return ok({"message": "Switched window via Alt+Tab"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="app.switch",
        description="Switch to another application window. Inputs: name (optional).",
        side_effect="local_write",
        inputs=("name",),
    ), _switch)

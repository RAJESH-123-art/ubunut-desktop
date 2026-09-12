"""
capabilities/terminal.py — Terminal and Shell Command capabilities.

Covers NIKKI capability family: 9 (TERMINAL)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.safety_guard import UnsafeActionError, assert_safe_shell_command
from core.system_utils import open_terminal


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _terminal_open(args: dict[str, Any], state: Any = None) -> Any:
        try:
            cwd = Path(args.get("cwd", ".")).expanduser().resolve()
            open_terminal(cwd)
            return ok({"opened": True, "cwd": str(cwd)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("terminal.open", "Open terminal emulator window", Cap.LOW, ("cwd",)), _terminal_open)

    def _terminal_run(args: dict[str, Any], state: Any = None) -> Any:
        cmd = args.get("command", "")
        timeout = int(args.get("timeout", 30))
        cwd = args.get("cwd", None)
        try:
            assert_safe_shell_command(cmd)
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                check=False,  # callers branch on returncode
            )
            return ok({
                "returncode": res.returncode,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
            })
        except UnsafeActionError as e:
            return fail(f"Safety guard blocked dangerous command: {e}")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("terminal.run", "Run shell command safely", Cap.WRITE, ("command", "timeout", "cwd")), _terminal_run)

    def _terminal_which(args: dict[str, Any], state: Any = None) -> Any:
        cmd = args.get("command", "")
        path = shutil.which(cmd)
        return ok({"command": cmd, "found": bool(path), "path": path or ""})

    register_cap(registry, Cap("terminal.which", "Find executable in PATH", Cap.READ, ("command",)), _terminal_which)

"""
capabilities/pty_terminal.py — Interactive Pseudo-Terminal (PTY) capabilities.

Covers NIKKI capability family: 9 (INTERACTIVE TERMINAL / PTY)
"""
from __future__ import annotations

import os
import pty
import select
import subprocess
import time
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.safety_guard import assert_safe_shell_command


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _pty_run(args: dict[str, Any], state: Any = None) -> Any:
        cmd = str(args.get("command", ""))
        timeout = max(1, min(600, int(args.get("timeout", 15))))
        if not cmd:
            return fail("command is required")
        master = None
        proc = None
        try:
            assert_safe_shell_command(cmd)
            master, slave = pty.openpty()
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
            os.close(slave)
            slave = None  # guard against double-close in finally

            output = []
            deadline = time.monotonic() + timeout
            finished = False
            while time.monotonic() < deadline:
                # Drain available output in small chunks
                r, _, _ = select.select([master], [], [], 0.5)
                if master in r:
                    try:
                        data = os.read(master, 4096)
                        if data:
                            output.append(data.decode("utf-8", errors="replace"))
                    except OSError:
                        # EIO: slave fully closed → process exited
                        finished = True
                        break
                if proc.poll() is not None:
                    # Give the pipe a moment to drain remaining output
                    drain_until = time.monotonic() + 0.5
                    while time.monotonic() < drain_until:
                        r, _, _ = select.select([master], [], [], 0.1)
                        if master in r:
                            try:
                                data = os.read(master, 4096)
                                if data:
                                    output.append(data.decode("utf-8", errors="replace"))
                            except OSError:
                                break
                        else:
                            break
                    finished = True
                    break

            if not finished and proc.poll() is None:
                # Timed out while still running — kill and report honestly
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return fail(
                    f"command still running after {timeout}s and was terminated; "
                    f"partial output: {''.join(output)[-500:]!r}"
                )

            returncode = proc.returncode
            return ok({
                "command": cmd,
                "output": "".join(output).strip(),
                "returncode": returncode,
                "timed_out": False,
            })
        except Exception as e:
            return fail(f"pty.run failed: {e}")
        finally:
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            if proc is not None and proc.poll() is None:
                proc.kill()

    register_cap(registry, Cap("pty.run", "Run shell command in an interactive pseudo-terminal (guarded, honest timeout reporting)", Cap.WRITE, ("command", "timeout")), _pty_run)

"""
capabilities/process.py — Process monitoring and management capabilities.

Covers NIKKI capability family: 12 (PROCESS)
"""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _process_list(args: dict[str, Any], state: Any = None) -> Any:
        try:
            out = subprocess.check_output(["ps", "-eo", "pid,user,comm,%cpu,%mem"], text=True)
            lines = out.strip().split("\n")[1:]
            processes = []
            for line in lines[:50]:
                parts = line.split(maxsplit=4)
                if len(parts) == 5:
                    processes.append({
                        "pid": int(parts[0]),
                        "user": parts[1],
                        "name": parts[2],
                        "cpu": parts[3],
                        "mem": parts[4],
                    })
            return ok({"processes": processes, "count": len(processes)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.list", "List running processes", Cap.READ, ()), _process_list)

    def _process_find(args: dict[str, Any], state: Any = None) -> Any:
        name = args.get("name", "")
        try:
            out = subprocess.check_output(["pgrep", "-l", name], text=True)
            matches = []
            for line in out.strip().split("\n"):
                if line:
                    parts = line.split(maxsplit=1)
                    matches.append({"pid": int(parts[0]), "name": parts[1] if len(parts) > 1 else ""})
            return ok({"query": name, "matches": matches})
        except subprocess.CalledProcessError:
            return ok({"query": name, "matches": []})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.find", "Find process by name", Cap.READ, ("name",)), _process_find)

    def _process_kill(args: dict[str, Any], state: Any = None) -> Any:
        pid = args.get("pid")
        if not pid:
            return fail("pid is required")
        try:
            os.kill(int(pid), signal.SIGKILL)
            return ok({"pid": pid, "killed": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.kill", "Kill process by PID", Cap.DESTRUCTIVE, ("pid",), requires_confirmation=True), _process_kill)

    def _process_start(args: dict[str, Any], state: Any = None) -> Any:
        cmd = args.get("command", "")
        if not cmd:
            return fail("command is required")
        try:
            proc = subprocess.Popen(cmd, shell=True)
            return ok({"command": cmd, "pid": proc.pid, "started": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.start", "Start a new background process", Cap.LOW, ("command",)), _process_start)

    def _process_stop(args: dict[str, Any], state: Any = None) -> Any:
        pid = args.get("pid")
        if not pid:
            return fail("pid is required")
        try:
            os.kill(int(pid), signal.SIGTERM)
            return ok({"pid": pid, "stopped": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.stop", "Stop process gracefully via SIGTERM", Cap.WRITE, ("pid",)), _process_stop)

    def _process_wait_for(args: dict[str, Any], state: Any = None) -> Any:
        pid = args.get("pid")
        timeout = int(args.get("timeout", 30))
        if not pid:
            return fail("pid is required")
        try:
            import psutil
            proc = psutil.Process(int(pid))
            proc.wait(timeout=timeout)
            return ok({"pid": pid, "exited": True})
        except ImportError:
            import time
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    os.kill(int(pid), 0)
                    time.sleep(0.5)
                except ProcessLookupError:
                    return ok({"pid": pid, "exited": True})
            return fail(f"Process {pid} still running after {timeout}s")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.wait_for", "Wait for process to finish", Cap.READ, ("pid", "timeout")), _process_wait_for)

    def _process_get_info(args: dict[str, Any], state: Any = None) -> Any:
        pid = args.get("pid")
        if not pid:
            return fail("pid is required")
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "pid,user,comm,%cpu,%mem,etime"], text=True
            )
            lines = [l for l in out.strip().split("\n") if l.strip()]
            if len(lines) < 2:
                return fail(f"Process {pid} not found")
            parts = lines[1].split(maxsplit=5)
            return ok({"pid": parts[0], "user": parts[1], "name": parts[2], "cpu": parts[3], "mem": parts[4], "elapsed": parts[5] if len(parts) > 5 else ""})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.get_info", "Get detailed info about a process by PID", Cap.READ, ("pid",)), _process_get_info)

    def _process_monitor(args: dict[str, Any], state: Any = None) -> Any:
        name = args.get("name", "")
        try:
            out = subprocess.check_output(["pgrep", "-l", name], text=True)
            alive = [l for l in out.strip().split("\n") if l]
            return ok({"name": name, "running": len(alive) > 0, "instances": len(alive)})
        except subprocess.CalledProcessError:
            return ok({"name": name, "running": False, "instances": 0})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("process.monitor", "Check if a named process is still running", Cap.READ, ("name",)), _process_monitor)

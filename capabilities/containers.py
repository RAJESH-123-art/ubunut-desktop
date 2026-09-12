"""
capabilities/containers.py — Container Management capabilities (Docker / Podman).

Covers NIKKI capability family: 27 (CONTAINERS)
"""
from __future__ import annotations

import shutil
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def install(registry: Any, *, approve_all: bool = False) -> None:
    tool = "docker" if shutil.which("docker") else ("podman" if shutil.which("podman") else None)

    def _container_list(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        code, out, err = run_shell([tool, "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"])
        if code != 0:
            return fail(err or "Failed to list containers")
        containers = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                containers.append({"id": parts[0], "name": parts[1], "status": parts[2], "image": parts[3]})
        return ok({"containers": containers, "tool": tool})

    register_cap(registry, Cap("container.list", "List containers", Cap.READ, ()), _container_list)

    def _container_start(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        cid = args.get("name_or_id", "")
        code, _out, err = run_shell([tool, "start", cid])
        return ok({"container": cid}) if code == 0 else fail(err)

    register_cap(registry, Cap("container.start", "Start container", Cap.LOW, ("name_or_id",)), _container_start)

    def _container_stop(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        cid = args.get("name_or_id", "")
        code, _out, err = run_shell([tool, "stop", cid])
        return ok({"container": cid}) if code == 0 else fail(err)

    register_cap(registry, Cap("container.stop", "Stop container", Cap.WRITE, ("name_or_id",)), _container_stop)

    def _container_inspect(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        cid = args.get("name_or_id", "")
        code, out, err = run_shell([tool, "inspect", cid])
        return ok({"container": cid, "details": out}) if code == 0 else fail(err)

    register_cap(registry, Cap("container.inspect", "Inspect container details and configuration", Cap.READ, ("name_or_id",)), _container_inspect)

    def _container_logs(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        cid = args.get("name_or_id", "")
        tail = str(args.get("tail", 100))
        code, out, err = run_shell([tool, "logs", "--tail", tail, cid])
        return ok({"container": cid, "logs": out or err}) if code == 0 else fail(err)

    register_cap(registry, Cap("container.logs", "Read stdout/stderr logs from container", Cap.READ, ("name_or_id", "tail")), _container_logs)

    def _container_exec(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        cid = args.get("name_or_id", "")
        cmd = args.get("command", "")
        if not cmd:
            return fail("command parameter is required")
        code, out, err = run_shell([tool, "exec", cid, "sh", "-c", cmd])
        return ok({"container": cid, "command": cmd, "stdout": out, "stderr": err}) if code == 0 else fail(err)

    register_cap(registry, Cap("container.exec", "Execute command inside running container", Cap.WRITE, ("name_or_id", "command")), _container_exec)

    def _container_remove(args: dict[str, Any], state: Any = None) -> Any:
        if not tool:
            return fail("Neither docker nor podman is installed")
        cid = args.get("name_or_id", "")
        code, _out, err = run_shell([tool, "rm", "-f", cid])
        return ok({"container": cid, "removed": True}) if code == 0 else fail(err)

    register_cap(registry, Cap("container.remove", "Remove container instance", Cap.DESTRUCTIVE, ("name_or_id",), requires_confirmation=True), _container_remove)


"""
capabilities/vms.py — Virtual Machine Management capabilities.

Covers NIKKI capability family: 28 (VIRTUAL MACHINES)
"""
from __future__ import annotations

import shutil
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _vm_list(args: dict[str, Any], state: Any = None) -> Any:
        if shutil.which("virsh"):
            code, out, err = run_shell(["virsh", "list", "--all"])
            return ok({"output": out, "tool": "virsh"}) if code == 0 else fail(err)
        elif shutil.which("VBoxManage"):
            code, out, err = run_shell(["VBoxManage", "list", "vms"])
            return ok({"output": out, "tool": "VBoxManage"}) if code == 0 else fail(err)
        return fail("No hypervisor CLI (virsh/VBoxManage) found")

    register_cap(registry, Cap("vm.list", "List virtual machines", Cap.READ, ()), _vm_list)

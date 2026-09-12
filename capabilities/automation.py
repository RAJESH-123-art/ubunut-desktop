"""
capabilities/automation.py — Macro, Automation & Trigger capabilities.

Covers NIKKI capability family: 39 (AUTOMATION)
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, register_cap


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _automation_run_macro(args: dict[str, Any], state: Any = None) -> Any:
        macro_name = str(args.get("name", ""))
        steps = args.get("steps", [])
        if not macro_name:
            return fail("name is required")
        if not isinstance(steps, list) or not steps:
            return fail("steps (non-empty list) is required")
        # A macro IS its steps executed in order — report honestly that this
        # stub does not execute them; real execution goes through the TaskRuntime.
        return fail(
            "automation.run_macro does not execute steps by itself; compose the "
            "steps as runtime actions instead (macros are plans, not executors)"
        )

    register_cap(registry, Cap("automation.run_macro", "NOT an executor — compose steps through the task runtime instead", Cap.LOW, ("name", "steps")), _automation_run_macro)

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/usr/lib/python3/dist-packages")

from core.atspi_navigator import wait_for_app
from core.runtime_adapters import execute_structured_plan_runtime
from core.runtime_observer import RuntimeObserver
from core.structured_automation import StructuredExecutor, validate_plan
from tasks.window_management import execute as manage_window


def main() -> int:
    existing = wait_for_app(["gnome-calculator", "calculator"], timeout=0.5)
    launched_by_test = existing is None
    steps: list[dict[str, object]] = []
    if launched_by_test:
        steps.append({
            "action": "launch_app",
            "args": {"app": "calculator"},
            "expect": {"app_running": True},
        })
    else:
        steps.append({"action": "focus_app", "args": {"app": "calculator"}})
    steps.extend([
        {
            "action": "click_ui",
            "args": {"app": "calculator", "text": "clear"},
            "expect": {"ui_text": "0"},
        },
        {"action": "click_ui", "args": {"app": "calculator", "text": "7"}},
        {"action": "click_ui", "args": {"app": "calculator", "text": "plus"}},
        {"action": "click_ui", "args": {"app": "calculator", "text": "5"}},
        {"action": "click_ui", "args": {"app": "calculator", "text": "equals"}},
        {"action": "read_ui", "args": {"app": "calculator", "target": "12"}},
        {
            "action": "assert_value",
            "args": {"value": "${step.7.text}", "operator": "equals", "expected": "12"},
        },
    ])
    plan = validate_plan({
        "summary": "Calculate seven plus five and verify the accessible result",
        "steps": steps,
    })
    executor = StructuredExecutor(
        approve_all=True,
        step_timeout=8.0,
        total_timeout=45.0,
    )
    try:
        observer = RuntimeObserver()
        result = execute_structured_plan_runtime(
            plan,
            "live structured calculator test",
            executor=executor,
            observe=lambda _runtime: observer.observe(),
        )
        print(f"LAUNCHED_BY_TEST={launched_by_test}")
        print(f"SUCCESS={result.success}")
        print(f"COMPLETED={result.state.verified_facts.get('structured_next_step', 0)}")
        print(f"MESSAGE={result.state.message}")
        for item in result.state.verified_facts.get("structured_evidence", []):
            print(f"EVIDENCE={item}")
        if not result.success:
            print("CALCULATOR_ACCESSIBILITY_SNAPSHOT")
            for line in executor._native_snapshot("calculator"):
                print(f"  {line}")
        print("RESULT=PASS" if result.success else "RESULT=FAIL")
        return 0 if result.success else 1
    finally:
        if launched_by_test:
            closed = manage_window(
                {"operation": "close", "window": "Calculator"},
                {},
            )
            print(f"TEST_LAUNCHED_CALCULATOR_CLOSED={closed}")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/usr/lib/python3/dist-packages")

from core.gui_controller import GUIController
from core.runtime_adapters import execute_structured_plan_runtime
from core.runtime_observer import RuntimeObserver
from core.structured_automation import StructuredExecutor, validate_plan
from scripts.live_cross_app_clipboard_test import (
    fresh_text_editor_target,
    fresh_writer_target,
    set_exact,
)


def main() -> int:
    marker = "structured_cross_app_payload_24680"
    gui = GUIController(safe_mode=False)
    writer = fresh_writer_target()
    set_exact(writer, marker)
    editor = fresh_text_editor_target(gui)
    set_exact(editor, "")

    plan = validate_plan({
        "summary": "Copy verified text from Writer into Text Editor",
        "steps": [
            {"action": "copy_ui", "args": {"app": "soffice", "target": marker}},
            {
                "action": "paste_ui",
                "args": {
                    "app": "gnome-text-editor",
                    "field": "document",
                    "text": "${step.1.text}",
                },
            },
            {"action": "read_ui", "args": {"app": "gnome-text-editor", "target": marker}},
            {
                "action": "assert_value",
                "args": {
                    "value": "${step.3.text}",
                    "operator": "equals",
                    "expected": marker,
                },
            },
        ],
    })
    executor = StructuredExecutor(approve_all=True, step_timeout=8.0, total_timeout=55.0)
    observer = RuntimeObserver()
    result = execute_structured_plan_runtime(
        plan,
        "live structured cross-app transfer",
        executor=executor,
        observe=lambda _runtime: observer.observe(),
    )
    print(f"SUCCESS={result.success}")
    print(f"COMPLETED={result.state.verified_facts.get('structured_next_step', 0)}")
    print(f"MESSAGE={result.state.message}")
    print(f"METHODS={[item.get('method') for item in result.state.verified_facts.get('structured_evidence', [])]}")
    print("RESULT=PASS" if result.success else "RESULT=FAIL")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

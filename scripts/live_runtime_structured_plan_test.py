"""Live proof that a fixed structured plan uses the authoritative runtime."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.runtime_adapters import execute_structured_plan_runtime
from core.structured_automation import StructuredExecutor, validate_plan


def main() -> int:
    browser = brave(headless=True).start()
    executor = StructuredExecutor(approve_all=True, total_timeout=30.0)
    executor.attach_page(browser.page)
    try:
        source = "runtime_bridge_value_314159"
        browser.page.set_content(f"""<!doctype html><main>
          <p>{source}</p>
          <label>Destination <input aria-label="Destination"></label>
          <button>This control must remain untouched</button>
        </main>""")
        plan = validate_plan({
            "summary": "Prove shared structured runtime",
            "steps": [
                {"action": "extract_text", "args": {"target": source}},
                {"action": "transform_text", "args": {
                    "value": "${step.1.text}", "operation": "upper",
                }},
                {"action": "assert_value", "args": {
                    "value": "${step.2.value}", "operator": "equals",
                    "expected": source.upper(),
                }},
                {"action": "click", "args": {"text": "This control must remain untouched"}, "when": {
                    "value": "${step.1.text}", "operator": "equals", "expected": "not-present",
                }},
                {"action": "fill", "args": {
                    "label": "Destination", "text": "${step.1.text}",
                }},
                {"action": "read_field", "args": {"label": "Destination"}},
            ],
        })

        def observe(_runtime):
            return {
                "interfaces": ["browser_dom"],
                "destination": browser.page.get_by_label("Destination").input_value(),
            }

        result = execute_structured_plan_runtime(
            plan,
            "prove shared structured runtime",
            executor=executor,
            page=browser.page,
            observe=observe,
        )
        completed = [(key, item.status) for key, item in result.state.subgoals.items()]
        evidence = result.state.verified_facts.get("structured_evidence", [])
        print(f"SUCCESS={result.success}")
        print(f"SUBGOALS={completed}")
        print(f"NEXT_STEP={result.state.verified_facts.get('structured_next_step')}")
        print(f"DESTINATION={observe(result.state)['destination']!r}")
        print(f"SKIPPED={any(item.get('skipped') for item in evidence)}")
        if not result.success or observe(result.state)["destination"] != source:
            return 1
        if not any(item.get("skipped") for item in evidence):
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        executor.close()
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Live local proof that a changed browser control is repaired before input."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.structured_automation import PlanStep, StructuredExecutor, validate_plan


def main() -> int:
    browser = brave(headless=True).start()
    repair_calls: list[dict] = []
    try:
        browser.page.set_content("""<!doctype html><title>Changed Layout Test</title>
          <main><p id="status">Waiting</p>
          <button onclick="document.querySelector('#status').textContent='Recovered action completed'">
          Continue with updated layout</button></main>""")

        def repair(_goal, _plan, _index, failed_step, error, evidence):
            repair_calls.append({"failed": failed_step.args["text"], "error": error, "evidence": evidence})
            return PlanStep(
                "click",
                {"text": "Continue with updated layout"},
                {"text": "Recovered action completed"},
            )

        plan = validate_plan({
            "summary": "Use the changed control label",
            "steps": [{"action": "click", "args": {"text": "Continue to checkout"}}],
        })
        executor = StructuredExecutor(approve_all=True, repair_callback=repair)
        executor.attach_page(browser.page)
        result = executor.execute(plan, goal="continue through the updated browser layout")
        status = browser.page.locator("#status").inner_text()
        evidence = result.evidence[0] if result.evidence else {}
        controls = repair_calls[0]["evidence"][-1].get("controls", []) if repair_calls else []
        print(f"SUCCESS={result.success}")
        print(f"COMPLETED={result.completed_steps}")
        print(f"REPAIRED_FROM={evidence.get('repaired_from_action')}")
        print(f"STATUS={status!r}")
        print(f"FRESH_CONTROL_VISIBLE={any(item.get('name') == 'Continue with updated layout' for item in controls)}")
        if not result.success or status != "Recovered action completed" or len(repair_calls) != 1:
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

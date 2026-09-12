"""Live proof that a runtime checkpoint resumes without replaying a browser step."""
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.runtime_adapters import execute_structured_plan_runtime, resume_structured_plan_runtime
from core.structured_automation import StructuredExecutor, validate_plan


def main() -> int:
    browser = brave(headless=True).start()
    executor = StructuredExecutor(approve_all=True, total_timeout=30.0)
    executor.attach_page(browser.page)
    try:
        browser.page.set_content("""<!doctype html><main>
          <label>Destination <input aria-label="Destination"></label>
        </main>""")
        plan = validate_plan({
            "summary": "Resume a browser form without replay",
            "steps": [
                {"action": "fill", "args": {"label": "Destination", "text": "resumed-value"}},
                {"action": "read_field", "args": {"label": "Destination"}},
            ],
        })
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "resume.json"
            first = execute_structured_plan_runtime(
                plan,
                "resume a browser form without replay",
                executor=executor,
                page=browser.page,
                checkpoint_path=checkpoint,
                max_steps=1,
            )
            after_first = browser.page.get_by_label("Destination").input_value()
            resumed = resume_structured_plan_runtime(
                "resume a browser form without replay",
                checkpoint,
                executor=executor,
                page=browser.page,
            )
        value = browser.page.get_by_label("Destination").input_value()
        print(f"FIRST_STATUS={first.state.status}")
        print(f"FIRST_NEXT_STEP={first.state.verified_facts.get('structured_next_step')}")
        print(f"AFTER_FIRST={after_first!r}")
        print(f"RESUMED_SUCCESS={resumed.success}")
        print(f"RESUMED_NEXT_STEP={resumed.state.verified_facts.get('structured_next_step')}")
        print(f"VALUE={value!r}")
        if (
            first.state.status != "running"
            or first.state.verified_facts.get("structured_next_step") != 1
            or after_first != "resumed-value"
            or not resumed.success
            or resumed.state.verified_facts.get("structured_next_step") != 2
            or value != "resumed-value"
        ):
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        executor.close()
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

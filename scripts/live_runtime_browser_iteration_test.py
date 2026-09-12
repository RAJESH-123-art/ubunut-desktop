"""Live local proof of runtime-discovered multi-item browser work.

No task-specific executor is used.  The runtime reads the live DOM, creates
one subgoal per discovered item, invokes the generic structured click
capability, verifies the changed DOM, and completes only when every item is
verified.  This is a compact generalization test for dynamic iteration.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.capability_registry import CapabilityRegistry, StructuredCapabilities
from core.structured_automation import StructuredExecutor
from core.task_runtime import RuntimeAction, RuntimeDecision, RuntimeSubgoalSpec, TaskRuntime


def main() -> int:
    browser = brave(headless=True).start()
    executor = StructuredExecutor(approve_all=True, total_timeout=30.0)
    executor.attach_page(browser.page)
    registry = CapabilityRegistry()
    capabilities = StructuredCapabilities(
        executor=executor,
        page=browser.page,
        goal="process every discovered local item",
    )
    capabilities.install(registry)
    try:
        browser.page.set_content("""<!doctype html><main>
          <h1>Runtime Iteration Test</h1><ul id='items'>
          <li data-item='Alpha'><button onclick="this.closest('li').dataset.done='true';this.textContent='Processed Alpha'">Process Alpha</button></li>
          <li data-item='Beta'><button onclick="this.closest('li').dataset.done='true';this.textContent='Processed Beta'">Process Beta</button></li>
          <li data-item='Gamma'><button onclick="this.closest('li').dataset.done='true';this.textContent='Processed Gamma'">Process Gamma</button></li>
          </ul></main>""")

        def observe(_runtime):
            items = browser.page.locator("#items li").evaluate_all(
                "els => els.map(el => ({name: el.dataset.item, done: el.dataset.done === 'true'}))"
            )
            return {"interfaces": ["browser_dom"], "items": items}

        def decide(_runtime, observed):
            items = list(observed["items"])
            declarations = tuple(
                RuntimeSubgoalSpec(f"item:{item['name']}", f"Process {item['name']}")
                for item in items
            )
            pending = next((item for item in items if not item["done"]), None)
            if pending is None:
                return RuntimeDecision(complete=True, message="All discovered items are verified", subgoals=declarations)
            label = f"Process {pending['name']}"
            return RuntimeDecision(
                action=RuntimeAction(
                    "structured.click",
                    {"text": label, "expect": {"text": f"Processed {pending['name']}"}},
                    subgoal=f"item:{pending['name']}",
                ),
                subgoals=declarations,
            )

        runtime = TaskRuntime.with_capabilities(
            capabilities=registry,
            observe=observe,
            decide=decide,
            verify=lambda action, outcome, _before, after, _runtime: (
                outcome.success
                and any(
                    item["name"] == action.subgoal.removeprefix("item:") and item["done"]
                    for item in after["items"]
                )
            ),
            final_verify=lambda _runtime, observed: all(item["done"] for item in observed["items"]),
            max_steps=8,
        )
        result = runtime.run("process every discovered local item")
        items = observe(result.state)["items"]
        print(f"SUCCESS={result.success}")
        print(f"PLAN_VERSION={result.state.plan_version}")
        print(f"SUBGOALS={[(key, value.status) for key, value in result.state.subgoals.items()]}")
        print(f"ITEMS={items}")
        if not result.success or not all(item["done"] for item in items):
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        capabilities.close()
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

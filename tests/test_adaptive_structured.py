from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.action_loop import ActionLoop, LoopResult, LoopRuntimeState
from core.structured_automation import (
    ActionNotDispatched,
    PlanStep,
    StructuredExecutor,
    UncertainStepOutcome,
    validate_plan,
)
from core.task_contract import ActionOutcome


class RecordingExecutor(StructuredExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dispatched: list[PlanStep] = []
        self.failure: Exception | None = None

    def _dispatch_step(self, step: PlanStep) -> dict:
        self.dispatched.append(step)
        if self.failure is not None:
            raise self.failure
        return {"confirmed_action": step.action}


class StructuredSingleStepTests(unittest.TestCase):
    def test_safe_step_dispatches_without_approval(self) -> None:
        executor = RecordingExecutor()
        outcome = executor.execute_step(PlanStep("wait", {"seconds": 0}))
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.dispatch_status, "confirmed")
        self.assertEqual(executor.dispatched[0].action, "wait")

    def test_consequential_step_requires_exact_plan_approval(self) -> None:
        executor = RecordingExecutor()
        step = PlanStep("click_ui", {"app": "calculator", "text": "7"})
        denied = executor.execute_step(step)
        self.assertEqual(denied.state, "blocked")
        self.assertEqual(executor.dispatched, [])

        approved_plans = []
        approved = executor.execute_step(
            step,
            approval_callback=lambda plan: approved_plans.append(plan) or True,
        )
        self.assertTrue(approved.success)
        self.assertEqual(len(approved_plans), 1)
        self.assertEqual(approved_plans[0].steps, (step,))

    def test_approval_callback_cannot_mutate_approved_step(self) -> None:
        executor = RecordingExecutor()
        step = PlanStep("click_ui", {"app": "calculator", "text": "7"})

        def mutate(plan):
            plan.steps[0].args["text"] = "8"
            return True

        outcome = executor.execute_step(step, approval_callback=mutate)
        self.assertEqual(outcome.state, "blocked")
        self.assertEqual(executor.dispatched, [])

    def test_uncertain_consequential_outcome_is_typed_and_not_retried(self) -> None:
        executor = RecordingExecutor(approve_all=True)
        executor.failure = UncertainStepOutcome("click may have landed")
        outcome = executor.execute_step(
            PlanStep("click_ui", {"app": "calculator", "text": "7"})
        )
        self.assertEqual(outcome.state, "uncertain")
        self.assertEqual(outcome.dispatch_status, "unknown")
        self.assertEqual(len(executor.dispatched), 1)

    def test_changed_repair_requires_separate_approval(self) -> None:
        replacement = PlanStep("click", {"text": "Continue"})

        def repair(*_args):
            return replacement

        executor = RecordingExecutor(repair_callback=repair)
        executor.failure = ActionNotDispatched("missing")
        outcome = executor.execute_step(
            PlanStep("navigate", {"url": "https://example.test"}),
            approval_callback=lambda _plan: False,
        )
        self.assertFalse(outcome.success)
        self.assertIn("replacement was not explicitly approved", outcome.error or "")
        self.assertEqual(len(executor.dispatched), 1)


class AdaptiveMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = ActionLoop(api_key="test", max_steps=3)

    def test_browser_decisions_map_to_validated_structured_steps(self) -> None:
        self.assertEqual(
            self.loop._decision_to_plan_step(
                {"action": "goto", "url": "example.test"}, browser_mode=True
            ).action,
            "navigate",
        )
        fill = self.loop._decision_to_plan_step(
            {"action": "type", "field": "Search", "text": "cats"},
            browser_mode=True,
        )
        self.assertEqual(fill.action, "fill")
        self.assertEqual(fill.args, {"label": "Search", "text": "cats"})

    def test_drag_decisions_map_to_structured_primitives(self) -> None:
        browser_drag = self.loop._decision_to_plan_step(
            {"action": "drag", "source": "Card A", "target": "Done"},
            browser_mode=True,
        )
        visual_drag = self.loop._decision_to_plan_step(
            {"action": "drag", "source": "blue node", "target": "canvas group"},
            app_hint="drawing app",
        )

        self.assertEqual(browser_drag.action, "drag_drop")
        self.assertEqual(browser_drag.args["source"], "Card A")
        self.assertEqual(visual_drag.action, "visual_drag")
        self.assertEqual(visual_drag.args["app"], "drawing app")
        self.assertTrue(StructuredExecutor._step_is_consequential(browser_drag))
        self.assertTrue(StructuredExecutor._step_is_consequential(visual_drag))

    def test_native_decisions_map_to_app_scoped_steps(self) -> None:
        click = self.loop._decision_to_plan_step(
            {"action": "click", "description": "7"}, app_hint="calculator"
        )
        key = self.loop._decision_to_plan_step(
            {"action": "key", "key": "Return"}, app_hint="calculator"
        )
        self.assertEqual(click.action, "click_ui")
        self.assertEqual(click.args["app"], "calculator")
        self.assertEqual(key.action, "hotkey_ui")
        self.assertEqual(key.args["keys"], ["Return"])

    def test_type_requires_explicit_field_and_shell_is_denied(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit field"):
            self.loop._decision_to_plan_step(
                {"action": "type", "text": "unsafe focus"},
                app_hint="writer",
            )
        with self.assertRaisesRegex(PermissionError, "run_shell denied"):
            self.loop._decision_to_plan_step(
                {"action": "run_shell", "command": "echo no"}
            )

    def test_cross_application_clipboard_steps_are_validated(self) -> None:
        plan = validate_plan({
            "summary": "Copy one accessible value and paste it into another app",
            "steps": [
                {
                    "action": "copy_ui",
                    "args": {"app": "writer", "target": "source value"},
                },
                {
                    "action": "paste_ui",
                    "args": {
                        "app": "text editor",
                        "field": "document",
                        "text": "${step.1.text}",
                    },
                },
            ],
        })
        self.assertEqual(plan.steps[0].action, "copy_ui")
        self.assertEqual(plan.steps[1].args["text"], "${step.1.text}")

    def test_runtime_state_stops_when_a_verified_native_target_disappears(self) -> None:
        before = LoopRuntimeState(
            app_hint="calculator", target_seen=True, open_apps=("Calculator",)
        )
        after = LoopRuntimeState(
            app_hint="calculator", target_seen=True, open_apps=("Text Editor",)
        )
        problem = self.loop._runtime_problem(before, after, browser_mode=False)
        self.assertIn("disappeared", problem)

    def test_runtime_state_allows_launch_before_target_has_been_seen(self) -> None:
        before = LoopRuntimeState(app_hint="calculator", target_seen=False)
        after = LoopRuntimeState(app_hint="calculator", target_seen=False)
        self.assertEqual(self.loop._runtime_problem(before, after, browser_mode=False), "")

    def test_runtime_state_uses_the_fresh_target_observation(self) -> None:
        state = self.loop._capture_runtime_state(
            "App: GNOME Calculator\nVisible interactive elements:\n- [push button] 7",
            app_hint="calculator",
        )
        self.assertTrue(state.target_seen)
        self.assertEqual(state.active_window, "GNOME Calculator")
        self.assertEqual(state.open_apps, ("GNOME Calculator",))


class ActionLoopStructuredIntegrationTests(unittest.TestCase):
    def test_runtime_bridge_uses_adaptive_decisions_and_final_verification(self) -> None:
        class FakeExecutor:
            def __init__(self, **_kwargs):
                self.calls = 0

            def execute_step(self, _step, **_kwargs):
                self.calls += 1
                return ActionOutcome(
                    state="succeeded",
                    dispatch_status="confirmed",
                    side_effect="read",
                )

            def close(self):
                pass

        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=2)
                self.observations = iter(["before", "after", "after"])
                self.decisions = iter([
                    {"action": "click", "description": "Continue"},
                    {"action": "done", "success": True, "message": "finished"},
                ])

            def _observe(self, app_hint="", browser_page=None):
                return next(self.observations)

            def _decide(self, *_args, **_kwargs):
                return next(self.decisions)

            def _verify_done(self, claimed_success, *_args, **_kwargs):
                return claimed_success, "finished"

        with patch("core.structured_automation.StructuredExecutor", FakeExecutor):
            result = TestLoop().build_runtime(
                "continue task", app_hint="calculator"
            ).run("continue task")

        self.assertTrue(result.success)
        self.assertEqual(result.state.status, "completed")
        self.assertTrue(any(event.phase == "verified" for event in result.state.events))

    def test_run_runtime_preserves_the_existing_loop_result_contract(self) -> None:
        class FakeExecutor:
            def __init__(self, **_kwargs):
                pass

            def execute_step(self, _step, **_kwargs):
                return ActionOutcome(
                    state="succeeded",
                    dispatch_status="confirmed",
                    side_effect="read",
                )

            def close(self):
                pass

        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=2)
                self.observations = iter(["before", "after"])
                self.decisions = iter([
                    {"action": "wait", "seconds": 0},
                    {"action": "done", "success": True, "message": "complete"},
                ])

            def _observe(self, app_hint="", browser_page=None):
                return next(self.observations)

            def _decide(self, *_args, **_kwargs):
                return next(self.decisions)

            def _verify_done(self, claimed_success, *_args, **_kwargs):
                return claimed_success, "complete"

            def _close_playwright(self):
                pass

        with patch("core.structured_automation.StructuredExecutor", FakeExecutor):
            result = TestLoop().run_runtime("wait and finish")

        self.assertTrue(result.success)
        self.assertEqual(result.message, "complete")
        self.assertEqual(result.steps[0].decision["action"], "wait")
        self.assertTrue(any(item["phase"] == "final_verification" for item in result.evidence))

    def test_run_dynamic_selects_runtime_for_new_native_and_browser_goals(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test")
                self.calls: list[str] = []

            def run_runtime(self, *args, **kwargs):
                self.calls.append("runtime")
                return LoopResult(True, "runtime")

            def run(self, *args, **kwargs):
                self.calls.append("legacy")
                return LoopResult(True, "legacy")

        loop = TestLoop()
        self.assertEqual(loop.run_dynamic("click save", app_hint="calculator").message, "runtime")
        self.assertEqual(loop.run_dynamic("open browser and visit https://example.test").message, "runtime")
        self.assertEqual(loop.calls, ["runtime", "runtime"])

    def test_run_dynamic_resumes_runtime_checkpoint_through_runtime(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test")
                self.calls: list[tuple[str, bool]] = []

            def run_runtime(self, *args, **kwargs):
                self.calls.append(("runtime", bool(kwargs.get("resume"))))
                return LoopResult(True, "runtime")

            def run(self, *args, **kwargs):
                self.calls.append(("legacy", bool(kwargs.get("resume"))))
                return LoopResult(True, "legacy")

        from tempfile import TemporaryDirectory
        from pathlib import Path
        from core.task_runtime import RuntimeState
        from core.atomic_write import atomic_write_json

        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "runtime.json"
            atomic_write_json(checkpoint, RuntimeState(goal="resume goal").payload())
            loop = TestLoop()
            result = loop.run_dynamic("resume goal", checkpoint_path=checkpoint, resume=True)

        self.assertEqual(result.message, "runtime")
        self.assertEqual(loop.calls, [("runtime", True)])

    def test_runtime_bridge_accepts_planner_declared_subgoals(self) -> None:
        class FakeExecutor:
            def __init__(self, **_kwargs):
                pass

            def execute_step(self, _step, **_kwargs):
                return ActionOutcome(
                    state="succeeded", dispatch_status="confirmed", side_effect="read"
                )

            def close(self):
                pass

        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=2)
                self.observations = iter(["before", "after"])
                self.decisions = iter([
                    {
                        "action": "wait",
                        "seconds": 0,
                        "subgoal": "prepare",
                        "subgoals": [{"id": "prepare", "description": "Prepare page"}],
                    },
                    {"action": "done", "success": True, "message": "complete"},
                ])

            def _observe(self, *_args, **_kwargs):
                return next(self.observations)

            def _decide(self, *_args, **_kwargs):
                return next(self.decisions)

            def _verify_done(self, claimed_success, *_args, **_kwargs):
                return claimed_success, "complete"

        with patch("core.structured_automation.StructuredExecutor", FakeExecutor):
            result = TestLoop().build_runtime("prepare page").run("prepare page")

        assert result.success
        assert result.state.subgoals["prepare"].status == "verified"

    def test_runtime_bridge_observation_exposes_live_interface_facts(self) -> None:
        class FakeExecutor:
            def __init__(self, **_kwargs):
                pass

            def attach_page(self, _page):
                pass

            def close(self):
                pass


        class Page:
            url = "https://example.test"

            def locator(self, _selector):
                class Locator:
                    def evaluate_all(self, _script):
                        return []

                    def inner_text(self, timeout=0):
                        return "Example"
                return Locator()

            def title(self):
                return "Example"

        class TestLoop(ActionLoop):
            def _observe(self, *_args, **_kwargs):
                return "Browser ready"

            def _decide(self, *_args, **_kwargs):
                return {"action": "done", "success": True, "message": "complete"}

            def _verify_done(self, *_args, **_kwargs):
                return True, "complete"

        with patch("core.structured_automation.StructuredExecutor", FakeExecutor):
            result = TestLoop(api_key="test").build_runtime(
                "inspect browser", browser_page=Page()
            ).run("inspect browser")

        assert "browser_dom" in result.state.available_interfaces

    def test_runtime_bridge_pauses_for_a_human_security_gate(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test")
                self.calls = 0

            def _observe(self, *_args, **_kwargs):
                return "Please verify that you are human using reCAPTCHA"

            def _decide(self, *_args, **_kwargs):
                self.calls += 1
                raise AssertionError("planner must not decide through a human gate")

        loop = TestLoop()
        result = loop.build_runtime("continue login").run("continue login")

        assert result.state.status == "paused_for_human"
        assert "CAPTCHA" in result.state.message
        assert loop.calls == 0

    def test_browser_recovery_reattaches_before_the_next_decision(self) -> None:
        attached = []

        class BrokenPage:
            @property
            def url(self):
                raise RuntimeError("browser connection closed")

            def title(self):
                raise RuntimeError("browser connection closed")

        class GoodPage:
            url = "https://example.test/recovered"

            def title(self):
                return "Recovered page"

        class FakeExecutor:
            def __init__(self, **_kwargs):
                pass

            def attach_page(self, page):
                attached.append(page)

            def close(self):
                pass

        class BrowserOnlyObserver:
            def __init__(self, *, page=None, **_kwargs):
                self.page = page

            def observe(self):
                return {"interfaces": ["browser_dom"], "browser": {"url": self.page.url}}

        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=1)
                self.pages = iter([BrokenPage(), GoodPage()])

            def _get_browser_page(self, *_args):
                return next(self.pages)

            def _close_playwright(self):
                pass

            def _observe(self, app_hint="", browser_page=None):
                return "Browser page is readable"

            def _decide(self, *_args, **_kwargs):
                return {"action": "done", "success": False, "message": "stop after recovery"}

        loop = TestLoop()
        with (
            patch("core.structured_automation.StructuredExecutor", FakeExecutor),
            patch("core.runtime_observer.RuntimeObserver", BrowserOnlyObserver),
        ):
            result = loop.run_runtime("use browser to check a page")

        self.assertFalse(result.success)
        self.assertEqual(len(attached), 2)
        recovery = next(
            item["observation"]["browser_recovery"]
            for item in result.evidence
            if item.get("observation", {}).get("browser_recovery")
        )
        self.assertTrue(recovery["recovered"])
        self.assertEqual(recovery["url"], "https://example.test/recovered")

    def test_checkpoint_is_saved_before_an_adaptive_decision(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=1)
                self.checkpoint_seen = False

            def _observe(self, app_hint="", browser_page=None):
                return "stable observation"

            def _decide(self, _goal, _observation, _history, browser_mode=False):
                payload = json.loads(self.checkpoint_path.read_text())
                self.checkpoint_seen = any(
                    item.get("action") == "decision_request"
                    for item in payload["evidence"]
                )
                return {"action": "done", "success": False, "message": "stop"}

        with TemporaryDirectory() as directory:
            path = Path(directory) / "loop.json"
            loop = TestLoop()
            loop.checkpoint_path = path
            result = loop.run("check before deciding", checkpoint_path=path)

        self.assertFalse(result.success)
        self.assertTrue(loop.checkpoint_seen)
        self.assertTrue(any(item.get("action") == "decision_response" for item in result.evidence))

    def test_browser_done_claim_requires_fresh_dom_text_evidence(self) -> None:
        class TestLoop(ActionLoop):
            def _observe(self, app_hint="", browser_page=None):
                return "Browser page: 'Test'\nVisible interactive elements:\n- [button] Still waiting"

        success, message = TestLoop(api_key="test")._verify_done(
            True,
            "",
            "The button now reads 'Order complete'.",
            browser_page=object(),
        )
        self.assertFalse(success)
        self.assertIn("does not contain", message)

    def test_security_challenge_stops_before_another_model_decision(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=2)
                self.decisions = 0

            def _observe(self, app_hint="", browser_page=None):
                return "Please verify that you are human using reCAPTCHA"

            def _decide(self, *_args, **_kwargs):
                self.decisions += 1
                return {"action": "click", "description": "checkbox"}

        loop = TestLoop()
        result = loop.run("continue through security check", approve_all=True)

        self.assertFalse(result.success)
        self.assertIn("Human intervention required", result.message)
        self.assertEqual(loop.decisions, 0)

    def test_done_verification_rejects_absent_target_application(self) -> None:
        class TestLoop(ActionLoop):
            def _observe(self, app_hint="", browser_page=None):
                return f"App {app_hint!r} does not appear to be running yet. Use launch_app to open it."

        success, message = TestLoop(api_key="test")._verify_done(
            True,
            "calculator",
            "calculation complete",
        )
        self.assertFalse(success)
        self.assertIn("no longer shows", message)

    def test_fallback_goal_authorization_does_not_approve_generated_click(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=1)

            def _observe(self, app_hint="", browser_page=None):
                return "calculator visible"

            def _decide(self, *_args, **_kwargs):
                return {"action": "click", "description": "7"}

        loop = TestLoop()
        result = loop.run("approved adaptive goal", app_hint="calculator")
        self.assertFalse(result.success)
        self.assertIn("exact one-step approval", result.message)

    def test_run_reuses_one_executor_observes_after_action_and_retains_evidence(self) -> None:
        created = []

        class FakeExecutor:
            def __init__(self, **kwargs):
                self.calls = 0
                self.closed = False
                self.repair_callback = kwargs.get("repair_callback")
                created.append(self)

            def attach_page(self, _page):
                raise AssertionError("native test must not attach a browser page")

            def execute_step(self, step, **_kwargs):
                self.calls += 1
                item = {"action": step.action, "verified": True}
                return ActionOutcome(
                    state="succeeded",
                    data=item,
                    evidence=[item],
                    dispatch_status="confirmed",
                    side_effect="read",
                )

            def close(self):
                self.closed = True

        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=2)
                self.observations = 0
                self.decisions = iter([
                    {"action": "wait", "seconds": 0},
                    {"action": "done", "success": True, "message": "complete"},
                ])

            def _observe(self, app_hint="", browser_page=None):
                self.observations += 1
                return f"observation-{self.observations}"

            def _decide(self, *_args, **_kwargs):
                return next(self.decisions)

            def _verify_done(self, claimed_success, *_args, **_kwargs):
                return claimed_success, "complete"

        loop = TestLoop()
        with patch("core.structured_automation.StructuredExecutor", FakeExecutor):
            result = loop.run("wait and finish")
        self.assertTrue(result.success)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].calls, 1)
        self.assertTrue(callable(created[0].repair_callback))
        self.assertTrue(created[0].closed)
        self.assertEqual(loop.observations, 2)
        self.assertTrue(any(item.get("verified") for item in result.evidence))
        self.assertTrue(any(item.get("action") == "post_action_observation" for item in result.evidence))

    def test_run_stops_immediately_on_uncertain_outcome(self) -> None:
        class FakeExecutor:
            def __init__(self, **_kwargs):
                pass

            def execute_step(self, *_args, **_kwargs):
                return ActionOutcome(
                    state="uncertain",
                    error="unknown click result",
                    uncertainty=["unknown click result"],
                    dispatch_status="unknown",
                )

            def close(self):
                pass

        class TestLoop(ActionLoop):
            def __init__(self):
                super().__init__(api_key="test", max_steps=3)
                self.decision_calls = 0

            def _observe(self, app_hint="", browser_page=None):
                return "calculator visible"

            def _decide(self, *_args, **_kwargs):
                self.decision_calls += 1
                return {"action": "click", "description": "7"}

        loop = TestLoop()
        with patch("core.structured_automation.StructuredExecutor", FakeExecutor):
            result = loop.run("click seven", app_hint="calculator", approve_all=True)
        self.assertFalse(result.success)
        self.assertIn("uncertain outcome", result.message)
        self.assertEqual(loop.decision_calls, 1)

    def test_incomplete_loop_checkpoint_resumes_the_same_verified_history(self) -> None:
        class TestLoop(ActionLoop):
            def __init__(self, decisions, max_steps):
                super().__init__(api_key="test", max_steps=max_steps)
                self.decisions = iter(decisions)

            def _observe(self, app_hint="", browser_page=None):
                return "plain observation"

            def _decide(self, *_args, **_kwargs):
                return next(self.decisions)

            def _verify_done(self, claimed_success, *_args, **_kwargs):
                return claimed_success, "complete"

        with TemporaryDirectory() as directory:
            path = Path(directory) / "loop.json"
            first = TestLoop([{"action": "wait", "seconds": 0}], max_steps=1)
            incomplete = first.run("wait and finish", checkpoint_path=path)
            self.assertFalse(incomplete.success)
            self.assertTrue(path.is_file())

            resumed = TestLoop(
                [{"action": "done", "success": True, "message": "complete"}],
                max_steps=2,
            ).run("wait and finish", checkpoint_path=path, resume=True)

        self.assertTrue(resumed.success)
        self.assertEqual(len(resumed.steps), 2)


if __name__ == "__main__":
    unittest.main()

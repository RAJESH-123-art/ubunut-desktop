from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.action_loop import ActionLoop
from core.action_policy import action_token, approved, requires_approval
from core.atspi_navigator import find_best_match, read_by_intent
from core.llm_planner import LLMPlanner
from core.parallel_runner import ParallelRunner
from core.replanner import Replanner
from core.result_bus import TaskResult, result_bus
from core.smart_parser import smart_parser
from core.structured_automation import (
    ActionNotDispatched,
    StructuredExecutor,
    StructuredPlanner,
    UncertainStepOutcome,
    _completion_text,
    _evidence_for_external_repair,
    _json_payload_from_completion,
    plan_fingerprint,
    validate_plan,
)
from core.structured_sessions import abandon_session, list_sessions, load_session
from core.task_contract import validate_task_params
from core.task_dag import TaskDAG, TaskNode
from core.verifier import Verifier, VerifySpec
from core.visual_locator import VisualLocator, VisualTarget, validate_visual_target
from core.workflow_engine import WorkflowEngine
from tasks import get_task_spec, list_tasks, task_catalog
from tasks.whatsapp_send import _verify_outgoing_message


class TaskDAGTests(unittest.TestCase):
    def test_missing_dependency_is_rejected(self) -> None:
        dag = TaskDAG()
        dag.add(TaskNode("child", "noop", deps=["missing"]))
        with self.assertRaisesRegex(ValueError, "missing dependencies"):
            dag.validate()

    def test_cycle_is_rejected(self) -> None:
        dag = TaskDAG()
        dag.add(TaskNode("a", "noop", deps=["b"]))
        dag.add(TaskNode("b", "noop", deps=["a"]))
        with self.assertRaisesRegex(ValueError, "cycle"):
            dag.validate()

    def test_failed_dependency_blocks_transitive_dependents(self) -> None:
        dag = TaskDAG()
        dag.add(TaskNode("root", "noop", status="failed"))
        dag.add(TaskNode("child", "noop", deps=["root"]))
        dag.add(TaskNode("grandchild", "noop", deps=["child"]))
        self.assertEqual(dag.propagate_blocked(), 2)
        self.assertEqual(dag.get("child").status, "blocked")
        self.assertEqual(dag.get("grandchild").status, "blocked")
        self.assertTrue(dag.is_complete())


class ParallelRunnerTests(unittest.TestCase):
    def tearDown(self) -> None:
        result_bus.clear()

    def test_nested_dependency_placeholders_preserve_exact_value_type(self) -> None:
        runner = ParallelRunner()
        result_bus.publish("source", TaskResult(ok=True, data={"urls": ["https://example.test"], "count": 2}))
        resolved = runner._resolve_dependencies(
            {"url": "${source.urls[0]}", "message": "count=${source.count}", "nested": ["${source.count}"]},
            ["source"],
        )
        self.assertEqual(resolved["url"], "https://example.test")
        self.assertEqual(resolved["message"], "count=2")
        self.assertEqual(resolved["nested"], [2])

    def test_sequential_recovery_satisfies_original_dependency(self) -> None:
        calls: list[str] = []
        dag = TaskDAG()
        dag.add(TaskNode("original", "primary", max_retries=0))
        dag.add(TaskNode("dependent", "dependent", deps=["original"], max_retries=0))

        def builder(intent: str, args: dict):
            def execute(final_args: dict, resources: dict) -> bool:
                calls.append(intent)
                if intent == "primary":
                    return False
                if intent == "alt_fail":
                    return False
                return True
            return execute, dict(args), {}

        custom = Replanner({
            "primary": [
                {"intent": "alt_fail", "args": {}},
                {"intent": "alt_ok", "args": {}},
            ]
        })
        import core.parallel_runner as runner_module
        previous = runner_module.replanner
        runner_module.replanner = custom
        try:
            summary = ParallelRunner(max_workers=1, timeout=5, executor_builder=builder).run(dag)
        finally:
            runner_module.replanner = previous

        self.assertEqual(dag.get("original").status, "done")
        self.assertEqual(dag.get("dependent").status, "done")
        self.assertEqual(calls, ["primary", "alt_fail", "alt_ok", "dependent"])
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["blocked"], 0)


class _FakeOptionLocator:
    def __init__(self, select):
        self._select = select

    def all_text_contents(self):
        return list(self._select.options)

    def inner_text(self):
        return self._select.selected


class _FakeSelect:
    def __init__(self):
        self.options = [
            "Select Download",
            "Windows 11 (multi-edition ISO for x64 devices)",
        ]
        self.selected = "Select Download"

    def get_attribute(self, name):
        return {"aria-label": "Select Download", "name": "product-edition"}.get(name)

    def locator(self, selector):
        return _FakeOptionLocator(self)

    def select_option(self, *, label, timeout):
        if label not in self.options:
            raise ValueError(label)
        self.selected = label


class _FakeSelectCollection:
    def __init__(self, select):
        self._select = select

    def count(self):
        return 1

    def nth(self, index):
        if index != 0:
            raise IndexError(index)
        return self._select


class _FakePage:
    def __init__(self):
        self.select = _FakeSelect()

    def locator(self, selector):
        if selector != "select:visible":
            raise ValueError(selector)
        return _FakeSelectCollection(self.select)


class _FakeCompletionMessage:
    content = '{"summary":"open site","steps":[{"action":"navigate","args":{"url":"https://example.test"}}]}'


class _FakeCompletionChoice:
    message = _FakeCompletionMessage()


class _FakeCompletionResponse:
    choices = [_FakeCompletionChoice()]


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return _FakeCompletionResponse()


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()


class PlannerResponseBoundaryTests(unittest.TestCase):
    def test_reasoning_field_is_not_treated_as_final_json(self) -> None:
        message = type("Message", (), {
            "content": None,
            "reasoning_content": '{"summary":"wait","steps":[{"action":"wait","args":{"seconds":1}}]}',
        })()
        response = type("Response", (), {
            "choices": [type("Choice", (), {"message": message})()],
        })()

        self.assertEqual(_completion_text(response), "")

    def test_provider_preamble_can_contain_one_valid_json_object(self) -> None:
        payload = _json_payload_from_completion(
            "Here is the plan: {\"summary\":\"wait\",\"steps\":[{\"action\":\"wait\",\"args\":{\"seconds\":1}}]}",
            label="Planner",
        )

        self.assertEqual(payload["summary"], "wait")

    def test_empty_provider_response_has_a_clear_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty response"):
            _json_payload_from_completion("", label="Planner")

    def test_tokenrouter_request_requires_json_and_disables_thinking(self) -> None:
        planner = StructuredPlanner(
            api_key="test",
            base_url="https://api.tokenrouter.com/v1",
        )

        self.assertEqual(
            planner._request_options(),
            {
                "response_format": {"type": "json_object"},
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            },
        )

    def test_empty_full_prompt_retries_once_with_compact_prompt(self) -> None:
        plan_json = json.dumps({
            "summary": "wait",
            "steps": [{"action": "wait", "args": {"seconds": 1}}],
        })

        class Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                content = None if self.calls == 1 else plan_json
                message = type("Message", (), {"content": content})()
                return type("Response", (), {
                    "choices": [type("Choice", (), {"message": message})()],
                })()

        completions = Completions()
        planner = StructuredPlanner(api_key="test")
        planner._client = type("Client", (), {
            "chat": type("Chat", (), {"completions": completions})(),
        })()

        plan = planner.plan("wait")

        self.assertEqual(completions.calls, 2)
        self.assertEqual(plan.steps[0].action, "wait")

    def test_reasoning_only_full_prompt_retries_once_with_compact_prompt(self) -> None:
        plan_json = json.dumps({
            "summary": "wait",
            "steps": [{"action": "wait", "args": {"seconds": 1}}],
        })

        class Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    message = type("Message", (), {
                        "content": None,
                        "reasoning_content": "I will make a plan.",
                    })()
                else:
                    message = type("Message", (), {"content": plan_json})()
                return type("Response", (), {
                    "choices": [type("Choice", (), {"message": message})()],
                })()

        completions = Completions()
        planner = StructuredPlanner(api_key="test")
        planner._client = type("Client", (), {
            "chat": type("Chat", (), {"completions": completions})(),
        })()

        plan = planner.plan("wait")

        self.assertEqual(completions.calls, 2)
        self.assertEqual(plan.steps[0].action, "wait")


class TaskContractTests(unittest.TestCase):
    def test_catalog_covers_every_planner_safe_task(self) -> None:
        catalog_names = {item["name"] for item in task_catalog()}
        self.assertEqual(
            catalog_names,
            set(list_tasks()) - {"run_command", "universal_fallback", "browser_action"},
        )

    def test_required_and_unknown_task_params_are_rejected(self) -> None:
        spec = get_task_spec("open_system_app")
        self.assertIsNotNone(spec)
        with self.assertRaisesRegex(ValueError, "missing required"):
            validate_task_params(spec, {})
        with self.assertRaisesRegex(ValueError, "unknown params"):
            validate_task_params(spec, {"app_name": "calculator", "invented": True})


class AtspiNavigatorTests(unittest.TestCase):
    def test_clear_intent_matches_actionable_c_control(self) -> None:
        class Action:
            nActions = 1

        class Node:
            def __init__(self, name, role, children=()):
                self.name = name
                self._role = role
                self._children = list(children)
                self.childCount = len(self._children)

            def getRoleName(self):
                return self._role

            def getChildAtIndex(self, index):
                return self._children[index]

            def queryAction(self):
                return Action()

        clear = Node("c", "push button")
        root = Node("calculator", "application", [clear])
        matched, considered = find_best_match(
            root,
            "clear",
            role_filter={"push button"},
            require_actionable=True,
        )
        self.assertIs(matched, clear)
        self.assertEqual(considered, 1)

    def test_read_intent_matches_unnamed_dynamic_text_content(self) -> None:
        class TextInterface:
            characterCount = 2

            def getText(self, start, end):
                return "12"

        class Node:
            name = ""
            childCount = 0

            def getRoleName(self):
                return "text"

            def queryText(self):
                return TextInterface()

        class Root:
            name = "calculator"
            childCount = 1

            def getRoleName(self):
                return "application"

            def getChildAtIndex(self, index):
                return Node()

        root = Root()
        with patch("core.atspi_navigator.wait_for_app", return_value=root):
            result = read_by_intent(["calculator"], "12", app_timeout=0.1)
        self.assertTrue(result.ok)
        self.assertEqual(result.value, "12")


class StructuredAutomationTests(unittest.TestCase):
    def test_unknown_action_is_rejected_before_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported action"):
            validate_plan({"steps": [{"action": "run_shell", "args": {}}]})

    def test_forbidden_recursive_task_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot invoke task"):
            validate_plan({
                "steps": [{
                    "action": "task",
                    "args": {"name": "run_command", "params": {}},
                }]
            })

    def test_invalid_task_params_are_rejected_during_plan_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required"):
            validate_plan({
                "steps": [{
                    "action": "task",
                    "args": {"name": "open_system_app", "params": {}},
                }]
            })

    def test_planner_uses_one_completion_for_whole_plan(self) -> None:
        planner = StructuredPlanner(api_key="test")
        client = _FakeClient()
        planner._client = client
        plan = planner.plan("open example")
        self.assertEqual(client.chat.completions.calls, 1)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].action, "navigate")

    def test_download_identity_constraints_are_validated(self) -> None:
        with self.assertRaisesRegex(TypeError, "extensions"):
            validate_plan({
                "steps": [{
                    "action": "download",
                    "args": {"text": "Download", "extensions": "iso"},
                }]
            })
        with self.assertRaisesRegex(ValueError, "source domain"):
            validate_plan({
                "steps": [{
                    "action": "download",
                    "args": {
                        "text": "Download",
                        "extensions": [".iso"],
                        "source_domain": "https://example.test/path",
                    },
                }]
            })

    def test_browser_host_confinement_rejects_unplanned_domain(self) -> None:
        executor = StructuredExecutor(approve_all=True)
        executor._allowed_hosts = {"example.test"}
        executor._assert_allowed_page_host("https://files.example.test/download")
        with self.assertRaisesRegex(PermissionError, "plan-declared hosts"):
            executor._assert_allowed_page_host("https://unexpected.test/")

    def test_host_confinement_rejects_parent_domain_and_wrong_loopback_port(self) -> None:
        executor = StructuredExecutor(approve_all=True)
        executor._allowed_hosts = {"files.example.test"}
        with self.assertRaisesRegex(PermissionError, "plan-declared hosts"):
            executor._assert_allowed_page_host("https://example.test/")

        executor._allowed_hosts = {"127.0.0.1"}
        executor._allowed_origins = {"http://127.0.0.1:8080"}
        executor._assert_allowed_page_host("http://127.0.0.1:8080/path")
        with self.assertRaisesRegex(PermissionError, "loopback origins"):
            executor._assert_allowed_page_host("http://127.0.0.1:9090/path")

    def test_find_control_rejects_ambiguity_and_near_disambiguates(self) -> None:
        class Candidate:
            def __init__(self, surrounding):
                self.surrounding = surrounding

            def is_visible(self):
                return True

            def is_enabled(self):
                return True

            def evaluate(self, script):
                return self.surrounding

        class Collection:
            def __init__(self, candidates):
                self.candidates = candidates

            def count(self):
                return len(self.candidates)

            def nth(self, index):
                return self.candidates[index]

        class Page:
            def __init__(self, candidates):
                self.candidates = Collection(candidates)

            def get_by_role(self, role, **kwargs):
                return self.candidates if role == "button" else Collection([])

            def get_by_text(self, *args, **kwargs):
                return Collection([])

        first = Candidate("Billing section Submit")
        second = Candidate("Shipping section Submit")
        executor = StructuredExecutor(approve_all=True)
        executor._page = Page([first, second])
        with self.assertRaisesRegex(RuntimeError, "Ambiguous control"):
            executor._find_control("Submit")
        self.assertIs(executor._find_control("Submit", "Shipping section"), second)

    def test_select_honors_dropdown_label(self) -> None:
        class Options:
            def __init__(self, select, checked=False):
                self.select = select
                self.checked = checked

            def all_text_contents(self):
                return list(self.select.options)

            def inner_text(self):
                return self.select.selected

        class Select:
            def __init__(self, label):
                self.label = label
                self.options = ["Choose", "Shared option"]
                self.selected = "Choose"

            def evaluate(self, script):
                return self.label

            def locator(self, selector):
                return Options(self, selector == "option:checked")

            def select_option(self, *, label, timeout):
                self.selected = label

        class Selects:
            def __init__(self, values):
                self.values = values

            def count(self):
                return len(self.values)

            def nth(self, index):
                return self.values[index]

        class Page:
            def __init__(self, values):
                self.values = values

            def locator(self, selector):
                return Selects(self.values)

        billing = Select("Billing country")
        shipping = Select("Shipping country")
        executor = StructuredExecutor(approve_all=True)
        executor._page = Page([billing, shipping])
        selected = executor._select("Shipping country", "Shared option")
        self.assertEqual(selected, "Shared option")
        self.assertEqual(billing.selected, "Choose")
        self.assertEqual(shipping.selected, "Shared option")

    def test_repair_planner_uses_one_completion_and_returns_one_step(self) -> None:
        class RepairCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                message = type("Message", (), {
                    "content": json.dumps({
                        "replacement": {
                            "action": "navigate",
                            "args": {"url": "https://replacement.test"},
                        }
                    })
                })()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()

        completions = RepairCompletions()
        client = type("Client", (), {
            "chat": type("Chat", (), {"completions": completions})()
        })()
        planner = StructuredPlanner(api_key="test")
        planner._client = client
        original = validate_plan({
            "steps": [{
                "action": "navigate",
                "args": {"url": "https://broken.test"},
            }]
        })
        replacement = planner.repair_step(
            "open replacement",
            original,
            1,
            original.steps[0],
            "unreachable",
            [],
        )
        self.assertEqual(completions.calls, 1)
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.action, "navigate")
        self.assertEqual(replacement.args["url"], "https://replacement.test")

    def test_condition_rejects_forward_step_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "only earlier steps"):
            validate_plan({
                "steps": [{
                    "action": "navigate",
                    "args": {"url": "https://example.test"},
                    "when": {
                        "value": "${step.1.text}",
                        "operator": "truthy",
                    },
                }]
            })

    def test_false_condition_skips_consequential_action_without_dispatch(self) -> None:
        class ConditionalExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = []

            def _execute_browser_step(self, step):
                self.calls.append(step.action)
                return {"text": "not-ready"}

        plan = validate_plan({
            "steps": [
                {"action": "extract_text", "args": {"target": "Status"}},
                {
                    "action": "click",
                    "args": {"text": "Submit"},
                    "when": {
                        "value": "${step.1.text}",
                        "operator": "equals",
                        "expected": "ready",
                    },
                },
            ]
        })
        executor = ConditionalExecutor()
        result = executor.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(executor.calls, ["extract_text"])
        self.assertTrue(result.evidence[1]["skipped"])
        self.assertEqual(result.completed_steps, 2)

    def test_transform_and_assert_run_locally_without_ui_dispatch(self) -> None:
        plan = validate_plan({
            "steps": [
                {
                    "action": "transform_text",
                    "args": {"value": "  Invoice-2048  ", "operation": "strip"},
                },
                {
                    "action": "transform_text",
                    "args": {
                        "value": "${step.1.value}",
                        "operation": "replace",
                        "old": "Invoice-",
                        "new": "INV-",
                    },
                },
                {
                    "action": "assert_value",
                    "args": {
                        "value": "${step.2.value}",
                        "operator": "equals",
                        "expected": "INV-2048",
                    },
                },
            ]
        })
        result = StructuredExecutor(approve_all=True).execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(result.evidence[1]["value"], "INV-2048")
        self.assertTrue(result.evidence[2]["asserted"])

    def test_condition_is_bound_into_plan_fingerprint(self) -> None:
        base = {
            "action": "click",
            "args": {"text": "Submit"},
        }
        first = validate_plan({
            "steps": [{
                **base,
                "when": {"value": "ready", "operator": "equals", "expected": "ready"},
            }]
        })
        second = validate_plan({
            "steps": [{
                **base,
                "when": {"value": "blocked", "operator": "equals", "expected": "ready"},
            }]
        })
        self.assertNotEqual(plan_fingerprint(first), plan_fingerprint(second))

    def test_generic_read_evidence_flows_between_native_and_browser_apps(self) -> None:
        class CrossAppExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.filled = ""

            def _execute_native_step(self, step):
                return {"text": "INV-2048", "target": step.args["target"]}

            def _execute_browser_step(self, step):
                self.filled = str(step.args["text"])
                return {"filled": step.args["label"]}

        plan = validate_plan({
            "steps": [
                {
                    "action": "read_ui",
                    "args": {"app": "invoice app", "target": "invoice number"},
                },
                {
                    "action": "fill",
                    "args": {
                        "label": "Reference",
                        "text": "${step.1.text}",
                    },
                },
            ]
        })
        executor = CrossAppExecutor()
        result = executor.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(executor.filled, "INV-2048")

    def test_generic_browser_actions_validate_unsafe_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "upload path must be absolute"):
            validate_plan({
                "steps": [{
                    "action": "upload_file",
                    "args": {"label": "Attachment", "path": "relative.txt"},
                }]
            })
        with self.assertRaisesRegex(ValueError, "tab title or URL"):
            validate_plan({"steps": [{"action": "switch_tab", "args": {}}]})

    def test_dialog_and_popup_schema_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid dialog type"):
            validate_plan({"steps": [{
                "action": "click",
                "args": {"text": "Open", "dialog": {"type": "modal"}},
            }]})
        with self.assertRaisesRegex(ValueError, "declared web URL"):
            validate_plan({"steps": [{
                "action": "click",
                "args": {"text": "Open", "popup": {"url": "/relative"}},
            }]})
        with self.assertRaisesRegex(ValueError, "both dialog and popup"):
            validate_plan({"steps": [{
                "action": "click",
                "args": {
                    "text": "Open",
                    "dialog": {"type": "alert"},
                    "popup": {"url": "https://example.test"},
                },
            }]})

    def test_dialog_success_and_mismatch_are_verified(self) -> None:
        class FakeDialog:
            type = "confirm"
            message = "Proceed with local test?"

            def __init__(self):
                self.accepted = False
                self.dismissed = False

            def accept(self, prompt_text=""):
                self.accepted = True

            def dismiss(self):
                self.dismissed = True

        class FakePage:
            url = "https://example.test"

            def __init__(self, dialog):
                self.dialog = dialog
                self.handler = None

            def title(self):
                return "Source"

            def once(self, event, handler):
                self.handler = handler

            def wait_for_timeout(self, milliseconds):
                return None

        class FakeControl:
            def __init__(self, page):
                self.page = page

            def click(self, **kwargs):
                self.page.handler(self.page.dialog)

        class DialogExecutor(StructuredExecutor):
            def _find_control(self, text, near=""):
                return FakeControl(self._page)

            @staticmethod
            def _page_fingerprint(page):
                return page.url, "", ""

            def _verify_expectation(self, expect, *, before_url=""):
                return None

            def _assert_allowed_page_host(self, url):
                return None

        accepted_dialog = FakeDialog()
        executor = DialogExecutor(approve_all=True)
        executor._page = FakePage(accepted_dialog)
        matching = validate_plan({"steps": [{
            "action": "click",
            "args": {
                "text": "Confirm",
                "dialog": {
                    "type": "confirm",
                    "accept": True,
                    "message_contains": "local test",
                },
            },
        }]}).steps[0]
        evidence = executor._execute_browser_step(matching)
        self.assertTrue(accepted_dialog.accepted)
        self.assertTrue(evidence["dialog"]["accepted"])

        mismatched_dialog = FakeDialog()
        executor._page = FakePage(mismatched_dialog)
        mismatched = validate_plan({"steps": [{
            "action": "click",
            "args": {
                "text": "Confirm",
                "dialog": {"type": "alert", "accept": True},
            },
        }]}).steps[0]
        with self.assertRaisesRegex(UncertainStepOutcome, "did not match"):
            executor._execute_browser_step(mismatched)
        self.assertTrue(mismatched_dialog.dismissed)

    def test_popup_ownership_and_url_mismatch(self) -> None:
        class FakePopup:
            def __init__(self, url):
                self.url = url
                self.marker = ""
                self.closed = False

            def wait_for_load_state(self, *args, **kwargs):
                return None

            def evaluate(self, script, value=None):
                if value is not None:
                    self.marker = value
                return self.marker

            def title(self):
                return "Expected Popup"

            def bring_to_front(self):
                return None

            def close(self):
                self.closed = True

        class PopupInfo:
            def __init__(self, popup):
                self.value = popup

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakePage:
            url = "https://example.test/source"

            def __init__(self, popup):
                self.popup = popup

            def title(self):
                return "Source"

            def expect_popup(self, **kwargs):
                return PopupInfo(self.popup)

        class FakeControl:
            def click(self, **kwargs):
                return None

        class PopupExecutor(StructuredExecutor):
            def _find_control(self, text, near=""):
                return FakeControl()

            @staticmethod
            def _page_fingerprint(page):
                return page.url, "", ""

            def _verify_expectation(self, expect, *, before_url=""):
                return None

            def _assert_allowed_page_host(self, url):
                return None

        step = validate_plan({"steps": [{
            "action": "click",
            "args": {
                "text": "Open popup",
                "popup": {
                    "url": "https://example.test/popup",
                    "title_contains": "Expected",
                },
            },
        }]}).steps[0]
        popup = FakePopup("https://example.test/popup")
        executor = PopupExecutor(
            approve_all=True,
            execution_id="popup-test",
        )
        executor._page = FakePage(popup)
        evidence = executor._execute_browser_step(step)
        self.assertIn(popup, executor._owned_pages)
        self.assertEqual(popup.marker, "desktop-agent:popup-test:1")
        self.assertEqual(evidence["popup_url"], popup.url)

        wrong_popup = FakePopup("https://unexpected.test/")
        mismatch_executor = PopupExecutor(approve_all=True)
        mismatch_executor._page = FakePage(wrong_popup)
        with self.assertRaisesRegex(UncertainStepOutcome, "did not match declared"):
            mismatch_executor._execute_browser_step(step)
        self.assertTrue(wrong_popup.closed)
        self.assertNotIn(wrong_popup, mismatch_executor._owned_pages)

    def test_consequential_browser_click_executes_exactly_once(self) -> None:
        class FailingClickExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                raise UncertainStepOutcome("click outcome unknown")

        plan = validate_plan({"steps": [{
            "action": "click",
            "args": {"text": "Submit"},
        }]})
        executor = FailingClickExecutor()
        result = executor.execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 1)

    def test_undispatched_click_uses_fresh_observation_for_bounded_repair(self) -> None:
        captured = {}

        def repair(goal, plan, index, step, error, evidence):
            captured["evidence"] = evidence
            return validate_plan({"steps": [{
                "action": "click",
                "args": {"text": "Current label"},
            }]}).steps[0]

        class RepairingExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True, repair_callback=repair)
                self.calls = []

            def _execute_browser_step(self, step):
                self.calls.append(step.args["text"])
                if step.args["text"] == "Old label":
                    raise ActionNotDispatched("target no longer exists")
                return {"clicked": step.args["text"]}

            def _repair_observation(self, step):
                return {
                    "kind": "browser_semantics",
                    "controls": [{"role": "button", "name": "Current label"}],
                }

        plan = validate_plan({"steps": [{
            "action": "click",
            "args": {"text": "Old label"},
        }]})
        executor = RepairingExecutor()
        result = executor.execute(plan, goal="click the current control")
        self.assertTrue(result.success)
        self.assertEqual(executor.calls, ["Old label", "Current label"])
        observation = captured["evidence"][-1]
        self.assertEqual(observation["action"], "live_observation")
        self.assertEqual(observation["controls"][0]["name"], "Current label")
        self.assertEqual(result.evidence[0]["repaired_from_action"], "click")

    def test_consequential_undispatched_step_repairs_twice_then_succeeds(self) -> None:
        repair_calls = []
        observations = []

        def repair(goal, plan, index, step, error, evidence):
            observation = evidence[-1]
            repair_calls.append(step.args["text"])
            observations.append(observation["observed_candidate"])
            replacements = {
                "Old label": "Intermediate label",
                "Intermediate label": "Current label",
            }
            return validate_plan({"steps": [{
                "action": "click",
                "args": {"text": replacements[step.args["text"]]},
            }]}).steps[0]

        class IterativeRepairExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True, repair_callback=repair)
                self.candidates = []
                self.external_actions = []

            def _execute_browser_step(self, step):
                label = step.args["text"]
                self.candidates.append(label)
                if label != "Current label":
                    raise ActionNotDispatched(f"{label} is stale")
                self.external_actions.append(label)
                return {"clicked": label}

            def _repair_observation(self, step):
                return {"observed_candidate": step.args["text"]}

        plan = validate_plan({"steps": [{
            "action": "click",
            "args": {"text": "Old label"},
        }]})
        executor = IterativeRepairExecutor()
        result = executor.execute(plan, goal="click the current control")

        self.assertTrue(result.success)
        self.assertEqual(repair_calls, ["Old label", "Intermediate label"])
        self.assertEqual(observations, ["Old label", "Intermediate label"])
        self.assertEqual(
            executor.candidates,
            ["Old label", "Intermediate label", "Current label"],
        )
        self.assertEqual(executor.external_actions, ["Current label"])
        self.assertEqual(result.evidence[0]["repaired_from_action"], "click")

    def test_repaired_consequential_uncertain_failure_executes_once(self) -> None:
        repair_calls = []

        def repair(goal, plan, index, step, error, evidence):
            repair_calls.append(step.args["text"])
            return validate_plan({"steps": [{
                "action": "click",
                "args": {"text": "Current label"},
            }]}).steps[0]

        class UncertainRepairExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True, repair_callback=repair)
                self.candidates = []
                self.external_actions = 0

            def _execute_browser_step(self, step):
                label = step.args["text"]
                self.candidates.append(label)
                if label == "Old label":
                    raise ActionNotDispatched("old target is gone")
                self.external_actions += 1
                raise UncertainStepOutcome("click happened but verification failed")

            def _repair_observation(self, step):
                return {"observed_candidate": step.args["text"]}

        plan = validate_plan({"steps": [{
            "action": "click",
            "args": {"text": "Old label"},
        }]})
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            executor = UncertainRepairExecutor()
            result = executor.execute(
                plan,
                checkpoint_path=checkpoint,
                goal="click once",
            )
            saved = json.loads(checkpoint.read_text())

        self.assertFalse(result.success)
        self.assertEqual(repair_calls, ["Old label"])
        self.assertEqual(executor.candidates, ["Old label", "Current label"])
        self.assertEqual(executor.external_actions, 1)
        self.assertTrue(saved["uncertain"])
        self.assertEqual(saved["in_progress"], 1)
        self.assertEqual(saved["phase"], "dispatched")

    def test_undispatched_repair_loop_is_bounded(self) -> None:
        repair_calls = []
        observations = []

        def repair(goal, plan, index, step, error, evidence):
            repair_calls.append(step.args["text"])
            observations.append(evidence[-1]["observed_candidate"])
            return validate_plan({"steps": [{
                "action": "click",
                "args": {"text": f"Replacement {len(repair_calls)}"},
            }]}).steps[0]

        class ExhaustedRepairExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True, repair_callback=repair)
                self.candidates = []
                self.external_actions = 0

            def _execute_browser_step(self, step):
                self.candidates.append(step.args["text"])
                raise ActionNotDispatched("target is not present")

            def _repair_observation(self, step):
                return {"observed_candidate": step.args["text"]}

        plan = validate_plan({"steps": [{
            "action": "click",
            "args": {"text": "Original"},
        }]})
        executor = ExhaustedRepairExecutor()
        result = executor.execute(plan, goal="find the current control")

        self.assertFalse(result.success)
        self.assertIn("exhausted maximum of 3 failed-step repairs", result.message)
        self.assertEqual(repair_calls, ["Original", "Replacement 1", "Replacement 2"])
        self.assertEqual(observations, repair_calls)
        self.assertEqual(
            executor.candidates,
            ["Original", "Replacement 1", "Replacement 2", "Replacement 3"],
        )
        self.assertEqual(executor.external_actions, 0)

    def test_close_tab_is_consequential(self) -> None:
        step = validate_plan({"steps": [{"action": "close_tab", "args": {}}]}).steps[0]
        self.assertTrue(StructuredExecutor._step_is_consequential(step))

    def test_sensitive_evidence_is_withheld_from_external_repair(self) -> None:
        filtered = _evidence_for_external_repair([
            {"step": 1, "action": "read_field", "text": "secret", "sensitive": True},
            {"step": 2, "action": "extract_text", "text": "public", "sensitive": False},
        ])
        self.assertNotIn("text", filtered[0])
        self.assertTrue(filtered[0]["value_withheld"])
        self.assertEqual(filtered[1]["text"], "public")

    def test_visual_action_rejects_blind_coordinates(self) -> None:
        with self.assertRaisesRegex(ValueError, "blind coordinates"):
            validate_plan({
                "steps": [{
                    "action": "visual_click",
                    "args": {
                        "app": "drawing app",
                        "target": "blue circle",
                        "x": 100,
                        "y": 200,
                    },
                }]
            })

    def test_visual_action_dispatches_as_one_structured_step(self) -> None:
        class VisualExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_visual_step(self, step):
                self.calls += 1
                return {
                    "method": "vision",
                    "confidence": 0.94,
                    "before_screenshot": "/tmp/before.png",
                    "after_screenshot": "/tmp/after.png",
                    "visual_change_ratio": 0.1,
                }

        plan = validate_plan({
            "steps": [{
                "action": "visual_click",
                "args": {
                    "app": "drawing app",
                    "target": "blue circle near the top left",
                    "minimum_confidence": 0.9,
                },
            }]
        })
        executor = VisualExecutor()
        result = executor.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(executor.calls, 1)
        self.assertEqual(result.evidence[0]["method"], "vision")

    def test_uncertain_visual_outcome_is_not_retried(self) -> None:
        class VisualExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_visual_step(self, step):
                self.calls += 1
                raise UncertainStepOutcome("click happened but change was not verified")

        plan = validate_plan({
            "steps": [{
                "action": "visual_click",
                "args": {"app": "drawing app", "target": "blue circle"},
            }]
        })
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            executor = VisualExecutor()
            result = executor.execute(
                plan,
                checkpoint_path=checkpoint,
                goal="click circle",
            )
            saved = json.loads(checkpoint.read_text())
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 1)
        self.assertTrue(saved["uncertain"])
        self.assertEqual(saved["in_progress"], 1)

    def test_visual_action_requires_plan_approval(self) -> None:
        class VisualExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=False)
                self.calls = 0

            def _execute_visual_step(self, step):
                self.calls += 1
                return {"ok": True}

        plan = validate_plan({
            "steps": [{
                "action": "visual_click",
                "args": {"app": "drawing app", "target": "blue circle"},
            }]
        })
        executor = VisualExecutor()
        result = executor.execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 0)

    def test_native_change_poll_waits_for_delayed_accessibility_update(self) -> None:
        class PollExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.snapshots = iter([("old",), ("new",)])

            def _native_snapshot(self, app_name):
                return next(self.snapshots)

        executor = PollExecutor()
        self.assertEqual(
            executor._await_native_change("app", ("old",)),
            ("new",),
        )

    def test_native_no_change_becomes_uncertain_without_replay(self) -> None:
        class PollExecutor(StructuredExecutor):
            def _native_snapshot(self, app_name):
                return ("same",)

        executor = PollExecutor(approve_all=True, step_timeout=1)
        with patch(
            "core.structured_automation.time.monotonic",
            side_effect=[0.0, 2.0],
        ):
            with self.assertRaises(UncertainStepOutcome):
                executor._await_native_change("app", ("same",))

    def test_native_actions_require_named_target_app(self) -> None:
        with self.assertRaisesRegex(ValueError, "target app"):
            validate_plan({
                "steps": [{"action": "click_ui", "args": {"text": "Save"}}]
            })

    def test_native_steps_dispatch_without_additional_planning(self) -> None:
        class NativeExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.actions = []

            def _execute_native_step(self, step):
                self.actions.append(step.action)
                return {"native": step.action}

        plan = validate_plan({
            "steps": [
                {"action": "launch_app", "args": {"app": "calculator"}},
                {
                    "action": "click_ui",
                    "args": {"app": "calculator", "text": "7"},
                    "expect": {"ui_text": "7"},
                },
            ]
        })
        executor = NativeExecutor()
        result = executor.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(executor.actions, ["launch_app", "click_ui"])

    def test_native_interaction_is_in_plan_approval_preflight(self) -> None:
        class NativeExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=False)
                self.calls = 0

            def _execute_native_step(self, step):
                self.calls += 1
                return {"ok": True}

        plan = validate_plan({
            "steps": [{
                "action": "type_ui",
                "args": {"app": "editor", "field": "Document", "text": "hello"},
            }]
        })
        executor = NativeExecutor()
        result = executor.execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 0)

    def test_plan_fingerprint_binds_exact_parameters(self) -> None:
        first = validate_plan({
            "summary": "click",
            "steps": [{"action": "click", "args": {"text": "Confirm"}}],
        })
        second = validate_plan({
            "summary": "click",
            "steps": [{"action": "click", "args": {"text": "Delete"}}],
        })
        self.assertNotEqual(plan_fingerprint(first), plan_fingerprint(second))

    def test_plan_approval_is_checked_before_first_action(self) -> None:
        class RefusingExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=False)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                return {"ok": True}

        plan = validate_plan({
            "steps": [
                {"action": "navigate", "args": {"url": "https://example.test"}},
                {"action": "click", "args": {"text": "Submit"}},
            ]
        })
        executor = RefusingExecutor()
        result = executor.execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 0)

    def test_verified_step_output_flows_to_later_step(self) -> None:
        class DataExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.received = ""

            def _execute_browser_step(self, step):
                if step.action == "navigate":
                    return {"label": "Continue"}
                self.received = step.args["text"]
                return {"clicked": self.received}

        plan = validate_plan({
            "steps": [
                {"action": "navigate", "args": {"url": "https://example.test"}},
                {"action": "click", "args": {"text": "${step.1.label}"}},
            ]
        })
        executor = DataExecutor()
        result = executor.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(executor.received, "Continue")

    def test_missing_step_reference_stops_dependent_action(self) -> None:
        class ReferenceExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                return {"ok": True}

        plan = validate_plan({
            "steps": [{"action": "click", "args": {"text": "${step.9.missing}"}}]
        })
        executor = ReferenceExecutor()
        result = executor.execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 0)

    def test_open_tab_falls_back_when_context_disallows_new_page(self) -> None:
        class FakeContext:
            def __init__(self):
                self.browser = None
                self.pages = []

            def new_page(self):
                raise RuntimeError("Please use browser.new_context()")

        class FakePage:
            def __init__(self, context):
                self.context = context
                self.url = "about:blank"
                self.marker = ""

            def title(self):
                return ""

            def evaluate(self, script, value=None):
                if value is not None:
                    self.marker = value
                    return None
                return self.marker

            def goto(self, url, **kwargs):
                self.url = url

            def bring_to_front(self):
                return None

        class FakeBrowser:
            def __init__(self):
                self.created = 0

            def new_page(self):
                self.created += 1
                context = FakeContext()
                context.browser = self
                page = FakePage(context)
                context.pages.append(page)
                return page

        class TabExecutor(StructuredExecutor):
            @staticmethod
            def _page_fingerprint(page):
                return page.url, "", ""

            def _verify_expectation(self, expect, *, before_url=""):
                return None

            def _assert_allowed_page_host(self, url):
                return None

        browser = FakeBrowser()
        base_context = FakeContext()
        base_context.browser = browser
        base_page = FakePage(base_context)
        base_context.pages.append(base_page)
        executor = TabExecutor(approve_all=True, execution_id="tabs")
        executor._page = base_page
        step = validate_plan({
            "steps": [{
                "action": "open_tab",
                "args": {"url": "https://second.test"},
            }]
        }).steps[0]
        evidence = executor._execute_browser_step(step)
        self.assertEqual(browser.created, 1)
        self.assertEqual(evidence["url"], "https://second.test")
        self.assertEqual(evidence["active_tab"], "desktop-agent:tabs:1")

    def test_dispatched_consequential_checkpoint_is_never_replayed(self) -> None:
        class ClickExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                return {"clicked": True}

        plan = validate_plan({
            "steps": [{"action": "click", "args": {"text": "Submit"}}]
        })
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            seed = ClickExecutor()
            seed._save_checkpoint(
                checkpoint,
                plan,
                [],
                0,
                in_progress=1,
                phase="dispatched",
                goal="submit",
            )
            executor = ClickExecutor()
            result = executor.execute(
                plan,
                checkpoint_path=checkpoint,
                resume=True,
                goal="submit",
            )
            saved = json.loads(checkpoint.read_text())
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 0)
        self.assertTrue(saved["uncertain"])
        self.assertEqual(saved["phase"], "dispatched")

    def test_prepared_consequential_checkpoint_can_execute_once(self) -> None:
        class ClickExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                return {"clicked": True}

        plan = validate_plan({
            "steps": [{"action": "click", "args": {"text": "Submit"}}]
        })
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            seed = ClickExecutor()
            seed._save_checkpoint(
                checkpoint,
                plan,
                [],
                0,
                in_progress=1,
                phase="prepared",
                goal="submit",
            )
            executor = ClickExecutor()
            result = executor.execute(
                plan,
                checkpoint_path=checkpoint,
                resume=True,
                goal="submit",
            )
        self.assertTrue(result.success)
        self.assertEqual(executor.calls, 1)

    def test_checkpoint_resume_restores_owned_active_tab_marker(self) -> None:
        class ResumeExecutor(StructuredExecutor):
            def _execute_browser_step(self, step):
                return {"url": step.args["url"]}

        plan = validate_plan({"steps": [
            {"action": "navigate", "args": {"url": "https://first.test"}},
            {"action": "navigate", "args": {"url": "https://second.test"}},
        ]})
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            executor = ResumeExecutor(approve_all=True, execution_id="execution")
            executor._save_checkpoint(
                checkpoint,
                plan,
                [{
                    "step": 1,
                    "action": "open_tab",
                    "active_tab": "desktop-agent:execution:1",
                }],
                1,
                goal="tabs",
            )
            result = executor.execute(
                plan,
                checkpoint_path=checkpoint,
                resume=True,
                goal="tabs",
            )
        self.assertTrue(result.success)
        self.assertEqual(executor._resume_tab_marker, "desktop-agent:execution:1")

    def test_checkpoint_resume_skips_completed_steps(self) -> None:
        class FirstRun(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = []

            def _execute_browser_step(self, step):
                self.calls.append(step.args["url"])
                if "second" in step.args["url"]:
                    raise RuntimeError("temporary failure")
                return {"url": step.args["url"]}

        class ResumeRun(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = []

            def _execute_browser_step(self, step):
                self.calls.append(step.args["url"])
                return {"url": step.args["url"]}

        plan = validate_plan({"steps": [
            {"action": "navigate", "args": {"url": "https://first.test"}},
            {"action": "navigate", "args": {"url": "https://second.test"}},
        ]})
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            first = FirstRun()
            self.assertFalse(first.execute(plan, checkpoint_path=checkpoint, goal="test").success)
            self.assertEqual(first.calls, [
                "https://first.test", "https://second.test", "https://second.test"
            ])
            resumed = ResumeRun()
            result = resumed.execute(
                plan, checkpoint_path=checkpoint, resume=True, goal="test"
            )
            self.assertTrue(result.success)
            self.assertEqual(resumed.calls, ["https://second.test"])

    def test_uncertain_consequential_checkpoint_is_not_replayed(self) -> None:
        class FailingClick(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                raise RuntimeError("click outcome unknown")

        plan = validate_plan({
            "steps": [{"action": "click", "args": {"text": "Submit"}}]
        })
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            first = FailingClick()
            self.assertFalse(first.execute(plan, checkpoint_path=checkpoint, goal="test").success)
            resumed = FailingClick()
            result = resumed.execute(
                plan, checkpoint_path=checkpoint, resume=True, goal="test"
            )
            self.assertFalse(result.success)
            self.assertIn("uncertain outcome", result.message)
            self.assertEqual(resumed.calls, 0)

    def test_safe_failed_step_is_repaired_once_without_restarting_plan(self) -> None:
        repair_calls = []

        class RepairExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(
                    approve_all=True,
                    repair_callback=self.repair,
                )
                self.urls = []

            def repair(self, goal, plan, index, step, error, evidence):
                repair_calls.append((goal, index, step.args["url"], error, list(evidence)))
                return validate_plan({
                    "steps": [{
                        "action": "navigate",
                        "args": {"url": "https://replacement.test"},
                    }]
                }).steps[0]

            def _execute_browser_step(self, step):
                self.urls.append(step.args["url"])
                if step.args["url"] == "https://broken.test":
                    raise RuntimeError("unreachable")
                return {"url": step.args["url"]}

        plan = validate_plan({"steps": [
            {"action": "navigate", "args": {"url": "https://broken.test"}},
            {"action": "navigate", "args": {"url": "https://final.test"}},
        ]})
        executor = RepairExecutor()
        result = executor.execute(plan, goal="repair this")
        self.assertTrue(result.success)
        self.assertEqual(len(repair_calls), 1)
        self.assertEqual(executor.urls, [
            "https://broken.test",
            "https://broken.test",
            "https://replacement.test",
            "https://final.test",
        ])
        self.assertEqual(result.evidence[0]["repaired_from_action"], "navigate")

    def test_consequential_uncertain_step_is_never_sent_for_repair(self) -> None:
        repair_calls = []

        class UnsafeExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(
                    approve_all=True,
                    repair_callback=lambda *args: repair_calls.append(args),
                )
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                raise RuntimeError("outcome unknown")

        plan = validate_plan({
            "steps": [{"action": "click", "args": {"text": "Send"}}]
        })
        executor = UnsafeExecutor()
        result = executor.execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(executor.calls, 1)
        self.assertEqual(repair_calls, [])

    def test_reference_failure_checkpoint_clears_in_progress_and_records_error(self) -> None:
        plan = validate_plan({
            "steps": [{"action": "navigate", "args": {"url": "https://example.test/${step.9.path}"}}]
        })
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            result = StructuredExecutor(approve_all=True).execute(
                plan,
                checkpoint_path=checkpoint,
                goal="reference failure",
            )
            saved = json.loads(checkpoint.read_text())
        self.assertFalse(result.success)
        self.assertIsNone(saved["in_progress"])
        self.assertIn("unavailable evidence", saved["error"])

    def test_total_timeout_checkpoint_clears_in_progress_and_records_error(self) -> None:
        plan = validate_plan({
            "steps": [{"action": "navigate", "args": {"url": "https://example.test"}}]
        })
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "session.json"
            executor = StructuredExecutor(
                approve_all=True,
                step_timeout=1,
                total_timeout=1,
            )
            with patch(
                "core.structured_automation.time.monotonic",
                side_effect=[0.0, 2.0],
            ):
                result = executor.execute(
                    plan,
                    checkpoint_path=checkpoint,
                    goal="timeout",
                )
            saved = json.loads(checkpoint.read_text())
        self.assertFalse(result.success)
        self.assertIsNone(saved["in_progress"])
        self.assertIn("total budget", saved["error"])

    def test_executor_retries_failed_step_only_once(self) -> None:
        class LocalExecutor(StructuredExecutor):
            def __init__(self):
                super().__init__(approve_all=True)
                self.calls = 0

            def _execute_browser_step(self, step):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient")
                return {"ok": True}

        plan = validate_plan({
            "summary": "retry",
            "steps": [{"action": "navigate", "args": {"url": "https://example.test"}}],
        })
        executor = LocalExecutor()
        result = executor.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(executor.calls, 2)
        self.assertEqual(result.completed_steps, 1)


class _FakeMessageTextLocator:
    def __init__(self, texts):
        self._texts = texts

    def all_inner_texts(self):
        return list(self._texts)


class _FakeDeliveryLocator:
    def __init__(self, icon):
        self._icon = icon

    @property
    def last(self):
        return self

    def count(self):
        return int(self._icon is not None)

    def is_visible(self, timeout=0):
        return self._icon is not None

    def get_attribute(self, name):
        return self._icon if name == "data-icon" else None


class _FakeOutgoingBubble:
    def __init__(self, text, icon):
        self._text = text
        self._icon = icon

    def inner_text(self):
        return f"{self._text} 10:30"

    def locator(self, selector):
        if selector == "span.selectable-text":
            return _FakeMessageTextLocator([self._text])
        return _FakeDeliveryLocator(self._icon)


class _FakeOutgoingCollection:
    def __init__(self, bubbles):
        self._bubbles = bubbles

    def count(self):
        return len(self._bubbles)

    def nth(self, index):
        return self._bubbles[index]


class _FakeWhatsAppPage:
    def __init__(self, bubbles):
        self._bubbles = bubbles

    def locator(self, selector):
        if selector != "div.message-out":
            raise AssertionError(f"Unexpected global selector: {selector}")
        return _FakeOutgoingCollection(self._bubbles)


class WhatsAppVerificationTests(unittest.TestCase):
    def _verify(self, bubbles, expected="hello", minimum_index=0):
        with patch("tasks.whatsapp_send.time.sleep", return_value=None):
            return _verify_outgoing_message(
                _FakeWhatsAppPage(bubbles),
                expected,
                timeout=0.01,
                minimum_index=minimum_index,
            )

    def test_exact_new_outgoing_text_and_scoped_indicator_are_accepted(self):
        result = self._verify([_FakeOutgoingBubble("hello", "msg-dblcheck")])
        self.assertIsNotNone(result)
        self.assertEqual(result["outgoing_text"], "hello")
        self.assertEqual(result["delivery_icon"], "msg-dblcheck")

    def test_stale_outgoing_bubble_is_not_accepted_for_new_send(self):
        result = self._verify(
            [_FakeOutgoingBubble("hello", "msg-dblcheck")],
            minimum_index=1,
        )
        self.assertIsNone(result)

    def test_wrong_outgoing_text_is_rejected(self):
        result = self._verify([_FakeOutgoingBubble("hello there", "msg-check")])
        self.assertIsNone(result)

    def test_indicator_must_be_inside_matching_outgoing_bubble(self):
        result = self._verify([_FakeOutgoingBubble("hello", None)])
        self.assertIsNone(result)


class VisualTargetValidationTests(unittest.TestCase):
    def test_confident_in_bounds_target_is_accepted(self):
        target = validate_visual_target(
            {
                "x": 100,
                "y": 50,
                "width": 80,
                "height": 40,
                "confidence": 0.93,
                "label": "blue circle",
            },
            image_width=1920,
            image_height=1080,
            minimum_confidence=0.85,
        )
        self.assertEqual(target.center, (140, 70))

    def test_low_confidence_target_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "below required"):
            validate_visual_target(
                {
                    "x": 100,
                    "y": 50,
                    "width": 80,
                    "height": 40,
                    "confidence": 0.4,
                },
                image_width=1920,
                image_height=1080,
                minimum_confidence=0.85,
            )

    def test_out_of_bounds_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_visual_target(
                {
                    "x": 1900,
                    "y": 50,
                    "width": 80,
                    "height": 40,
                    "confidence": 0.95,
                },
                image_width=1920,
                image_height=1080,
                minimum_confidence=0.85,
            )

    def test_high_contrast_refinement_snaps_nearby_point_to_component(self):
        import cv2
        import numpy as np

        image = np.full((400, 700, 3), 240, dtype=np.uint8)
        image[120:240, 220:440] = (160, 97, 18)  # BGR blue
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "canvas.png"
            self.assertTrue(cv2.imwrite(str(path), image))
            refined = VisualLocator.refine_high_contrast_target(
                path,
                VisualTarget(264, 258, 2, 2, 0.99, "blue rectangle"),
            )
        self.assertEqual(refined.center, (330, 180))


class StructuredSessionTests(unittest.TestCase):
    def _write_session(self, directory, session_id="a" * 20, **updates):
        payload = {
            "version": 1,
            "goal": "open example",
            "execution_id": "execution",
            "plan": {
                "summary": "Open example",
                "steps": [{
                    "action": "navigate",
                    "args": {"url": "https://example.test"},
                    "expect": {},
                }],
            },
            "fingerprint": "fingerprint",
            "completed_steps": 0,
            "in_progress": None,
            "complete": False,
            "uncertain": False,
            "error": "",
            "evidence": [],
            "updated_at": 1.0,
        }
        payload.update(updates)
        path = Path(directory) / f"{session_id}.json"
        path.write_text(json.dumps(payload))
        return session_id

    def test_list_and_inspect_session_return_structured_state(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            session_id = self._write_session(
                directory,
                in_progress=1,
                phase="dispatched",
            )
            listed = list_sessions(directory=directory)
            inspected = load_session(session_id, directory=directory)
        self.assertEqual(listed[0]["session_id"], session_id)
        self.assertEqual(listed[0]["total_steps"], 1)
        self.assertEqual(listed[0]["phase"], "dispatched")
        self.assertEqual(inspected["phase"], "dispatched")
        self.assertIn("1. navigate", inspected["plan_description"])

    def test_abandon_preserves_audit_record_and_prevents_resume_state(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            session_id = self._write_session(
                directory,
                in_progress=1,
                uncertain=True,
                error="outcome unknown",
            )
            abandoned = abandon_session(session_id, directory=directory)
            persisted = load_session(session_id, directory=directory)
        self.assertTrue(abandoned["abandoned"])
        self.assertTrue(persisted["complete"])
        self.assertTrue(persisted["uncertain"])
        self.assertIsNone(persisted["in_progress"])

    def test_session_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory_name:
            with self.assertRaisesRegex(ValueError, "Session ID"):
                load_session("../session", directory=Path(directory_name))


class ActionLoopTests(unittest.TestCase):
    def test_adaptive_shell_command_is_denied_without_dispatch(self) -> None:
        with patch("tasks.run_command.execute") as execute:
            result = ActionLoop(api_key="test")._act({
                "action": "run_shell",
                "command": "echo should-not-run",
            })
        self.assertIn("denied", result)
        execute.assert_not_called()

    def test_browser_select_option_tolerates_punctuation_difference(self) -> None:
        page = _FakePage()
        result = ActionLoop(api_key="test")._act_browser(
            {
                "action": "select_option",
                "description": "Select Download",
                "option": "Windows 11 multi-edition ISO for x64 devices",
            },
            page,
        )
        self.assertIn("selected option", result)
        self.assertEqual(
            page.select.selected,
            "Windows 11 (multi-edition ISO for x64 devices)",
        )


class SmartParserTests(unittest.TestCase):
    def test_explicit_url_overrides_install_keyword_scoring(self) -> None:
        raw = "visit https://www.microsoft.com/en-us/software-download/windows11"
        result = smart_parser.parse(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.intent, "visit_url")
        self.assertEqual(
            result.params,
            {"url": "https://www.microsoft.com/en-us/software-download/windows11"},
        )
        self.assertEqual(result.confidence, 1.0)

    def test_downloading_iso_is_not_application_install(self) -> None:
        result = smart_parser.parse("download the Windows 11 ISO into Downloads")
        self.assertTrue(result is None or result.intent != "install_app")


class LLMPlannerTests(unittest.TestCase):
    def test_planner_defaults_fail_fast(self) -> None:
        planner = LLMPlanner(api_key="test")
        self.assertLessEqual(planner.timeout, 30.0)


class ActionPolicyTests(unittest.TestCase):
    def test_approval_is_bound_to_exact_params(self) -> None:
        token = action_token("run_command", {"command": "echo safe"})
        self.assertTrue(approved("run_command", {"command": "echo safe"}, {token}))
        self.assertFalse(approved("run_command", {"command": "echo changed"}, {token}))
        self.assertFalse(approved("system_power", {"command": "echo safe"}, {token}))

    def test_module_aliases_are_classified_by_operation(self) -> None:
        self.assertTrue(requires_approval("file_operations", {"operation": "delete"}))
        self.assertTrue(requires_approval("file_operations", {"operation": "move"}))
        self.assertFalse(requires_approval("file_operations", {"operation": "read"}))
        self.assertTrue(requires_approval("create_folder", {"folder_name": "Reports"}))
        self.assertTrue(requires_approval("folder_operations", {"operation": "create"}))
        self.assertTrue(requires_approval("window_management", {"operation": "close"}))
        self.assertFalse(requires_approval("window_management", {"operation": "list"}))


class WorkflowEngineTests(unittest.TestCase):
    def test_run_stack_unwinds_after_unexpected_failure(self) -> None:
        engine = WorkflowEngine()

        def fail(*_args, **_kwargs) -> bool:
            raise RuntimeError("unexpected")

        engine._run = fail
        self.assertFalse(engine.run("broken"))
        self.assertEqual(engine._run_stack, [])


class VerifierTests(unittest.TestCase):
    def _verifier_with(self, values: list[bool | None]) -> Verifier:
        verifier = Verifier()
        methods = ["_check_ocr", "_check_atspi", "_check_cli", "_check_template"]
        for method, value in zip(methods, values, strict=True):
            setattr(verifier, method, lambda *_args, result=value: result)
        return verifier

    def test_no_evidence_fails_closed(self) -> None:
        verifier = self._verifier_with([None, None, None, None])
        self.assertFalse(verifier.verify(VerifySpec(settle_wait=0)))

    def test_contradictory_evidence_fails_by_default(self) -> None:
        verifier = self._verifier_with([True, False, None, None])
        self.assertFalse(verifier.verify(VerifySpec(settle_wait=0)))

    def test_unavailable_evidence_does_not_contradict_success(self) -> None:
        verifier = self._verifier_with([True, None, None, None])
        self.assertTrue(verifier.verify(VerifySpec(settle_wait=0)))

    def test_any_mode_can_accept_one_confirmation(self) -> None:
        verifier = self._verifier_with([True, False, None, None])
        self.assertTrue(verifier.verify(VerifySpec(settle_wait=0, require_all=False)))


if __name__ == "__main__":
    unittest.main()

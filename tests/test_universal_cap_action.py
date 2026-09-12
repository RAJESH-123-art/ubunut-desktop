"""Tests for the universal `cap` action in structured plans.

Covers validation, approval gating, dispatch through the merged registry,
evidence fields for ${step.N.field} references, and runtime-adapter wiring.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.runtime_adapters import execute_structured_plan_runtime
from core.structured_automation import (
    PlanStep,
    StructuredExecutor,
    StructuredPlan,
    _universal_catalog_compact,
    validate_plan,
)


def _plan_with_cap(cap: str, cap_args: dict | None = None) -> StructuredPlan:
    return validate_plan({
        "summary": "cap test plan",
        "steps": [
            {"action": "cap", "args": {"cap": cap, "args": cap_args or {}}},
        ],
    })


class CapValidationTests(unittest.TestCase):
    def test_cap_action_validates_with_name_and_args(self) -> None:
        plan = _plan_with_cap("calc.evaluate", {"expression": "1+1"})
        self.assertEqual(plan.steps[0].action, "cap")
        self.assertEqual(plan.steps[0].args["cap"], "calc.evaluate")
        self.assertEqual(plan.steps[0].args["args"], {"expression": "1+1"})

    def test_cap_action_accepts_missing_args(self) -> None:
        plan = validate_plan({
            "summary": "cap test plan",
            "steps": [{"action": "cap", "args": {"cap": "system.get_username"}}],
        })
        self.assertEqual(plan.steps[0].args.get("args", {}), {})

    def test_cap_requires_capability_name(self) -> None:
        with self.assertRaises(ValueError):
            validate_plan({
                "summary": "bad",
                "steps": [{"action": "cap", "args": {}}],
            })

    def test_cap_rejects_non_object_args(self) -> None:
        with self.assertRaises(TypeError):
            validate_plan({
                "summary": "bad",
                "steps": [{"action": "cap", "args": {"cap": "calc.evaluate", "args": "1+1"}}],
            })

    def test_cap_cannot_invoke_forbidden_or_prefixed_names(self) -> None:
        for name in ("run_command", "universal_fallback", "structured.click", "task:file_write"):
            with self.assertRaises(ValueError):
                validate_plan({
                    "summary": "bad",
                    "steps": [{"action": "cap", "args": {"cap": name}}],
                })

    def test_unknown_cap_names_are_still_valid_plans(self) -> None:
        # Validation stays permissive (registry decides); dispatch fails.
        plan = _plan_with_cap("does.not_exist")
        self.assertEqual(plan.steps[0].args["cap"], "does.not_exist")


class CapCatalogTests(unittest.TestCase):
    def test_catalog_lists_capabilities_compactly(self) -> None:
        catalog = _universal_catalog_compact()
        self.assertIn("calc.evaluate(expression)", catalog)
        self.assertIn("system.get_username", catalog)
        # [c] marks confirmation-required caps
        self.assertIn("fs.delete_file(path)[c]", catalog)

    def test_catalog_excludes_prefixed_structured_and_task_caps(self) -> None:
        catalog = _universal_catalog_compact()
        self.assertNotIn("structured.", catalog)
        self.assertNotIn("task:", catalog)


class CapApprovalTests(unittest.TestCase):
    def test_read_cap_is_not_consequential(self) -> None:
        step = PlanStep("cap", {"cap": "calc.evaluate", "args": {"expression": "1"}})
        self.assertFalse(StructuredExecutor._step_is_consequential(step))
        self.assertEqual(StructuredExecutor._step_side_effect(step), "read")

    def test_confirmation_cap_is_consequential(self) -> None:
        step = PlanStep("cap", {"cap": "fs.delete_file", "args": {"path": "/tmp/x"}})
        self.assertTrue(StructuredExecutor._step_is_consequential(step))
        self.assertEqual(StructuredExecutor._step_side_effect(step), "destructive")

    def test_external_side_effect_cap_is_consequential(self) -> None:
        step = PlanStep("cap", {"cap": "comm.send_whatsapp", "args": {}})
        self.assertTrue(StructuredExecutor._step_is_consequential(step))

    def test_local_write_cap_is_not_consequential(self) -> None:
        step = PlanStep("cap", {"cap": "fs.write", "args": {"path": "/tmp/x", "content": "a"}})
        self.assertFalse(StructuredExecutor._step_is_consequential(step))
        self.assertEqual(StructuredExecutor._step_side_effect(step), "local_write")

    def test_unknown_cap_is_treated_as_consequential(self) -> None:
        step = PlanStep("cap", {"cap": "does.not_exist"})
        self.assertTrue(StructuredExecutor._step_is_consequential(step))


class CapDispatchTests(unittest.TestCase):
    """Dispatch tests run harmless read/local caps on this machine."""

    def test_read_cap_dispatches_and_returns_data_fields(self) -> None:
        executor = StructuredExecutor(approve_all=True)
        outcome = executor.execute_step(
            PlanStep("cap", {"cap": "calc.evaluate", "args": {"expression": "2+3*4"}}),
            goal="test cap dispatch",
        )
        self.assertEqual(outcome.state, "succeeded", outcome.error)
        self.assertEqual(outcome.data["cap"], "calc.evaluate")
        self.assertEqual(outcome.data["result"], 14)
        self.assertEqual(outcome.evidence[0]["result"], 14)

    def test_unknown_cap_fails_without_dispatch(self) -> None:
        executor = StructuredExecutor(approve_all=True)
        outcome = executor.execute_step(
            PlanStep("cap", {"cap": "does.not_exist"}),
            goal="test cap dispatch",
        )
        self.assertEqual(outcome.state, "failed")
        self.assertIn("Unknown capability", outcome.error or "")

    def test_confirmation_cap_blocked_without_approval(self) -> None:
        executor = StructuredExecutor()  # approve_all=False
        outcome = executor.execute_step(
            PlanStep("cap", {"cap": "fs.delete_file", "args": {"path": "/tmp/never"}}),
            goal="test cap gating",
        )
        # Blocked by the one-step approval gate before dispatch.
        self.assertEqual(outcome.state, "blocked")
        self.assertIn("requires", outcome.error or "")

    def test_confirmation_cap_runs_with_approve_all(self) -> None:
        # Use a harmless confirmation cap: dev packages are not installed here.
        executor = StructuredExecutor(approve_all=True)
        outcome = executor.execute_step(
            PlanStep("cap", {"cap": "system.get_info", "args": {}}),
            goal="test approve_all",
        )
        self.assertEqual(outcome.state, "succeeded", outcome.error)
        self.assertIn("hostname", outcome.data)

    def test_evidence_reference_resolves_across_cap_steps(self) -> None:
        from core.structured_automation import _resolve_step_references

        evidence = [
            {"step": 1, "action": "cap", "cap": "calc.evaluate", "result": 42},
        ]
        resolved = _resolve_step_references(
            "answer is ${step.1.result}", evidence
        )
        self.assertEqual(resolved, "answer is 42")


class CapRuntimeAdapterTests(unittest.TestCase):
    def test_runtime_dispatches_cap_steps_through_merged_registry(self) -> None:
        plan = StructuredPlan(
            "calculate then assert",
            (
                PlanStep("cap", {"cap": "calc.evaluate", "args": {"expression": "6*7"}}),
                PlanStep(
                    "assert_value",
                    {"value": "${step.1.result}", "operator": "equals", "expected": 42},
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory_name:
            checkpoint = Path(directory_name) / "runtime.json"
            result = execute_structured_plan_runtime(
                plan,
                "calculate then assert",
                executor=StructuredExecutor(approve_all=True),
                checkpoint_path=checkpoint,
            )
        self.assertTrue(result.success, result.state.message)
        evidence = result.state.verified_facts["structured_evidence"]
        self.assertEqual(evidence[0]["cap"], "calc.evaluate")
        self.assertEqual(evidence[0]["result"], 42)
        self.assertEqual(result.state.verified_facts["structured_next_step"], 2)

    def test_runtime_blocked_cap_fails_plan_with_step_index(self) -> None:
        plan = StructuredPlan(
            "unknown capability",
            (PlanStep("cap", {"cap": "does.not_exist"}),),
        )
        with tempfile.TemporaryDirectory() as directory_name:
            checkpoint = Path(directory_name) / "runtime.json"
            result = execute_structured_plan_runtime(
                plan,
                "unknown capability",
                executor=StructuredExecutor(approve_all=True),
                checkpoint_path=checkpoint,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.state.status, "failed")
        self.assertIn("does.not_exist", result.state.message)


class CapPlannerInjectionTests(unittest.TestCase):
    def test_plan_request_includes_universal_catalog(self) -> None:
        from core.structured_automation import StructuredPlanner

        captured: dict[str, str] = {}

        def fake_completion(system: str, user: str) -> str:
            captured["system"] = system
            captured["user"] = user
            return json.dumps({
                "summary": "ok",
                "steps": [{"action": "wait", "args": {"seconds": 0}}],
            })

        planner = StructuredPlanner(api_key="test-key")
        with patch.object(planner, "_completion", side_effect=fake_completion):
            planner.plan("do nothing")
        self.assertIn("UNIVERSAL CAPABILITY CATALOG", captured["user"])
        self.assertIn("calc.evaluate(expression)", captured["user"])

    def test_plan_prompt_documents_cap_action(self) -> None:
        from core.structured_automation import _COMPACT_PLAN_PROMPT, _PLAN_PROMPT

        self.assertIn("- cap: {\"cap\":", _PLAN_PROMPT)
        self.assertIn("cap(cap_name,args", _COMPACT_PLAN_PROMPT)


if __name__ == "__main__":
    unittest.main()

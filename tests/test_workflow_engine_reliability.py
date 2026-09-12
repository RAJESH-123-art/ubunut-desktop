from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.task_contract import TaskResult, TaskSpec
from core.workflow_engine import StepResult, WorkflowEngine


class WorkflowEngineReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WorkflowEngine(approve_all=True)
        adaptive_patch = patch.object(
            self.engine, "_adaptive_retries", lambda wf, step, base: base
        )
        record_patch = patch.object(
            self.engine,
            "_record_step",
            lambda wf, step, success, duration: None,
        )
        adaptive_patch.start()
        record_patch.start()
        self.addCleanup(adaptive_patch.stop)
        self.addCleanup(record_patch.stop)

    @staticmethod
    def _module(execute):
        return SimpleNamespace(
            setup=dict,
            execute=execute,
            cleanup=lambda _resources: None,
        )

    def _run_step(self, execute, *, config=None, spec=None, previous=None):
        step_config = {"task": "focused_task", "retry_wait": 0, **(config or {})}
        with (
            patch("tasks.get_task", return_value=self._module(execute)),
            patch("tasks.get_task_spec", return_value=spec),
        ):
            return self.engine._run_step(step_config, "focused", {}, previous or {})

    def test_preserves_unified_outcome_error_and_evidence_and_does_not_retry(self) -> None:
        calls = 0
        expected_evidence = [{"source": "remote", "status": "accepted"}]

        def execute(_args, _resources):
            nonlocal calls
            calls += 1
            return TaskResult(
                state="uncertain",
                error="confirmation timed out",
                evidence=expected_evidence,
                dispatch_status="dispatched",
            )

        result = self._run_step(
            execute,
            config={"retries": 3},
            spec=TaskSpec(name="focused_task", side_effect="external"),
        )

        self.assertEqual(calls, 1)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "confirmation timed out")
        self.assertEqual(result.evidence, expected_evidence)
        self.assertIsNotNone(result.outcome)
        assert result.outcome is not None
        self.assertEqual(result.outcome.state, "uncertain")
        self.assertEqual(result.outcome.side_effect, "external")

    def test_unknown_destructive_failure_is_not_retried(self) -> None:
        calls = 0

        def execute(_args, _resources):
            nonlocal calls
            calls += 1
            return TaskResult(
                state="failed",
                error="delete acknowledgement unavailable",
                dispatch_status="unknown",
                side_effect="read",
            )

        result = self._run_step(
            execute,
            config={"retries": 2},
            spec=TaskSpec(name="focused_task", side_effect="destructive"),
        )

        self.assertEqual(calls, 1)
        self.assertEqual(result.error, "delete acknowledgement unavailable")
        assert result.outcome is not None
        self.assertEqual(result.outcome.side_effect, "destructive")

    def test_legacy_false_remains_retryable(self) -> None:
        returned = iter([False, True])
        calls = 0

        def execute(_args, _resources):
            nonlocal calls
            calls += 1
            return next(returned)

        result = self._run_step(
            execute,
            config={"retries": 1},
            spec=TaskSpec(name="focused_task", side_effect="external"),
        )

        self.assertEqual(calls, 2)
        self.assertTrue(result.success)
        assert result.outcome is not None
        self.assertEqual(result.outcome.state, "succeeded")

    def test_dependencies_resolve_names_and_keys_and_fail_clearly(self) -> None:
        successful = StepResult("producer")
        successful.success = True
        successful.aliases = ("producer", "source-key")
        skipped = StepResult("optional")
        skipped.skipped = True
        skipped.aliases = ("optional",)
        failed = StepResult("failed")
        failed.error = "upstream broke"
        failed.aliases = ("failed",)
        previous = {
            "producer#0": successful,
            "optional#1": skipped,
            "failed#2": failed,
        }

        accepted = self._run_step(
            lambda _args, _resources: True,
            config={"depends_on": ["source-key", "optional"]},
            previous=previous,
        )
        self.assertTrue(accepted.success)

        missing = self._run_step(
            lambda _args, _resources: self.fail("missing dependency executed"),
            config={"depends_on": ["absent"]},
            previous=previous,
        )
        self.assertEqual(missing.error, "Missing dependency 'absent'")

        blocked = self._run_step(
            lambda _args, _resources: self.fail("failed dependency executed"),
            config={"depends_on": ["failed"]},
            previous=previous,
        )
        self.assertEqual(blocked.error, "Dependency 'failed' failed: upstream broke")

    def test_parallel_results_and_default_run_if_use_declared_order(self) -> None:
        modules = {
            "slow_failure": self._module(
                lambda _args, _resources: (time.sleep(0.03), False)[1]
            ),
            "fast_success": self._module(lambda _args, _resources: True),
        }
        steps = [
            {"task": "slow_failure", "parallel": True},
            {"task": "fast_success", "parallel": True},
        ]
        with (
            patch("tasks.get_task", side_effect=lambda name: modules[name]),
            patch("tasks.get_task_spec", return_value=TaskSpec(name="ignored", side_effect="read")),
        ):
            results = self.engine._run_parallel_batch(steps, "focused", {}, {})

        self.assertEqual(list(results), ["slow_failure#0", "fast_success#1"])
        conditional = self._run_step(
            lambda _args, _resources: self.fail("condition should have skipped"),
            config={"run_if": "failure"},
            previous=results,
        )
        self.assertTrue(conditional.skipped)

    def test_explicit_condition_dependency_selects_named_prior_step(self) -> None:
        failure = StepResult("first")
        failure.error = "expected failure"
        failure.aliases = ("first",)
        success = StepResult("second")
        success.success = True
        success.aliases = ("second",)

        result = self._run_step(
            lambda _args, _resources: True,
            config={"run_if": "failure", "run_if_on": "first"},
            previous={"first#0": failure, "second#1": success},
        )
        self.assertTrue(result.success)

    def test_conditional_skip_counts_as_workflow_completion(self) -> None:
        workflow = {
            "focused": {
                "steps": [
                    {"task": "focused_task"},
                    {"task": "focused_task", "run_if": "failure"},
                ]
            }
        }
        module = self._module(lambda _args, _resources: True)
        with (
            patch.object(self.engine, "_load_yaml", return_value=workflow),
            patch("tasks.get_task", return_value=module),
            patch("tasks.get_task_spec", return_value=TaskSpec(name="focused_task", side_effect="read")),
        ):
            self.assertTrue(self.engine.run("focused", shared_resources={}))


if __name__ == "__main__":
    unittest.main()

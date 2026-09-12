from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.parallel_runner import ParallelRunner
from core.result_bus import result_bus
from core.task_contract import TaskResult
from core.task_dag import TaskDAG, TaskNode


class ParallelRunnerSafetyTests(unittest.TestCase):
    def tearDown(self) -> None:
        result_bus.clear()

    def test_preserves_unified_result_and_dependency_data(self) -> None:
        seen: dict[str, object] = {}
        dag = TaskDAG()
        dag.add(TaskNode("producer", "read_source", max_retries=0))
        dag.add(TaskNode(
            "consumer",
            "consume",
            args={"value": "${producer.value}"},
            deps=["producer"],
            max_retries=0,
        ))

        expected = TaskResult(
            state="succeeded",
            data={"value": 42},
            evidence=[{"source": "test"}],
            confidence=0.8,
            uncertainty=["minor caveat"],
            dispatch_status="confirmed",
            side_effect="read",
            metadata={"trace_id": "trace-1"},
        )

        def builder(intent: str, args: dict):
            def execute(final_args: dict, resources: dict) -> object:
                if intent == "read_source":
                    return expected
                seen.update(final_args)
                return True

            return execute, dict(args), {}

        summary = ParallelRunner(
            max_workers=1, timeout=5, executor_builder=builder
        ).run(dag)

        stored = result_bus.get("producer")
        assert stored is not None
        self.assertEqual(summary["done"], 2)
        self.assertEqual(seen["value"], 42)
        self.assertIs(stored, expected)
        self.assertEqual(stored.evidence, [{"source": "test"}])
        self.assertEqual(stored.confidence, 0.8)
        self.assertEqual(stored.uncertainty, ["minor caveat"])
        self.assertEqual(stored.metadata, {"trace_id": "trace-1"})
        self.assertEqual(stored.dispatch_status, "confirmed")

    def test_bool_executor_retains_published_data_and_contract_fields(self) -> None:
        dag = TaskDAG()
        dag.add(TaskNode("legacy", "legacy", max_retries=0))

        def builder(intent: str, args: dict):
            def execute(final_args: dict, resources: dict) -> bool:
                result_bus.publish(
                    resources["dag_node_id"],
                    TaskResult(
                        ok=True,
                        data={"value": "published"},
                        evidence=[{"kind": "legacy"}],
                        confidence=0.9,
                        dispatch_status="confirmed",
                        side_effect="read",
                        metadata={"owner": "executor"},
                    ),
                )
                return True

            return execute, dict(args), {}

        ParallelRunner(max_workers=1, timeout=5, executor_builder=builder).run(dag)

        stored = result_bus.get("legacy")
        legacy_node = dag.get("legacy")
        assert stored is not None
        assert legacy_node is not None
        self.assertEqual(legacy_node.result, {"value": "published"})
        self.assertEqual(stored.evidence, [{"kind": "legacy"}])
        self.assertEqual(stored.confidence, 0.9)
        self.assertEqual(stored.metadata, {"owner": "executor"})
        self.assertEqual(stored.dispatch_status, "confirmed")

    def test_uncertain_and_possibly_dispatched_effects_are_not_repeated(self) -> None:
        cases = [
            TaskResult(
                state="uncertain",
                error="verification unavailable",
                side_effect="read",
                dispatch_status="not_dispatched",
            ),
            TaskResult(
                state="failed",
                error="response lost",
                side_effect="external",
                dispatch_status="dispatched",
            ),
            TaskResult(
                state="failed",
                error="delete response lost",
                side_effect="destructive",
                dispatch_status="unknown",
            ),
            TaskResult(
                state="failed",
                error="write response lost",
                side_effect="local_write",
                dispatch_status="unknown",
            ),
        ]
        intents = ["read_source", "send", "delete", "file_write"]

        for outcome, intent in zip(cases, intents):
            with self.subTest(state=outcome.state, intent=intent):
                result_bus.clear()
                calls = 0
                dag = TaskDAG()
                dag.add(TaskNode("task", intent, max_retries=2))

                def builder(
                    _intent: str,
                    args: dict,
                    selected_outcome: TaskResult = outcome,
                ):
                    def execute(final_args: dict, resources: dict) -> TaskResult:
                        nonlocal calls
                        calls += 1
                        return selected_outcome

                    return execute, dict(args), {}

                fake_replanner = Mock()
                with patch("core.parallel_runner.replanner", fake_replanner):
                    ParallelRunner(
                        max_workers=1, timeout=5, executor_builder=builder
                    ).run(dag)

                task_node = dag.get("task")
                assert task_node is not None
                self.assertEqual(calls, 1)
                self.assertEqual(task_node.retry_count, 0)
                self.assertEqual(task_node.status, "failed")
                fake_replanner.on_failure.assert_not_called()
                self.assertIs(result_bus.get("task"), outcome)

    def test_legacy_bool_failure_keeps_retry_behavior(self) -> None:
        calls = 0
        dag = TaskDAG()
        dag.add(TaskNode("legacy", "safe_legacy", max_retries=1))

        def builder(intent: str, args: dict):
            def execute(final_args: dict, resources: dict) -> bool:
                nonlocal calls
                calls += 1
                return calls == 2

            return execute, dict(args), {}

        summary = ParallelRunner(
            max_workers=1, timeout=5, executor_builder=builder
        ).run(dag)

        legacy_node = dag.get("legacy")
        assert legacy_node is not None
        self.assertEqual(calls, 2)
        self.assertEqual(legacy_node.retry_count, 1)
        self.assertEqual(summary["done"], 1)

    def test_safe_unified_failure_keeps_replanning_behavior(self) -> None:
        dag = TaskDAG()
        node = TaskNode("safe", "read_source", max_retries=0)
        dag.add(node)

        def builder(intent: str, args: dict):
            return (
                lambda final_args, resources: TaskResult(
                    state="failed",
                    error="temporary read failure",
                    side_effect="read",
                    dispatch_status="unknown",
                ),
                dict(args),
                {},
            )

        fake_replanner = Mock()
        fake_replanner.on_failure.return_value = False
        with patch("core.parallel_runner.replanner", fake_replanner):
            ParallelRunner(
                max_workers=1, timeout=5, executor_builder=builder
            ).run(dag)

        fake_replanner.on_failure.assert_called_once_with(node, dag)
        self.assertEqual(node.status, "failed")


if __name__ == "__main__":
    unittest.main()

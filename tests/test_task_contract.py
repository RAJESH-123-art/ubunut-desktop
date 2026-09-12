from __future__ import annotations

import threading
import time
import unittest

from core.result_bus import ResultBus
from core.result_bus import TaskResult as BusTaskResult
from core.task_contract import ActionOutcome, TaskResult, normalize_task_result


class TaskResultCompatibilityTests(unittest.TestCase):
    def test_success_constructor_and_positional_bool_are_preserved(self) -> None:
        keyword = TaskResult(success=True)
        positional = TaskResult(False, {"value": 3})

        self.assertEqual(keyword.state, "succeeded")
        self.assertTrue(keyword.success)
        self.assertTrue(keyword.ok)
        self.assertTrue(keyword)
        self.assertEqual(keyword.data, {})

        self.assertEqual(positional.state, "failed")
        self.assertFalse(positional.success)
        self.assertFalse(positional.ok)
        self.assertFalse(positional)
        self.assertEqual(positional.data, {"value": 3})

    def test_ok_constructor_and_result_bus_defaults_are_preserved(self) -> None:
        result = TaskResult(ok=True)

        self.assertEqual(result.state, "succeeded")
        self.assertTrue(result.success)
        self.assertTrue(result.ok)
        self.assertIsNone(result.data)
        self.assertEqual(result.metadata, {})

    def test_success_and_ok_cannot_disagree(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot disagree"):
            TaskResult(success=True, ok=False)

    def test_explicit_state_supports_non_success_terminal_states(self) -> None:
        result = TaskResult(
            state="skipped",
            dispatch_status="not_attempted",
            side_effect="read",
            uncertainty=["condition_not_met"],
        )

        self.assertFalse(result)
        self.assertFalse(result.success)
        self.assertFalse(result.ok)
        self.assertEqual(result.state, "skipped")

        paused = TaskResult(
            state="paused_for_human",
            dispatch_status="not_attempted",
            side_effect="read",
        )
        self.assertEqual(paused.state, "paused_for_human")

    def test_existing_and_new_fields_are_retained(self) -> None:
        action = ActionOutcome(
            state="succeeded",
            data={"clicked": "Save"},
            evidence=[{"kind": "ui_text", "value": "Saved"}],
            confidence=0.9,
            uncertainty=["visual_match"],
            dispatch_status="confirmed",
            side_effect="external",
            warnings=["slow response"],
            metadata={"attempt": 1},
        )
        result = TaskResult(
            success=True,
            data={"id": 7},
            evidence=[{"kind": "record", "id": 7}],
            warnings=["cached"],
            error=None,
            confidence=0.8,
            uncertainty=["eventual_consistency"],
            dispatch_status="confirmed",
            side_effect="external",
            metadata={"source": "test"},
            actions=[action],
        )

        self.assertEqual(result.data, {"id": 7})
        self.assertEqual(result.error, None)
        self.assertEqual(result.metadata, {"source": "test"})
        self.assertEqual(result.evidence[0]["id"], 7)
        self.assertEqual(result.warnings, ["cached"])
        self.assertEqual(result.confidence, 0.8)
        self.assertEqual(result.uncertainty, ["eventual_consistency"])
        self.assertEqual(result.actions, [action])

    def test_confidence_is_validated_and_normalized_to_float(self) -> None:
        self.assertEqual(ActionOutcome(state="succeeded", confidence=1).confidence, 1.0)
        self.assertIsNone(ActionOutcome(state="failed").confidence)

        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence), self.assertRaisesRegex(
                ValueError, "between 0.0 and 1.0"
            ):
                ActionOutcome(state="failed", confidence=confidence)
        with self.assertRaisesRegex(TypeError, "confidence must be a number"):
            ActionOutcome(state="failed", confidence=True)

    def test_invalid_contract_literals_and_action_entries_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid outcome state"):
            ActionOutcome(state="done")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "Invalid dispatch status"):
            ActionOutcome(state="failed", dispatch_status="maybe")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "Invalid side-effect"):
            ActionOutcome(state="failed", side_effect="network")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "ActionOutcome"):
            TaskResult(True, actions=[{"state": "succeeded"}])  # type: ignore[list-item]


class NormalizeTaskResultTests(unittest.TestCase):
    def test_standard_result_passes_through_without_context_overrides(self) -> None:
        result = TaskResult(True, metadata={"request_id": "abc"})
        self.assertIs(normalize_task_result(result), result)

    def test_context_overrides_copy_without_losing_fields(self) -> None:
        original = TaskResult(
            True,
            data={"path": "/tmp/report"},
            evidence=[{"kind": "file"}],
            metadata={"request_id": "abc"},
        )
        normalized = normalize_task_result(
            original,
            side_effect="local_write",
            dispatch_status="confirmed",
        )

        self.assertIsNot(normalized, original)
        self.assertEqual(normalized.data, original.data)
        self.assertEqual(normalized.evidence, original.evidence)
        self.assertEqual(normalized.metadata, original.metadata)
        self.assertEqual(normalized.side_effect, "local_write")
        self.assertEqual(normalized.dispatch_status, "confirmed")

    def test_action_outcome_is_promoted_and_retained(self) -> None:
        action = ActionOutcome(
            state="uncertain",
            evidence=[{"kind": "screenshot"}],
            confidence=0.4,
            uncertainty=["postcondition_not_observed"],
            dispatch_status="dispatched",
            side_effect="external",
            error="Save was issued but could not be verified",
        )
        result = normalize_task_result(action)

        self.assertEqual(result.state, "uncertain")
        self.assertFalse(result)
        self.assertEqual(result.actions, [action])
        self.assertEqual(result.evidence, action.evidence)
        self.assertEqual(result.uncertainty, action.uncertainty)

    def test_bool_none_and_truthiness_adapters_are_explicit(self) -> None:
        legacy_true = normalize_task_result(
            True,
            side_effect="read",
            dispatch_status="confirmed",
        )
        no_result = normalize_task_result(None)
        truthy = normalize_task_result("legacy success")

        self.assertTrue(legacy_true)
        self.assertIsNone(legacy_true.confidence)
        self.assertEqual(legacy_true.uncertainty, ["legacy_boolean_result"])
        self.assertEqual(legacy_true.side_effect, "read")
        self.assertEqual(legacy_true.dispatch_status, "confirmed")

        self.assertFalse(no_result)
        self.assertEqual(no_result.uncertainty, ["legacy_none_result"])
        self.assertIn("no result", no_result.error or "")

        self.assertTrue(truthy)
        self.assertEqual(truthy.uncertainty, ["result_truthiness_adapter"])
        self.assertEqual(truthy.metadata["legacy_type"], "str")

    def test_legacy_result_object_is_adapted(self) -> None:
        class LegacyResult:
            def __init__(self) -> None:
                self.ok = False
                self.data = {"partial": 2}
                self.error = "legacy failure"
                self.metadata = {"attempt": 2}

        result = normalize_task_result(
            LegacyResult(),
            side_effect="destructive",
            dispatch_status="not_dispatched",
        )

        self.assertFalse(result)
        self.assertEqual(result.data, {"partial": 2})
        self.assertEqual(result.error, "legacy failure")
        self.assertEqual(result.metadata, {"attempt": 2})
        self.assertEqual(result.uncertainty, ["legacy_result_adapter"])
        self.assertEqual(result.side_effect, "destructive")
        self.assertEqual(result.dispatch_status, "not_dispatched")


class ResultBusCompatibilityTests(unittest.TestCase):
    def test_result_bus_reexports_the_unified_class(self) -> None:
        self.assertIs(BusTaskResult, TaskResult)

    def test_result_bus_publishes_and_waits_for_unified_results(self) -> None:
        bus = ResultBus()
        result = BusTaskResult(
            ok=True,
            data={"urls": ["https://example.test"]},
            metadata={"source": "fixture"},
            evidence=[{"kind": "url"}],
            confidence=0.95,
            dispatch_status="confirmed",
            side_effect="read",
        )

        bus.publish("search", result)

        self.assertIs(bus.get("search"), result)
        self.assertIs(bus.wait_for("search", timeout=0.01), result)
        stored = bus.get("search")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.metadata["source"], "fixture")
        self.assertEqual(stored.confidence, 0.95)

    def test_multiple_waiters_receive_the_same_publication(self) -> None:
        bus = ResultBus()
        received = []
        ready = threading.Barrier(3)

        def wait() -> None:
            ready.wait()
            received.append(bus.wait_for("shared", timeout=1.0))

        threads = [threading.Thread(target=wait) for _ in range(2)]
        for thread in threads:
            thread.start()
        ready.wait()
        time.sleep(0.02)
        expected = TaskResult(ok=True, data={"value": 42})
        bus.publish("shared", expected)
        for thread in threads:
            thread.join(timeout=1.0)

        self.assertEqual(received, [expected, expected])
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_wait_for_all_uses_one_shared_timeout_budget(self) -> None:
        bus = ResultBus()
        started = time.monotonic()
        results = bus.wait_for_all(["first", "second"], timeout=0.05)
        elapsed = time.monotonic() - started

        self.assertEqual(results, {"first": None, "second": None})
        self.assertLess(elapsed, 0.09)


if __name__ == "__main__":
    unittest.main()

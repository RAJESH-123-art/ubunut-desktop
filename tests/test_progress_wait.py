from __future__ import annotations

from core.progress_wait import wait_for_condition


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_wait_completes_only_on_condition_after_observed_progress() -> None:
    clock = _Clock()
    observations = iter([
        {"bytes": 0, "complete": False},
        {"bytes": 10, "complete": False},
        {"bytes": 20, "complete": False},
        {"bytes": 20, "complete": True},
    ])

    result = wait_for_condition(
        lambda: next(observations),
        lambda item: item["complete"],
        progress=lambda item: item["bytes"],
        timeout=5,
        stall_timeout=1,
        interval=0.25,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.satisfied
    assert result.observation["bytes"] == 20
    assert len(result.samples) == 4


def test_wait_marks_unchanged_operation_stalled_before_safety_timeout() -> None:
    clock = _Clock()

    result = wait_for_condition(
        lambda: {"bytes": 10, "complete": False},
        lambda item: item["complete"],
        progress=lambda item: item["bytes"],
        timeout=10,
        stall_timeout=1,
        interval=0.5,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.status == "stalled"
    assert clock.value == 1.0


def test_wait_without_progress_uses_timeout_only_as_safety_limit() -> None:
    clock = _Clock()

    result = wait_for_condition(
        lambda: {"ready": False},
        lambda item: item["ready"],
        timeout=1,
        interval=0.5,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.status == "timed_out"

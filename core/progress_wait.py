"""Condition and progress based waiting for long-running desktop work."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

WaitStatus = Literal["satisfied", "stalled", "timed_out"]


@dataclass(frozen=True)
class WaitSample:
    elapsed: float
    observation: Any
    progress: Any


@dataclass(frozen=True)
class ProgressWaitResult:
    status: WaitStatus
    observation: Any
    samples: tuple[WaitSample, ...] = field(default_factory=tuple)

    @property
    def satisfied(self) -> bool:
        return self.status == "satisfied"


def wait_for_condition(
    observe: Callable[[], Any],
    condition: Callable[[Any], bool],
    *,
    progress: Callable[[Any], Any] | None = None,
    timeout: float = 30.0,
    stall_timeout: float | None = None,
    interval: float = 0.25,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ProgressWaitResult:
    """Wait for a real condition while progress continues.

    A changing progress value renews the stall window.  ``timeout`` remains a
    safety ceiling; completion is reported only when ``condition`` is true.
    """
    started = clock()
    deadline = started + max(0.0, timeout)
    stall_limit = max(0.0, stall_timeout if stall_timeout is not None else timeout)
    samples: list[WaitSample] = []
    last_progress_at = started
    previous_progress: Any = object()
    latest: Any = None

    while True:
        latest = observe()
        current = progress(latest) if progress is not None else None
        now = clock()
        samples.append(WaitSample(max(0.0, now - started), latest, current))
        if condition(latest):
            return ProgressWaitResult("satisfied", latest, tuple(samples))
        if progress is not None and current != previous_progress:
            previous_progress = current
            last_progress_at = now
        if now >= deadline:
            return ProgressWaitResult("timed_out", latest, tuple(samples))
        if progress is not None and now - last_progress_at >= stall_limit:
            return ProgressWaitResult("stalled", latest, tuple(samples))
        sleeper(min(max(0.01, interval), max(0.01, deadline - now)))

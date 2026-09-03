"""
Generic poll-until-condition helper.

Replaces blind `time.sleep(N)` calls used as a substitute for checking real
state. Found duplicated (in spirit) across window_management.py,
open_system_app.py, atspi_install.py, klavaro_automation.py and others —
see TASK_KNOWLEDGE_BASE.md Part 3.

Prefer this whenever the actual intent is "wait for X to become true",
not "wait exactly N seconds and hope."
"""
from __future__ import annotations

import time
from collections.abc import Callable


def wait_until(
    check_fn: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.1,
) -> bool:
    """
    Poll check_fn() every `interval` seconds until it returns True or
    `timeout` seconds have elapsed.

    Returns True as soon as check_fn() returns True, False if the timeout
    is reached without success. Exceptions raised inside check_fn are
    treated as "not ready yet" and swallowed — callers that care about the
    distinction should catch/log inside their own check_fn.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if check_fn():
                return True
        except Exception:
            pass
        time.sleep(interval)
    # One last check right at the deadline in case the last sleep overshot it
    try:
        return bool(check_fn())
    except Exception:
        return False

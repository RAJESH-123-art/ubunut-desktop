"""
Utility task — pause automation for N seconds.

Useful between multi-step workflows where the UI needs time to react.

Args:
    seconds (int|float): How long to wait. Must be 0–300. Default: 1.
"""
import time

from loguru import logger

from core.logger import finish, start

_MAX_WAIT = 300   # safety cap — 5 minutes


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    """Pause for the requested duration."""
    task_name = "wait_seconds"
    start(task_name)
    try:
        raw = args.get("seconds", 1)
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"'seconds' must be a number, got {raw!r}")

        if seconds < 0:
            raise ValueError(f"'seconds' must be ≥ 0, got {seconds}")
        if seconds > _MAX_WAIT:
            raise ValueError(
                f"'seconds' must be ≤ {_MAX_WAIT} (5 min safety cap), got {seconds}. "
                f"Use multiple wait steps for longer pauses."
            )

        logger.info(f"Waiting {seconds:.1f}s…")
        time.sleep(seconds)
        logger.info(f"✅ Waited {seconds:.1f}s")
        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass

"""
System power management — shutdown, restart, suspend, hibernate.

Decision flow:
  1. Sanity-check: valid action required
  2. Warn the user before destructive actions (shutdown / restart)
  3. Try the primary command; fall back to the secondary if it fails
  4. Hibernate has no fallback — only one standard tool

Args:
    action (str): "shutdown" | "poweroff" | "restart" | "reboot"
               | "suspend" | "sleep" | "hibernate"
"""
import time

from loguru import logger

from core.logger import finish, notify, start
from core.shell_fallback import try_commands

_VALID_ACTIONS = {"shutdown", "poweroff", "restart", "reboot", "suspend", "sleep", "hibernate"}

# Grace period before an irreversible power action actually runs. This is
# NOT a full interactive confirmation (the agent has no reliable way to
# accept a "cancel" keypress mid-task) — it's a real, honest safety margin:
# a loud, hard-to-miss critical notification + console warning, with enough
# time for a human who sees it to kill the process (Ctrl+C) if this wasn't
# actually intended. Previously this was just a print() statement with zero
# delay — easy to miss, zero time to react.
_GRACE_SECONDS = 5

# (primary_cmd, fallback_cmd_or_None)
_ACTION_MAP: dict[str, tuple[list[str], list[str] | None]] = {
    "shutdown":  (["systemctl", "poweroff"],  ["shutdown", "-h", "now"]),
    "poweroff":  (["systemctl", "poweroff"],  ["shutdown", "-h", "now"]),
    "restart":   (["systemctl", "reboot"],    ["reboot"]),
    "reboot":    (["systemctl", "reboot"],    ["reboot"]),
    "suspend":   (["systemctl", "suspend"],   ["pm-suspend"]),
    "sleep":     (["systemctl", "suspend"],   ["pm-suspend"]),
    "hibernate": (["systemctl", "hibernate"], None),
}

_DESTRUCTIVE = {"shutdown", "poweroff", "restart", "reboot"}


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "system_power"
    start(task_name)
    try:
        action = str(args.get("action", "")).strip().lower()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not action:
            raise ValueError(
                f"'action' is required. Valid: {sorted(_VALID_ACTIONS)}"
            )
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown action {action!r}. Valid: {sorted(_VALID_ACTIONS)}"
            )

        # ── Warn before destructive actions ───────────────────────────
        # SAFETY: explicit confirm=False lets a caller (or a future
        # confirmation-UI layer) abort before anything irreversible happens.
        if action in _DESTRUCTIVE and args.get("confirm", True) is False:
            logger.info(f"Power action {action!r} cancelled via confirm=False")
            finish("error", task_name)
            return False

        if action in _DESTRUCTIVE:
            print(
                f"\n⚠️  WARNING: About to {action.upper()} the system in "
                f"{_GRACE_SECONDS} seconds. SAVE ALL OPEN WORK NOW.\n"
                f"   Press Ctrl+C now to cancel.\n"
            )
            logger.warning(f"Destructive power action requested: {action!r} — {_GRACE_SECONDS}s grace period")
            notify(
                f"System will {action} in {_GRACE_SECONDS}s — save your work! (Ctrl+C in terminal to cancel)",
                critical=True,
            )
            time.sleep(_GRACE_SECONDS)

        primary, fallback = _ACTION_MAP[action]
        candidates = [primary] if fallback is None else [primary, fallback]

        # ── Try primary, then fallback ──────────────────────────────────────────
        if try_commands(candidates):
            logger.info(f"✅ Power action {action!r} succeeded")
            notify(f"Power: {action}")
            finish("success", task_name)
            return True

        if fallback is None:
            raise RuntimeError(
                f"Power action {action!r} failed — '{primary[0]}' is the only supported tool "
                "and it did not succeed."
            )

        raise RuntimeError(
            f"Power action {action!r} failed — neither '{primary[0]}' nor "
            f"'{fallback[0]}' succeeded."
        )

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"action": "suspend"}, r)
    cleanup(r)

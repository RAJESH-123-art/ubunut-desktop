"""
Control system volume via pactl (primary) or amixer (fallback).

Decision flow:
  1. Sanity-check action value
  2. Try pactl — modern PulseAudio / PipeWire tool
  3. Fall back to amixer if pactl is unavailable or fails
  4. Abort with clear message if both tools are missing

Args:
    action (str): "up" | "down" | "mute" | "unmute" | "set". Default: "up".
    level (str):  Numeric percentage string, e.g. "50". Used only for action="set".
"""
from loguru import logger

from core.logger import finish, notify, start
from core.shell_fallback import try_commands

_VALID_ACTIONS = {"up", "down", "mute", "unmute", "set"}


def _pactl_cmd(action: str, level: str) -> list[str]:
    """Build the pactl argv for the given action."""
    sink = "@DEFAULT_SINK@"
    if action == "up":
        return ["pactl", "set-sink-volume", sink, "+10%"]
    if action == "down":
        return ["pactl", "set-sink-volume", sink, "-10%"]
    if action == "set":
        return ["pactl", "set-sink-volume", sink, f"{level}%"]
    if action == "mute":
        return ["pactl", "set-sink-mute", sink, "1"]    # explicit ON
    return ["pactl", "set-sink-mute", sink, "0"]        # explicit OFF (unmute)


def _amixer_cmd(action: str, level: str) -> list[str]:
    """Build the amixer argv for the given action."""
    if action == "up":
        return ["amixer", "set", "Master", "10%+"]
    if action == "down":
        return ["amixer", "set", "Master", "10%-"]
    if action == "set":
        return ["amixer", "set", "Master", f"{level}%"]
    if action == "mute":
        return ["amixer", "set", "Master", "mute"]
    return ["amixer", "set", "Master", "unmute"]        # unmute


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "volume_control"
    start(task_name)
    try:
        action = str(args.get("action", "up")).strip().lower()
        level  = str(args.get("level",  "50")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown action {action!r}. Valid: {sorted(_VALID_ACTIONS)}"
            )
        if action == "set" and not level.isdigit():
            raise ValueError(f"'level' must be a numeric string for action='set', got {level!r}")

        logger.info(f"Volume action={action!r}" + (f" level={level}%" if action == "set" else ""))

        # ── Try pactl first, then amixer ──────────────────────────────────────
        candidates = [_pactl_cmd(action, level), _amixer_cmd(action, level)]
        if try_commands(candidates):
            logger.info(f"✅ Volume {action}")
            notify(f"Volume: {action}" + (f" → {level}%" if action == "set" else ""))
            finish("success", task_name)
            return True

        raise RuntimeError(
            "Volume control failed — neither 'pactl' nor 'amixer' is available or succeeded.\n"
            "Install PulseAudio/PipeWire (pactl) or ALSA utils (amixer)."
        )

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"action": "up"}, r)
    cleanup(r)

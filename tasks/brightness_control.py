"""
Control screen brightness via brightnessctl (primary) or xrandr (fallback).

Decision flow:
  1. Sanity-check action value
  2. Try brightnessctl — works on most modern Linux desktops
  3. Fall back to xrandr: detect connected display, then adjust gamma
  4. Abort with clear message if both tools fail

Args:
    action (str): "up" | "down" | "set". Default: "up".
    level (str):  Numeric percentage string, e.g. "70". Used only for action="set".
                  For xrandr fallback, converted to 0.0–1.0 range.
"""
import shutil
import subprocess

from loguru import logger

from core.logger import finish, notify, start
from core.shell_fallback import try_commands

_VALID_ACTIONS = {"up", "down", "set"}


def _run(cmd: list[str], timeout: int = 10) -> bool:
    """Run a command and return True if it exits with code 0."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.debug(f"{cmd[0]} stderr: {result.stderr.strip()}")
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"Command {cmd} failed: {exc}")
        return False


def _brightnessctl(action: str, level: str) -> bool:
    """Adjust brightness with brightnessctl. Returns True on success."""
    if action == "up":
        cmd = ["brightnessctl", "set", "+10%"]
    elif action == "down":
        cmd = ["brightnessctl", "set", "10%-"]
    else:  # set
        cmd = ["brightnessctl", "set", f"{level}%"]

    logger.debug(f"brightnessctl cmd: {' '.join(cmd)}")
    return try_commands([cmd])


def _detect_display() -> str | None:
    """Return the name of the first connected display via xrandr, or None."""
    if not shutil.which("xrandr"):
        return None
    try:
        result = subprocess.run(
            ["xrandr"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if " connected" in line:
                display = line.split()[0]
                logger.debug(f"Detected display: {display}")
                return display
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"xrandr display detection failed: {exc}")
    return None


def _get_xrandr_brightness(display: str) -> float:
    """Read current xrandr brightness for the display (0.0–1.0). Defaults to 1.0."""
    try:
        result = subprocess.run(
            ["xrandr", "--verbose"], capture_output=True, text=True, timeout=10
        )
        in_display = False
        for line in result.stdout.splitlines():
            if display in line and " connected" in line:
                in_display = True
            if in_display and line.strip().lower().startswith("brightness:"):
                return float(line.split(":")[-1].strip())
    except Exception as exc:
        logger.debug(f"xrandr brightness read failed: {exc}")
    return 1.0


def _xrandr(action: str, level: str) -> bool:
    """Adjust brightness with xrandr --brightness using relative deltas."""
    display = _detect_display()
    if not display:
        logger.debug("xrandr: no connected display detected")
        return False

    if action == "set":
        try:
            brightness = round(int(level) / 100, 2)
        except (ValueError, ZeroDivisionError):
            logger.debug(f"xrandr: invalid level {level!r}")
            return False

        cmd = ["xrandr", "--output", display, "--brightness", str(brightness)]
        logger.debug(f"xrandr cmd: {' '.join(cmd)}")
        return try_commands([cmd])

    # Read current brightness and apply a relative ±0.10 delta. This is a
    # stateful read-modify-write that a static candidate list can't express,
    # so it keeps running directly via _run() instead of try_commands().
    current = _get_xrandr_brightness(display)
    delta   = +0.10 if action == "up" else -0.10
    brightness = round(max(0.10, min(1.0, current + delta)), 2)
    logger.debug(f"xrandr: current={current:.2f} delta={delta:+.2f} → {brightness:.2f}")

    cmd = ["xrandr", "--output", display, "--brightness", str(brightness)]
    logger.debug(f"xrandr cmd: {' '.join(cmd)}")
    return _run(cmd)


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "brightness_control"
    start(task_name)
    try:
        action = str(args.get("action", "up")).strip().lower()
        level  = str(args.get("level",  "70")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown action {action!r}. Valid: {sorted(_VALID_ACTIONS)}"
            )
        if action == "set":
            if not level.isdigit():
                raise ValueError(f"'level' must be a numeric string for action='set', got {level!r}")
            if not 0 <= int(level) <= 100:
                raise ValueError(f"'level' must be between 0 and 100, got {level!r}")

        logger.info(f"Brightness action={action!r}" + (f" level={level}%" if action == "set" else ""))

        # ── Try brightnessctl first, then xrandr ──────────────────────────────
        if _brightnessctl(action, level):
            logger.info(f"✅ Brightness {action} via brightnessctl")
            notify(f"Brightness: {action}" + (f" → {level}%" if action == "set" else ""))
            finish("success", task_name)
            return True

        if _xrandr(action, level):
            logger.info(f"✅ Brightness {action} via xrandr")
            notify(f"Brightness: {action}" + (f" → {level}%" if action == "set" else ""))
            finish("success", task_name)
            return True

        raise RuntimeError(
            "Brightness control failed — neither 'brightnessctl' nor 'xrandr' succeeded.\n"
            "Install brightnessctl or ensure xrandr is available with a connected display."
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

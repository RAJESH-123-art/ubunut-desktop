import datetime
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

from loguru import logger

# ── Paths ─────────────────────────────────────────────────────────────────────
LOGS_DIR = Path(__file__).parent.parent / "logs" / "screenshots"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
log_file = Path(__file__).parent.parent / "logs" / "automation.log"

logger.add(
    log_file,
    format="{time} | {level} | {name} | {message}",
    rotation="10 MB",
    retention="7 days",
    enqueue=True,
)

IS_WAYLAND = os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland"


def get_system_info() -> Dict[str, Any]:
    """Collect system metadata for debugging context."""
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python_ver": platform.python_version(),
        "desktop_env": os.getenv("XDG_CURRENT_DESKTOP", "unknown"),
        "session_type": os.getenv("XDG_SESSION_TYPE", "unknown"),
    }


def _screenshot_gnome(path: Path) -> bool:
    """GNOME Wayland screenshot via gnome-screenshot."""
    if not shutil.which("gnome-screenshot"):
        return False
    try:
        result = subprocess.run(
            ["gnome-screenshot", "-f", str(path)],
            capture_output=True, text=True, timeout=10,
            check=False,  # returncode checked below
        )
        return result.returncode == 0 and path.exists()
    except Exception as exc:
        logger.debug(f"gnome-screenshot failed: {exc}")
        return False


def _screenshot_grim(path: Path) -> bool:
    """Wayland screenshot via grim (wlroots compositors: Sway, Hyprland…)."""
    if not shutil.which("grim"):
        return False
    try:
        result = subprocess.run(
            ["grim", str(path)],
            capture_output=True, text=True, timeout=10,
            check=False,  # returncode checked below
        )
        return result.returncode == 0 and path.exists()
    except Exception as exc:
        logger.debug(f"grim failed: {exc}")
        return False


def _screenshot_scrot(path: Path) -> bool:
    """X11 / XWayland screenshot via scrot."""
    if not shutil.which("scrot"):
        return False
    try:
        result = subprocess.run(
            ["scrot", str(path)],
            capture_output=True, text=True, timeout=10,
            check=False,  # returncode checked below
        )
        return result.returncode == 0 and path.exists()
    except Exception as exc:
        logger.debug(f"scrot failed: {exc}")
        return False


def _screenshot_mss(path: Path, region: Dict[str, int] | None = None) -> bool:
    """Screenshot via mss (works on X11 / XWayland)."""
    try:
        from mss import mss
        from PIL import Image
        with mss() as sct:
            monitor = region if region else sct.monitors[0]
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
            img.save(str(path))
        return path.exists()
    except Exception as exc:
        logger.debug(f"mss failed: {exc}")
        return False


def take_screenshot(name: str | None = None, region: Dict[str, int] | None = None) -> str:
    """
    Capture the screen and return the absolute path to the saved PNG.

    Strategy (in order):
      1. gnome-screenshot  — GNOME Wayland (full-screen only)
      2. grim              — wlroots-based Wayland compositors
      3. scrot             — X11 / XWayland
      4. mss               — X11 / XWayland fallback
    """
    # Microsecond precision + a short random suffix -- second-granularity
    # timestamps alone collide when multiple screenshots are taken within
    # the same second (e.g. parallel DAG nodes), silently overwriting each
    # other while every individual call still reports success. Verified
    # live: a 3-node parallel "screenshot" DAG produced only 1 file on disk
    # despite the DAG reporting all 3 nodes done.
    import os as _os
    timestamp = datetime.datetime.now(tz=None).astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")
    unique_suffix = _os.urandom(3).hex()
    base_name = (name or "screenshot").replace(" ", "_")
    screenshot_path = LOGS_DIR / f"{base_name}_{timestamp}_{unique_suffix}.png"

    # Region capture MUST be tried first when a region is given -- gnome-screenshot
    # and grim are full-screen-only and silently IGNORE the region entirely, so
    # trying them first (as this used to) means a region request always came back
    # as a full-screen image whenever they succeeded, with no error or warning.
    # Verified live: requesting a 400x300 region returned a full 1366x768 image.
    success = False
    if region:
        success = _screenshot_mss(screenshot_path, region)
        if not success:
            logger.warning(
                "Region capture via mss failed -- falling back to a full-screen "
                "screenshot (region will NOT be honored)"
            )

    if not success and IS_WAYLAND:
        # Try Wayland-native tools (full-screen only)
        success = _screenshot_gnome(screenshot_path) or _screenshot_grim(screenshot_path)

    # Fallback: X11 / XWayland tools
    if not success:
        success = _screenshot_scrot(screenshot_path) or _screenshot_mss(screenshot_path)

    if success:
        logger.info(f"Screenshot saved: {screenshot_path}")
        return str(screenshot_path)
    else:
        logger.error("All screenshot methods failed")
        return ""


def log_action(action: str, window: str | None = None,
               take_shoot: bool = True, extras: Dict[str, Any] | None = None) -> None:
    """Unified logging with optional screenshot and metadata."""
    meta: Dict[str, Any] = {"action": action, "window": window}
    if extras:
        meta.update(extras)
    logger.info(json.dumps(meta, ensure_ascii=False, default=str))
    if take_shoot:
        path = take_screenshot(name=action.replace(" ", "_"))
        logger.info(f"Action screenshot: {path}")


def notify(message: str, critical: bool = False, title: str = "Automation"):
    """Desktop notification — works on both X11 and Wayland via notify-send."""
    urgency = "critical" if critical else "normal"
    try:
        result = subprocess.run(
            ["notify-send", "-t", "5000", "-u", urgency, title, message],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            logger.warning(f"Desktop notification failed: {result.stderr}")
    except FileNotFoundError:
        logger.warning("notify-send not found; install libnotify-bin")
    except Exception as exc:
        logger.error(f"Notification error: {exc}")


def telegram(message: str, silent: bool = False):
    """Optional Telegram notification (requires TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        logger.debug("Telegram not configured, skipping")
        return
    try:
        from telegram import Bot  # type: ignore[import-not-found]
        bot = Bot(token=token)
        bot.send_message(chat_id=chat_id, text=message, disable_notification=silent)
        logger.info("Telegram notification sent")
    except Exception as exc:
        logger.error(f"Telegram error: {exc}")


def start(task_name: str, task_desc: str | None = None) -> Dict[str, Any]:
    """Log task start with context."""
    meta: Dict[str, Any] = {
        "task": task_name,
        "status": "started",
        "ts": datetime.datetime.now(tz=None).astimezone().isoformat(),
    }
    if task_desc:
        meta["description"] = task_desc
    logger.info(json.dumps(meta, ensure_ascii=False))
    return meta


def finish(status: str, task_name: str, err: BaseException | None = None) -> Dict[str, Any]:
    """Log task completion or error."""
    meta: Dict[str, Any] = {
        "task": task_name,
        "status": status,
        "ts": datetime.datetime.now(tz=None).astimezone().isoformat(),
    }
    if err:
        meta["error"] = str(err)
        meta["error_type"] = type(err).__name__
    logger.info(json.dumps(meta, ensure_ascii=False))
    return meta

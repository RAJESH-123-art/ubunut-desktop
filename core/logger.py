import os, datetime, json, platform
from pathlib import Path
from typing import Optional, Dict, Any
from mss import mss
from loguru import logger
import os

# Central config path
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

def get_system_info() -> Dict[str, Any]:
    """Collect system metadata for debugging context."""
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python_ver": platform.python_version(),
        "desktop_env": os.getenv("XDG_CURRENT_DESKTOP", "unknown"),
        "session_type": os.getenv("XDG_SESSION_TYPE", "unknown"),
    }

def take_screenshot(name: Optional[str] = None, region: Optional[Dict[str, int]] = None) -> str:
    """Capture screen or region. Returns absolute path to saved image."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = name or "screenshot"
    filename = f"{base_name}_{timestamp}.png"
    screenshot_path = LOGS_DIR / filename
    
    # Check for Wayland and use grim if available
    if os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland":
        try:
            if region:
                # For regions with grim we need different approach (slurp)
                grim_cmd = f"grim -g '{region["width"]}x{region["height"]}+{region["left"]}+{region["top"]}' '{screenshot_path}'"
            else:
                grim_cmd = f"grim '{screenshot_path}'"
            os.system(grim_cmd)
            logger.info(f"Screenshot saved (Wayland): {screenshot_path}")
            return str(screenshot_path)
        except Exception as e:
            logger.warning(f"grim failed, falling back to mss: {e}")
    
    # Fallback to mss (works on X11)
    try:
        with mss() as sct:
            if region:
                img = sct.grab(region)
            else:
                img = sct.grab(sct.monitors[0])
            sct.to_png(img, output=str(screenshot_path))
        logger.info(f"Screenshot saved: {screenshot_path}")
        return str(screenshot_path)
    except Exception as exc:
        logger.error(f"Screenshot failed: {exc}")
        return ""

def log_action(action: str, window: Optional[str] = None, 
              take_shoot: bool = True, extras: Optional[Dict[str, Any]] = None) -> None:
    """Unified logging with optional screenshot and metadata."""
    meta = {"action": action, "window": window}
    if extras:
        meta.update(extras)
    logger.info(json.dumps(meta, ensure_ascii=False, default=str))
    if take_shoot:
        path = take_screenshot(name=action.replace(" ", "_"))
        logger.info(f"Action screenshot: {path}")

def notify(message: str, critical: bool = False, title: str = "Automation"):
    """Desktop notification using system (works on KDE/GNOME/etc)."""
    urgency = "critical" if critical else "normal"
    try:
        import subprocess
        result = subprocess.run(["notify-send", "-t", "5000", "-u", urgency, title, message],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.warning(f"Desktop notification failed: {result.stderr}")
    except Exception as exc:
        logger.error(f"Notification error: {exc}")

# Optional Telegram notifications (if configured in .env)
def telegram(message: str, silent: bool = False):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        logger.debug("Telegram not configured, skipping")
        return
    try:
        from telegram import Bot
        bot = Bot(token=token)
        bot.send_message(chat_id=chat_id, text=message, disable_notification=silent)
        logger.info("Telegram notification sent")
    except Exception as exc:
        logger.error(f"Telegram error: {exc}")

def start(task_name: str, task_desc: Optional[str] = None) -> Dict[str, Any]:
    """Log task start with context for subsequent logs."""
    meta = {"task": task_name, "status": "started", "ts": datetime.datetime.now().isoformat()}
    if task_desc:
        meta["description"] = task_desc
    logger.info(json.dumps(meta, ensure_ascii=False))
    return meta

def finish(status: str, task_name: str, err: Optional[BaseException] = None) -> Dict[str, Any]:
    """Log task completion or error."""
    meta = {"task": task_name, "status": status, "ts": datetime.datetime.now().isoformat()}
    if err:
        meta["error"] = str(err)
        meta["error_type"] = type(err).__name__
    logger.info(json.dumps(meta, ensure_ascii=False))
    return meta
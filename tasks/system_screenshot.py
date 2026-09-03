"""
Take a desktop screenshot and save to logs/screenshots/.

Decision flow (Klavaro pattern):
  1. Build filename from name arg or timestamp
  2. Call take_screenshot() — tries gnome-screenshot → grim → scrot → mss
  3. VERIFY: confirm file exists and size > 0
  4. Abort with clear message if file missing or empty

Args:
    name (str): Optional filename prefix (default "desktop_screenshot_<timestamp>")
"""
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from core.logger import finish, notify, start, take_screenshot


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "system_screenshot"
    start(task_name)
    try:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        name      = str(args.get("name") or f"desktop_screenshot_{timestamp}")

        path_str = take_screenshot(name=name)

        # ── Verify: file must exist and be non-empty ───────────────────────────
        if not path_str:
            logger.error(
                "Screenshot failed — take_screenshot() returned empty path.\n"
                "Check that gnome-screenshot, grim, scrot, or mss is installed."
            )
            finish("error", task_name)
            return False

        path = Path(path_str)
        if not path.exists():
            logger.error(f"Screenshot path reported but file missing: {path_str}")
            finish("error", task_name)
            return False

        size = path.stat().st_size
        if size == 0:
            logger.error(f"Screenshot file is empty (0 bytes): {path_str}")
            finish("error", task_name)
            return False

        logger.info(f"✅ Screenshot saved: {path_str} ({size:,} bytes)")
        notify(f"Screenshot: {path.name}")
        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({}, r)
    cleanup(r)

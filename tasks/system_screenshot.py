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
from pathlib import Path

from loguru import logger

from core.logger import finish, notify, start, take_screenshot
from core.task_contract import TaskResult


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> TaskResult:
    task_name = "system_screenshot"
    start(task_name)
    try:
        # take_screenshot() already stamps a unique, microsecond-precision
        # timestamp + random suffix onto whatever base name it's given --
        # pre-computing our own timestamp here was redundant AND buggy (this
        # used UTC while take_screenshot()'s own timestamp uses local time,
        # producing confusing double-timestamped filenames like
        # "..._17-03-15_...22-33-15.png" that were actually the same instant).
        name = str(args.get("name") or "desktop_screenshot")

        path_str = take_screenshot(name=name)

        # ── Verify: file must exist and be non-empty ───────────────────────────
        if not path_str:
            logger.error(
                "Screenshot failed — take_screenshot() returned empty path.\n"
                "Check that gnome-screenshot, grim, scrot, or mss is installed."
            )
            finish("error", task_name)
            return TaskResult(False, error="Screenshot backend returned no path")

        path = Path(path_str)
        if not path.exists():
            logger.error(f"Screenshot path reported but file missing: {path_str}")
            finish("error", task_name)
            return TaskResult(False, error=f"Screenshot file missing: {path_str}")

        size = path.stat().st_size
        if size == 0:
            logger.error(f"Screenshot file is empty (0 bytes): {path_str}")
            finish("error", task_name)
            return TaskResult(False, error=f"Screenshot file is empty: {path_str}")

        logger.info(f"✅ Screenshot saved: {path_str} ({size:,} bytes)")
        notify(f"Screenshot: {path.name}")
        finish("success", task_name)
        return TaskResult(
            True,
            data={"path": str(path), "bytes": size},
            evidence=[{"kind": "file", "path": str(path), "bytes": size}],
        )

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({}, r)
    cleanup(r)

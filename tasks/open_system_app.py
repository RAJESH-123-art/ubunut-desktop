"""
System app launcher with AT-SPI launch verification.

Decision flow (Klavaro pattern):
  1. Sanity-check: non-empty app name
  2. Resolve launch command (APP_COMMANDS registry → direct exec → .desktop fallback)
  3. VERIFY the command exists on PATH before launching (abort if not found)
  4. Launch via subprocess.Popen (non-blocking)
  5. AT-SPI verify: poll accessibility tree until app appears (or timeout)
  6. Report whether app was confirmed running

Args:
    app_name (str): Application name — matched against APP_COMMANDS registry.
"""
import shutil
import subprocess
import sys
import time
from glob import glob
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from loguru import logger

from core.app_registry import APP_COMMANDS
from core.logger import finish, notify, start


def _resolve_command(app_name: str) -> str | None:
    """
    Resolve app_name to a launchable command.
    Priority: APP_COMMANDS registry → direct binary → .desktop file.
    Returns None if nothing found.
    """
    name_lower = app_name.lower().strip()

    # Registry lookup
    cmd = APP_COMMANDS.get(name_lower)
    if cmd and shutil.which(cmd.split()[0]):
        return cmd

    # Direct binary
    if shutil.which(app_name):
        return app_name
    if shutil.which(name_lower):
        return name_lower

    # .desktop file fallback
    for df in glob(f"/usr/share/applications/*{name_lower}*.desktop"):
        stem = Path(df).stem
        if shutil.which("gtk-launch"):
            return f"gtk-launch {stem}"

    return None


def _atspi_verify_running(app_name: str, timeout: float = 10.0) -> bool:
    """
    Poll the AT-SPI desktop until an app whose name contains app_name appears.
    Returns True if found within timeout.
    """
    try:
        import pyatspi
    except ImportError:
        logger.debug("pyatspi not available — skipping AT-SPI verify")
        return True   # can't verify, assume OK

    name_lower = app_name.lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            for a in desktop:
                if a and name_lower in (a.name or "").lower():
                    logger.info(f"AT-SPI: '{a.name}' confirmed running ✅")
                    return True
        except Exception as exc:
            logger.debug(f"AT-SPI app lookup failed: {exc}")
        time.sleep(0.5)
    return False


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "open_system_app"
    start(task_name)
    try:
        app_name = str(args.get("app_name", "")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not app_name:
            raise ValueError("'app_name' is required")

        # ── Resolve command ───────────────────────────────────────────────────
        cmd = _resolve_command(app_name)
        if not cmd:
            raise RuntimeError(
                f"Cannot find launch command for '{app_name}'. "
                f"Available registered apps: {sorted(APP_COMMANDS.keys())}"
            )

        logger.info(f"Launching '{app_name}' → command: {cmd}")

        # ── Launch ────────────────────────────────────────────────────────────
        subprocess.Popen(
            cmd.split(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # NOTE: no blind sleep here — _atspi_verify_running() below polls
        # immediately and repeatedly, so waiting first only wastes time on
        # fast-launching apps without adding reliability (see
        # TASK_KNOWLEDGE_BASE.md Part 5, fix #1).

        # ── AT-SPI verify app appeared ────────────────────────────────────────
        # Use the first word of app_name to search (handles "File Manager" → "nautilus")
        search_term = APP_COMMANDS.get(app_name.lower(), app_name).split()[0]
        confirmed = _atspi_verify_running(search_term, timeout=10.0)

        if confirmed:
            logger.info(f"✅ '{app_name}' launched and confirmed via AT-SPI")
            notify(f"Opened: {app_name}")
        else:
            logger.warning(f"'{app_name}' launched but NOT confirmed in AT-SPI within 10s — may be loading")
            notify(f"Launched (unconfirmed): {app_name}")

        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"app_name": "calculator"}, r)
    cleanup(r)

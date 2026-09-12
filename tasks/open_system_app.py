"""
System app launcher with AT-SPI launch verification.

Decision flow (Klavaro pattern):
  1. Sanity-check: non-empty app name
  2. Resolve launch command:
       a. APP_COMMANDS registry   — fast path for well-known apps
       b. direct binary on PATH
       c. core.app_finder         — full index of every installed .desktop
                                     app (system, user, snap, flatpak) —
                                     this is what makes ANY installed Ubuntu
                                     application openable, not just the
                                     curated registry
       d. legacy filename glob fallback
  3. Launch via subprocess.Popen (non-blocking)
  4. AT-SPI verify: poll accessibility tree until app appears (or timeout)
  5. Report whether app was confirmed running

Args:
    app_name (str): Application name — matched against APP_COMMANDS first,
        then against every installed application's Name/GenericName/id.
"""
import shlex
import shutil
import subprocess
import sys
from glob import glob
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from loguru import logger

from core.app_registry import APP_COMMANDS
from core.logger import finish, notify, start


def _resolve_launch(app_name: str) -> tuple[str | None, str]:
    """
    Resolve app_name to a launchable command.

    Priority:
      1. APP_COMMANDS registry — fast path for a handful of well-known apps
      2. Direct binary on PATH
      3. core.app_finder — full index of EVERY installed app (system, user,
         snap, flatpak .desktop files), so any installed Ubuntu application
         can be opened, not just the curated APP_COMMANDS list
      4. Legacy glob fallback over /usr/share/applications by filename

    Returns (command, verify_name) where verify_name is the best string to
    search the AT-SPI tree for afterward. (None, "") if nothing found.
    """
    name_lower = app_name.lower().strip()

    # 1. Registry lookup
    cmd = APP_COMMANDS.get(name_lower)
    if cmd and shutil.which(cmd.split()[0]):
        return cmd, cmd.split()[0]

    # 2. Direct binary
    if shutil.which(app_name):
        return app_name, app_name
    if shutil.which(name_lower):
        return name_lower, name_lower

    # 3. Full desktop-application index (covers everything installed)
    from core.app_finder import find_app
    found = find_app(app_name)
    if found:
        launch = found.launch_command()
        if launch:
            logger.info(f"app_finder: resolved '{app_name}' -> '{found.name}' ({launch})")
            verify_name = found.wm_class or found.name.split()[0]
            return launch, verify_name

    # 4. Legacy glob fallback
    for df in glob(f"/usr/share/applications/*{name_lower}*.desktop"):
        stem = Path(df).stem
        if shutil.which("gtk-launch"):
            return f"gtk-launch {stem}", stem

    return None, ""


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
        cmd, verify_name = _resolve_launch(app_name)
        if not cmd:
            from core.app_finder import list_app_names
            raise RuntimeError(
                f"Cannot find launch command for '{app_name}'. "
                f"Not in curated registry ({sorted(APP_COMMANDS.keys())}) "
                f"and no matching installed application found. "
                f"Try 'agent.py --list-apps' to see all {len(list_app_names())} "
                f"discovered installed applications."
            )

        logger.info(f"Launching '{app_name}' → command: {cmd}")

        # ── Launch ────────────────────────────────────────────────────────────
        # shlex.split (not str.split) so Exec= commands with quoted arguments
        # (common in .desktop files, e.g. `env FOO="bar baz" app`) still work.
        subprocess.Popen(
            shlex.split(cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # NOTE: no blind sleep here — wait_until_running() below polls
        # immediately and repeatedly, so waiting first only wastes time on
        # fast-launching apps without adding reliability (see
        # TASK_KNOWLEDGE_BASE.md Part 5, fix #1).

        # ── Verify app appeared (AT-SPI + process, shared with core.app_state) ──
        from core.app_state import wait_until_running
        search_term = (verify_name or app_name).split()[0]
        confirmed = wait_until_running(search_term, timeout=10.0)

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

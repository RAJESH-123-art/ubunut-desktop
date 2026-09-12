"""
Application running-state detection.

Extracted out of tasks/open_system_app.py's private post-launch verification
so "is this app running?" / "what apps are running?" is a reusable, testable
capability -- usable by any task, workflow, DAG step, or natural-language
query, not just the moment right after launching something.

Two independent evidence sources (either one confirming is enough):
  1. AT-SPI accessibility tree -- the desktop-level app registry GNOME/GTK
     apps register into. Catches anything with an accessible UI.
  2. Process list (pgrep) -- catches background/headless processes and apps
     that don't expose a good AT-SPI name, using the real binary resolved
     via core.app_registry / core.app_finder so a fuzzy human name like
     "calculator" still matches the actual running process
     ("gnome-calculator").

Usage:
    from core.app_state import is_app_running, list_running_apps, wait_until_running

    is_app_running("spotify")
    wait_until_running("gnome-text-editor", timeout=10.0)   # poll after launching
    list_running_apps()                                     # -> ["Files", "Firefox", ...]
"""
from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from loguru import logger


def _atspi_running_apps() -> list[str]:
    """Return the names of every app currently registered in the AT-SPI desktop."""
    try:
        import pyatspi
        desktop = pyatspi.Registry.getDesktop(0)
        names: list[str] = []
        for app in desktop:
            if app is None:
                continue
            try:
                if app.name:
                    names.append(app.name)
            except Exception as exc:
                logger.debug(f"app_state: skipping unnamed AT-SPI app: {exc}")
                continue
        return names
    except Exception as exc:
        logger.debug(f"app_state: AT-SPI desktop enumeration failed: {exc}")
        return []


def _resolve_process_pattern(name: str) -> str:
    """
    Best-effort: turn a human app name into the pattern most likely to match
    its real running process -- the same resolution tasks/open_system_app.py
    uses to launch it in the first place (registry -> app_finder -> raw name).
    """
    from core.app_registry import APP_COMMANDS
    key = name.lower().strip()
    cmd = APP_COMMANDS.get(key)
    if cmd:
        return cmd.split()[0]

    try:
        from core.app_finder import find_app
        found = find_app(name)
        if found and found.exec_cmd:
            return found.exec_cmd.split()[0].split("/")[-1]
    except Exception as exc:
        logger.debug(f"app_state: app_finder lookup failed for {name!r}: {exc}")

    return name


def _process_running(pattern: str) -> bool:
    if not pattern:
        return False
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=5,
                            check=False)  # returncode is the answer
        return r.returncode == 0
    except Exception as exc:
        logger.debug(f"app_state: pgrep check failed for {pattern!r}: {exc}")
        return False


def _norm(s: str) -> str:
    """
    Strip spaces/hyphens/underscores before comparing app names. AT-SPI's
    app.name is very often a hyphenated binary name ("gnome-text-editor"),
    while callers naturally pass the human .desktop Name= ("text editor",
    with a space) -- a naive substring check between those never matches.
    Confirmed live: is_app_running("text editor") returned False for a
    genuinely running Text Editor because "text editor" (space) is not a
    substring of "gnome-text-editor" (hyphen).
    """
    return s.lower().replace(" ", "").replace("-", "").replace("_", "")


def is_app_running(name: str) -> bool:
    """
    Return True if an app matching `name` is currently running -- checked via
    BOTH AT-SPI (accessible apps) and the process list (resolved binary).
    Either one confirming is enough.
    """
    name_norm = _norm(name)
    if not name_norm:
        return False

    for app_name in _atspi_running_apps():
        if name_norm in _norm(app_name):
            return True

    pattern = _resolve_process_pattern(name)
    return _process_running(pattern)


def wait_until_running(name: str, timeout: float = 10.0, interval: float = 0.5) -> bool:
    """Poll is_app_running() until it's True or `timeout` seconds elapse."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_app_running(name):
            return True
        time.sleep(interval)
    return False


def list_running_apps() -> list[str]:
    """Return the de-duplicated, sorted names of every AT-SPI-visible running app."""
    names = {n for n in _atspi_running_apps() if n}
    return sorted(names)

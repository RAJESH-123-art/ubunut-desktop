"""
Warm local-Chrome CDP browser manager (Wayland-ready).

Default browser: Google Chrome (local, already logged-in profile copy).
Attaches over CDP so automation uses the user's REAL sessions (no QR/logins).

Chrome M136+ blocks CDP on the default profile, so we maintain a synced COPY
of the real profile at ~/.config/desktop_automation/chrome_profile.
The copy is refreshed only when stale (cache!), not on every run.
"""

import shutil
import socket
import subprocess
import time
from pathlib import Path

from loguru import logger

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
AUTOMATION_DIR = Path.home() / ".config" / "desktop_automation"
CHROME_PROFILE_COPY = AUTOMATION_DIR / "chrome_profile"
PROFILE_STALE_SECONDS = 7 * 24 * 3600  # re-sync weekly at most
SYNC_EXCLUDES = [
    "Cache", "Cache_Data", "Code Cache", "GPUCache", "GrShaderCache",
    "ShaderCache", "SingletonLock", "SingletonSocket", "SingletonCookie",
]


def _find_chrome() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "brave-browser", "chromium"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _cdp_alive(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", CDP_PORT), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _profile_copy_stale() -> bool:
    """True if the profile copy is missing or older than the stale window."""
    if not CHROME_PROFILE_COPY.exists():
        return True
    marker = AUTOMATION_DIR / ".profile_synced"
    if not marker.exists():
        return True
    age = time.time() - marker.stat().st_mtime
    return age > PROFILE_STALE_SECONDS


def sync_profile(force: bool = False) -> bool:
    """
    Copy the real Chrome profile into the automation copy (cached).
    Refuses to sync while normal Chrome is running rather than terminating it.
    Returns True if a sync happened.
    """
    real = Path.home() / ".config" / "google-chrome"
    if not real.exists():
        logger.warning("No real Chrome profile found; skipping sync")
        return False
    if not force and not _profile_copy_stale():
        logger.debug("Profile copy fresh — skipping sync (cached)")
        return False

    # Copying a live profile is unsafe, but killing every Chrome process can
    # destroy unsaved user work. Require the user to close Chrome explicitly.
    running = subprocess.run(["pgrep", "-f", "google-chrome|chrome"], capture_output=True, check=False)
    if running.returncode == 0:
        logger.warning("Chrome is running; close it before syncing the automation profile")
        return False

    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a"]
    for ex in SYNC_EXCLUDES:
        cmd += ["--exclude", ex]
    cmd += [str(real) + "/", str(CHROME_PROFILE_COPY) + "/"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error(f"Profile sync failed: {result.stderr}")
        return False
    _ = (AUTOMATION_DIR / ".profile_synced").write_text(str(time.time()))
    logger.info("Chrome profile synced (logins preserved)")
    return True


def ensure_chrome_cdp() -> str:
    """
    Make sure Chrome is running with CDP enabled; return the CDP URL.
    Reuses the running instance (warm browser = seconds faster).
    """
    if _cdp_alive():
        logger.debug(f"Reusing warm Chrome CDP on port {CDP_PORT}")
        return CDP_URL

    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("Chrome not found. Install: sudo apt install google-chrome-stable")

    if _profile_copy_stale():
        _ = sync_profile()

    logger.info("Launching Chrome with CDP (copied real profile)...")
    _ = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_PROFILE_COPY}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(20):
        if _cdp_alive():
            time.sleep(0.5)
            logger.info("Chrome CDP ready")
            return CDP_URL
        time.sleep(0.5)
    raise RuntimeError("Chrome CDP did not start. Close Chrome and retry.")


def close_cdp_chrome() -> bool:
    """
    Close the automation's dedicated CDP Chrome instance (port 9222).

    This is the automation-owned browser (separate user-data-dir + debug port),
    so it is safe to close after a task — it never touches the user's normal Chrome.
    Returns True if a close was performed.
    """
    if not _cdp_alive():
        return False
    try:
        _ = subprocess.run(
            ["pkill", "-f", f"remote-debugging-port={CDP_PORT}"],
            capture_output=True, check=False,
        )
        logger.info("Closed automation's CDP Chrome instance")
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Failed to close CDP Chrome: {exc}")
        return False


def open_url(url: str) -> bool:
    """Open a URL in the warm CDP Chrome (falls back to plain launch)."""
    try:
        _ = ensure_chrome_cdp()
    except (RuntimeError, OSError) as exc:
        logger.warning(f"CDP unavailable ({exc}); falling back to plain launch")
        chrome = _find_chrome() or "firefox"
        _ = subprocess.Popen(
            [chrome, url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        _ = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # leave the tab open for the user; do not close
    return True

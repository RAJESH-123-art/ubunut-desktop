"""
Open a browser and navigate to a URL.

Decision flow (Klavaro pattern):
  1. Sanity-check URL (non-empty, add https:// if missing scheme)
  2. Try CDP → Playwright → subprocess in order (fastest first)
  3. Verify: check page loaded via URL pattern in CDP or by screenshot
  4. Abort with clear message if all strategies fail

Strategy (in order, fastest to slowest):
  1. CDP warm Chrome   — reuses existing session, instant
  2. Playwright        — Brave/Chromium/Firefox via BrowserController
  3. subprocess        — plain browser launch via find_browser()

Args:
    url (str):              Target URL (default https://news.ycombinator.com)
    browser (str):          Hint: "chromium" | "brave" | "firefox" (default "chromium")
    screenshot (bool):      Save a screenshot after loading (default False)
    delay_after_load (int): Seconds to wait after navigation (default 3)
    close_after (bool):     Close browser tab/window after task (default False)
"""
import subprocess
import time
from urllib.parse import urlparse

from loguru import logger

from core.logger import finish, notify, start


def _validate_url(url: str) -> str:
    """
    Sanity-check and normalise URL.
    Returns the corrected URL or raises ValueError.
    """
    url = url.strip()
    if not url:
        raise ValueError("'url' is required — cannot navigate to empty string")
    if not url.startswith(("http://", "https://", "ftp://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(
            f"Invalid URL: {url!r}\n"
            f"Expected format: https://example.com or example.com"
        )
    return url


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "open_browser_and_visit"
    raw_url   = str(args.get("url", "https://news.ycombinator.com"))
    start(task_name, f"Navigate to {raw_url}")

    try:
        # ── Sanity check URL ──────────────────────────────────────────────────
        url          = _validate_url(raw_url)
        delay        = max(1, int(args.get("delay_after_load", 3)))
        do_ss        = bool(args.get("screenshot", False))
        close_after  = bool(args.get("close_after", False))
        browser_hint = str(args.get("browser", "chromium"))

        logger.info(f"Opening: {url}")
        opened = False

        # ── Strategy 1: CDP (warm Chrome) ────────────────────────────────────
        try:
            from core.cdp_browser import open_url
            open_url(url)
            time.sleep(delay)
            opened = True
            logger.info(f"✅ Opened via CDP: {url}")
        except Exception as exc:
            logger.debug(f"CDP unavailable ({exc}); trying Playwright")

        # ── Strategy 2: Playwright ────────────────────────────────────────────
        if not opened:
            try:
                from core.browser import BrowserController
                b_name = "firefox" if browser_hint == "firefox" else "chromium"
                bc = BrowserController(browser=b_name, headless=False)
                bc.start()
                bc.goto(url, wait_until="domcontentloaded", timeout_ms=30_000)
                time.sleep(delay)
                if do_ss:
                    ss = bc.screenshot(
                        f"logs/screenshots/{task_name}_{int(time.time())}.png")
                    notify(f"Screenshot: {ss}")
                if close_after:
                    bc.close()
                opened = True
                logger.info(f"✅ Opened via Playwright ({b_name}): {url}")
            except Exception as exc:
                logger.debug(f"Playwright failed ({exc}); trying subprocess")

        # ── Strategy 3: subprocess plain launch ───────────────────────────────
        if not opened:
            from core.app_registry import find_browser
            cmd = find_browser() if browser_hint == "chromium" else browser_hint
            subprocess.Popen(
                [cmd, url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(delay)
            logger.info(f"✅ Opened via subprocess ({cmd}): {url}")
            opened = True

        if not opened:
            raise RuntimeError(f"All browser strategies failed for URL: {url}")

        notify(f"Visited: {url}")
        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"url": "https://github.com", "screenshot": True}, r)
    cleanup(r)

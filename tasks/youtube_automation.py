"""
YouTube automation — search and play videos.

Decision flow (Klavaro pattern):
  1. Sanity-check: non-empty search query
  2. Try CDP → Playwright → GUI fallback in order
  3. Verify: confirm browser navigated to YouTube results page
  4. Abort with clear message if query is empty

Strategy (in order):
  1. CDP Chrome (warm, reuses logged-in session) — fastest
  2. Playwright + BrowserController (Brave)       — reliable DOM control
  3. GUI fallback (subprocess + xdotool clicks)   — no browser setup needed

Args:
    search_query (str): What to search for. Alias: query.
    play_first (bool):  Click the first result (default True).
"""
import subprocess
import time

from loguru import logger

from core.logger import finish, start


def setup() -> dict:
    """Try CDP → Playwright → GUI."""
    try:
        from core.cdp_browser import ensure_chrome_cdp
        ensure_chrome_cdp()
        logger.info("YouTube: using warm CDP Chrome")
        return {"mode": "cdp"}
    except Exception as exc:
        logger.debug(f"CDP unavailable ({exc}), trying Playwright")

    try:
        from core.browser_manager import ensure_brave_browser
        browser = ensure_brave_browser()
        logger.info("YouTube: using Playwright (Brave)")
        return {"browser": browser, "mode": "playwright"}
    except Exception as exc:
        logger.debug(f"Playwright unavailable ({exc}), using GUI fallback")

    logger.info("YouTube: using GUI fallback mode")
    return {"mode": "gui"}


def execute(args: dict, resources: dict) -> bool:
    """Search YouTube and optionally play the first result."""
    task_name = "youtube_automation"
    start(task_name)
    try:
        return _execute_inner(args, resources)
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def _execute_inner(args: dict, resources: dict) -> bool:
    """Inner execute body — wrapped by execute() for logging."""
    from core.logger import finish
    task_name = "youtube_automation"
    query = (args.get("search_query") or args.get("query", "")).strip()

    # ── Sanity check ───────────────────────────────────────────────────────────────────────────
    if not query:
        raise ValueError(
            "'search_query' is required — e.g. {'search_query': 'RRR Naatu Naatu'}"
        )

    play_first = bool(args.get("play_first", True))
    url  = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
    mode = resources.get("mode", "gui")

    logger.info(f"YouTube search: '{query}' via {mode}")
    logger.info(f"URL: {url}")

    # ── CDP path ──────────────────────────────────────────────────────────────
    if mode == "cdp":
        try:
            from core.cdp_browser import open_url
            result = open_url(url)
            if result:
                logger.info("✅ YouTube opened via CDP")
                return True
        except Exception as exc:
            logger.warning(f"CDP failed ({exc}); falling back to Playwright")
            mode = "playwright"

    # ── Playwright path ───────────────────────────────────────────────────────
    if mode == "playwright":
        browser = resources.get("browser")
        if browser:
            try:
                browser.goto(url, timeout_ms=30_000)
                time.sleep(3)

                # Verify: YouTube results loaded
                current = browser.page.url
                if "youtube.com" not in current:
                    logger.warning(f"Navigation may have failed — current URL: {current}")

                if play_first:
                    for sel in [
                        "ytd-video-renderer a#video-title",
                        "a#video-title",
                        "#contents ytd-video-renderer:first-child a",
                    ]:
                        try:
                            video = browser.page.wait_for_selector(sel, timeout=5_000)
                            if video:
                                video.click()
                                time.sleep(2)
                                logger.info("▶ First video clicked via Playwright ✅")
                                return True
                        except Exception as exc:
                            logger.debug(f"Video selector failed: {exc}")
                            continue
                    logger.warning("Could not find first video via Playwright — returning search page")

                logger.info("✅ YouTube search page loaded via Playwright")
                return True
            except Exception as exc:
                logger.warning(f"Playwright failed ({exc}); using GUI fallback")

    # ── GUI fallback ──────────────────────────────────────────────────────────
    from core.app_registry import find_browser
    from core.gui_controller import GUIController

    browser_cmd = find_browser()
    logger.info(f"YouTube GUI fallback: {browser_cmd} {url}")
    subprocess.Popen(
        [browser_cmd, url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(7)

    if play_first:
        gui = GUIController(safe_mode=False)
        w, h = gui.get_screen_size()
        # Click typical first video thumbnail positions
        for x, y in [
            (int(w * 0.25), int(h * 0.32)),
            (int(w * 0.30), int(h * 0.37)),
            (320, 280),
        ]:
            gui.click(x, y)
            time.sleep(1.0)

        # Tab to first video link
        gui.press("home")
        time.sleep(0.3)
        for _ in range(10):
            gui.press("tab")
            time.sleep(0.1)
        gui.press("enter")
        time.sleep(2)
        gui.press("k")   # YouTube: toggle play

    logger.info("✅ YouTube GUI automation done")
    finish("success", "youtube_automation")
    return True


def cleanup(resources: dict) -> None:
    # Browser managed by shared manager — do not close here
    pass

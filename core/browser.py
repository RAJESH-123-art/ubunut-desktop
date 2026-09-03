from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Literal

from loguru import logger
from playwright.sync_api import Browser, Page, sync_playwright

# Valid Playwright wait_until values
_WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
# Valid Playwright selector state values
_SelectorState = Literal["attached", "detached", "hidden", "visible"]


class BrowserController:
    """
    Thin wrapper around Playwright for Chromium/Firefox automation.
    Supports custom executable (e.g., Brave via BROWSER_EXECUTABLE env).
    """

    def __init__(self, browser: str = "chromium", headless: bool = False,
                 executable_path: str | None = None) -> None:
        self.browser_name = browser
        self.headless = headless
        self.executable_path = executable_path or os.getenv("BROWSER_EXECUTABLE")
        self._pw = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    def start(self) -> BrowserController:
        # Detect whether we're inside a running event loop.
        # If so, run sync_playwright in a dedicated thread to avoid conflicts.
        in_loop = False
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            pass

        if in_loop:
            t = threading.Thread(target=self._start_browser_sync)
            t.start()
            t.join()
        else:
            self._start_browser_sync()
        return self

    def _start_browser_sync(self) -> None:
        self._pw = sync_playwright().start()
        if self.browser_name == "firefox":
            self._browser = self._pw.firefox.launch(headless=self.headless)
        else:
            launch_kwargs: dict[str, Any] = {"headless": self.headless}
            if self.executable_path and os.path.isfile(self.executable_path):
                launch_kwargs["executable_path"] = self.executable_path
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._page = self._browser.new_page()
        logger.info(
            f"Browser started: {self.browser_name} "
            f"headless={self.headless} exec={self.executable_path}"
        )

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not started — call .start() first")
        return self._page

    def goto(self, url: str, wait_until: _WaitUntil = "load",
             timeout_ms: int = 30_000) -> None:
        self.page.goto(url, wait_until=wait_until, timeout=timeout_ms)

    def click(self, selector: str, timeout_ms: int = 30_000) -> None:
        self.page.click(selector, timeout=timeout_ms)

    def type(self, selector: str, text: str, delay_ms: int = 20,
             clear: bool = True) -> None:
        locator = self.page.locator(selector)
        if clear:
            locator.fill("")
        locator.type(text, delay=delay_ms)

    def wait_for(self, selector: str, timeout_ms: int = 30_000,
                 state: _SelectorState = "visible") -> None:
        self.page.wait_for_selector(selector, timeout=timeout_ms, state=state)

    def screenshot(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.page.screenshot(path=path, full_page=True)
        return path

    def scroll(self, direction: str = "down", amount_px: int = 600,
               selector: str | None = None) -> None:
        """
        Scroll the page (or a specific scrollable element if `selector` is
        given) by `amount_px` pixels. direction: "down" | "up".
        """
        dy = amount_px if direction == "down" else -amount_px
        if selector:
            self.page.locator(selector).evaluate(
                "(el, dy) => el.scrollBy(0, dy)", dy
            )
        else:
            self.page.mouse.wheel(0, dy)

    def scroll_into_view(self, selector: str, timeout_ms: int = 30_000) -> None:
        """Scroll a specific element into view (useful before clicking it)."""
        self.page.locator(selector).scroll_into_view_if_needed(timeout=timeout_ms)

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
        self._browser = None
        self._pw = None


# ── Convenience factory for Brave ──────────────────────────────────────────

def brave(headless: bool = False) -> BrowserController:
    default = "/usr/bin/brave-browser"
    exe = os.getenv("BROWSER_EXECUTABLE", default if os.path.isfile(default) else None)
    return BrowserController(browser="chromium", headless=headless, executable_path=exe)

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Literal, cast

from loguru import logger
from playwright.sync_api import Browser, Page, sync_playwright

# Valid Playwright wait_until values
_WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
_LoadState = Literal["domcontentloaded", "load", "networkidle"]
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
        self._context: Any | None = None
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
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
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


    def upload_file(self, selector: str, path: str) -> None:
        """Set a real local file on an HTML file input."""
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.page.set_input_files(selector, path)

    def download_file(self, selector: str, path: str, timeout_ms: int = 30_000) -> str:
        """Click a download control, wait for completion, and save it to ``path``."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self.page.expect_download(timeout=timeout_ms) as info:
            self.page.click(selector, timeout=timeout_ms)
        download = info.value
        download.save_as(path)
        if not os.path.isfile(path):
            raise RuntimeError(f"Browser download did not create {path!r}")
        return path

    def set_cookie(self, cookie: dict[str, Any]) -> None:
        """Set one browser-context cookie and require its name/value/url or domain."""
        if not cookie.get("name") or "value" not in cookie:
            raise ValueError("cookie requires non-empty 'name' and a 'value'")
        if not cookie.get("url") and not cookie.get("domain"):
            raise ValueError("cookie requires either 'url' or 'domain'")
        self.page.context.add_cookies([cast(Any, cookie)])

    def get_cookies(self, urls: list[str] | None = None) -> list[dict[str, Any]]:
        """Return cookies visible to this browser context."""
        return cast(list[dict[str, Any]], self.page.context.cookies(urls or []))

    def handle_dialog(
        self,
        selector: str,
        accept: bool = True,
        prompt_text: str | None = None,
        timeout_ms: int = 30_000,
    ) -> dict[str, str]:
        """Click a control that opens a JS dialog, handle it, and return observed details."""
        observed: dict[str, str] = {}

        def _handle(dialog: Any) -> None:
            observed.update({"type": dialog.type, "message": dialog.message})
            if accept:
                dialog.accept(prompt_text=prompt_text)
            else:
                dialog.dismiss()

        self.page.once("dialog", _handle)
        self.page.click(selector, timeout=timeout_ms)
        if not observed:
            raise RuntimeError(f"Clicking {selector!r} did not open a browser dialog")
        return observed

    def wait_for_load_state(self, state: _LoadState = "load", timeout_ms: int = 30_000) -> None:
        """Wait for a concrete page lifecycle state instead of sleeping blindly."""
        self.page.wait_for_load_state(state=state, timeout=timeout_ms)

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None


# ── Convenience factory for Brave ──────────────────────────────────────────

def brave(headless: bool = False) -> BrowserController:
    default = "/usr/bin/brave-browser"
    exe = os.getenv("BROWSER_EXECUTABLE", default if os.path.isfile(default) else None)
    return BrowserController(browser="chromium", headless=headless, executable_path=exe)

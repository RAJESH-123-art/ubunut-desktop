from __future__ import annotations
import os
import time
from typing import Optional, Dict, Any
from loguru import logger

from playwright.sync_api import sync_playwright, Browser, Page
import asyncio
import sys
import threading
import queue

class BrowserController:
    """
    Thin wrapper around Playwright for Chromium/Firefox automation.
    Supports custom executable (e.g., Brave via BROWSER_EXECUTABLE env).
    """

    def __init__(self, browser: str = "chromium", headless: bool = False, executable_path: Optional[str] = None):
        self.browser_name = browser
        self.headless = headless
        self.executable_path = executable_path or os.getenv("BROWSER_EXECUTABLE")
        self._pw = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    def start(self):
        # Check if we're already in an asyncio loop
        try:
            loop = asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False
            
        if in_loop:
            # Create a new thread for sync_playwright to avoid loop conflicts
            import threading
            self._thread = threading.Thread(target=self._start_browser_sync)
            self._thread.start()
            self._thread.join()
        else:
            self._start_browser_sync()
        return self
    
    def _start_browser_sync(self):
        self._pw = sync_playwright().start()
        if self.browser_name == "firefox":
            self._browser = self._pw.firefox.launch(headless=self.headless)
        else:
            # chromium w/ optional Brave executable
            launch_kwargs: Dict[str, Any] = {"headless": self.headless}
            if self.executable_path and os.path.isfile(self.executable_path):
                launch_kwargs["executable_path"] = self.executable_path
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._page = self._browser.new_page()
        logger.info(f"Browser started: {self.browser_name} headless={self.headless} exec={self.executable_path}")

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not started")
        return self._page

    def goto(self, url: str, wait_until: str = "load", timeout_ms: int = 30000):
        self.page.goto(url, wait_until=wait_until, timeout=timeout_ms)

    def click(self, selector: str, timeout_ms: int = 30000):
        self.page.click(selector, timeout=timeout_ms)

    def type(self, selector: str, text: str, delay_ms: int = 20, clear: bool = True):
        locator = self.page.locator(selector)
        if clear:
            locator.fill("")
        locator.type(text, delay=delay_ms)

    def wait_for(self, selector: str, timeout_ms: int = 30000, state: str = "visible"):
        self.page.wait_for_selector(selector, timeout=timeout_ms, state=state)

    def screenshot(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.page.screenshot(path=path, full_page=True)
        return path

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
        self._browser = None
        self._pw = None

# Convenience factory for Brave

def brave(headless: bool = False) -> BrowserController:
    # Common brave path on Ubuntu
    default = "/usr/bin/brave-browser"
    exe = os.getenv("BROWSER_EXECUTABLE", default if os.path.isfile(default) else None)
    return BrowserController(browser="chromium", headless=headless, executable_path=exe)

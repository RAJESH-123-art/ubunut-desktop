"""
Electron Navigator — the Electron/CDP slice of Layer 3, covering apps
Layer 2 (AT-SPI) genuinely cannot see at all.

Confirmed live against VS Code (see TASK_KNOWLEDGE_BASE.md Part 14):
AT-SPI exposed exactly 2 nodes total for VS Code (the app + one empty
frame) — zero buttons, zero menus. But VS Code, like every Electron app,
renders its entire UI with Chromium underneath, so it speaks the same
Chrome DevTools Protocol (CDP) already used elsewhere in this project for
whatsapp_send.py/youtube_automation.py. Connecting via CDP found 134 real
interactive elements with real labels ("File", "Edit", "Terminal", ...),
and clicking "File" successfully opened a real dropdown with 28 menu items
— a multi-step interaction that AT-SPI's popover tests (Nautilus,
gnome-text-editor) could NOT achieve in this same environment, because CDP
injects events directly into the Chromium renderer, bypassing whatever
OS-level window-focus requirement blocked those AT-SPI attempts.

Scope: works on Electron apps launched with --remote-debugging-port set.
Most Electron apps (like Chrome) will NOT open a debug port if an existing
instance of the same app is already running without one — the new process
just focuses the old window instead. Use a distinct `user_data_dir` to run
an isolated instance instead of disrupting the user's main session, or
accept that the FIRST launch of the session must include the flag.
"""
from __future__ import annotations

import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from loguru import logger

_STOPWORDS = {"the", "a", "an", "to", "in", "on", "of", "and", "with", "for", "into"}


@dataclass
class ElectronResult:
    ok: bool
    reason: str = ""
    matched_text: str = ""
    candidates_considered: int = 0


def _cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def ensure_electron_cdp(
    binary: str,
    port: int,
    extra_args: list[str] | None = None,
    user_data_dir: str | None = None,
    timeout: float = 15.0,
) -> bool:
    """
    Ensure `binary` (e.g. "code") is running with a CDP debugging port open
    on `port`. Reuses an existing debug session if one is already
    listening; otherwise launches a NEW instance with the flag set.
    Returns True once the CDP endpoint responds, False on timeout/failure.
    """
    if _cdp_alive(port):
        logger.debug(f"electron_navigator: CDP already alive on port {port}")
        return True

    args = [binary, f"--remote-debugging-port={port}"]
    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")
    if extra_args:
        args.extend(extra_args)

    try:
        subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logger.error(f"electron_navigator: failed to launch {binary!r}: {exc}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cdp_alive(port):
            logger.info(f"electron_navigator: '{binary}' CDP ready on port {port}")
            return True
        time.sleep(0.3)
    logger.warning(f"electron_navigator: '{binary}' did not open CDP port {port} within {timeout}s")
    return False


def _keywords(phrase: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) >= 1}


def _score(label: str, keywords: set[str]) -> float:
    label_words = set(re.findall(r"[a-z0-9]+", label.lower()))
    overlap = len(keywords & label_words)
    if overlap == 0:
        overlap = sum(1 for kw in keywords if len(kw) >= 3 and kw in label.lower())
    return overlap / max(len(keywords), 1) if overlap else 0.0


def click_by_intent(port: int, phrase: str, timeout: float = 6.0) -> ElectronResult:
    """
    Connect to an already-running Electron app's CDP endpoint (see
    `ensure_electron_cdp`) and click the element (by aria-label, role, or
    visible text) whose label best matches `phrase`.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ElectronResult(False, reason="playwright not installed")

    keywords = _keywords(phrase)
    if not keywords:
        return ElectronResult(False, reason="no usable keywords extracted from phrase")

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            if not browser.contexts or not browser.contexts[0].pages:
                return ElectronResult(False, reason="no pages found on CDP connection")
            page = browser.contexts[0].pages[0]

            candidates = page.locator('[role="button"], [aria-label], .action-label').all()
            best_elem, best_score, best_text = None, 0.0, ""
            considered = 0
            for elem in candidates:
                try:
                    # SAFETY: many Electron apps keep hidden/collapsed DOM
                    # elements around for keyboard shortcuts or a hamburger
                    # "More" overflow menu (confirmed live against VS Code—
                    # a "Terminal" menubar button existed in the DOM but
                    # wasn't actually visible/clickable in the current
                    # layout). Skipping invisible elements is the CDP-side
                    # equivalent of atspi_navigator's `require_actionable`
                    # check — same class of "decoy node" problem, different
                    # mechanism.
                    if not elem.is_visible():
                        continue
                    aria_label = elem.get_attribute("aria-label")
                    label = (aria_label or elem.inner_text() or "").strip()
                except Exception as exc:
                    logger.debug(f"electron nav: skipping unreadable element: {exc}")
                    continue
                if not label:
                    continue
                # SAFETY: a large wrapping container with no aria-label of
                # its own can still match role="button" and fall back to
                # inner_text() — which then swallows ALL descendant text.
                # Confirmed live: searching "file" matched a giant welcome-
                # page container (hundreds of characters, starting with
                # "File\nEdit\n...") instead of the actual "File" menu
                # button, purely because both technically contain the word
                # "file". Real button/menu labels are short phrases — cap
                # inner_text-derived labels; aria-label is app-curated and
                # already short, so it's exempt from the cap.
                if not aria_label and len(label) > 60:
                    continue
                considered += 1
                score = _score(label, keywords)
                if score > best_score:
                    best_elem, best_score, best_text = elem, score, label

            if best_elem is None:
                return ElectronResult(
                    False, candidates_considered=considered,
                    reason=f"no VISIBLE element matched {phrase!r} among {considered} visible candidates ({len(candidates)} total in DOM)",
                )

            best_elem.click(timeout=timeout * 1000)
            logger.info(f"electron_navigator: clicked {best_text!r} for intent {phrase!r}")
            return ElectronResult(True, matched_text=best_text, candidates_considered=len(candidates))
    except Exception as exc:
        return ElectronResult(False, reason=f"CDP interaction failed: {exc}")


def type_by_intent(
    port: int,
    phrase: str,
    text: str,
    timeout: float = 6.0,
    press_enter: bool = False,
) -> ElectronResult:
    """
    Connect to an Electron app's CDP endpoint and type `text` into the
    input element (by aria-label, placeholder, role, or visible text)
    whose label best matches `phrase`.
    
    Args:
        port: CDP port (from ensure_electron_cdp)
        phrase: Description of the input field (e.g. "search", "command palette", "chat input")
        text: Text to type
        timeout: Max seconds to wait
        press_enter: Whether to press Enter after typing
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ElectronResult(False, reason="playwright not installed")

    keywords = _keywords(phrase)
    if not keywords:
        return ElectronResult(False, reason="no usable keywords extracted from phrase")

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            if not browser.contexts or not browser.contexts[0].pages:
                return ElectronResult(False, reason="no pages found on CDP connection")
            page = browser.contexts[0].pages[0]

            candidates = page.locator(
                'input[type="text"], input[type="search"], input:not([type]), '
                'textarea, [role="textbox"], [role="searchbox"], '
                '[contenteditable="true"]'
            ).all()
            
            best_elem, best_score, best_label = None, 0.0, ""
            considered = 0
            for elem in candidates:
                try:
                    if not elem.is_visible():
                        continue
                    aria_label = elem.get_attribute("aria-label")
                    placeholder = elem.get_attribute("placeholder")
                    label = (aria_label or placeholder or elem.inner_text() or "").strip()
                except Exception as exc:
                    logger.debug(f"electron nav: skipping unreadable element: {exc}")
                    continue
                if not label:
                    continue
                if not aria_label and not placeholder and len(label) > 60:
                    continue
                considered += 1
                score = _score(label, keywords)
                if score > best_score:
                    best_elem, best_score, best_label = elem, score, label

            if best_elem is None:
                return ElectronResult(
                    False, candidates_considered=considered,
                    reason=f"no VISIBLE input matched {phrase!r} among {considered} visible candidates",
                )

            best_elem.click(timeout=timeout * 1000)
            best_elem.fill(text)
            logger.info(f"electron_navigator: typed into {best_label!r} for intent {phrase!r}")
            
            if press_enter:
                best_elem.press("Enter")
                logger.info("electron_navigator: pressed Enter after typing")
            
            return ElectronResult(True, matched_text=best_label, candidates_considered=considered)
    except Exception as exc:
        return ElectronResult(False, reason=f"CDP type interaction failed: {exc}")


if __name__ == "__main__":
    import sys
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 9333
    phrase_arg = " ".join(sys.argv[2:]) or "file"
    print(click_by_intent(port_arg, phrase_arg))

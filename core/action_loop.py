"""
ActionLoop -- closed observe -> decide -> act -> verify loop for adaptive
GUI automation. This is the piece that makes automation behave like a human
instead of a blind script: it looks again after every action instead of
assuming a pre-written plan survived contact with reality.

Perception strategy (fast-first -- the "accessibility API is the cheat
code" pattern; see core/atspi_navigator.py and core/semantic_vision.py):
  1. AT-SPI accessibility tree (near-instant, free, exact) -- tried first
  2. Screenshot + vision model (NVIDIA_API_KEY) -- only when AT-SPI can't
     see the relevant app (canvas apps, games, or nothing recognizable)

Decision strategy:
  Each step sends the goal + current observation + history to the text LLM
  (APINEX_API_KEY) and asks for exactly ONE next action from a small,
  fixed vocabulary (click/select_option/type/key/wait/scroll/done). One small
  decision at a time -- not a whole plan upfront -- is what lets it recover
  from surprises the way a human would.

Safety:
  The loop never invents its own execution mechanism. Every mapped action is
  validated and dispatched by core.structured_automation.StructuredExecutor.
  Shell execution is intentionally unavailable here: approval for a general
  adaptive goal must never be laundered into approval for a model-generated
  command. This module only decides WHAT to do next; it does not change HOW actions
  execute or bypass any existing safety mechanism.

Trust:
  The model's own "done"/success claim is NEVER accepted at face value.
  _verify_done() re-observes independently before agreeing a claimed
  success actually holds (see VERCEPT_LEVEL_ROADMAP.md §6 -- a past run
  hallucinated success on a calculator that was actually showing a
  malformed expression, with no real observation data behind the claim).
  A claimed failure is trusted as-is; only claimed *success* is checked.

Fully inert without APINEX_API_KEY (the decision step needs it) --
with no key, `ActionLoop.available()` is False.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from core.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from core.structured_automation import StructuredPlan


DECISION_SYSTEM_PROMPT = """You are the decision-making step of a desktop automation loop on Ubuntu (GNOME/Wayland).
You are given: a GOAL, the CURRENT OBSERVATION (what's on screen right now), and the HISTORY of
actions already taken this run. Decide exactly ONE next action to make progress toward the goal.

Respond with ONLY a single JSON object (no markdown fences, no commentary), using one of these shapes:
  {"action": "launch_app", "app_name": "calculator"}
  {"action": "click", "description": "exact visible control text", "near": "optional nearby section heading", "expect": {"ui_text": "optional expected native text"}}
  {"action": "select_option", "description": "dropdown label", "option": "visible option text"}
  {"action": "type", "field": "explicit visible field label", "text": "exact text"}
  {"action": "key", "key": "Return"}
  {"action": "wait", "seconds": 1.5}
  {"action": "scroll", "direction": "down", "amount_px": 600}
  {"action": "drag", "source": "precise source description", "target": "precise destination description"}
  {"action": "cap", "cap": "capability.name", "args": {"param": "value"}}
  {"action": "done", "success": true, "message": "why the goal is achieved"}
  {"action": "done", "success": false, "message": "why it can't proceed further"}

The "cap" action runs a reusable OS-level capability directly and returns its
verified result. Use it INSTEAD of GUI clicking whenever the goal is about
data, files, or computation — it is faster, deterministic, and verified.
Most-used capabilities (args in parentheses):
  fs.write (path, content) | fs.create_folder (path) | fs.copy (src, dst) | fs.find (pattern, directory)
  sheet.write_cell (path, cell, value) | sheet.append_rows (path, rows) | sheet.save (path)
  calc.evaluate (expression) — pure arithmetic like "4+5+3-5", no eval
  data.write_csv (path, rows) | data.read_csv (path) | data.transform (data, operation) | data.dedupe (rows, key) | data.sort_rows (rows, key)
  research.search_web (query) | research.extract_page_text () | research.save_results (path, results)
  browser.open () | browser.goto (url) | browser.extract_text () | browser.download (url, destination)
  verify.file_exists (path, min_bytes) | verify.spreadsheet (path, cell, expected) | verify.download_complete (folder, filename_contains, min_bytes)
  wait.for_file (path, timeout) | wait.for_download (folder, filename_contains, timeout) | wait.for_text (text, timeout)
Examples:
  {"action": "cap", "cap": "sheet.append_rows", "args": {"path": "/home/user/Docs/data.xlsx", "rows": [["Name","Age"],["Elon",53]]}}
  {"action": "cap", "cap": "calc.evaluate", "args": {"expression": "4+5+3-5"}}
  {"action": "cap", "cap": "verify.file_exists", "args": {"path": "/home/user/Downloads/report.csv", "min_bytes": 10}}
Rules for "cap":
- Use absolute paths for file operations.
- Prefer cap-based file/data/spreadsheet/computation actions over simulating a
  GUI app; only drive LibreOffice's GUI when the user explicitly asks to see it.
- After a producing cap (sheet/data/fs write), follow with a verify.* cap to
  confirm the deliverable exists/matches before claiming done.

Rules:
- Pick "done" with success=false rather than looping on an action that isn't working.
- Shell commands are not available in this loop. Never return run_shell.
- Never attempt to solve a CAPTCHA, enter a password, approve a passkey, or guess a 2FA code.
  Stop so the user can complete that security boundary.
- If the goal requires an application that is not yet running (check the OBSERVATION -- if
  its name isn't listed as an open app), ALWAYS use "launch_app" to open it FIRST, before
  trying anything else. Do NOT try to open an app by clicking "Activities", a taskbar, a
  menu icon, or an app-grid icon -- launch_app already resolves and launches ANY installed
  application directly and reliably (it searches the full system application index, not
  just a few well-known names), and is far faster and more reliable than guessing icon
  positions visually. Only fall back to clicking icons if launch_app reports the app
  wasn't found at all.
- Window focus for raw keyboard input ("type"/"key") cannot always be guaranteed on this
  system, so "click" on AT-SPI-visible buttons is the MORE reliable default action here,
  even though it takes more individual steps -- prefer it for calculators, dialogs, and
  anything with clickable digit/letter buttons.
- Only use "type" for free-text fields where no equivalent buttons exist (e.g. a search box
  or a text form field). Always provide the field's exact visible/accessibility label; typing
  into unknown focus is not available. After typing, check the NEXT observation carefully.
- Multi-step numeric/calculator entry (e.g. computing "5 + 3"): the OBSERVATION includes
  the live display/expression content (shown as "(content: '...')" on a text/entry line) --
  READ IT before every click to know exactly what has already been entered so far, since
  clicking digit buttons back-to-back concatenates them into one number (e.g. clicking "5"
  then "3" with no operator in between types the number "53", NOT "5" followed separately
  by "3"). Plan each click against the CURRENT actual displayed expression, not just your
  own memory of what you intended -- if the display doesn't show what you expect after a
  click, correct course on the next step rather than continuing to click your original plan.
"""

# Extra rules appended to the system prompt only for browser/web-content goals
# (see _is_browser_task). Encourages the model to use precise DOM-level
# actions instead of fumbling with address-bar clicks -- the exact failure
# mode observed live: repeatedly clicking/re-typing into an address bar
# without ever confirming the text landed, because AT-SPI mostly can't see
# inside rendered web page content the way it can see native GTK widgets.
BROWSER_PROMPT_ADDENDUM = """
This goal involves a WEB BROWSER. You have ONE extra action available, and the
observation you're given is read directly from the page's real DOM (exact
visible links/buttons/inputs), not a guess from pixels or an accessibility tree:
  {"action": "goto", "url": "https://..."}
  {"action": "select_option", "description": "dropdown label", "option": "visible option text"}
  {"action": "drag", "source": "visible draggable text", "target": "visible drop target text"}

Rules specific to browser goals:
- If CURRENT OBSERVATION already shows the relevant target page, do NOT navigate
  again. Repeated goto actions reset form state and are a failure loop.
- STRONGLY prefer "goto" with a direct, complete URL over clicking an address bar
  and typing a query into it -- e.g. for a search, go straight to
  "https://www.google.com/search?q=<query>" in ONE step instead of clicking the
  address bar, typing, then pressing Enter separately. This is far more reliable.
- For "click", describe the element by its VISIBLE TEXT exactly as shown in the
  observation (e.g. "Images", "Next", "Accept all") -- it will be matched against
  real page text, not guessed visually. If several controls have identical text,
  include "near" with the visible section heading that contains the intended one.
- Use "select_option" for HTML dropdowns. Copy the dropdown description and option
  text from the DOM observation; do not try to click a hidden option as page text.
- For "type", provide the exact visible label of the target input in "field". Typing
  into whichever element happens to be focused is not available.
"""

# Phrases like 'save it as a file named X', 'save as X.csv', 'create a
# spreadsheet called X' -- captures the claimed filename so a "done" success
# claim that mentions saving something can be checked against the real
# filesystem instead of trusted blindly (see _verify_done).
_CLAIMED_FILENAME_RE = re.compile(
    r'(?:file|spreadsheet|document|sheet)s?\s+(?:named|called)\s+["\']?([\w.-]+)["\']?'
    r'|saved?\s+as\s+(?:a\s+)?(?:file\s+)?(?:named\s+|called\s+)?["\']?([\w.-]+)["\']?',
    re.IGNORECASE,
)
_SEARCH_DIRS_FOR_SAVED_FILES = ("", "Desktop", "Documents", "Downloads")

_HUMAN_INTERVENTION_PATTERNS = (
    (re.compile(r"\b(captcha|recaptcha|hcaptcha)\b", re.IGNORECASE), "CAPTCHA"),
    (re.compile(r"\b(verify (?:that )?you are human|human verification)\b", re.IGNORECASE), "human verification"),
    (re.compile(r"\b(two[- ]factor|2fa|one[- ]time (?:password|code)|verification code)\b", re.IGNORECASE), "two-factor authentication"),
    (re.compile(r"\b(passkey|security key|enter (?:your )?password)\b", re.IGNORECASE), "credential entry"),
)


def _human_intervention_reason(observation: str) -> str:
    for pattern, reason in _HUMAN_INTERVENTION_PATTERNS:
        if pattern.search(observation):
            return reason
    return ""

# Phrases like 'the result displayed is 8', 'shows 8', 'answer is 8', 'display
# now shows 42' -- captures a claimed on-screen numeric value so a "done"
# success claim about what a display/result/answer shows can be checked
# against a FRESH real observation instead of trusted blindly. Added after a
# live run hallucinated "The result displayed is 8" for a calculator whose
# real, independently-checked AT-SPI display was actually empty -- the
# existing _verify_done() checks (app still running, some observation
# exists) both passed even though the specific claimed fact was false.
_CLAIMED_RESULT_RE = re.compile(
    r'(?:result|display|answer|output|value|shows?|equals?|displayed)\s*'
    r'(?:is|shows|now shows|as|of|:)?\s*["\']?(-?\d+(?:\.\d+)?)["\']?',
    re.IGNORECASE,
)

# A second, independent pattern for the very common "A + B = C" equation
# phrasing (e.g. "5 + 3 = 8", "compute 5 + 3 = 8") -- confirmed live that
# THIS phrasing slips right past _CLAIMED_RESULT_RE above (it only matches
# keyword-led claims like "result is X"), so _extract_claimed_result()
# silently returned "" and skipped verification entirely, letting a false
# claim ('...= 8' when the real display showed '56') through unchecked.
# Takes the LAST '= N' in the text, since that's the final stated result of
# an equation, not an intermediate one.
_CLAIMED_EQUALS_RE = re.compile(r'=\s*["\']?(-?\d+(?:\.\d+)?)["\']?', re.IGNORECASE)

# A browser completion message often names the observed label that proves the
# task finished (for example, "the button now reads 'Order complete'").
# Preserve that claim as an independently verifiable condition instead of
# treating a non-empty page observation as sufficient evidence of success.
_CLAIMED_BROWSER_TEXT_RE = re.compile(
    r"\b(?:read(?:s)?|say(?:s)?|show(?:s)?|display(?:s)?|label(?:led)?\s+(?:is|as))"
    r"\s*[:=-]?\s*[\"']([^\"']{3,160})[\"']",
    re.IGNORECASE,
)


def _extract_claimed_result(*texts: str) -> str:
    """Pull a plausible claimed on-screen numeric value out of the model's
    own success message, if it makes a specific 'shows/displays/equals X'
    claim, OR states an explicit equation result like '5 + 3 = 8'."""
    for text in texts:
        equals_matches = _CLAIMED_EQUALS_RE.findall(text)
        if equals_matches:
            return equals_matches[-1]
        m = _CLAIMED_RESULT_RE.search(text)
        if m:
            return m.group(1)
    return ""


def _extract_claimed_browser_text(*texts: str) -> list[str]:
    """Return distinct quoted browser labels used as a completion claim."""
    claims: list[str] = []
    for text in texts:
        for item in _CLAIMED_BROWSER_TEXT_RE.findall(text):
            value = item.strip()
            if value and value not in claims:
                claims.append(value)
    return claims


def _extract_claimed_filename(*texts: str) -> str:
    """Pull a plausible target filename out of the goal and/or the model's
    own claimed success message, if either mentions saving/creating one."""
    for text in texts:
        m = _CLAIMED_FILENAME_RE.search(text)
        if m:
            name = (m.group(1) or m.group(2) or "").strip(" .,'\"")
            if len(name) >= 3:
                return name
    return ""


def _claimed_file_exists(name: str) -> bool:
    """
    Best-effort check: does a file whose name contains `name` exist in any of
    the common save locations (home dir top level, Desktop, Documents,
    Downloads)? Deliberately shallow (not a full recursive filesystem
    search) so this stays fast -- these are exactly the directories
    tasks/create_folder.py, tasks/file_operations.py etc. already default to.
    """
    stem = Path(name).stem.lower()
    home = Path.home()
    for sub in _SEARCH_DIRS_FOR_SAVED_FILES:
        d = home / sub if sub else home
        try:
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.is_file() and stem in p.stem.lower():
                    return True
        except OSError:
            continue
    return False


_BROWSER_NAMES = ("firefox", "chrome", "chromium", "brave", "browser")


def _detect_requested_browser(app_hint: str, goal: str) -> str:
    """
    Return "firefox" if the request specifically named Firefox, else ""
    (meaning: no specific non-Chrome browser was named -- use the default
    Chrome-family path). Mirrors the same "respect what was literally named"
    fix already applied to agent.py's open_browser/visit_url/search_web
    intents -- this closes the same gap for the adaptive loop's browser mode.
    """
    text = f"{app_hint} {goal}".lower()
    if re.search(r"\bfirefox\b", text):
        return "firefox"
    return ""


def _is_browser_task(app_hint: str, goal: str) -> bool:
    """
    True if this goal is about interacting with WEB PAGE CONTENT rather than a
    native desktop app. Native AT-SPI observation is unreliable for browser
    page content (confirmed live -- a "search X in firefox" goal looped
    without progress because the loop couldn't tell whether typed text landed
    in the page at all). Browser-task goals are instead routed through
    Playwright/CDP DOM access (see _observe_browser_dom / ActionLoop._act_browser),
    which reads and interacts with the actual page structure precisely instead
    of guessing from an accessibility tree or pixels.
    """
    text = f"{app_hint} {goal}".lower()
    if any(name in text for name in _BROWSER_NAMES):
        return True
    return bool(re.search(r"\b(website|webpage|web page|search|google|url)\b", text))


def _observe_browser_dom(page: Any) -> str:
    """
    Read the current page's URL, title, and visible interactive elements
    directly from the DOM via Playwright -- far more reliable for web content
    than AT-SPI (which mostly can't see inside a browser's rendered page) or
    vision-model pixel guessing.
    """
    try:
        url = page.url
        title = page.title()
        candidates = page.locator(
            'a, button, input, textarea, select, '
            '[role="button"], [role="link"], [role="textbox"], [role="searchbox"]'
        )
        lines: list[str] = []
        # Large external pages can contain thousands of matching links.  Do
        # not materialize every locator just to report a compact observation;
        # bound the DOM work itself so an adaptive step remains responsive.
        candidate_count = min(candidates.count(), 40)
        for index in range(candidate_count):
            el = candidates.nth(index)
            try:
                if not el.is_visible():
                    continue
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                text = (el.inner_text() or "").strip()
                placeholder = el.get_attribute("placeholder") or ""
                aria = el.get_attribute("aria-label") or ""
                label = (text or aria or placeholder).strip()[:120]
                if tag == "select":
                    options = el.locator("option").all_text_contents()
                    clean_options = [option.strip() for option in options if option.strip()]
                    selected = el.input_value()
                    label = label or aria or "select"
                    lines.append(
                        f"[select] {label} | selected={selected!r} | options={clean_options[:12]!r}"
                    )
                    continue
                if not label:
                    continue
                lines.append(f"[{tag}] {label}")
            except Exception as exc:
                logger.debug(f"browser element enumeration skipped one node: {exc}")
                continue
        header = f"Browser page: {title!r} ({url})\nVisible interactive elements:\n"
        return header + ("\n".join(f"- {l}" for l in lines) if lines else "(none found)")
    except Exception as exc:
        logger.debug(f"ActionLoop: browser DOM observation failed: {exc}")
        return ""


@dataclass
class LoopStep:
    observation: str
    decision: dict[str, Any]
    result: str


@dataclass
class LoopResult:
    success: bool
    message: str = ""
    steps: list[LoopStep] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoopRuntimeState:
    """Verified target context carried across one adaptive run.

    The model only receives a compact observation.  The executor retains this
    separate state so it can detect that focus or the controlled browser tab
    changed underneath it before another action is dispatched.
    """

    app_hint: str = ""
    target_seen: bool = False
    active_window: str = ""
    open_apps: tuple[str, ...] = ()
    browser_url: str = ""
    browser_title: str = ""
    observation: str = ""

    def evidence(self, *, phase: str, step: int) -> dict[str, Any]:
        return {
            "step": step,
            "action": "runtime_state",
            "phase": phase,
            "app_hint": self.app_hint,
            "target_seen": self.target_seen,
            "active_window": self.active_window,
            "open_apps": list(self.open_apps),
            "browser_url": self.browser_url,
            "browser_title": self.browser_title,
        }


def _observe_atspi(app_hint: str = "") -> str:
    """
    Fast, free, local observation: list interactive elements from running
    apps via the AT-SPI accessibility tree. Reuses atspi_navigator's own
    walk/name/role helpers rather than re-implementing tree traversal.
    Returns "" if nothing usable was found (caller should fall back to vision).
    """
    try:
        import pyatspi

        from core.atspi_navigator import _name, _role, _walk  # reuse, don't duplicate

        desktop = pyatspi.Registry.getDesktop(0)
        hint_aliases = {
            "libreoffice": "soffice",
            "libreoffice calc": "soffice",
            "libreoffice writer": "soffice",
            "libreoffice impress": "soffice",
        }
        matched_hint = hint_aliases.get(app_hint.lower(), app_hint).lower()
        lines: list[str] = []
        apps_seen: list[str] = []
        for app in desktop:
            if app is None:
                continue
            app_name = app.name or ""
            if matched_hint and matched_hint not in app_name.lower():
                continue
            apps_seen.append(app_name)
            count = 0
            for node in _walk(app):
                role = _role(node)
                name = _name(node)
                # Text/entry nodes (e.g. a calculator's result display) often
                # have an EMPTY .name -- the actual displayed value lives in
                # their text CONTENT instead. Read that too, or the loop is
                # blind to on-screen state changes it needs to detect success.
                content = ""
                if role in ("text", "entry") and not name:
                    try:
                        t = node.queryText()
                        content = t.getText(0, t.characterCount).strip()
                    except Exception:
                        content = ""
                if not name and not content:
                    continue
                if role in ("push button", "toggle button", "menu item", "entry",
                            "text", "combo box", "check box", "radio button", "link"):
                    label = name or f"(content: {content!r})"
                    lines.append(f"[{role}] {label}")
                    count += 1
                if count >= 60:
                    break
            if lines:
                break  # first matching app with content is enough for one step

        if not lines:
            return ""
        header = f"App: {apps_seen[0] if apps_seen else 'unknown'}\nVisible interactive elements:\n"
        return header + "\n".join(f"- {l}" for l in lines)
    except Exception as exc:
        logger.debug(f"ActionLoop: AT-SPI observation failed: {exc}")
        return ""


def _observe_vision(description_hint: str = "") -> str:
    """Fallback observation via screenshot + vision model description."""
    try:
        import base64

        from core.logger import take_screenshot
        from core.semantic_vision import semantic_vision

        if not semantic_vision.api_key:
            return ""
        path = take_screenshot(name="action_loop_observe")
        with open(path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        client = semantic_vision._get_client()
        resp = client.chat.completions.create(
            model=semantic_vision.vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "List the visible clickable UI elements (buttons, links, "
                     "fields, menu items) in this screenshot, one per line, in the form 'label: short description'. "
                     "Be concise, max 30 items."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            max_tokens=600,
            temperature=0.1,
        )
        return "Screen (via vision):\n" + (resp.choices[0].message.content or "")
    except Exception as exc:
        logger.debug(f"ActionLoop: vision observation failed: {exc}")
        return ""


class ActionLoop:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("NIKKI_TEXT_API_KEY") or os.getenv("APINEX_API_KEY")
        self.base_url = base_url or os.getenv("NIKKI_TEXT_BASE_URL", os.getenv("APINEX_BASE_URL", "https://api.apinex.bond/v1"))
        # Tactical decisions and whole-plan generation share the APINEX text
        # endpoint. The vision pipeline remains independently configured with
        # NVIDIA_API_KEY and NVIDIA_VISION_MODEL.
        self.model = model or os.getenv("NIKKI_TEXT_MODEL", os.getenv("APINEX_TEXT_MODEL", "free/gpt-5.6-luna"))
        # No hard limit by default — reads ALOOP_MAX_STEPS from env (Vercept-level behaviour).
        # Set ALOOP_MAX_STEPS=0 for truly unlimited (use with care).
        _env_steps = os.getenv("ALOOP_MAX_STEPS", "50")
        self.max_steps = max_steps if max_steps is not None else (int(_env_steps) if _env_steps.isdigit() else 50)
        # Dynamic per-step timeout: reads ALOOP_TIMEOUT (seconds). Default 120s
        # gives slow API calls and large page loads time to complete.
        _env_timeout = os.getenv("ALOOP_TIMEOUT", "120")
        try:
            self.timeout = timeout if timeout is not None else float(_env_timeout)
        except ValueError:
            self.timeout = 120.0
        self._client = None
        self._pw = None  # sync_playwright() handle, only created for browser-task runs
        self._browser_context = None  # Firefox's persistent context, if that's what got used

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def _get_browser_page(self, requested_browser: str = "", goal: str = "") -> Any:
        """
        Open a page in the browser the user actually asked for.

        - "firefox" -> a REAL Playwright-driven Firefox window (own,
          persistent automation profile under ~/.config/desktop_automation/
          firefox_profile so it remembers state across runs). This does NOT
          attach to the user's already-open everyday Firefox window --
          Firefox has no equivalent of Chrome's --remote-debugging-port for
          attaching to an arbitrary running session, so a genuinely separate
          (but real, visible, literally Firefox) instance is used instead.
        - "chrome"/"chromium"/"brave"/unspecified -> the SAME warm,
          profile-synced Chrome instance already used by
          tasks/whatsapp_send.py and tasks/youtube_automation.py (see
          core/cdp_browser.py), which DOES preserve real logins via its
          profile-copy/sync mechanism.

        Whichever it is, the important thing this fixes: a request that
        explicitly names "firefox" must actually run in Firefox, not be
        silently satisfied by Chrome just because Chrome is easier to attach
        to -- that was a real regression, caught live, in an earlier version
        of this method.

        Returns None (caller falls back to AT-SPI/vision) if unavailable.
        """
        try:
            from playwright.sync_api import sync_playwright
            if self._pw is None:
                self._pw = sync_playwright().start()

            if requested_browser == "firefox":
                profile_dir = Path.home() / ".config" / "desktop_automation" / "firefox_profile"
                profile_dir.mkdir(parents=True, exist_ok=True)
                self._browser_context = self._pw.firefox.launch_persistent_context(
                    user_data_dir=str(profile_dir), headless=False,
                )
                page = self._browser_context.pages[0] if self._browser_context.pages else self._browser_context.new_page()
                return page

            from core.cdp_browser import CDP_URL, ensure_chrome_cdp
            ensure_chrome_cdp()
            browser = self._pw.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            # Reuse the page matching the current workflow stage. Microsoft and
            # other form sites can open transient locale/confirmation tabs, so
            # "last tab wins" may resume the wrong step when several remain.
            goal_lower = goal.lower()
            wants_language = any(
                word in goal_lower for word in ("language", "english", "confirm", "64-bit")
            )
            wants_edition = any(
                phrase in goal_lower
                for phrase in ("select download", "multi-edition", "iso edition", "download now")
            )

            def page_score(page: Any) -> int:
                url = (page.url or "").lower()
                if not url or url == "about:blank":
                    return -100
                score = 1
                if "microsoft.com" in goal_lower and "microsoft.com" in url:
                    score += 2
                if wants_language and "/software-download/locale" in url:
                    score += 10
                if wants_edition and "/software-download/windows11" in url:
                    score += 10
                if wants_edition and "/software-download/locale" in url:
                    score -= 5
                return score

            candidates = [page for page in context.pages if page_score(page) > -100]
            if candidates:
                page = max(candidates, key=page_score)
                page.bring_to_front()
                logger.debug(f"ActionLoop: reusing page for current stage: {page.url}")
                return page
            return context.new_page()
        except Exception as exc:
            logger.debug(f"ActionLoop: could not open {requested_browser or 'chrome'} page ({exc}); falling back to AT-SPI/vision")
            return None

    def _close_playwright(self) -> None:
        if self._browser_context is not None:
            try:
                self._browser_context.close()
            except Exception as exc:
                logger.debug(f"browser context close failed: {exc}")
            self._browser_context = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception as exc:
                logger.debug(f"playwright stop failed: {exc}")
            self._pw = None

    @staticmethod
    def _browser_page_readable(page: Any) -> bool:
        """Return whether the current browser tab can still provide basic state."""
        if page is None:
            return False
        try:
            _ = page.url
            page.title()
            return True
        except Exception:
            return False

    def _recover_browser_page(self, requested_browser: str, goal: str) -> Any:
        """Reconnect to a readable browser page without replaying an action.

        The first reconnection preserves the existing Playwright runtime.  If
        that runtime itself is stale, recreate it once and try again.  The
        caller still re-observes and checks the result before taking a new
        action, so a recovered tab is never assumed to be at the old state.
        """
        page = self._get_browser_page(requested_browser, goal)
        if self._browser_page_readable(page):
            return page
        self._close_playwright()
        page = self._get_browser_page(requested_browser, goal)
        return page if self._browser_page_readable(page) else None

    def _observe(self, app_hint: str = "", browser_page: Any = None) -> str:
        if browser_page is not None:
            obs = _observe_browser_dom(browser_page)
            if obs:
                return obs
        obs = _observe_atspi(app_hint)
        if obs:
            return obs
        # When app_hint is given but AT-SPI found no matching running app, the
        # target app simply isn't open yet -- the model doesn't need to SEE
        # the screen to know that; it needs to call launch_app. Falling back
        # to a full vision screenshot+API-call here is pure waste (confirmed
        # live: this was the ONLY screenshot needed in an otherwise fully
        # AT-SPI, zero-screenshot run -- every button click after launch used
        # AT-SPI directly). Skip straight to a cheap textual hint instead.
        if app_hint:
            return f"App {app_hint!r} does not appear to be running yet. Use launch_app to open it."
        obs = _observe_vision()
        return obs or "(no observation available -- screen may be empty or unreadable)"

    @staticmethod
    def _app_matches(name: str, candidates: list[str]) -> bool:
        wanted = re.sub(r"[^a-z0-9]", "", name.lower())
        return bool(wanted) and any(
            wanted in re.sub(r"[^a-z0-9]", "", candidate.lower())
            for candidate in candidates
        )

    def _capture_runtime_state(
        self,
        observation: str,
        *,
        app_hint: str = "",
        browser_page: Any = None,
        previous: LoopRuntimeState | None = None,
    ) -> LoopRuntimeState:
        """Capture cross-application state without making it model-visible noise."""
        open_apps: list[str] = []
        active_window = ""
        # The AT-SPI observer reads the app tree that the next native action
        # will use. Treat that fresh observation as authoritative: a second
        # global desktop walk is redundant, less specific, and can fail on a
        # disconnected accessibility bus. Browser state is captured below.
        observed_app = ""
        if observation.startswith("App:"):
            observed_app = observation.splitlines()[0].removeprefix("App:").strip()
            if observed_app:
                open_apps.append(observed_app)
                active_window = observed_app
        observed_target = bool(app_hint and self._app_matches(app_hint, open_apps))

        browser_url = ""
        browser_title = ""
        if browser_page is not None:
            try:
                browser_url = str(browser_page.url or "")
                browser_title = str(browser_page.title() or "")
            except Exception as exc:
                logger.debug(f"ActionLoop: runtime browser state unavailable: {exc}")

        seen_now = observed_target or self._app_matches(app_hint, open_apps)
        return LoopRuntimeState(
            app_hint=app_hint,
            target_seen=(previous.target_seen if previous else False) or seen_now,
            active_window=active_window,
            open_apps=tuple(open_apps),
            browser_url=browser_url,
            browser_title=browser_title,
            observation=observation,
        )

    def _runtime_problem(
        self,
        previous: LoopRuntimeState,
        current: LoopRuntimeState,
        *,
        browser_mode: bool,
    ) -> str:
        """Reject a stale or lost target before a follow-up action can misfire."""
        if browser_mode:
            if previous.browser_url and not current.browser_url:
                return "The controlled browser tab is no longer readable"
            return ""
        if previous.target_seen and current.app_hint and not self._app_matches(
            current.app_hint, list(current.open_apps)
        ):
            return f"Target application {current.app_hint!r} disappeared during the run"
        return ""

    @staticmethod
    def _load_checkpoint(path: Path, goal: str) -> tuple[list[LoopStep], list[dict[str, Any]], LoopRuntimeState] | None:
        """Load only an incomplete checkpoint for the exact requested goal."""
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("goal") != goal or payload.get("complete"):
            return None
        raw_steps = payload.get("steps", [])
        raw_evidence = payload.get("evidence", [])
        raw_state = payload.get("runtime_state", {})
        if not isinstance(raw_steps, list) or not isinstance(raw_evidence, list) or not isinstance(raw_state, dict):
            return None
        try:
            steps = [
                LoopStep(
                    observation=str(item["observation"]),
                    decision=dict(item["decision"]),
                    result=str(item["result"]),
                )
                for item in raw_steps
                if isinstance(item, dict)
            ]
            state = LoopRuntimeState(
                app_hint=str(raw_state.get("app_hint", "")),
                target_seen=bool(raw_state.get("target_seen", False)),
                active_window=str(raw_state.get("active_window", "")),
                open_apps=tuple(str(item) for item in raw_state.get("open_apps", [])),
                browser_url=str(raw_state.get("browser_url", "")),
                browser_title=str(raw_state.get("browser_title", "")),
                observation=str(raw_state.get("observation", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return steps, [item for item in raw_evidence if isinstance(item, dict)], state

    @staticmethod
    def _save_checkpoint(
        path: Path | None,
        *,
        goal: str,
        steps: list[LoopStep],
        evidence: list[dict[str, Any]],
        runtime_state: LoopRuntimeState,
        complete: bool,
        message: str = "",
    ) -> None:
        if path is None:
            return
        payload = {
            "goal": goal,
            "steps": [
                {"observation": step.observation, "decision": step.decision, "result": step.result}
                for step in steps
            ],
            "evidence": evidence,
            "runtime_state": {
                "app_hint": runtime_state.app_hint,
                "target_seen": runtime_state.target_seen,
                "active_window": runtime_state.active_window,
                "open_apps": list(runtime_state.open_apps),
                "browser_url": runtime_state.browser_url,
                "browser_title": runtime_state.browser_title,
                "observation": runtime_state.observation,
            },
            "complete": complete,
            "message": message,
            "updated_at": time.time(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload)
        except OSError as exc:
            logger.warning(f"ActionLoop: could not save checkpoint {path}: {exc}")

    def _decide(self, goal: str, observation: str, history: list[LoopStep], browser_mode: bool = False) -> dict[str, Any]:
        history_text = "\n".join(
            f"{i+1}. did {s.decision.get('action')}({ {k: v for k, v in s.decision.items() if k != 'action'} }) -> {s.result}"
            for i, s in enumerate(history[-6:])  # last 6 steps is enough context
        ) or "(none yet)"

        system_prompt = DECISION_SYSTEM_PROMPT + (BROWSER_PROMPT_ADDENDUM if browser_mode else "")
        # Keep one decision request small and bounded.  External pages can
        # have long labels, dynamic menus, or embedded JSON in controls; that
        # must not turn a single Observe→Decide iteration into an unbounded
        # model request.
        observation = observation[:8_000]
        user_msg = f"GOAL: {goal}\n\nCURRENT OBSERVATION:\n{observation}\n\nHISTORY:\n{history_text}"
        client = self._get_client()

        # A malformed/garbled JSON response is a transient model-formatting
        # glitch, not a genuine "I give up" signal -- confirmed live, a
        # response literally contained stray shell-prompt-looking text mixed
        # into the JSON ('{"user@host:~$ launch_app {"app_name": ...}').
        # Previously ANY single parse failure ended the WHOLE loop
        # immediately with success=False, wasting the entire run over one
        # bad token sample. Retry the same decision request a couple of
        # times before actually giving up.
        last_error_text = ""
        for attempt in range(3):
            try:
                logger.info(
                    f"ActionLoop: requesting decision {attempt + 1}/3 "
                    f"(observation_chars={len(observation)})"
                )
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=300,
                    temperature=0.1,
                    timeout=min(self.timeout, 25.0),
                )
            except Exception as exc:
                last_error_text = f"decision request failed: {type(exc).__name__}: {exc}"
                logger.warning(f"ActionLoop: {last_error_text}")
                continue
            text = (resp.choices[0].message.content or "").strip()
            # Strip accidental markdown fences
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            try:
                return json.loads(text)
            except Exception:
                last_error_text = text
                logger.warning(
                    f"ActionLoop: could not parse decision JSON (attempt {attempt+1}/3): {text!r}"
                )

        return {
            "action": "done",
            "success": False,
            "message": f"Model decision unavailable after 3 attempts: {last_error_text[:200]}",
        }

    def _ensure_focused(self, app_hint: str) -> bool:
        """
        Best-effort: bring the app matching `app_hint` to foreground before
        sending raw keyboard input. type_text()/hotkey() send keystrokes to
        whatever window the OS currently has focused -- with NO app_hint to
        aim at, they can silently land on the wrong window (confirmed live:
        keystrokes went to the terminal/editor instead of Calculator when
        this step was skipped). Silent-and-continue on failure, matching
        atspi_navigator._try_focus_app's own contract.
        """
        if not app_hint:
            return False
        try:
            from core.atspi_navigator import _try_focus_app, wait_for_app
            app = wait_for_app([app_hint], timeout=2.0)
            if app is None:
                return False
            return _try_focus_app(app)
        except Exception as exc:
            logger.debug(f"ActionLoop: focus attempt failed: {exc}")
            return False

    def _verify_done(self, claimed_success: bool, app_hint: str, message: str, browser_page: Any = None, goal: str = "") -> tuple[bool, str]:
        """
        Independent check before trusting the model's own "done" claim.
        Never let self-reported success stand alone -- a live run once
        hallucinated success on a calculator that actually showed a
        malformed expression, with zero real observation data backing the
        claim (see VERCEPT_LEVEL_ROADMAP.md §6). This re-observes fresh,
        independently of whatever the model said, before agreeing.

        A second live case (found later, worse): for a goal with NO specific
        app_hint -- common for multi-part compound goals like "...create a
        spreadsheet ... save it as X" -- the app_hint check below never
        applies at all, and the ONLY thing left to check was "did a fresh
        observation return something, anything", which is nearly always
        true. A run confidently claimed a spreadsheet was "created and saved
        as elonmusk" after only doing a Google search -- no such file existed
        anywhere. If the goal or the model's own message claims a file was
        saved/created, that claim is now checked against the real filesystem.
        """
        if not claimed_success:
            return False, message  # a claimed failure needs no extra scrutiny

        claimed_filename = _extract_claimed_filename(goal, message)
        if claimed_filename and not _claimed_file_exists(claimed_filename):
            return False, (
                f"UNVERIFIED (treated as failed): model claimed success ({message!r}) "
                f"mentioning a saved file named {claimed_filename!r}, but no such file "
                f"exists in the home directory, Desktop, Documents, or Downloads."
            )

        fresh = self._observe(app_hint, browser_page=browser_page)
        if not fresh or fresh.startswith("(no observation available"):
            return False, (
                f"UNVERIFIED (treated as failed): model claimed success ({message!r}) "
                f"but a fresh observation immediately afterward returned nothing -- "
                f"cannot confirm the target app is even still running."
            )
        target_absent = app_hint and fresh.startswith(f"App {app_hint!r} does not appear to be running")
        wrong_app = (
            app_hint
            and fresh.startswith("App:")
            and app_hint.lower() not in fresh.splitlines()[0].lower()
        )
        if target_absent or wrong_app:
            return False, (
                f"UNVERIFIED (treated as failed): model claimed success ({message!r}) "
                f"but a fresh observation no longer shows {app_hint!r} running at all."
            )

        claimed_result = _extract_claimed_result(message)
        if claimed_result:
            # Check specifically within observed TEXT-FIELD CONTENT (e.g. a
            # calculator's actual display value), not the whole observation
            # string -- a naive whole-text substring check is nearly useless
            # here since e.g. a calculator's own keypad always has a button
            # literally labeled "8" regardless of what its display shows,
            # which would make any claimed digit trivially "found". Verified
            # live: this exact gap let a hallucinated "result displayed is 8"
            # claim pass even though _extract_claimed_result correctly pulled
            # out "8" -- the bare `"8" in fresh` check matched the keypad
            # button, not the (empty) real display.
            content_values = re.findall(r"\(content: '([^']*)'\)", fresh)
            if not any(claimed_result == c.strip() for c in content_values):
                return False, (
                    f"UNVERIFIED (treated as failed): model claimed the display/result "
                    f"shows {claimed_result!r} ({message!r}), but no text field in a "
                    f"fresh observation of the actual screen contains that exact value -- "
                    f"treating this as a hallucinated claim rather than a verified fact."
                )
        if browser_page is not None:
            for claimed_text in _extract_claimed_browser_text(message):
                if self._normalized_browser_text(claimed_text) not in self._normalized_browser_text(fresh):
                    return False, (
                        f"UNVERIFIED (treated as failed): model claimed browser text "
                        f"{claimed_text!r} ({message!r}), but a fresh DOM observation "
                        "does not contain that text."
                    )
        return True, message

    @staticmethod
    def _normalized_browser_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().lower()

    def _decision_to_plan_step(
        self,
        decision: dict[str, Any],
        *,
        app_hint: str = "",
        browser_mode: bool = False,
    ):
        from core.structured_automation import PlanStep, validate_plan

        action = str(decision.get("action", "")).strip()
        expect = decision.get("expect", {})
        if not isinstance(expect, dict):
            raise TypeError("Adaptive decision expect must be an object")
        if action == "run_shell":
            raise PermissionError(
                "run_shell denied: adaptive approval does not authorize a generated command"
            )
        # "cap" decisions bypass structured vocabulary and dispatch the
        # universal capabilities/ registry directly (act() handles them);
        # validate only the shape here.
        if action == "cap":
            cap_name = str(decision.get("cap", "")).strip()
            if not cap_name:
                raise ValueError("cap decision requires a 'cap' capability name")
            if not isinstance(decision.get("args", {}), dict):
                raise ValueError("cap decision 'args' must be an object")
            return {"action": "cap", "cap": cap_name, "args": decision.get("args", {})}
        if action == "goto":
            url = str(decision.get("url", "")).strip()
            if url and not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            step = PlanStep("navigate", {"url": url}, expect)
        elif browser_mode and action == "click":
            step = PlanStep(
                "click",
                {
                    "text": str(decision.get("description", "")).strip(),
                    "near": str(decision.get("near", "")).strip(),
                },
                expect,
            )
        elif browser_mode and action == "select_option":
            step = PlanStep(
                "select_option",
                {
                    "label": str(decision.get("description", "")).strip(),
                    "option": str(decision.get("option", "")).strip(),
                },
                expect,
            )
        elif browser_mode and action == "type":
            field_name = str(decision.get("field", "")).strip()
            if not field_name:
                raise ValueError("Adaptive browser type requires an explicit field label")
            step = PlanStep(
                "fill",
                {"label": field_name, "text": str(decision.get("text", ""))},
                expect,
            )
        elif browser_mode and action == "key":
            step = PlanStep("press", {"key": str(decision.get("key", "Enter"))}, expect)
        elif browser_mode and action == "scroll":
            step = PlanStep(
                "scroll",
                {
                    "direction": str(decision.get("direction", "down")),
                    "amount_px": int(decision.get("amount_px", 600)),
                },
                expect,
            )
        elif browser_mode and action == "drag":
            step = PlanStep(
                "drag_drop",
                {
                    "source": str(decision.get("source", "")).strip(),
                    "target": str(decision.get("target", "")).strip(),
                },
                expect,
            )
        elif action == "launch_app":
            app = str(decision.get("app_name", "") or app_hint).strip()
            step = PlanStep("launch_app", {"app": app}, expect or {"app_running": True})
        elif action == "click":
            step = PlanStep(
                "click_ui",
                {
                    "app": app_hint,
                    "text": str(decision.get("description", "")).strip(),
                },
                expect,
            )
        elif action == "type":
            field_name = str(decision.get("field", "")).strip()
            if not field_name:
                raise ValueError("Adaptive native type requires an explicit field label")
            step = PlanStep(
                "type_ui",
                {"app": app_hint, "field": field_name, "text": str(decision.get("text", ""))},
                expect,
            )
        elif action == "key":
            step = PlanStep(
                "hotkey_ui",
                {"app": app_hint, "keys": [str(decision.get("key", "Enter"))]},
                expect,
            )
        elif action == "wait":
            step = PlanStep("wait", {"seconds": decision.get("seconds", 1.0)}, expect)
        elif action == "drag":
            step = PlanStep(
                "visual_drag",
                {
                    "app": app_hint,
                    "source": str(decision.get("source", "")).strip(),
                    "target": str(decision.get("target", "")).strip(),
                    "minimum_confidence": float(decision.get("minimum_confidence", 0.9)),
                    "duration_seconds": float(decision.get("duration_seconds", 0.8)),
                },
                expect,
            )
        else:
            raise ValueError(f"Unsupported adaptive action {action!r}")
        return validate_plan({
            "summary": "Adaptive structured step",
            "steps": [{"action": step.action, "args": step.args, "expect": step.expect}],
        }).steps[0]

    @staticmethod
    def _outcome_message(outcome: Any) -> str:
        if outcome.success:
            return f"structured {outcome.data.get('action', 'action')} confirmed"
        return outcome.error or outcome.state

    def _act(self, decision: dict[str, Any], app_hint: str = "", browser_page: Any = None) -> str:
        from core.structured_automation import StructuredExecutor

        executor = StructuredExecutor(approve_all=True)
        try:
            step = self._decision_to_plan_step(
                decision, app_hint=app_hint, browser_mode=browser_page is not None
            )
            outcome = executor.execute_step(step, page=browser_page)
            return self._outcome_message(outcome)
        except Exception as exc:
            return f"action raised exception: {exc}"
        finally:
            executor.close()


    def _act_browser(self, decision: dict[str, Any], page: Any) -> str:
        """Compatibility wrapper that dispatches through StructuredExecutor."""
        from core.structured_automation import StructuredExecutor

        class CompatibilityExecutor(StructuredExecutor):
            def _execute_browser_step(self, step):
                if step.action == "select_option":
                    selected = self._select(
                        str(step.args.get("label", "")), str(step.args["option"])
                    )
                    return {"selected": selected}
                return super()._execute_browser_step(step)

        executor = CompatibilityExecutor(approve_all=True)
        try:
            step = self._decision_to_plan_step(decision, browser_mode=True)
            outcome = executor.execute_step(step, page=page)
            if outcome.success and step.action == "select_option":
                return f"selected option {outcome.data['selected']!r}"
            return self._outcome_message(outcome)
        except Exception as exc:
            return f"browser action raised exception: {exc}"
        finally:
            executor.close()

    def build_runtime(
        self,
        goal: str,
        *,
        app_hint: str = "",
        browser_page: Any = None,
        approve_all: bool = False,
        approval_callback: Callable[[StructuredPlan], bool] | None = None,
        checkpoint_path: str | Path | None = None,
    ):
        """Expose this adaptive loop through the shared task runtime.

        This is the migration boundary for new entry points.  It deliberately
        reuses the proven ``StructuredExecutor`` and its bounded repair logic;
        the runtime becomes the durable owner of observations, outcomes, and
        final verification without creating another GUI execution mechanism.
        """
        from core.runtime_observer import RuntimeObserver
        from core.structured_automation import StructuredExecutor, StructuredPlanner
        from core.task_runtime import (
            RuntimeAction,
            RuntimeDecision,
            RuntimeSubgoalSpec,
            TaskRuntime,
        )

        # Universal OS capability registry — the capabilities/ layer becomes
        # reachable from the adaptive loop via the "cap" decision action.
        # Built once per runtime; unknown names fail gracefully at dispatch.
        universal_registry: Any = None

        def _universal_caps():
            nonlocal universal_registry
            if universal_registry is None:
                from core.runtime_adapters import build_runtime_registry
                universal_registry = build_runtime_registry(
                    executor=executor,
                    page=browser_page,
                    goal=goal,
                    approve_all=approve_all,
                    approval_callback=approval_callback,
                )
            return universal_registry

        browser_mode = browser_page is not None
        requested_browser = _detect_requested_browser(app_hint, goal)
        history: list[LoopStep] = []
        completion_message = {"value": ""}
        repair_planner = StructuredPlanner(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            timeout=self.timeout,
        )
        executor = StructuredExecutor(
            approve_all=approve_all,
            repair_callback=repair_planner.repair_step if repair_planner.available() else None,
            repair_approval_callback=approval_callback,
        )
        if browser_page is not None:
            executor.attach_page(browser_page)
        structured_observer = RuntimeObserver(
            page=browser_page,
            directories=[Path.home() / "Downloads"],
        )

        def observe(_runtime):
            nonlocal browser_page
            recovery: dict[str, Any] | None = None
            if browser_mode and not self._browser_page_readable(browser_page):
                recovered_page = self._recover_browser_page(requested_browser, goal)
                if recovered_page is not None:
                    browser_page = recovered_page
                    executor.attach_page(browser_page)
                    structured_observer.page = browser_page
                    recovery = {"recovered": True, "url": str(browser_page.url)}
            facts = structured_observer.observe()
            if recovery is not None:
                facts["browser_recovery"] = recovery
            if browser_mode and not self._browser_page_readable(browser_page):
                facts["browser_recovery_error"] = "Browser page is unavailable after one safe reconnect attempt"
            facts["text"] = self._observe(app_hint, browser_page=browser_page)
            return facts

        def decide(_runtime, observed):
            recovery_error = str(observed.get("browser_recovery_error", "")).strip()
            if recovery_error:
                return RuntimeDecision(
                    action=RuntimeAction("browser_unavailable", {"reason": recovery_error}, subgoal="browser recovery")
                )
            text = str(observed.get("text", ""))
            intervention = _human_intervention_reason(text)
            if intervention:
                return RuntimeDecision(
                    action=RuntimeAction("human_intervention", {"reason": intervention}, subgoal="human intervention")
                )
            decision_payload = self._decide(goal, text, history, browser_mode=browser_mode)
            raw_subgoals = decision_payload.get("subgoals", [])
            if not isinstance(raw_subgoals, list):
                raise TypeError("Adaptive decision subgoals must be a list")
            subgoals: list[RuntimeSubgoalSpec] = []
            for item in raw_subgoals:
                if not isinstance(item, dict):
                    raise TypeError("Adaptive decision subgoals must contain objects")
                dependencies = item.get("depends_on", [])
                if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
                    raise ValueError("Adaptive subgoal depends_on must be a list of ids")
                subgoals.append(RuntimeSubgoalSpec(
                    id=str(item.get("id", "")).strip(),
                    description=str(item.get("description", "")).strip(),
                    depends_on=tuple(dependencies),
                ))
            if decision_payload.get("action") == "done":
                completion_message["value"] = str(decision_payload.get("message", ""))
                if bool(decision_payload.get("success", False)):
                    return RuntimeDecision(complete=True, message=completion_message["value"], subgoals=tuple(subgoals))
                return RuntimeDecision(
                    action=RuntimeAction("planner_stopped", {"message": completion_message["value"]}),
                    subgoals=tuple(subgoals),
                )
            return RuntimeDecision(
                action=RuntimeAction(
                    str(decision_payload.get("action", "")),
                    {"decision": decision_payload},
                    subgoal=str(decision_payload.get("subgoal", "")).strip(),
                ),
                subgoals=tuple(subgoals),
            )

        def act(action, _runtime):
            from core.task_contract import ActionOutcome

            if action.capability == "human_intervention":
                return ActionOutcome(
                    state="paused_for_human",
                    error=f"Human intervention required for {action.args['reason']}",
                    dispatch_status="not_attempted",
                    side_effect="read",
                )
            if action.capability == "browser_unavailable":
                return ActionOutcome(
                    state="blocked",
                    error=str(action.args["reason"]),
                    dispatch_status="not_attempted",
                    side_effect="read",
                )
            if action.capability == "planner_stopped":
                return ActionOutcome(
                    state="failed",
                    error=str(action.args.get("message") or "Planner stopped without success"),
                    dispatch_status="not_attempted",
                    side_effect="read",
                )
            # Universal capability dispatch: the decision named a capability
            # from the capabilities/ registry (fs.*, calc.*, sheet.*, verify.*,
            # wait.*, data.*, ...).  Route it through the real registry with
            # its contracts instead of the structured vocabulary.
            if action.capability == "cap":
                payload = dict(action.args.get("decision", action.args))
                cap_name = str(payload.get("cap", "")).strip()
                cap_args = payload.get("args", {})
                if not cap_name or not isinstance(cap_args, dict):
                    return ActionOutcome(
                        state="failed",
                        error="cap decision requires 'cap' (name) and 'args' (object)",
                        dispatch_status="not_attempted",
                        side_effect="read",
                    )
                try:
                    registry = _universal_caps()
                    from core.task_runtime import RuntimeAction as _RA
                    outcome = registry.execute(_RA(cap_name, cap_args), _runtime)
                except Exception as exc:
                    outcome = ActionOutcome(
                        state="failed",
                        error=f"capability dispatch raised {type(exc).__name__}: {exc}",
                        dispatch_status="unknown",
                        side_effect="read",
                    )
                history.append(LoopStep("", {"action": "cap", "cap": cap_name, **cap_args}, self._outcome_message(outcome)))
                return outcome
            decision_payload = dict(action.args["decision"])
            try:
                step = self._decision_to_plan_step(
                    decision_payload, app_hint=app_hint, browser_mode=browser_mode
                )
                outcome = executor.execute_step(
                    step,
                    goal=goal,
                    approval_callback=approval_callback,
                    page=browser_page,
                )
            except Exception as exc:
                outcome = ActionOutcome(
                    state="failed",
                    error=str(exc),
                    dispatch_status="not_attempted",
                    side_effect="read",
                )
            history.append(LoopStep("", decision_payload, self._outcome_message(outcome)))
            return outcome

        def verify(action, outcome, before, after, _runtime):
            if not outcome.success:
                return False
            if action.capability in {"wait", "scroll"}:
                return True
            before_text = str(before.get("text", "")).strip()
            after_text = str(after.get("text", "")).strip()
            return bool(after_text) and after_text != before_text

        def final_verify(_runtime, _observed):
            return self._verify_done(
                True,
                app_hint,
                completion_message["value"],
                browser_page=browser_page,
                goal=goal,
            )[0]

        runtime = TaskRuntime(
            observe=observe,
            decide=decide,
            act=act,
            verify=verify,
            final_verify=final_verify,
            checkpoint_path=checkpoint_path,
            max_steps=self.max_steps,
        )
        # The runtime owns the executor for the whole goal.  Close it when a
        # terminal state is reached, never between individual adaptive steps.
        original_run = runtime.run

        def run_with_cleanup(*args, **kwargs):
            try:
                return original_run(*args, **kwargs)
            finally:
                executor.close()

        runtime.run = run_with_cleanup  # type: ignore[method-assign]
        return runtime

    def run_runtime(
        self,
        goal: str,
        app_hint: str = "",
        *,
        approve_all: bool = False,
        approval_callback: Callable[[StructuredPlan], bool] | None = None,
        checkpoint_path: str | Path | None = None,
        resume: bool = False,
    ) -> LoopResult:
        """Run an adaptive goal through ``TaskRuntime``.

        The return type intentionally remains ``LoopResult`` so callers can
        migrate one entry point at a time.  The native ``run`` method remains
        available while its richer browser reconnect and legacy-checkpoint
        behavior is migrated into the shared runtime.
        """
        if not self.available():
            return LoopResult(False, "APINEX_API_KEY not set -- ActionLoop unavailable")

        browser_mode = _is_browser_task(app_hint, goal)
        browser_page: Any = None
        if browser_mode:
            browser_page = self._get_browser_page(_detect_requested_browser(app_hint, goal), goal)
            if browser_page is None:
                return LoopResult(False, "Could not acquire a readable browser page for safe DOM automation")

        try:
            runtime = self.build_runtime(
                goal,
                app_hint=app_hint,
                browser_page=browser_page,
                approve_all=approve_all,
                approval_callback=approval_callback,
                checkpoint_path=checkpoint_path,
            )
            if resume:
                if checkpoint_path is None:
                    return LoopResult(False, "Runtime resume requires a checkpoint path")
                from core.task_runtime import load_runtime_state

                state = load_runtime_state(checkpoint_path)
                if state.goal != goal:
                    return LoopResult(False, "Runtime checkpoint belongs to a different goal")
                result = runtime.resume(state)
            else:
                result = runtime.run(goal)
            steps: list[LoopStep] = []
            evidence = []
            for event in result.state.events:
                evidence.append(event.payload())
                decision = event.action.get("args", {}).get("decision")
                if event.phase == "action" and isinstance(decision, dict):
                    steps.append(LoopStep(
                        str(event.observation.get("text", "")),
                        decision,
                        str(event.outcome.get("error") or event.outcome.get("state", "")),
                    ))
            return LoopResult(result.success, result.state.message, steps, evidence)
        finally:
            self._close_playwright()

    def run_dynamic(
        self,
        goal: str,
        app_hint: str = "",
        *,
        approve_all: bool = False,
        approval_callback: Callable[[StructuredPlan], bool] | None = None,
        checkpoint_path: str | Path | None = None,
        resume: bool = False,
    ) -> LoopResult:
        """Choose the authoritative runtime when its adapter is available.

        New native and browser actions use ``TaskRuntime``.  The legacy loop
        is kept only for resuming legacy checkpoints, whose in-progress
        action state has not yet been migrated to the runtime format.
        """
        if resume:
            if checkpoint_path is not None:
                try:
                    from core.task_runtime import load_runtime_state

                    runtime_state = load_runtime_state(checkpoint_path)
                    if runtime_state.goal == goal:
                        return self.run_runtime(
                            goal,
                            app_hint=app_hint,
                            approve_all=approve_all,
                            approval_callback=approval_callback,
                            checkpoint_path=checkpoint_path,
                            resume=True,
                        )
                except (OSError, ValueError, TypeError):
                    # This is an existing legacy ActionLoop checkpoint.
                    pass
            # Runtime checkpoints are durable, but automatic replay of an
            # interrupted action is refused.  Legacy checkpoints retain their
            # existing session-aware recovery behavior until migrated.
            return self.run(
                goal,
                app_hint=app_hint,
                approve_all=approve_all,
                approval_callback=approval_callback,
                checkpoint_path=checkpoint_path,
                resume=True,
            )
        return self.run_runtime(
            goal,
            app_hint=app_hint,
            approve_all=approve_all,
            approval_callback=approval_callback,
            checkpoint_path=checkpoint_path,
        )


    def run(
        self,
        goal: str,
        app_hint: str = "",
        *,
        approve_all: bool = False,
        approval_callback: Callable[[StructuredPlan], bool] | None = None,
        checkpoint_path: str | Path | None = None,
        resume: bool = False,
    ) -> LoopResult:
        if not self.available():
            return LoopResult(success=False, message="APINEX_API_KEY not set -- ActionLoop unavailable")

        from core.structured_automation import StructuredExecutor, StructuredPlanner

        browser_mode = _is_browser_task(app_hint, goal)
        requested_browser = _detect_requested_browser(app_hint, goal)
        browser_page: Any = None
        if browser_mode:
            browser_page = self._get_browser_page(requested_browser, goal)
            browser_mode = browser_page is not None

        # Adaptive decisions and structured repairs use the same bounded
        # planner credential, but repairs remain executor-owned: they receive
        # fresh semantic evidence and must pass the exact replacement-plan
        # approval gate before dispatch.  Without this bridge, an adaptive
        # run stopped on a renamed browser/native control even though the
        # shared executor already had safe repair machinery.
        repair_planner = StructuredPlanner(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            timeout=self.timeout,
        )
        executor = StructuredExecutor(
            approve_all=approve_all,
            repair_callback=(
                repair_planner.repair_step if repair_planner.available() else None
            ),
            repair_approval_callback=approval_callback,
        )
        if browser_page is not None:
            executor.attach_page(browser_page)
        path = Path(checkpoint_path) if checkpoint_path else None
        restored = self._load_checkpoint(path, goal) if resume and path else None
        steps: list[LoopStep] = restored[0] if restored else []
        structured_evidence: list[dict[str, Any]] = restored[1] if restored else []
        observation = self._observe(app_hint, browser_page=browser_page)
        runtime_state = self._capture_runtime_state(
            observation,
            app_hint=app_hint,
            browser_page=browser_page,
            previous=restored[2] if restored else None,
        )
        structured_evidence.append(
            runtime_state.evidence(phase="resumed" if restored else "initial", step=len(steps))
        )

        def finish(success: bool, message: str, *, complete: bool = True) -> LoopResult:
            self._save_checkpoint(
                path,
                goal=goal,
                steps=steps,
                evidence=structured_evidence,
                runtime_state=runtime_state,
                complete=complete,
                message=message,
            )
            return LoopResult(success=success, message=message, steps=steps, evidence=structured_evidence)
        logger.info(
            f"ActionLoop: starting goal={goal!r} browser_mode={browser_mode} "
            f"browser={requested_browser or 'chrome (default)'}"
        )
        try:
            for i in range(len(steps), self.max_steps):
                if browser_mode and not self._browser_page_readable(browser_page):
                    previous_url = runtime_state.browser_url
                    browser_page = self._recover_browser_page(requested_browser, goal)
                    if browser_page is None:
                        message = (
                            "Controlled browser page became unavailable and one reconnect "
                            "attempt could not restore a readable tab"
                        )
                        structured_evidence.append({
                            "step": i + 1,
                            "action": "browser_recovery",
                            "recovered": False,
                            "previous_url": previous_url,
                            "message": message,
                        })
                        logger.warning(f"ActionLoop: {message}")
                        return finish(False, message, complete=False)
                    executor.attach_page(browser_page)
                    observation = self._observe(app_hint, browser_page=browser_page)
                    runtime_state = self._capture_runtime_state(
                        observation,
                        app_hint=app_hint,
                        browser_page=browser_page,
                        previous=runtime_state,
                    )
                    structured_evidence.append({
                        "step": i + 1,
                        "action": "browser_recovery",
                        "recovered": True,
                        "previous_url": previous_url,
                        "url": runtime_state.browser_url,
                        "title": runtime_state.browser_title,
                    })
                intervention = _human_intervention_reason(observation)
                if intervention:
                    message = (
                        f"Human intervention required for {intervention}; automatic continuation refused"
                    )
                    logger.warning(f"ActionLoop: {message}")
                    return finish(False, message)
                runtime_problem = self._runtime_problem(
                    runtime_state,
                    self._capture_runtime_state(
                        observation,
                        app_hint=app_hint,
                        browser_page=browser_page,
                        previous=runtime_state,
                    ),
                    browser_mode=browser_mode,
                )
                if runtime_problem:
                    logger.warning(f"ActionLoop: {runtime_problem}")
                    return finish(False, runtime_problem)
                # Persist before the remote planning request.  A process or
                # browser crash during decision-making has dispatched no UI
                # action, but without this checkpoint it would erase the last
                # verified observation and make safe resume impossible to
                # distinguish from an untouched run.
                structured_evidence.append({
                    "step": i + 1,
                    "action": "decision_request",
                    "browser_url": runtime_state.browser_url,
                    "observation_chars": len(observation),
                })
                self._save_checkpoint(
                    path,
                    goal=goal,
                    steps=steps,
                    evidence=structured_evidence,
                    runtime_state=runtime_state,
                    complete=False,
                    message="Awaiting adaptive planner decision",
                )
                decision = self._decide(goal, observation, steps, browser_mode=browser_mode)
                structured_evidence.append({
                    "step": i + 1,
                    "action": "decision_response",
                    "decision_action": str(decision.get("action", "")),
                })
                logger.info(f"ActionLoop[{i+1}/{self.max_steps}]: decision={decision}")

                if decision.get("action") == "done":
                    claimed_success = bool(decision.get("success", False))
                    claimed_message = str(decision.get("message", ""))
                    success, message = self._verify_done(
                        claimed_success,
                        app_hint,
                        claimed_message,
                        browser_page=browser_page,
                        goal=goal,
                    )
                    if claimed_success and not success:
                        logger.warning(
                            f"ActionLoop: verification gate REJECTED a claimed success -- {message}"
                        )
                    steps.append(LoopStep(observation, decision, "loop ended"))
                    logger.info(f"ActionLoop: done (success={success}): {message}")
                    return finish(success, message)

                if decision.get("action") == "cap":
                    # Universal capability dispatch through the shared registry
                    # (same path as build_runtime's act()).
                    try:
                        from core.runtime_adapters import build_runtime_registry
                        cap_name = str(decision.get("cap", "")).strip()
                        cap_args = decision.get("args", {})
                        if not isinstance(cap_args, dict):
                            cap_args = {}
                        if not hasattr(self, "_universal_registry") or self._universal_registry is None:
                            self._universal_registry = build_runtime_registry(
                                executor=executor,
                                page=browser_page,
                                goal=goal,
                                approve_all=approve_all,
                                approval_callback=approval_callback,
                            )
                        from core.task_runtime import RuntimeAction as _RA
                        from core.task_runtime import RuntimeState as _RS
                        cap_state = _RS(goal=goal)
                        outcome = self._universal_registry.execute(
                            _RA(cap_name, cap_args), cap_state
                        )
                    except Exception as exc:
                        from core.task_contract import ActionOutcome

                        outcome = ActionOutcome(
                            state="failed",
                            error=f"capability dispatch failed: {exc}",
                            dispatch_status="unknown",
                            side_effect="read",
                        )
                    structured_evidence.extend(outcome.evidence)
                    result = self._outcome_message(outcome)
                    steps.append(LoopStep(observation, decision, result))
                    logger.info(f"ActionLoop[{i+1}/{self.max_steps}] cap {decision.get('cap')}: result={result}")
                    # cap outcomes (e.g. file writes) often don't change the
                    # screen; skip the unchanged-screen hallucination guard
                    # for verified capability results (their evidence carries
                    # the verification instead of the screen diff).
                    if outcome.state in ("failed", "uncertain"):
                        observation = self._observe(app_hint, browser_page=browser_page)
                        if outcome.state == "uncertain":
                            return finish(False, f"Capability {decision.get('cap')} returned uncertain outcome: {outcome.error}")
                        # failed caps are recoverable: continue the loop so the
                        # model can diagnose and choose a different route
                    continue

                try:
                    structured_step = self._decision_to_plan_step(
                        decision,
                        app_hint=app_hint,
                        browser_mode=browser_mode,
                    )
                    outcome = executor.execute_step(
                        structured_step,
                        goal=goal,
                        evidence=structured_evidence,
                        approval_callback=approval_callback,
                        page=browser_page,
                    )
                except Exception as exc:
                    from core.task_contract import ActionOutcome

                    outcome = ActionOutcome(
                        state="failed",
                        error=str(exc),
                        dispatch_status="not_attempted",
                        side_effect="read",
                    )
                structured_evidence.extend(outcome.evidence)
                result = self._outcome_message(outcome)
                post_observation = self._observe(app_hint, browser_page=browser_page)
                post_runtime_state = self._capture_runtime_state(
                    post_observation,
                    app_hint=app_hint,
                    browser_page=browser_page,
                    previous=runtime_state,
                )
                structured_evidence.append({
                    "step": i + 1,
                    "action": "post_action_observation",
                    "observation": post_observation,
                })
                structured_evidence.append(
                    post_runtime_state.evidence(phase="post_action", step=i + 1)
                )

                # ── Per-step hallucination guard (Vercept-level: verify every step) ──────
                # If the action claimed success but the screen didn't change at all,
                # this is a likely hallucination. Warn and treat as uncertain so the
                # loop doesn't blindly repeat a no-op action until max_steps.
                _skip_actions = {"wait", "done", "scroll"}
                if (
                    outcome.state == "succeeded"
                    and decision.get("action") not in _skip_actions
                    and post_observation.strip() == observation.strip()
                    and observation.strip()  # only flag if we had a real observation
                ):
                    _hall_msg = (
                        f"Step {i+1}: action '{decision.get('action')}' claimed success "
                        f"but screen observation is unchanged — possible hallucination. "
                        f"Marking uncertain and stopping."
                    )
                    logger.warning(f"ActionLoop: {_hall_msg}")
                    structured_evidence.append({
                        "step": i + 1,
                        "action": "hallucination_guard",
                        "message": _hall_msg,
                    })
                    return finish(False, _hall_msg)
                # ─────────────────────────────────────────────────────────────────────────

                logger.info(f"ActionLoop[{i+1}/{self.max_steps}]: result={result}")
                steps.append(LoopStep(observation, decision, result))
                observation = post_observation
                runtime_state = post_runtime_state
                self._save_checkpoint(
                    path,
                    goal=goal,
                    steps=steps,
                    evidence=structured_evidence,
                    runtime_state=runtime_state,
                    complete=False,
                )

                if outcome.state == "uncertain":
                    message = f"Adaptive structured action has uncertain outcome: {outcome.error}"
                    logger.warning(message)
                    return finish(False, message)
                if outcome.state == "blocked":
                    return finish(False, outcome.error or "Adaptive structured action was not approved")

            return finish(
                False,
                f"max_steps ({self.max_steps}) reached without completion",
                complete=False,
            )
        finally:
            executor.close()
            self._close_playwright()


# Singleton — picks up ALOOP_MAX_STEPS and ALOOP_TIMEOUT from environment at import time.
action_loop = ActionLoop()

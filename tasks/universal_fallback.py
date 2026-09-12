"""
Universal fallback — catch-all handler for completely unknown commands.

This is the last-resort task, tried only when no other intent matches.

Strategy (attempted in order, returns True on first success):
  1. URL check       — if raw_command looks like a URL, open with xdg-open
  2. CLI Registry    — check config/cli_registry.yaml for a matching known
                       task pattern (Layer 1, see ARCHITECTURE_EVOLUTION.md)
  3. Adaptive loop   — Layer 2 (PROMOTED, see note below): general-purpose
                       observe/decide/act/verify loop (core/action_loop.py),
                       multi-step, no dedicated task file needed, no
                       coordinate guessing. Needs APINEX_API_KEY.
  4. AT-SPI Navigator — Layer 3: if raw_command names an already-running
                       app + an action ("in nautilus create new folder"),
                       read that app's live AT-SPI tree and click the best
                       match — single click only (see core/atspi_navigator.py)
  5. Electron Navigator — Layer 4: same idea as #4, for Electron apps via CDP
  6. Semantic Vision — Layer 5: cloud vision model, single click, last resort
                       before blind guessing
  7. Known binary    — if the first token of normalized is on PATH, run it
  8. Token scan      — scan all tokens; run the first one found on PATH
  9. Record & fail   — append to ~/.config/desktop_automation/unknown_commands.txt
                       and print a user-friendly tip, then return False

Why the adaptive loop (#3) is tried BEFORE the single-shot layers (#4-#6):
Layers 4-6 can only ever perform ONE click or ONE type action before giving
up -- they cannot handle anything that genuinely needs multiple steps. The
adaptive loop is a strict superset of what they can do (it can finish in a
single step too, just via one LLM decision call instead of a keyword-scored
guess), so trying it first means any fallback-routed command gets a real,
human-like "look at the screen, decide, act, look again" attempt by
default -- not only as a last resort after weaker layers already failed.
When APINEX_API_KEY isn't configured, the adaptive loop is silently
skipped and layers 4-6 run exactly as before -- no behavior change for
users without that key configured.

Args:
    raw_command (str): Original user input.
    normalized  (str): Normalized/cleaned input (may equal raw_command).
"""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from core.logger import finish, notify, start

_URL_RE    = re.compile(r"https?://|[\w-]+\.[a-z]{2,}", re.IGNORECASE)
_LOG_FILE  = Path.home() / ".config" / "desktop_automation" / "unknown_commands.txt"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _looks_like_url(text: str) -> str | None:
    """
    Return the URL string if text contains a URL, else None.
    Prefers explicit http(s):// prefix; also matches bare domain patterns.
    """
    # Prefer explicit scheme first
    m = re.search(r"https?://\S+", text, re.IGNORECASE)
    if m:
        return m.group(0)
    # Bare domain pattern (e.g. "google.com", "github.com/user/repo")
    m = re.search(r"\b[\w-]+\.[a-z]{2,}\S*", text, re.IGNORECASE)
    if m:
        return m.group(0)
    return None


_APP_ACTION_PATTERNS = [
    re.compile(r"\bin\s+(?P<app>[a-zA-Z][\w-]{2,})\s+(?P<action>.{3,})", re.IGNORECASE),
    re.compile(r"(?P<action>.{3,}?)\s+in\s+(?P<app>[a-zA-Z][\w-]{2,})\s*$", re.IGNORECASE),
]
_APP_ACTION_STOPWORDS = {"the", "and", "then", "it", "a", "my", "this"}


def _extract_app_action(text: str) -> tuple[str, str] | None:
    """
    Best-effort extraction of (app_name, action_phrase) from patterns like
    "in nautilus create new folder" or "create new folder in files".

    Returns None if no confident match — deliberately conservative. A wrong
    app-name guess here would route an AT-SPI click at the wrong running
    application, so this only returns a result when the sentence shape is
    unambiguous ("... in <app> ..."), never a fuzzy guess.
    """
    for pattern in _APP_ACTION_PATTERNS:
        m = pattern.search(text)
        if m:
            app = m.group("app").strip().lower()
            action = m.group("action").strip()
            if app and action and app not in _APP_ACTION_STOPWORDS:
                return app, action
    return None


def _record_unknown(raw_command: str) -> None:
    """Append timestamp + raw_command to the unknown-commands log file."""
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] {raw_command}\n")
        logger.debug(f"Unknown command logged to {_LOG_FILE}")
    except OSError as exc:
        logger.debug(f"Could not write to unknown-commands log: {exc}")


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "universal_fallback"
    start(task_name)
    try:
        raw_command = str(args.get("raw_command", "")).strip()
        normalized  = str(args.get("normalized",  raw_command)).strip()

        logger.info(f"Universal fallback triggered for: {raw_command!r}")

        # ── 1. URL check ──────────────────────────────────────────────────────
        url = _looks_like_url(raw_command)
        if url:
            logger.info(f"Detected URL — opening: {url}")
            try:
                subprocess.Popen(
                    ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info(f"✅ Opened URL: {url}")
                notify(f"Opening: {url}")
                finish("success", task_name)
                return True
            except OSError as exc:
                logger.debug(f"xdg-open failed: {exc}")

        # ── 2. CLI Registry ─ known task pattern -> proven shell command ───────
        try:
            from core.cli_registry import try_run as _cli_registry_try_run
            if _cli_registry_try_run(raw_command):
                logger.info("✅ Handled by CLI registry (Layer 1)")
                finish("success", task_name)
                return True
        except ImportError as exc:
            logger.debug(f"cli_registry unavailable: {exc}")

        # ── 3. Adaptive closed loop ─ Layer 2 (PROMOTED): observe/decide/act ──
        # See the module docstring above for why this runs here, ahead of
        # the single-shot layers (4-6) below, instead of after them.
        try:
            from core.action_loop import action_loop
            if action_loop.available():
                app_hint = ""
                app_action = _extract_app_action(raw_command)
                if app_action:
                    app_hint = app_action[0]
                logger.info(f"Adaptive loop (Layer 2): trying goal={normalized!r} app_hint={app_hint!r}")
                run_kwargs = {
                    "app_hint": app_hint,
                    "approve_all": bool(resources.get("approve_all", False)),
                    "approval_callback": resources.get("approval_callback"),
                }
                loop_result = action_loop.run_dynamic(normalized, **run_kwargs)
                if loop_result.success:
                    logger.info(f"✅ Handled by adaptive loop (Layer 2): {loop_result.message}")
                    notify(f"{normalized[:60]} (adaptive loop)")
                    finish("success", task_name)
                    return True
                logger.debug(f"Adaptive loop: did not complete -- {loop_result.message}")
            else:
                logger.debug("Adaptive loop unavailable: APINEX_API_KEY not set")
        except ImportError as exc:
            logger.debug(f"action_loop unavailable: {exc}")
        except Exception as exc:
            logger.debug(f"Adaptive loop error: {exc}")

        # Legacy direct mutation layers do not provide typed evidence, exact
        # per-step approval, or uncertainty boundaries. Keep them available
        # only under explicit --yes while migration to StructuredExecutor finishes.
        if not bool(resources.get("approve_all", False)):
            logger.warning(
                "Universal fallback stopped after unified adaptive execution; "
                "legacy direct UI layers require explicit --yes"
            )
            _record_unknown(raw_command)
            finish("error", task_name)
            return False

        # ── 4. AT-SPI Navigator ─ Layer 3: any already-running GTK/Qt app ────
        # Only fires when an app name + action phrase are BOTH confidently
        # extracted AND that app is actually running right now — never
        # guesses wildly. See core/atspi_navigator.py and
        # TASK_KNOWLEDGE_BASE.md Part 9.
        try:
            from core.atspi_navigator import click_by_intent, wait_for_app
            app_action = _extract_app_action(raw_command)
            if app_action:
                app_frag, action_phrase = app_action
                if wait_for_app([app_frag], timeout=1.5) is not None:
                    logger.info(f"AT-SPI navigator: trying app={app_frag!r} action={action_phrase!r}")
                    nav_result = click_by_intent([app_frag], action_phrase, app_timeout=3.0)
                    if nav_result.ok:
                        logger.info(f"✅ Handled by AT-SPI navigator (Layer 2): {action_phrase!r} in {app_frag!r}")
                        notify(f"{action_phrase} ({app_frag})")
                        finish("success", task_name)
                        return True
                    logger.debug(f"AT-SPI navigator: no match/failed — {nav_result.reason}")
        except ImportError as exc:
            logger.debug(f"atspi_navigator unavailable: {exc}")

        # ── 5. Electron Navigator ─ Layer 4: VS Code, Slack, Discord, etc. ──────
        # Only fires for KNOWN Electron apps when app name + action are confidently
        # extracted. Launches isolated instance with CDP if not already running.
        # Bypasses window-focus requirement that blocks Layer 2 (see TASK_KNOWLEDGE_BASE Part 14).
        # Uses dedicated ports per app to avoid conflicts; isolated user_data_dir
        # prevents disrupting the user's main session.
        try:
            from core.electron_navigator import (
                click_by_intent,
                ensure_electron_cdp,
                type_by_intent,
            )
            
            # Known Electron apps with dedicated CDP ports (non-overlapping)
            ELECTRON_APPS = {
                "code":       {"port": 9333, "binary": "code",       "aliases": ["vscode", "vs code"]},
                "slack":      {"port": 9334, "binary": "slack",      "aliases": []},
                "discord":    {"port": 9335, "binary": "discord",    "aliases": []},
                "teams":      {"port": 9336, "binary": "teams",      "aliases": ["msteams"]},
                "obsidian":   {"port": 9337, "binary": "obsidian",   "aliases": []},
            }
            
            def _extract_electron_app_action(text: str) -> tuple[str, str] | None:
                """
                Extract (app_key, action_phrase) for KNOWN Electron apps only.
                
                Deliberately conservative: only matches apps in ELECTRON_APPS registry.
                Patterns supported:
                  - "in vscode click file" / "in code open terminal"
                  - "click file in vscode" / "open terminal in code"
                  - "code new terminal" / "vscode open file"
                Returns None if no confident match — never guesses.
                """
                text_lower = text.lower()
                for app_key, info in ELECTRON_APPS.items():
                    names = [app_key] + info["aliases"]
                    for name in names:
                        if name in text_lower:
                            patterns = [
                                rf"\bin\s+{re.escape(name)}\s+(?P<action>.{{3,}})",
                                rf"(?P<action>.{{3,}}?)\s+in\s+{re.escape(name)}\s*$",
                                rf"^{re.escape(name)}\s+(?P<action>.{{3,}})",
                            ]
                            for pat in patterns:
                                m = re.search(pat, text_lower)
                                if m:
                                    action = m.group("action").strip()
                                    if action and action not in _APP_ACTION_STOPWORDS:
                                        return app_key, action
                return None
            
            ea = _extract_electron_app_action(raw_command)
            if ea:
                app_key, action_phrase = ea
                info = ELECTRON_APPS[app_key]
                port = info["port"]
                binary = info["binary"]
                
                # Isolated user_data_dir to avoid disrupting user's main session
                user_data_dir = f"/tmp/electron_navigator_{app_key}_{os.getuid()}"
                
                logger.info(f"Electron navigator: ensuring {binary!r} on port {port} for action {action_phrase!r}")
                if ensure_electron_cdp(binary, port, user_data_dir=user_data_dir, timeout=20.0):
                    nav_result = click_by_intent(port, action_phrase, timeout=8.0)
                    if nav_result.ok:
                        logger.info(f"✅ Handled by Electron navigator (Layer 3): {action_phrase!r} in {app_key!r}")
                        notify(f"{action_phrase} ({app_key})")
                        finish("success", task_name)
                        return True
                    logger.debug(f"Electron navigator: no match/failed — {nav_result.reason}")
                else:
                    logger.debug(f"Electron navigator: could not start CDP for {binary}")

            # ── Electron typing support: "in vscode type hello", "type hello in code" ────
            def _extract_electron_type(text: str) -> tuple[str, str] | None:
                """
                Extract (app_key, text_to_type) for typing in Electron apps.
                Patterns:
                  - "in vscode type hello world"
                  - "type hello in code"
                  - "code type hello"
                """
                text_lower = text.lower()
                for app_key, info in ELECTRON_APPS.items():
                    names = [app_key] + info["aliases"]
                    for name in names:
                        if name in text_lower:
                            patterns = [
                                rf"\bin\s+{re.escape(name)}\s+type\s+(?P<typetext>.+)",
                                rf"type\s+(?P<typetext>.+)\s+in\s+{re.escape(name)}\s*$",
                                rf"^{re.escape(name)}\s+type\s+(?P<typetext>.+)",
                            ]
                            for pat in patterns:
                                m = re.search(pat, text_lower)
                                if m:
                                    typetext = m.group("typetext").strip()
                                    if typetext:
                                        return app_key, typetext
                return None
            
            et = _extract_electron_type(raw_command)
            if et:
                app_key, text_to_type = et
                info = ELECTRON_APPS[app_key]
                port = info["port"]
                binary = info["binary"]
                
                user_data_dir = f"/tmp/electron_navigator_{app_key}_{os.getuid()}"
                
                logger.info(f"Electron navigator: ensuring {binary!r} on port {port} for typing")
                if ensure_electron_cdp(binary, port, user_data_dir=user_data_dir, timeout=20.0):
                    # Try to type into the most relevant input (search, command palette, etc.)
                    # Use the action phrase as context for finding the right input
                    type_result = type_by_intent(port, "search input command palette", text_to_type, press_enter=False)
                    if type_result.ok:
                        logger.info(f"✅ Typed in Electron app (Layer 3): {text_to_type!r} in {app_key!r}")
                        notify(f"Typed in {app_key}")
                        finish("success", task_name)
                        return True
                    logger.debug(f"Electron navigator typing: no match/failed — {type_result.reason}")
                else:
                    logger.debug(f"Electron navigator: could not start CDP for {binary}")
        except ImportError as exc:
            logger.debug(f"electron_navigator unavailable: {exc}")
        except Exception as exc:
            logger.debug(f"Electron navigator error: {exc}")

        # -- 6. Semantic Vision -- Layer 5: cloud vision model, single click --
        # Only used when nothing else matched. Needs NVIDIA_API_KEY set; silently
        # skipped otherwise (see core/semantic_vision.py). Uses the (normalized)
        # command text itself as the description of what to find and click.
        try:
            from core.semantic_vision import semantic_vision
            if semantic_vision.api_key:
                logger.info(f"Semantic vision (Layer 5): trying description={normalized!r}")
                vision_result = semantic_vision.find_and_click(normalized)
                if vision_result.success:
                    logger.info(f"Handled by semantic vision (Layer 5) via {vision_result.method}")
                    notify(f"{normalized[:60]} (vision)")
                    finish("success", task_name)
                    return True
                logger.debug(f"Semantic vision: no match -- {vision_result.error}")
            else:
                logger.debug("Semantic vision unavailable: NVIDIA_API_KEY not set")
        except ImportError as exc:
            logger.debug(f"semantic_vision unavailable: {exc}")
        except Exception as exc:
            logger.debug(f"Semantic vision error: {exc}")

        # ── 7. Raw shell fallback removed ─────────────────────────────────────
        # SAFETY: this path runs raw, un-vetted text via shell=True purely
        # because its first word happens to resolve to a real binary on
        # PATH — which is true for rm/mv/dd/kill/sudo etc. by default on
        # any Linux system. A single smart_parser misfire could otherwise
        # execute something like "rm -rf ~" with zero further checks. The
        # is_dangerous_shell_command() guard below blocks known-catastrophic
        # patterns before anything runs (see TASK_KNOWLEDGE_BASE.md / the
        # "perfection" pass this was added in).
        # Natural-language fallback must never become shell code merely because
        # its first token exists on PATH. Explicit shell execution has its own
        # consequential run_command intent and approval boundary.
        tokens = normalized.split()

        # ── 8. Token scan ─ find any runnable binary among all tokens ─────
        # SAFETY: this only ever launches a single bare token with no
        # arguments (subprocess.Popen([tok])) — it can't express "rm -rf /"
        # by construction, so no additional guard is needed here.
        for tok in tokens:
            if len(tok) >= 3 and shutil.which(tok):
                logger.info(f"Token scan found binary: {tok!r} — launching")
                try:
                    subprocess.Popen(
                        [tok],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    logger.info(f"✅ Launched binary from token: {tok!r}")
                    notify(f"Launched: {tok}")
                    finish("success", task_name)
                    return True
                except OSError as exc:
                    logger.debug(f"Popen({tok!r}) failed: {exc}")

        # ── 9. Record & fail ──────────────────────────────────────────────────
        _record_unknown(raw_command)

        print(
            f"\n💡 Could not execute: {raw_command!r}\n"
            f"   Logged to: ~/.config/desktop_automation/unknown_commands.txt\n"
            f"   Tip: Add a custom intent in core/smart_parser.py INTENT_DEFS\n"
            f"        or create a task in tasks/ and wire it in agent.py\n"
        )
        logger.warning(f"Universal fallback exhausted all strategies for: {raw_command!r}")
        finish("error", task_name)
        return False

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"raw_command": "do something weird", "normalized": "do something weird"}, r)
    cleanup(r)

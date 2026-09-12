#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║              AUTONOMOUS DESKTOP AGENT                             ║
║  Self-healing execution — no AI, no internet, no API key.        ║
║                                                                   ║
║  Usage:                                                           ║
║    python agent.py "install vlc"                                  ║
║    python agent.py "yt pe rrr song chalao"                        ║
║    python agent.py "watsap pe darling ko hi bhejo"                ║
║    python agent.py "take ss"                                      ║
║    python agent.py --workflow daily_cleanup                       ║
║    python agent.py --list                                         ║
╚═══════════════════════════════════════════════════════════════════╝

How it works (zero AI):
  1. SmartParser   — fuzzy keyword scoring + multilingual word map
                     understands typos, romanized Hindi/Telugu, slang
  2. StrategyExecutor — tries multiple strategies per task in order
                        best-first based on historical success
  3. Verifier      — OCR + AT-SPI + CLI confirm each action worked
  4. Memory        — records every win/fail, adapts order next run
  5. WorkflowEngine — parallel/sequential multi-step orchestration
                      with exponential back-off retry
"""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.session import Session
    from core.structured_automation import StructuredPlan
    from core.task_dag import TaskDAG


def _load_secrets_env() -> None:
    """
    Load config/secrets.env (plain KEY=value, gitignored) into os.environ.

    Without this, APINEX_API_KEY / NVIDIA_API_KEY only ever reach the
    process when launched via scripts/agent_prompt.sh or the systemd service
    (both `source` the file themselves) -- a direct `python agent.py "..."`
    run from a terminal never saw them, silently disabling the AI tiers
    (core/llm_planner.py Tier 5, core/semantic_vision.py Layer 4,
    core/action_loop.py) even when a key was configured. That made the agent
    look like it only ever runs the ~20 hardcoded deterministic intents.

    Real exported env vars / systemd's EnvironmentFile= always win -- this
    only fills in what isn't already set.
    """
    path = Path(__file__).resolve().parent / "config" / "secrets.env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_secrets_env()

from loguru import logger

from core.smart_parser import ParsedIntent, smart_parser
from core.strategy_executor import Strategy, StrategyExecutor
from core.verifier import (
    VerifySpec,
    app_installed_spec,
    browser_open_spec,
    whatsapp_open_spec,
)
from core.workflow_engine import WorkflowEngine
from tasks import list_tasks

# ─────────────────────────────────────────────────────────────────────────────
# TASK DISPATCH  — maps intent → StrategyExecutor with strategies + verify specs
# ─────────────────────────────────────────────────────────────────────────────

def _build_executor(intent: ParsedIntent) -> tuple[StrategyExecutor, dict[str, Any], dict[str, Any]]:
    """
    Return (executor, args, resources) for a parsed intent.
    Each intent gets multiple ordered strategies + verification specs.
    """
    name   = intent.intent
    params = intent.params
    res: dict[str, Any] = {}

    # ── Install app ─────────────────────────────────────────────────────────────────────
    if name == "install_app":
        app = params.get("app_name", "").strip()
        if not app:
            raise ValueError("Could not extract app name from your command")

        from core.app_registry import normalize_package
        pkg  = normalize_package(app)
        # Privileged credentials must come from the process environment or an
        # interactive policy agent, never tracked YAML configuration.
        _pwd = os.getenv("AUTOMATION_SUDO_PASSWORD", "")
        args = {"app_name": app, "package_name": pkg, "password": _pwd}
        spec = app_installed_spec(pkg)

        def atspi_fn(a, r):
            from tasks.atspi_install import execute
            return execute(a, r)

        # Pure GUI flow: only AT-SPI App Center strategy (no CLI fallback)
        ex = StrategyExecutor("install_app")
        ex.add(Strategy("gui_appcenter", atspi_fn, verify_spec=spec, retry_wait=5,
                        max_retries=1))
        return ex, args, res

    # ── YouTube ───────────────────────────────────────────────────────────────
    if name == "youtube":
        query = params.get("query", "").strip() or "trending music"
        args  = {"search_query": query, "play_first": True}
        spec  = VerifySpec(expect_text=["youtube"])

        def yt_fn(a, r):
            from tasks.youtube_automation import execute
            return execute(a, r)

        ex = StrategyExecutor("youtube")
        ex.add(Strategy("youtube", yt_fn, verify_spec=spec, retry_wait=3))
        return ex, args, res

    # ── WhatsApp ────────────────────────────────────────────────────────────────────────
    if name == "whatsapp_send":
        import re as _re
        contact = params.get("contact", "").strip()
        message = params.get("message", "").strip()
        raw_lo  = intent.raw_input.lower()

        # ─ Known contact aliases ─────────────────────────────────────────
        _known_contacts = [
            "darling","mom","dad","papa","mama","bhai","bhaiya",
            "didi","sis","bro","friend","yaar","janu","baby",
        ]
        # Words that are DEFINITELY NOT contact names
        _not_contact = {
            "to","send","msg","message","me","in","hi","hello","hey",
            "whatsapp","watsap","open","browser","chat","bhejo","bhej",
            "pe","ko","ka","say","saying","text","on","and","good",
            "morning","night","evening","afternoon","gm","gn",
            "you","i","we","they","he","she","it","are","is","am",
            "was","were","have","has","how","what","when","where",
            "a","an","the","of","for","with","from","by","at","via",
        }
        _msg_filler = {
            "msg","message","send","bhejo","bhej",
            "whatsapp","watsap","open","browser","chat",
            "saying","say","text","to","on","via","at",
        }
        # Greetings scan list — longest first to prefer multi-word matches
        _greetings = [
            "good morning","good night","good evening","good afternoon","good day",
            "how are you","what's up","i love you","love you","miss you",
            "thinking of you","take care","stay safe",
            "happy birthday","congratulations","congrats",
            "hello","hi","hey","hii","helo","namaste",
            "okay","ok","yes","no","gm","gn",
        ]

        # ─ Step 1: Recover contact ───────────────────────────────────────
        if not contact or contact.lower() in _not_contact:
            for kc in _known_contacts:            # check known aliases first
                if kc in raw_lo:
                    contact = kc; break
            else:
                m = _re.search(r'\bto\s+(\w+)', raw_lo)
                if m and m.group(1).lower() not in _not_contact:
                    contact = m.group(1)
                else:
                    # SAFETY: only fall back to a default real contact when
                    # the input literally, normalizedly mentions WhatsApp --
                    # checked as an exact token, not fuzzy/substring scoring.
                    # Without this guard, whatsapp_send can be reached by
                    # keyword-overlap coincidence alone (e.g. "what apps are
                    # running" scores whatsapp_send > 0 purely because "what"
                    # is a literal prefix-substring of "whatsapp" and fuzzy-
                    # matches "chat") and would otherwise silently send a
                    # REAL message to a REAL contact on a total misparse.
                    if "whatsapp" not in intent.normalized_input.split():
                        raise ValueError(
                            "whatsapp_send matched but no real contact could be "
                            "extracted, and the input doesn't literally mention "
                            "WhatsApp -- refusing to guess a default contact"
                        )
                    contact = "darling"

        # ─ Step 2: Recover message — ALWAYS try greeting scan first ────────
        # This supersedes whatever the parser extracted because parser
        # extraction is unreliable for messages embedded in long commands.
        found_greeting = ""
        for gr in sorted(_greetings, key=len, reverse=True):
            if gr in raw_lo:
                found_greeting = gr; break

        if found_greeting:
            message = found_greeting          # greeting always wins
        elif message and message.lower() not in _msg_filler:
            # Parser gave something meaningful — strip filler/contact/prepositions
            clean_words = [
                w for w in message.split()
                if w.lower() not in (_msg_filler | _not_contact | {contact.lower()})
            ]
            message = " ".join(clean_words).strip() or "hi"
        else:
            message = "hi"                    # safe fallback

        logger.info(f"WhatsApp → contact={contact!r}  message={message!r}")
        args = {"contact": contact, "message": message}
        spec    = whatsapp_open_spec()

        def wa_fn(a, r):
            from tasks.whatsapp_send import execute
            return execute(a, r)

        ex = StrategyExecutor("whatsapp_send")
        ex.add(Strategy("whatsapp", wa_fn, verify_spec=spec, retry_wait=5))
        return ex, args, res

    # ── Screenshot ────────────────────────────────────────────────────────────
    if name in ("screenshot", "system_screenshot"):
        args = {}

        def ss_fn(a, r):
            from tasks.system_screenshot import execute
            return execute(a, r)

        ex = StrategyExecutor("system_screenshot")
        ex.add(Strategy("screenshot", ss_fn, retry_wait=1))
        return ex, args, res

    # ── Open browser / visit URL ──────────────────────────────────────────────
    # ── Open browser / visit URL ──────────────────────────────────────────────────────────────────
    if name in ("open_browser", "visit_url"):
        # Re-extract URL from raw input — normalization strips dots so
        # "github.com" becomes "github com" and the regex fails.
        import re as _re
        url = params.get("url", "")
        if not url:
            m = _re.search(
                r'https?://\S+|[\w-]+\.[a-z]{2,}(?:/\S*)?',
                intent.raw_input, _re.IGNORECASE
            )
            url = m.group(0) if m else "https://www.google.com"
        if url and not url.startswith("http"):
            url = f"https://{url}"

        # Respect a specific browser actually named in the request (e.g.
        # "open firefox browser") instead of always hardcoding chromium.
        # Without this, ANY mention of a browser name routes here (this is
        # the generic open_browser intent) and the user's literal choice
        # was being silently discarded in favor of Chrome every time.
        browser_m = _re.search(r'\b(firefox|chromium|chrome|brave)\b', intent.raw_input, _re.IGNORECASE)
        browser = browser_m.group(1).lower() if browser_m else "chromium"

        args = {"url": url, "browser": browser, "screenshot": False}
        spec = browser_open_spec(browser=browser if browser == "firefox" else "")

        def browser_fn(a, r):
            from tasks.open_browser_and_visit import execute
            return execute(a, r)

        ex = StrategyExecutor("open_browser")
        ex.add(Strategy("browser", browser_fn, verify_spec=spec, retry_wait=3))
        return ex, args, res

    # ── Search web ────────────────────────────────────────────────────────────
    if name == "search_web":
        import re as _re
        query = params.get("query", "").strip()
        url   = f"https://www.google.com/search?q={query.replace(' ', '+')}"

        # Same fix as open_browser/visit_url above: respect a specific
        # browser named in the request (e.g. "search cats in firefox")
        # instead of always hardcoding chromium.
        browser_m = _re.search(r'\b(firefox|chromium|chrome|brave)\b', intent.raw_input, _re.IGNORECASE)
        browser = browser_m.group(1).lower() if browser_m else "chromium"

        args  = {"url": url, "browser": browser, "screenshot": False}

        def search_fn(a, r):
            from tasks.open_browser_and_visit import execute
            return execute(a, r)

        ex = StrategyExecutor("search_web")
        ex.add(Strategy("search", search_fn, verify_spec=browser_open_spec(browser=browser if browser == "firefox" else ""), retry_wait=3))
        return ex, args, res

    # ── Open app ──────────────────────────────────────────────────────────────
    if name == "open_app":
        app  = params.get("app_name", "").strip()
        args = {"app_name": app}

        def open_fn(a, r):
            from tasks.open_system_app import execute
            return execute(a, r)

        ex = StrategyExecutor("open_app")
        ex.add(Strategy("open", open_fn, retry_wait=2))
        return ex, args, res

    # ── Window management ─────────────────────────────────────────────────────────────────
    if name in ("window_minimize", "window_maximize", "window_close"):
        op  = name.split("_")[1]   # "minimize" | "maximize" | "close"
        win = params.get("window", "")  # empty = active window
        args = {"operation": op, "window": win}

        def win_fn(a, r):
            from tasks.window_management import execute
            return execute(a, r)

        ex = StrategyExecutor(name)
        ex.add(Strategy("window", win_fn, retry_wait=1))
        return ex, args, res

    # ── File delete ───────────────────────────────────────────────────────────
    if name == "file_delete":
        file = params.get("file_name", "").strip()
        args = {"operation": "delete", "file_name": file}

        def del_fn(a, r):
            from tasks.file_operations import execute
            return execute(a, r)

        ex = StrategyExecutor("file_delete")
        ex.add(Strategy("delete", del_fn, retry_wait=1))
        return ex, args, res

    # ── Organize downloads ────────────────────────────────────────────────────
    if name == "organize_downloads":
        args = {
            "create_subfolders": {
                "Images":    ["jpg", "jpeg", "png", "gif", "webp"],
                "Documents": ["pdf", "doc", "docx", "txt"],
                "Archives":  ["zip", "tar", "gz", "7z", "rar"],
                "Code":      ["py", "js", "ts", "sh", "yaml"],
            },
            "move_videos": True,
            "delete_junk": True,
        }

        def org_fn(a, r):
            from tasks.organize_downloads import execute
            return execute(a, r)

        ex = StrategyExecutor("organize_downloads")
        ex.add(Strategy("organize", org_fn, retry_wait=2))
        return ex, args, res

    # ── Wait ───────────────────────────────────────────────────────────────────────────
    if name == "wait_seconds":
        secs = int(params.get("seconds", "2"))
        args = {"seconds": secs}

        def wait_fn(a, r):
            from tasks.wait_seconds import execute
            return execute(a, r)

        ex = StrategyExecutor("wait_seconds")
        ex.add(Strategy("wait", wait_fn, retry_wait=0))
        return ex, args, res

    # ── Type text ────────────────────────────────────────────────────────────────────
    if name == "type_text":
        text = params.get("text", "").strip()
        if not text:
            raise ValueError("Could not extract text to type from your command")
        args = {"text": text, "app_name": params.get("app_name", "").strip()}

        def type_fn(a, r):
            from tasks.type_text import execute
            return execute(a, r)

        ex = StrategyExecutor("type_text")
        ex.add(Strategy("type", type_fn, retry_wait=1))
        return ex, args, res

    # ── Volume control ────────────────────────────────────────────────────────────────
    if name == "volume_control":
        raw_action = params.get("action", "").strip().lower()
        # Normalize variants: increase/decrease → up/down
        action_map = {"increase": "up", "decrease": "down"}
        action = action_map.get(raw_action, raw_action) or "up"
        level  = params.get("level", "50").strip()
        args   = {"action": action, "level": level}

        def vol_fn(a, r):
            from tasks.volume_control import execute
            return execute(a, r)

        ex = StrategyExecutor("volume_control")
        ex.add(Strategy("volume", vol_fn, retry_wait=1))
        return ex, args, res

    # ── Brightness control ──────────────────────────────────────────────────────────
    if name == "brightness_control":
        raw_action = params.get("action", "").strip().lower()
        # Map synonyms to canonical values
        action_map = {"increase": "up", "decrease": "down", "dim": "down", "brighten": "up"}
        action = action_map.get(raw_action, raw_action) or "up"
        level  = params.get("level", "50").strip()
        args   = {"action": action, "level": level}

        def bright_fn(a, r):
            from tasks.brightness_control import execute
            return execute(a, r)

        ex = StrategyExecutor("brightness_control")
        ex.add(Strategy("brightness", bright_fn, retry_wait=1))
        return ex, args, res

    # ── Lock screen ────────────────────────────────────────────────────────────────────
    if name == "lock_screen":
        args = {}

        def lock_fn(a, r):
            from tasks.lock_screen import execute
            return execute(a, r)

        ex = StrategyExecutor("lock_screen")
        ex.add(Strategy("lock", lock_fn, retry_wait=1))
        return ex, args, res

    # ── System power (shutdown / restart / suspend) ────────────────────────────────
    if name == "system_power":
        action = params.get("action", "shutdown").strip().lower() or "shutdown"
        args   = {"action": action, "confirm": True}

        def power_fn(a, r):
            from tasks.system_power import execute
            return execute(a, r)

        ex = StrategyExecutor("system_power")
        ex.add(Strategy("power", power_fn, retry_wait=1))
        return ex, args, res

    # ── Create folder ───────────────────────────────────────────────────────────────
    if name == "create_folder":
        folder_name = params.get("folder_name", "").strip()
        if not folder_name:
            # Do not silently substitute a broader executor here. The caller must
            # authorize universal fallback using the exact parameters it executes.
            raise ValueError("Could not extract folder name from your command")

        desktop = Path.home() / "Desktop"
        location = str(desktop if desktop.exists() else Path.home())
        args = {"folder_name": folder_name, "location": location}

        def folder_fn(a, r):
            from tasks.create_folder import execute
            return execute(a, r)

        ex = StrategyExecutor("create_folder")
        ex.add(Strategy("create_folder", folder_fn, retry_wait=1))
        return ex, args, res

    # ── Run shell command ───────────────────────────────────────────────────────────
    if name == "run_command":
        import re as _re
        # Re-extract from the ORIGINAL (non-normalized) input so flags like
        # -la / --verbose / special chars are preserved intact.
        cmd = ""
        for trigger in ("execute", "shell", "command", "run"):
            m = _re.search(rf"(?i)\b{trigger}\b\s+(.*)", intent.raw_input)
            if m:
                cmd = m.group(1).strip()
                break
        if not cmd:
            cmd = params.get("command", "").strip()   # normalized fallback
        if not cmd:
            raise ValueError("Could not extract command to run from your input")
        args = {"command": cmd, "authorized": True}

        def cmd_fn(a, r):
            from tasks.run_command import execute
            return execute(a, r)

        ex = StrategyExecutor("run_command")
        ex.add(Strategy("shell", cmd_fn, retry_wait=1))
        return ex, args, res

    # ── Hotkey / keyboard shortcut ──────────────────────────────────────────────────
    if name == "hotkey":
        keys = params.get("keys", "").strip()
        if not keys:
            # No trigger word matched — scan normalized input for known key names
            _KEY_NAMES = {
                "ctrl","alt","shift","super","win","tab","enter","return",
                "escape","esc","backspace","delete","del","home","end",
                "space","up","down","left","right",
                "f1","f2","f3","f4","f5","f6","f7","f8","f9","f10","f11","f12",
            }
            tokens = intent.normalized_input.split()
            found  = [t for t in tokens if t.lower() in _KEY_NAMES or (len(t)==1 and t.isalpha())]
            keys   = " ".join(found)
        args = {"keys": keys}

        def hotkey_fn(a, r):
            from tasks.hotkey import execute
            return execute(a, r)

        ex = StrategyExecutor("hotkey")
        ex.add(Strategy("hotkey", hotkey_fn, retry_wait=1))
        return ex, args, res

    # ── Klavaro exercise ────────────────────────────────────────────────────────────────
    if name == "klavaro_exercise":
        exercise = params.get("exercise", "").strip().lower() or "velocity"
        valid_modes = {"velocity", "adaptability", "basic", "fluidness"}
        if exercise not in valid_modes:
            exercise = "velocity"
        args = {"exercise": exercise}

        def klav_fn(a, r):
            from tasks.klavaro_automation import execute
            return execute(a, r)

        ex = StrategyExecutor("klavaro_exercise")
        ex.add(Strategy("klavaro", klav_fn, retry_wait=5))
        return ex, args, res

    # ── System info ─────────────────────────────────────────────────────────────────────
    if name == "system_info":
        import re as _re
        # Pull /tmp/xxx.txt path from raw input if parser missed it
        save_path = params.get("save_path", "")
        if not save_path:
            m = _re.search(r'(/tmp/\S+\.txt|~/\S+\.txt|/home/\S+\.txt)', intent.raw_input)
            save_path = m.group(1) if m else "/tmp/system_info.txt"
        args = {"save_path": save_path}

        def sysinfo_fn(a, r):
            from tasks.system_info import execute
            return execute(a, r)

        ex = StrategyExecutor("system_info")
        ex.add(Strategy("sysinfo", sysinfo_fn, retry_wait=1))
        return ex, args, res

    # ── Open terminal ───────────────────────────────────────────────────────────────────
    if name == "open_terminal":
        args = {}

        def term_fn(a, r):
            import subprocess
            for t in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal", "bash"]:
                try:
                    subprocess.Popen([t], start_new_session=True,
                                     stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    print(f"✅ Opened terminal: {t}")
                    return True
                except FileNotFoundError:
                    continue
            return False

        ex = StrategyExecutor("open_terminal")
        ex.add(Strategy("terminal", term_fn, retry_wait=1))
        return ex, args, res

    # ── Kill / stop process ─────────────────────────────────────────────────────────────
    if name == "process_kill":
        process_name = params.get("process_name", "").strip()
        args = {"process_name": process_name}

        def kill_fn(a, r):
            import subprocess
            pname = a.get("process_name", "").strip()
            if not pname:
                print("\u274c No process name given")
                return False

            # Resolve through the same full-application index open_app uses,
            # so "kill gnome calculator process" or a fuzzy/partial name
            # still targets the real binary (e.g. "gnome-calculator") instead
            # of pkill -f-ing a raw, unmatched string that will never hit a
            # real process (a hyphen vs. space mismatch alone would silently
            # no-op otherwise).
            candidates = [pname]
            cleaned = pname
            for filler in ("process", "app", "application", "window"):
                cleaned = cleaned.replace(filler, "").strip()
            if cleaned and cleaned != pname:
                candidates.append(cleaned)

            from core.app_finder import find_app
            for candidate in candidates:
                found = find_app(candidate)
                if found:
                    binary = found.exec_cmd.split()[0].split("/")[-1] if found.exec_cmd else ""
                    if binary and binary not in candidates:
                        candidates.append(binary)
                    break

            for candidate in candidates:
                proc = subprocess.run(["pkill", "-f", candidate], capture_output=True, check=False)
                if proc.returncode == 0:
                    print(f"\u2705 Killed process matching: {candidate!r}")
                    return True

            print(f"\u26a0\ufe0f  No process matching any of {candidates} found")
            return False

        ex = StrategyExecutor("process_kill")
        ex.add(Strategy("kill", kill_fn, retry_wait=1))
        return ex, args, res

    # ── Desktop notification ────────────────────────────────────────────────────────────
    if name == "notify":
        title   = params.get("title", "Agent").strip() or "Agent"
        message = params.get("message", "").strip() or intent.raw_input
        args = {"title": title, "message": message}

        def notify_fn(a, r):
            import subprocess
            title = a.get("title", "Agent")
            message = a.get("message", "")
            try:
                subprocess.run(["notify-send", title, message], check=True)
                print(f"\u2705 Notification sent: {title} \u2014 {message}")
                return True
            except Exception as exc:
                print(f"\u274c Failed to send notification: {exc}")
                return False

        ex = StrategyExecutor("notify")
        ex.add(Strategy("notify", notify_fn, retry_wait=1))
        return ex, args, res

    # ── File read ───────────────────────────────────────────────────────────────────────
    if name == "file_read":
        import re as _re
        file_path = params.get("file_path", "")
        if not file_path:
            m = _re.search(r'(/tmp/\S+|~/\S+|/home/\S+|\./\S+)', intent.raw_input)
            file_path = m.group(1) if m else ""
        args = {"file_path": file_path}

        def read_fn(a, r):
            from pathlib import Path
            fp = Path(a.get("file_path", "").replace("~", str(Path.home())))
            if not fp.exists():
                print(f"❌ File not found: {fp}")
                return False
            print(fp.read_text())
            return True

        ex = StrategyExecutor("file_read")
        ex.add(Strategy("read", read_fn, retry_wait=1))
        return ex, args, res

    # ── File write ──────────────────────────────────────────────────────────────────────
    if name == "file_write":
        import re as _re
        file_path = params.get("file_path", "")
        if not file_path:
            m = _re.search(r'(/tmp/\S+|~/\S+|/home/\S+)', intent.raw_input)
            file_path = m.group(1) if m else "/tmp/agent_output.txt"
        content = params.get("content", "").strip() or ""
        args = {"file_path": file_path, "content": content}

        def write_fn(a, r):
            from pathlib import Path
            fp = Path(a["file_path"].replace("~", str(Path.home())))
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(a.get("content", "") + "\n")
            print(f"✅ Written to {fp}")
            return True

        ex = StrategyExecutor("file_write")
        ex.add(Strategy("write", write_fn, retry_wait=1))
        return ex, args, res

    # Never hide a broader fallback behind approval for a narrower parsed intent.
    # `run_command` owns fallback authorization and binds it to exact parameters.
    raise ValueError(f"No executor defined for intent {name!r}")


# ─────────────────────────────────────────────────────────────────────────────
# RUN ONE NATURAL LANGUAGE COMMAND
# ─────────────────────────────────────────────────────────────────────────────

def run_command(
    raw: str,
    *,
    approved_actions: set[str] | None = None,
    approve_all: bool = False,
    structured_plan_approver: Callable[["StructuredPlan"], bool] | None = None,
) -> bool:
    """
    Parse a natural language string and execute with full self-healing.
    Supports compound commands: "install vlc and then open youtube".
    """
    import re as _re

    from core.action_policy import approved, requires_approval

    def is_approved(intent_name: str, params: dict[str, Any]) -> bool:
        return approve_all or approved(intent_name, params, approved_actions)

    def fallback_params(command: str, normalized: str | None = None) -> dict[str, Any]:
        return {
            "raw_command": command,
            "normalized": normalized if normalized is not None else command,
        }

    def run_fallback(command: str, normalized: str | None = None) -> bool:
        args = fallback_params(command, normalized)
        if not is_approved("universal_fallback", args):
            print("Refusing universal fallback without explicit approval for this exact command.")
            return False
        from tasks.universal_fallback import execute as _fallback
        return bool(_fallback(args, {
            "approve_all": approve_all,
            "approval_callback": structured_plan_approver,
        }))

    # ── Klavaro shortcut ──────────────────────────────────────────────────────────────
    # Intercept BEFORE parse_multi splits on "and"/"then".
    # The full sentence is preserved so we can find the exercise mode anywhere in it.
    if _re.search(r'\bklavaro\b', raw, _re.IGNORECASE):
        m = _re.search(
            r'\b(velocity|adaptability|basic|fluidness)\b', raw, _re.IGNORECASE
        )
        exercise = m.group(1).lower() if m else "velocity"
        print(f"\n🎯 Klavaro detected — exercise: {exercise}")
        logger.info(f"Klavaro shortcut: exercise={exercise!r} from {raw!r}")
        from tasks.klavaro_automation import execute as _klav
        ok = _klav({"exercise": exercise}, {})
        print(f"   {'\u2705 Done' if ok else '\u274c Failed'}")
        return ok

    # Design-platform shortcut (Canva / Figma / VistaCreate template automation).
    # Ported from the now-retired legacy/intent_parser.py path so this pipeline
    # is the single place that understands it (see VERCEPT_LEVEL_ROADMAP.md).
    _design_m = _re.search(
        r'\bopen\s+(?:a\s+|the\s+)?(?P<platform>canva|figma|vistacreate)\b'
        r'(?:\s+design)?(?:\s+with\s+(?:a\s+|the\s+)?(?P<template>.+?)\s+template)?\b',
        raw, _re.IGNORECASE,
    )
    if _design_m:
        platform = _design_m.group("platform").lower()
        template = (_design_m.group("template") or "Instagram Story").strip()
        print(f"\n🎨 Design platform detected — {platform}, template: {template!r}")
        logger.info(f"Design-platform shortcut: platform={platform!r} template={template!r} from {raw!r}")
        from tasks.canva_template import (
            cleanup as _ct_cleanup,
        )
        from tasks.canva_template import (
            execute as _ct_exec,
        )
        from tasks.canva_template import (
            setup as _ct_setup,
        )
        _res = _ct_setup()
        try:
            ok = _ct_exec({"platform": platform, "template": template}, _res)
        finally:
            _ct_cleanup(_res)
        print(f"   {'✅ Done' if ok else '❌ Failed'}")
        return ok

    # App-running-status shortcut (Step 4 of the Ubuntu-control build order:
    # "detect running application" as a directly queryable capability, not
    # just an internal post-launch check). Intercepted before smart_parser
    # since no INTENT_DEFS entry cleanly separates this from open_app/
    # window_close given how much keyword overlap ('open', 'running') exists.
    _running_list_m = _re.search(
        r'\b(list|show)\b.*\b(running|open)\s+(apps|applications)\b'
        r'|\b(apps|applications)\b.*\b(are\s+)?(running|open)\b',
        raw, _re.IGNORECASE,
    )
    if _running_list_m:
        from core.app_state import list_running_apps
        apps = list_running_apps()
        print(f"\n\U0001f5a5\ufe0f  {len(apps)} running application(s):")
        for a in apps:
            print(f"  \u2022 {a}")
        return True

    _running_check_m = _re.search(
        r'\bis\s+(?P<app>.+?)\s+(running|open)\b', raw, _re.IGNORECASE,
    )
    if _running_check_m:
        app_name = _running_check_m.group("app").strip()
        from core.app_state import is_app_running
        running = is_app_running(app_name)
        print(f"\n\U0001f5a5\ufe0f  {app_name!r} running? {'\u2705 yes' if running else '\u274c no'}")
        return running

    # System info shortcut
    _sysinfo_pats = [
        r'\b(system\s*info|sysinfo|hardware\s*info|system\s*specs|system\s*report)\b',
        r'\bcollect\b.*(ubuntu|kernel|cpu|ram|gpu|disk)',
        r'\b(ubuntu|kernel|cpu|ram|gpu|disk).*(version|info|model|capacity)',
    ]
    if any(_re.search(p, raw, _re.IGNORECASE) for p in _sysinfo_pats):
        import re as _re2
        m = _re2.search(r'(/tmp/\S+\.txt|~/\S+\.txt|/home/\S+\.txt)', raw)
        save_path = m.group(1) if m else "/tmp/system_info.txt"
        print(f"\n\U0001f5a5\ufe0f  System info collecting to {save_path}")
        from tasks.system_info import execute as _sysinfo
        ok = _sysinfo({"save_path": save_path}, {})
        print(f"   {'\u2705 Done' if ok else '\u274c Failed'}")
        return bool(ok)

    intents = smart_parser.parse_multi(raw)

    # Tier 5: AI planner escalation for complex/unreliable-looking prose.
    # Only fires if the deterministic parse looks untrustworthy for this
    # input AND an NVIDIA_API_KEY is configured; otherwise fully inert and
    # behavior is unchanged from before this tier existed.
    from core.llm_planner import (
        is_confidently_resolvable_open_app,
        llm_planner,
        looks_gui_shaped,
        looks_unreliable,
    )
    if (looks_unreliable(intents, raw)
            and not is_confidently_resolvable_open_app(intents)
            and llm_planner.available()):
        logger.info("run_command: deterministic parse looks unreliable for this input, escalating")

        # Plan the whole goal once, then execute deterministic local primitives.
        # A valid structured plan owns this run: if one step fails, stop with
        # concrete evidence rather than restarting the entire goal through
        # multiple adaptive/LLM fallback layers.
        from core.structured_automation import (
            plan_and_execute_runtime as run_structured,
        )
        print("\nCreating one structured plan, then executing it locally...")
        structured_result = run_structured(
            raw,
            approve_all=approve_all,
            approval_callback=structured_plan_approver,
        )
        if structured_result is not None:
            print(f"   {structured_result.message}")
            if structured_result.evidence:
                print(f"   Verified steps: {structured_result.completed_steps}")
            return structured_result.success

        # GUI-shaped goals (mentions a known app + real interaction, e.g.
        # "compute 12x7 in calculator") go to the adaptive action_loop.py
        # FIRST, ahead of the upfront-script planner. A one-shot bash script
        # can only tell whether a delegated `agent.py "..."` call exited 0,
        # not whether the click/type actually landed -- confirmed live to
        # produce a false PASS on a calculator task (VERCEPT_LEVEL_ROADMAP.md).
        # The adaptive loop re-observes after every action and independently
        # verifies before accepting "done", so it gets first shot at exactly
        # the goals where that distinction matters most. action_loop.py needs
        # the same APINEX_API_KEY as llm_planner, so llm_planner.available()
        # already tells us whether this branch is even worth trying.
        gui_app_hint = looks_gui_shaped(raw)
        adaptive_approved = is_approved("universal_fallback", fallback_params(raw))
        if gui_app_hint and adaptive_approved:
            from core.action_loop import action_loop
            if action_loop.available():
                print(f"\nParse looks unreliable and this looks like a GUI task in {gui_app_hint!r} -- trying the adaptive loop first...")
                loop_result = action_loop.run_dynamic(
                    raw,
                    app_hint=gui_app_hint,
                    approve_all=approve_all,
                    approval_callback=structured_plan_approver,
                )
                if loop_result.success:
                    print(f"   Adaptive loop succeeded: {loop_result.message}")
                    return True
                print(f"   Adaptive loop did not confirm success ({loop_result.message}) -- trying AI script planner...")

        if not adaptive_approved:
            print("Refusing adaptive and AI-script fallback without explicit approval for this exact command.")
            return False

        if not approve_all:
            print("Skipping AI-generated shell plan: it requires explicit --yes authorization.")
            return False

        print("\nParse looks unreliable for this instruction -- trying AI planner...")
        ai_ok = llm_planner.plan_and_execute(raw, authorized=True)
        if ai_ok:
            print("   AI plan succeeded")
            return True
        if ai_ok is False:
            # A failed upfront script doesn't mean the goal is impossible --
            # universal_fallback still has AT-SPI/Electron/vision layers and
            # the adaptive closed loop (action_loop.py) left to try, which
            # can succeed at GUI-shaped goals a one-shot bash script can't.
            # Don't give up on the first attempt when smarter layers remain.
            #
            # IMPORTANT: this must call universal_fallback on the FULL raw
            # instruction and return its result directly -- NOT fall through
            # to the `intents` list below. That list is the same deterministic
            # parse that was already judged unreliable enough to escalate to
            # the AI planner in the first place (that's why we're here at
            # all). Falling through to re-run it clause-by-clause used to
            # silently execute garbled per-clause intents (e.g. a fragment
            # like "tell me the result" coincidentally AT-SPI-clicking some
            # unrelated on-screen element and reporting a false "Done") while
            # the real Layer 5 adaptive loop was never actually invoked on
            # the whole goal, despite the message below promising it would be.
            print("   AI plan failed -- trying universal fallback (adaptive loop, vision, etc.)")
            fb_ok = run_fallback(raw)
            print(f"   {'✅ Done' if fb_ok else '❌ Failed'} (universal fallback)")
            return fb_ok
        else:
            print("   AI planner unavailable/failed -- falling back to deterministic parse")

    if not intents:
        logger.warning(f"SmartParser: no intent matched for {raw!r} — trying universal fallback")
        print(f"\n⚠️  No intent matched for: {raw!r}")
        print("   Trying universal fallback...")
        ok = run_fallback(raw)
        print(f"   {'✅ Done' if ok else '❌ Failed'}")
        return ok

    overall = True
    for intent in intents:
        print(f"\n🎯 Intent: {intent.intent}  (confidence={intent.confidence:.0%})")
        print(f"   Params: {intent.params}")
        if requires_approval(intent.intent, intent.params) and not is_approved(intent.intent, intent.params):
            print(f"   Refusing consequential action {intent.intent!r} without explicit approval (--yes or UI confirmation).")
            overall = False
            continue
        try:
            executor, args, resources = _build_executor(intent)
            ok = executor.run(args, resources)
        except ValueError as exc:
            # Param extraction failed — still try universal fallback
            logger.warning(f"Param extraction failed ({exc}); trying universal fallback")
            print(f"   ⚠️  {exc} — trying universal fallback")
            ok = run_fallback(intent.raw_input, intent.normalized_input)

        print(f"   {'✅ Done' if ok else '❌ Failed'}")
        overall = overall and ok

    return overall


def _run_goal_dag(
    dag: "TaskDAG",
    session: "Session",
    *,
    approve_all: bool = False,
) -> dict:
    """
    Run a TaskDAG via ParallelRunner while persisting progress to `session`
    so an interrupted run can be resumed later with `agent.py --resume <id>`
    instead of starting the whole goal over. Autosaves periodically during
    the run (not just at the end) so a crash -- not just a clean Ctrl+C --
    still leaves a resumable session behind.
    """
    import threading

    from core.action_policy import requires_approval
    from core.parallel_runner import ParallelRunner

    def executor_builder(intent: str, task_args: dict):
        if requires_approval(intent, task_args) and not approve_all:
            raise PermissionError(
                f"Goal step {intent!r} requires explicit approval; rerun with --yes"
            )
        from core.smart_parser import ParsedIntent
        intent_obj = ParsedIntent(
            intent=intent,
            params=task_args,
            confidence=1.0,
            raw_input=str(task_args),
            normalized_input=str(task_args),
        )
        executor, exec_args, resources = _build_executor(intent_obj)
        def fn(a, r):
            return executor.run(a, r)
        return fn, exec_args, resources

    runner = ParallelRunner(max_workers=4, executor_builder=executor_builder)

    stop_autosave = threading.Event()

    def _autosave_loop() -> None:
        while not stop_autosave.wait(3.0):
            session.capture_dag(dag)
            session.save()

    autosave_thread = threading.Thread(target=_autosave_loop, daemon=True)
    autosave_thread.start()

    session.log("started", goal=session.goal)
    session.capture_dag(dag)
    session.save()
    print(f"Session: {session.id}  (resume anytime with: agent.py --resume {session.id})")

    try:
        summary = runner.run(dag)
    except KeyboardInterrupt:
        stop_autosave.set()
        session.capture_dag(dag)
        session.status = "stopped"
        session.log("interrupted")
        session.save()
        print(f"\nInterrupted -- progress saved. Resume with: agent.py --resume {session.id}")
        sys.exit(130)
    except Exception as exc:
        stop_autosave.set()
        session.capture_dag(dag)
        session.status = "failed"
        session.log("exception", error=str(exc))
        session.save()
        raise
    finally:
        stop_autosave.set()
        autosave_thread.join(timeout=5.0)
        if autosave_thread.is_alive():
            logger.warning("Session autosave thread did not stop before final save")

    session.capture_dag(dag)
    session.status = "done" if not (summary.get("failed", 0) or summary.get("blocked", 0)) else "failed"
    session.log("finished", summary=summary)
    session.save()
    print(f"\nGoal complete: {summary}")
    if summary.get("failed", 0) or summary.get("blocked", 0):
        print(f"Some steps failed -- resume with: agent.py --resume {session.id}")
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Autonomous Desktop Agent — self-healing, no AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", nargs="?", help="Natural language command")
    parser.add_argument("--workflow", metavar="NAME",
                        help="Run a named workflow from config/workflow.yaml")
    parser.add_argument("--workflow-file", default="config/workflow.yaml",
                        metavar="FILE", help="Path to workflow YAML file")
    parser.add_argument("--list", action="store_true",
                        help="List all available tasks")
    parser.add_argument("--history", nargs="?", const=20, type=int, metavar="N",
                        help="Show recent task activity; optionally choose how many entries")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Explicitly approve consequential actions for this invocation")
    parser.add_argument("--list-apps", metavar="QUERY", nargs="?", const="",
                        help="List installed Ubuntu applications the agent can open "
                             "(system/user/snap/flatpak .desktop files); optionally "
                             "filter by a search QUERY, e.g. --list-apps editor")
    # Phase 2: Goal Planner + Parallel Execution
    parser.add_argument("--goal", metavar="GOAL",
                        help="High-level goal — auto-planned and parallel-executed")
    parser.add_argument("--parallel", action="store_true",
                        help="Run compound command tasks in parallel")
    # Resumable Sessions (goal-driven DAG runs persist progress and can resume)
    parser.add_argument("--resume", metavar="SESSION_ID",
                        help="Resume a previously interrupted --goal run by session id")
    parser.add_argument("--sessions", action="store_true",
                        help="List saved sessions (id, progress, goal)")
    # Blueprints (named, saved, reusable multi-step workflows on top of --goal)
    parser.add_argument("--save-blueprint", metavar="NAME",
                        help="Save the --goal as a named, reusable blueprint (also runs it once)")
    parser.add_argument("--run-blueprint", metavar="NAME",
                        help="Run a previously saved blueprint by name")
    parser.add_argument("--test-blueprint", metavar="NAME",
                        help="Run each step of a saved blueprint independently and report per-step results")
    parser.add_argument("--blueprints", action="store_true",
                        help="List saved blueprints")
    parser.add_argument("--delete-blueprint", metavar="NAME",
                        help="Delete a saved blueprint")
    # Phase 3: Daemon + Event Triggers
    parser.add_argument("--daemon", action="store_true",
                        help="Start background daemon (24/7 reactive automation)")
    parser.add_argument("--daemon-stop", action="store_true",
                        help="Stop running daemon")
    parser.add_argument("--watch", metavar="SPEC",
                        help="Add trigger: 'time:09:00 → take screenshot'")
    parser.add_argument("--triggers", action="store_true",
                        help="List active triggers")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable tasks:")
        for t in sorted(list_tasks()):
            print(f"  \u2022 {t}")
        return

    if args.history is not None:
        from core.automation_service import automation_service

        records = automation_service.recent(max(0, args.history))
        if not records:
            print("No recorded task activity yet.")
            return
        print(f"\nRecent task activity ({len(records)}):")
        for record in records:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(record["started_at"]))
            detail = f" — {record['error']}" if record.get("error") else ""
            print(f"  {stamp}  {record['state']:10s}  {record['task']}{detail}")
        return

    if args.list_apps is not None:
        from core.app_finder import list_app_names
        query = args.list_apps.lower().strip()
        names = list_app_names()
        if query:
            names = [n for n in names if query in n.lower()]
        print(f"\n{len(names)} installed application(s){f' matching {query!r}' if query else ''}:")
        for n in names:
            print(f"  \u2022 {n}")
        print("\nOpen any of these with: agent.py \"open <name>\"")
        return

    if args.workflow:
        engine = WorkflowEngine(approve_all=args.yes)
        ok = engine.run(args.workflow, args.workflow_file)
        sys.exit(0 if ok else 1)

    # Phase 3: Daemon handlers
    if args.daemon:
        from core.daemon import daemon
        daemon.start()  # blocks until SIGINT
        return

    if args.daemon_stop:
        from core.daemon import stop_daemon
        stop_daemon()
        print("Daemon stopped")
        return

    if args.watch:
        # Parse spec: "time:09:00 → take screenshot"
        import re
        from pathlib import Path

        import yaml
        m = re.match(r'(\w+):(.+?)\s*→\s*(.+)', args.watch)
        if m:
            kind, spec, goal = m.groups()
            entry = {"type": kind, "name": f"{kind}_{spec}", "goal": goal, kind: spec}
            # Append to triggers.yaml
            tf = Path("config/triggers.yaml")
            data = yaml.safe_load(tf.read_text()) if tf.exists() else {"triggers": []}
            data["triggers"].append(entry)
            tf.write_text(yaml.dump(data))
            print(f"Trigger added: {kind}:{spec} → {goal}")
        else:
            print("Invalid format. Use: 'time:09:00 → take screenshot'")
        return

    if args.triggers:
        from core.daemon import get_daemon_status
        status = get_daemon_status()
        print(f"Daemon running: {status['running']}")
        print(f"Triggers: {status['triggers']}")
        for t in status['trigger_details']:
            print(f"  • {t['name']} ({t['type']}) → {t['goal']} [cooldown={t['cooldown']}s]")
        return

    # Sessions: list or resume
    if args.sessions:
        from core.session import Session
        sessions = Session.list_all()
        if not sessions:
            print("No saved sessions.")
        else:
            print(f"\n{len(sessions)} saved session(s):")
            for s in sessions:
                print(f"  {s.summary()}")
            print("\nResume one with: agent.py --resume <id>")
        return

    if args.resume:
        from core.session import Session
        session = Session.load(args.resume)
        if session is None:
            print(f"No session found with id {args.resume!r}. Use --sessions to list them.")
            sys.exit(1)
        print(f"\nResuming session {session.id} -- {session.summary()}")
        dag = session.to_dag()
        summary = _run_goal_dag(dag, session, approve_all=args.yes)
        sys.exit(0 if not (summary['failed'] or summary.get('blocked', 0)) else 1)

    # Blueprints: list, delete, test, or run a saved one
    if args.blueprints:
        from core.blueprint import Blueprint
        bps = Blueprint.list_all()
        if not bps:
            print("No saved blueprints.")
        else:
            print(f"\n{len(bps)} saved blueprint(s):")
            for bp in bps:
                print(f"  {bp.summary()}")
            print("\nRun one with: agent.py --run-blueprint <name>")
            print("Test its steps individually with: agent.py --test-blueprint <name>")
        return

    if args.delete_blueprint:
        from core.blueprint import Blueprint
        bp = Blueprint.load(args.delete_blueprint)
        if bp is None:
            print(f"No blueprint named {args.delete_blueprint!r}. Use --blueprints to list.")
            sys.exit(1)
        bp.delete()
        print(f"Deleted blueprint {args.delete_blueprint!r}.")
        return

    if args.test_blueprint:
        from core.blueprint import Blueprint
        from core.smart_parser import ParsedIntent
        bp = Blueprint.load(args.test_blueprint)
        if bp is None:
            print(f"No blueprint named {args.test_blueprint!r}. Use --blueprints to list.")
            sys.exit(1)
        print(f"\nTesting blueprint {bp.name!r} -- {len(bp.steps)} step(s), each run independently:\n")
        all_ok = True
        from core.action_policy import requires_approval
        for i, step in enumerate(bp.steps, 1):
            step_args = step.get("args", {}) or {}
            if requires_approval(step["intent"], step_args) and not args.yes:
                print(f"  [{i}/{len(bp.steps)}] {step['intent']}: REFUSED (rerun with --yes)")
                all_ok = False
                continue
            intent_obj = ParsedIntent(
                intent=step["intent"], params=step_args,
                confidence=1.0, raw_input=str(step), normalized_input=str(step),
            )
            try:
                executor, exec_args, resources = _build_executor(intent_obj)
                ok = executor.run(exec_args, resources)
            except Exception as exc:
                ok = False
                print(f"  [{i}/{len(bp.steps)}] {step['intent']}: EXCEPTION {exc}")
            else:
                print(f"  [{i}/{len(bp.steps)}] {step['intent']}: {'✅ OK' if ok else '❌ FAILED'}")
            all_ok = all_ok and ok
        print(f"\nBlueprint test {'passed -- all steps OK' if all_ok else 'had failures'}.")
        sys.exit(0 if all_ok else 1)

    if args.run_blueprint:
        from core.blueprint import Blueprint
        from core.session import Session
        bp = Blueprint.load(args.run_blueprint)
        if bp is None:
            print(f"No blueprint named {args.run_blueprint!r}. Use --blueprints to list.")
            sys.exit(1)
        print(f"\nRunning blueprint {bp.name!r} -- {bp.summary()}")
        dag = bp.to_dag()
        session = Session.new(f"blueprint:{bp.name}")
        summary = _run_goal_dag(dag, session, approve_all=args.yes)
        sys.exit(0 if not (summary['failed'] or summary.get('blocked', 0)) else 1)

    # Phase 2: Goal planner handler
    if args.goal:
        from core.goal_planner import goal_planner
        from core.llm_planner import (
            is_confidently_resolvable_open_app,
            llm_planner,
            looks_unreliable,
        )
        from core.session import Session
        from core.smart_parser import smart_parser

        # Tier 5 escalation -- mirrors run_command()'s logic exactly, so
        # --goal gets the same AI-planning power for unreliable/novel
        # prose that the plain `agent.py "<command>"` path already has.
        # Previously --goal ONLY ever used the deterministic SmartParser/
        # TaskDAG path, with no escalation at all -- a real gap, since a
        # goal is exactly where a user is most likely to type something
        # the deterministic parser can't decompose.
        intents = smart_parser.parse_multi(args.goal)
        session = None
        if (looks_unreliable(intents, args.goal)
                and not is_confidently_resolvable_open_app(intents)
                and llm_planner.available()):
            if not args.yes:
                print("AI-planned goals require explicit approval; rerun with --yes.")
                sys.exit(1)
            print("\nGoal parse looks unreliable -- using AI planner (Tier 5) for the whole goal...")
            if args.save_blueprint:
                print("   Note: --save-blueprint is skipped for AI-planned goals -- an "
                      "improvised script has no reusable {intent, args} steps to save.")
            session = Session.new(args.goal)
            session.log("started", mode="ai_planner")
            ai_ok = llm_planner.plan_and_execute(args.goal)
            if ai_ok:
                session.status = "done"
                session.log("finished", ai_ok=ai_ok)
                session.save()
                print("\nGoal complete (AI planner): success")
                sys.exit(0)
            session.log("ai_planner_failed_falling_back", ai_ok=ai_ok)
            print(f"\nAI planner {'failed' if ai_ok is False else 'unavailable'} -- falling back to deterministic goal planning...")

        dag = goal_planner.plan(args.goal)
        if args.save_blueprint:
            from core.blueprint import Blueprint
            bp = Blueprint.from_goal(args.save_blueprint, args.goal)
            bp.save()
            print(f"Saved blueprint {bp.name!r} ({len(bp.steps)} step(s)) -- rerun anytime with: agent.py --run-blueprint {bp.name}")
        if session is None:
            session = Session.new(args.goal)
        summary = _run_goal_dag(dag, session, approve_all=args.yes)
        sys.exit(0 if not (summary['failed'] or summary.get('blocked', 0)) else 1)

    if args.command:
        ok = run_command(args.command, approve_all=args.yes)
        sys.exit(0 if ok else 1)

    parser.print_help()


if __name__ == "__main__":
    main()

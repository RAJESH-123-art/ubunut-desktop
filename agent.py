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

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.session import Session
    from core.task_dag import TaskDAG

from loguru import logger

from core.smart_parser import ParsedIntent, smart_parser
from core.strategy_executor import Strategy, StrategyExecutor
from core.verifier import (
    VerifySpec,
    app_installed_spec,
    browser_open_spec,
    screenshot_taken_spec,
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
        # Load password from config so it’s always available
        try:
            from config.config_loader import load_config
            _cfg = load_config()
            _pwd = _cfg.get("install", {}).get("sudo_password", "")
        except Exception:
            _pwd = ""
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
        spec = screenshot_taken_spec()

        def ss_fn(a, r):
            from tasks.system_screenshot import execute
            return execute(a, r)

        ex = StrategyExecutor("system_screenshot")
        ex.add(Strategy("screenshot", ss_fn, verify_spec=spec, retry_wait=1))
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
        args = {"url": url, "browser": "chromium", "screenshot": False}
        spec = browser_open_spec()

        def browser_fn(a, r):
            from tasks.open_browser_and_visit import execute
            return execute(a, r)

        ex = StrategyExecutor("open_browser")
        ex.add(Strategy("browser", browser_fn, verify_spec=spec, retry_wait=3))
        return ex, args, res

    # ── Search web ────────────────────────────────────────────────────────────
    if name == "search_web":
        query = params.get("query", "").strip()
        url   = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        args  = {"url": url, "browser": "chromium", "screenshot": False}

        def search_fn(a, r):
            from tasks.open_browser_and_visit import execute
            return execute(a, r)

        ex = StrategyExecutor("search_web")
        ex.add(Strategy("search", search_fn, verify_spec=browser_open_spec(), retry_wait=3))
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
        args = {"text": text}

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
        args   = {"action": action}

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
            # No name extracted — likely a false positive (e.g. "make me a sandwich").
            # Route to universal fallback instead of failing silently.
            logger.info("create_folder: no folder name found — routing to universal fallback")
            args = {"raw_command": intent.raw_input, "normalized": intent.normalized_input}
            def _uf(a, r):
                from tasks.universal_fallback import execute
                return execute(a, r)
            ex = StrategyExecutor("universal_fallback")
            ex.add(Strategy("fallback", _uf, retry_wait=0))
            return ex, args, res

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
        args = {"command": cmd}

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
                print("❌ No process name given")
                return False
                r = subprocess.run(["pkill", "-f", pname], capture_output=True, check=False)

            if r.returncode == 0:
                print(f"✅ Killed process: {pname}")
                return True
            print(f"⚠️  No process matching '{pname}' found")
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
                print(f"✅ Notification sent: {title} — {message}")
                return True
            except Exception as exc:
                print(f"❌ Failed to send notification: {exc}")
                return False

            return execute(a, r)

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

    # ── Unknown intent → universal fallback ──────────────────────────────────────────
    logger.warning(f"No executor defined for intent {name!r} — routing to universal fallback")
    # Prefer raw_command/normalized carried in params (set by SmartParser.parse_multi
    # or GoalPlanner for clauses that matched no known intent) — falling back to
    # intent.raw_input/normalized_input for direct/legacy callers.
    args = {
        "raw_command": params.get("raw_command", intent.raw_input),
        "normalized": params.get("normalized", intent.normalized_input),
    }

    def unk_fn(a, r):
        from tasks.universal_fallback import execute
        return execute(a, r)

    ex = StrategyExecutor("universal_fallback")
    ex.add(Strategy("fallback", unk_fn, retry_wait=0))
    return ex, args, res


# ─────────────────────────────────────────────────────────────────────────────
# RUN ONE NATURAL LANGUAGE COMMAND
# ─────────────────────────────────────────────────────────────────────────────

def run_command(raw: str) -> bool:
    """
    Parse a natural language string and execute with full self-healing.
    Supports compound commands: "install vlc and then open youtube".
    """
    import re as _re

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
        from tasks.canva_template import setup as _ct_setup, execute as _ct_exec, cleanup as _ct_cleanup
        _res = _ct_setup()
        try:
            ok = _ct_exec({"platform": platform, "template": template}, _res)
        finally:
            _ct_cleanup(_res)
        print(f"   {'✅ Done' if ok else '❌ Failed'}")
        return ok

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
        return ok

    intents = smart_parser.parse_multi(raw)

    # Tier 5: AI planner escalation for complex/unreliable-looking prose.
    # Only fires if the deterministic parse looks untrustworthy for this
    # input AND an NVIDIA_API_KEY is configured; otherwise fully inert and
    # behavior is unchanged from before this tier existed.
    from core.llm_planner import llm_planner, looks_unreliable
    if looks_unreliable(intents, raw) and llm_planner.available():
        logger.info("run_command: deterministic parse looks unreliable for this input, trying AI planner (Tier 5)")
        print("\nParse looks unreliable for this instruction -- trying AI planner...")
        ai_ok = llm_planner.plan_and_execute(raw)
        if ai_ok:
            print("   AI plan succeeded")
            return True
        if ai_ok is False:
            # A failed upfront script doesn't mean the goal is impossible --
            # universal_fallback still has AT-SPI/Electron/vision layers and
            # the adaptive closed loop (action_loop.py) left to try, which
            # can succeed at GUI-shaped goals a one-shot bash script can't.
            # Don't give up on the first attempt when smarter layers remain.
            print("   AI plan failed -- trying universal fallback (adaptive loop, vision, etc.)")
        else:
            print("   AI planner unavailable/failed -- falling back to deterministic parse")

    if not intents:
        logger.warning(f"SmartParser: no intent matched for {raw!r} — trying universal fallback")
        print(f"\n\u26a0\ufe0f  No intent matched for: {raw!r}")
        print("   Trying universal fallback...")
        from tasks.universal_fallback import execute as _fallback
        ok = _fallback({"raw_command": raw, "normalized": raw}, {})
        print(f"   {'\u2705 Done' if ok else '\u274c Failed'}")
        return ok

    overall = True
    for intent in intents:
        print(f"\n🎯 Intent: {intent.intent}  (confidence={intent.confidence:.0%})")
        print(f"   Params: {intent.params}")
        try:
            executor, args, resources = _build_executor(intent)
            ok = executor.run(args, resources)
        except ValueError as exc:
            # Param extraction failed — still try universal fallback
            logger.warning(f"Param extraction failed ({exc}); trying universal fallback")
            print(f"   ⚠️  {exc} — trying universal fallback")
            from tasks.universal_fallback import execute as _fallback
            ok = _fallback({"raw_command": intent.raw_input, "normalized": intent.normalized_input}, {})

        print(f"   {'✅ Done' if ok else '❌ Failed'}")
        overall = overall and ok

    return overall


def _run_goal_dag(dag: "TaskDAG", session: "Session") -> dict:
    """
    Run a TaskDAG via ParallelRunner while persisting progress to `session`
    so an interrupted run can be resumed later with `agent.py --resume <id>`
    instead of starting the whole goal over. Autosaves periodically during
    the run (not just at the end) so a crash -- not just a clean Ctrl+C --
    still leaves a resumable session behind.
    """
    import threading

    from core.parallel_runner import ParallelRunner

    def executor_builder(intent: str, task_args: dict):
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

    session.capture_dag(dag)
    session.status = "done" if summary.get("failed", 0) == 0 else "failed"
    session.log("finished", summary=summary)
    session.save()
    print(f"\nGoal complete: {summary}")
    if summary.get("failed", 0):
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

    if args.workflow:
        engine = WorkflowEngine()
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
        summary = _run_goal_dag(dag, session)
        sys.exit(0 if summary['failed'] == 0 else 1)

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
        for i, step in enumerate(bp.steps, 1):
            intent_obj = ParsedIntent(
                intent=step["intent"], params=step.get("args", {}) or {},
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
        summary = _run_goal_dag(dag, session)
        sys.exit(0 if summary['failed'] == 0 else 1)

    # Phase 2: Goal planner handler
    if args.goal:
        from core.goal_planner import goal_planner
        from core.session import Session
        dag = goal_planner.plan(args.goal)
        if args.save_blueprint:
            from core.blueprint import Blueprint
            bp = Blueprint.from_goal(args.save_blueprint, args.goal)
            bp.save()
            print(f"Saved blueprint {bp.name!r} ({len(bp.steps)} step(s)) -- rerun anytime with: agent.py --run-blueprint {bp.name}")
        session = Session.new(args.goal)
        summary = _run_goal_dag(dag, session)
        sys.exit(0 if summary['failed'] == 0 else 1)

    if args.command:
        ok = run_command(args.command)
        sys.exit(0 if ok else 1)

    parser.print_help()


if __name__ == "__main__":
    main()

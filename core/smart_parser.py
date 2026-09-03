"""
Smart intent parser — no AI, no LLM, no internet.

Techniques:
  1. Word normalization map  — expands abbreviations, romanized Hindi/Telugu,
                               slang, typos into clean English tokens.
  2. Intent keyword scoring  — each intent has a set of trigger words;
                               score = matched / total keywords.
  3. Fuzzy character overlap — catches partial matches (typ0s, short forms).
  4. Param extraction        — pulls the relevant value (app name, query,
                               contact, URL) from the normalized text.

Examples that work without AI:
  "yt pe rrr song chalao"     → youtube   query="rrr song"
  "vlc install karo pls"      → install   app="vlc"
  "watsap me darling hi bhej" → whatsapp  contact="darling" message="hi"
  "get me gimp"               → install   app="gimp"
  "take ss"                   → screenshot
  "open my browser"           → open_browser
  "github.com kholo"          → open_browser  url="github.com"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# WORD MAP
# Maps individual tokens → canonical English equivalents.
# Empty string means "drop this token" (filler word).
# ─────────────────────────────────────────────────────────────────────────────
WORD_MAP: dict[str, str] = {
    # ── Filler / politeness ──────────────────────────────────────────────────
    "pls": "", "plz": "", "please": "", "bhai": "", "yaar": "",
    "bro": "", "re": "", "na": "", "toh": "", "to": "to",
    "mujhe": "", "muje": "", "mere": "", "mera": "", "meri": "",

    # ── Action verbs (Hindi/Telugu romanized → English) ──────────────────────
    "karo": "",        "kar": "",         "karna": "",
    "chalao": "play",  "chala": "play",   "bajao": "play",  "baja": "play",
    "kholo": "open",   "khol": "open",    "launch": "open",
    "band": "close",   "bnd": "close",
    "bhejo": "send",   "bhej": "send",    "bheja": "send",
    "dhundho": "search","dhundo": "search","dhund": "search","khojo": "search",
    "dekho": "show",   "dikhao": "show",  "dikha": "show",
    "likho": "type",   "likha": "type",   "type": "type",
    "hatao": "delete", "hata": "delete",  "mita": "delete", "mitao": "delete",
    "lo": "get",       "lao": "get",      "la": "get",      "dedo": "get",
    "lagao": "install","laga": "install",

    # ── Prepositions / conjunctions ──────────────────────────────────────────
    "pe": "on",    "par": "on",   "mein": "in",  "me": "in",
    "aur": "and",  "or": "and",   "phir": "then","thenr": "then",
    "se": "from",  "ko": "to",    "ka": "of",    "ki": "of",

    # ── Platform abbreviations ───────────────────────────────────────────────
    "yt": "youtube",   "ytb": "youtube",
    "wa": "whatsapp",  "wp": "whatsapp",  "watsap": "whatsapp",
    "wattsapp": "whatsapp", "whatsaap": "whatsapp",
    "ss": "screenshot","sc": "screenshot",
    "goog": "google",  "fb": "facebook",  "gh": "github",
    "ig": "instagram", "insta": "instagram",
    "dl": "download",  "dwnld": "download",
    "inst": "install", "instl": "install",

    # ── Common app names / typos ─────────────────────────────────────────────
    "vlc": "vlc",           "gimp": "gimp",         "obs": "obs",
    "vscode": "vscode",     "vs": "vscode",
    "spotfy": "spotify",    "spotifi": "spotify",
    "crome": "chrome",      "chorme": "chrome",      "chrrome": "chrome",
    "ffox": "firefox",      "firfox": "firefox",
    "tlegram": "telegram",  "telgram": "telegram",
    "discrd": "discord",    "discordd": "discord",

    # ── Volume / Audio (Hindi romanized + English variants) ──────────────────
    "vol": "volume",
    "aawaz": "volume",   "awaaz": "volume",   "awaj": "volume",
    "louder": "up",      "softer": "down",    "quieter": "down",
    "kam": "down",        "zyada": "up",       # Hindi: less → down, more → up

    # ── Brightness (Hindi romanized) ─────────────────────────────────────────
    "ujala": "bright",   "chamak": "bright",

    # ── Power (common aliases) ────────────────────────────────────────────────
    "reboot": "restart",  "poweroff": "shutdown",

    # ── Lock (Hindi romanized) ────────────────────────────────────────────────
    "taala": "lock",

    # ── Create / Folder (Hindi romanized) ─────────────────────────────────────
    "banao": "create",   "bana": "create",
    "nayi": "new",       "naya": "new",

    # ── Typing / Klavaro (Hindi romanized + aliases) ────────────────────
    "likhna": "type",
    "tez": "speed",       # Hindi: fast → useful in velocity context
    "shuddh": "accuracy", # Hindi: accurate
    "tezi": "speed",
}

# ─────────────────────────────────────────────────────────────────────────────
# INTENT DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────
# Each intent has:
#   keywords    – trigger tokens (any match raises score)
#   min_score   – minimum score fraction to accept
#   extract     – dict of param_name → extraction rule string
#
# Extraction rule formats:
#   "after:tok1|tok2"    → tokens AFTER the first matching trigger
#   "before:tok1|tok2"   → tokens BEFORE the first matching trigger
#   "regex:<pattern>"    → first regex match in normalized text
#   "all_after:tok1"     → everything after trigger to end
# ─────────────────────────────────────────────────────────────────────────────
INTENT_DEFS: dict[str, dict] = {
    "youtube": {
        "keywords": {"youtube", "yt", "play", "video", "song", "music", "watch", "bajao"},
        "min_score": 0.10,
        "extract": {"query": "all_after:youtube|play|search|watch|song|music|on|find"},
    },
    "install_app": {
        "keywords": {"install", "setup", "get", "download", "add"},
        "min_score": 0.10,   # fewer keywords → higher per-match score
        "extract": {"app_name": "all_after:install|setup|get|download|add"},
    },
    "whatsapp_send": {
        "keywords": {"whatsapp", "send", "message", "msg", "chat", "bhejo", "bhej", "darling"},
        "min_score": 0.12,   # lowered so 'whatsapp darling hi' matches (1/8=0.125)
        "extract": {
            # Try: word just before 'to/on/for/ko/pe' (e.g. 'darling to send hi')
            # then fall back to word after 'to/send/bhejo' in agent.py recovery logic
            "contact": "word_before:to|on|for",
            # message comes after the action word or is a well-known greeting
            "message": "all_after:saying|say|text|send|bhejo|bhej",
        },
    },
    "screenshot": {
        "keywords": {"screenshot", "capture", "screen", "snap", "ss", "sc"},
        "min_score": 0.12,
        "extract": {},
    },
    "search_web": {
        "keywords": {"search", "google", "find", "lookup", "look", "dhundho"},
        "min_score": 0.15,
        "extract": {"query": "all_after:search|google|find|for|about"},
    },
    "open_browser": {
        "keywords": {"browser", "chrome", "brave", "firefox", "internet"},
        "min_score": 0.18,   # raised so window ops with exact match beat it
        "extract": {"url": "regex:https?://\\S+|[\\w-]+\\.[a-z]{2,}(?:/\\S*)?"},
    },
    "visit_url": {
        "keywords": {"visit", "go", "navigate", "open", "github", "google", "youtube"},
        "min_score": 0.12,
        "extract": {"url": "regex:https?://\\S+|[\\w-]+\\.[a-z]{2,}(?:/\\S*)?"},
    },
    "open_app": {
        "keywords": {"open", "launch", "start", "run", "kholo", "khol"},
        "min_score": 0.15,
        "extract": {"app_name": "all_after:open|launch|start|run"},
    },
    "window_minimize": {
        # Smaller keyword set — 'minimize' alone scores 1/2=0.50 which
        # beats open_browser's 'chrome' score of 1/5=0.20 on 'minimize chrome'.
        "keywords": {"minimize", "chhota"},
        "min_score": 0.20,
        "extract": {"window": "all_after:minimize|hide|window|app"},
    },
    "window_maximize": {
        "keywords": {"maximize", "fullscreen", "expand"},
        "min_score": 0.30,  # raised from 0.20 — prevents 'minimize' fuzzy-matching here
        "extract": {"window": "all_after:maximize|fullscreen|window|app"},
    },
    "window_close": {
        "keywords": {"close", "quit", "exit", "kill"},   # fewer = 1/4=0.25 on exact match
        "min_score": 0.18,
        "extract": {"window": "all_after:close|quit|exit|window|app"},
    },
    "file_delete": {
        "keywords": {"delete", "remove", "trash", "hatao", "mita"},
        "min_score": 0.20,
        "extract": {"file_name": "all_after:delete|remove|file|trash"},
    },
    "organize_downloads": {
        "keywords": {"organize", "clean", "sort", "downloads", "files", "folder"},
        "min_score": 0.15,
        "extract": {},
    },
    # ── Type text ─────────────────────────────────────────────────────────────
    "type_text": {
        "keywords": {"type", "write", "input", "keyboard"},
        "min_score": 0.20,
        "extract": {"text": "all_after:type|write|input|keyboard"},
    },
    # ── Volume / Audio ────────────────────────────────────────────────────────
    "volume_control": {
        "keywords": {"volume", "sound", "audio", "mute"},
        "min_score": 0.20,
        "extract": {
            "action": "regex:up|down|mute|unmute|set|increase|decrease",
            "level":  "regex:\\d+",
        },
    },
    # ── Brightness ────────────────────────────────────────────────────────────
    "brightness_control": {
        "keywords": {"brightness", "bright", "dim"},
        "min_score": 0.30,
        "extract": {
            "action": "regex:up|down|increase|decrease|set|dim|brighten",
            "level":  "regex:\\d+",
        },
    },
    # ── Lock screen ───────────────────────────────────────────────────────────
    "lock_screen": {
        "keywords": {"lock"},
        "min_score": 0.80,   # high threshold — exact/near-exact only
        "extract": {},
    },
    # ── System power ──────────────────────────────────────────────────────────
    "system_power": {
        "keywords": {"shutdown", "restart", "suspend", "hibernate"},
        "min_score": 0.18,
        "extract": {"action": "regex:shutdown|restart|reboot|suspend|sleep|hibernate|poweroff"},
    },
    # ── Create folder ─────────────────────────────────────────────────────────
    "create_folder": {
        "keywords": {"create", "new", "make", "folder", "directory"},
        "min_score": 0.15,
        "extract": {"folder_name": "all_after:folder|directory|called|named"},
    },
    # ── Run shell command ─────────────────────────────────────────────────────
    "run_command": {
        "keywords": {"execute", "shell", "command"},
        "min_score": 0.30,
        "extract": {"command": "all_after:execute|shell|command"},
    },
    # ── Hotkey / keyboard shortcut ────────────────────────────────────────────
    "hotkey": {
        "keywords": {"ctrl", "alt", "shift", "press", "shortcut", "hotkey"},
        "min_score": 0.15,
        "extract": {"keys": "all_after:press|hotkey|shortcut"},
    },
    # ── Klavaro typing exercises ────────────────────────────────────────────────────
    # High-specificity keyword set — "klavaro" alone scores 0.20 (1/5),
    # which beats open_app's 0.17 for the same input.
    "klavaro_exercise": {
        "keywords": {"klavaro", "velocity", "adaptability", "typing", "exercise"},
        "min_score": 0.12,
        "extract": {
            # Capture which Klavaro exercise mode to run
            "exercise": "regex:velocity|adaptability|basic|fluidness",
        },
    },
    # NOTE: 'system_screenshot' merged into 'screenshot' intent above.
    # Both agent.py branches handle the task correctly.
    "wait_seconds": {
        "keywords": {"wait", "pause", "sleep", "ruko", "ruk"},
        "min_score": 0.20,
        "extract": {"seconds": "regex:\\d+"},
    },
    # ── System info collection ─────────────────────────────────────────
    "system_info": {
        "keywords": {"system", "info", "collect", "specs", "hardware", "report"},
        "min_score": 0.15,
        "extract": {
            "save_path": "regex:/tmp/\\S+\.txt|~/\\S+\.txt|/home/\\S+\.txt",
        },
    },
    # ── Write / create a file ──────────────────────────────────────────
    "file_write": {
        "keywords": {"write", "save", "create", "file"},
        "min_score": 0.25,
        "extract": {
            "file_path": "regex:/tmp/\\S+|~/\\S+|/home/\\S+",
            "content":   "all_after:write|save|content|with",
        },
    },
    # ── Read a file ───────────────────────────────────────────────────
    "file_read": {
        "keywords": {"read", "show", "print", "display", "open", "file"},
        "min_score": 0.25,
        "extract": {
            "file_path": "regex:/tmp/\\S+|~/\\S+|/home/\\S+",
        },
    },
    # ── Desktop notification ──────────────────────────────────────────
    "notify": {
        "keywords": {"notify", "alert", "notification", "remind", "popup"},
        "min_score": 0.20,
        "extract": {
            "title":   "after:title|notify|alert",
            "message": "all_after:message|say|with|popup|that",
        },
    },
    # ── Open terminal ─────────────────────────────────────────────────
    "open_terminal": {
        "keywords": {"terminal", "bash", "shell", "konsole", "cmd"},
        "min_score": 0.30,
        "extract": {},
    },
    # ── Kill / stop a process ─────────────────────────────────────────
    "process_kill": {
        "keywords": {"kill", "stop", "terminate", "close", "process"},
        "min_score": 0.25,
        "extract": {
            "process_name": "all_after:kill|stop|terminate|close|process",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# PARSED RESULT
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ParsedIntent:
    intent: str
    params: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    raw_input: str = ""
    normalized_input: str = ""

    def __str__(self) -> str:
        return f"Intent({self.intent!r} conf={self.confidence:.2f} params={self.params})"


# ─────────────────────────────────────────────────────────────────────────────
# SMART PARSER
# ─────────────────────────────────────────────────────────────────────────────
class SmartParser:
    """
    Rule-based NL parser that works without AI.
    Handles multi-language romanized input, typos, slang, abbreviations.
    """

    # ── Normalisation ─────────────────────────────────────────────────────────

    def _normalize(self, text: str) -> str:
        """Lowercase, strip punctuation, expand WORD_MAP."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", " ", text)
        tokens = text.split()
        expanded: list[str] = []
        for tok in tokens:
            replacement = WORD_MAP.get(tok)
            if replacement is None:
                expanded.append(tok)      # unknown → keep as-is
            elif replacement:
                expanded.append(replacement)  # known → replace
            # empty string → drop (filler word)
        return " ".join(expanded)

    # ── Fuzzy matching ────────────────────────────────────────────────────────

    def _fuzzy_score(self, word: str, keyword: str) -> float:
        """Character bigram overlap score between word and keyword."""
        def bigrams(s: str) -> set[str]:
            return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else {s}
        w_bi, k_bi = bigrams(word), bigrams(keyword)
        if not w_bi or not k_bi:
            return 1.0 if word == keyword else 0.0
        common = len(w_bi & k_bi)
        return (2.0 * common) / (len(w_bi) + len(k_bi))

    def _token_matches_keyword(self, token: str, keyword: str) -> float:
        """Return match strength [0.0–1.0] between token and keyword."""
        if token == keyword:
            return 1.0
        # Substring match only when BOTH token AND keyword are ≥3 chars.
        # Without the keyword length guard, a 2-char keyword like 'sc' would
        # substring-match inside any word containing 'sc' (e.g. 'fullscreen').
        if len(token) >= 3 and len(keyword) >= 3 and (token in keyword or keyword in token):
            return 0.8
        # Only count fuzzy score when it's strong (≥0.55) to avoid weak false positives.
        score = self._fuzzy_score(token, keyword) if len(token) >= 3 else 0.0
        return score if score >= 0.55 else 0.0

    # ── Param extraction ──────────────────────────────────────────────────────

    def _extract(self, text: str, rule: str) -> str:
        """Extract a param value from normalized text using a rule string."""
        tokens = text.split()

        if rule.startswith("all_after:"):
            triggers = rule[10:].split("|")
            for i, tok in enumerate(tokens):
                if any(self._token_matches_keyword(tok, tr) >= 0.8 for tr in triggers):
                    rest = " ".join(tokens[i + 1:]).strip()
                    if rest:
                        return rest
            return ""

        if rule.startswith("after:"):
            triggers = rule[6:].split("|")
            for i, tok in enumerate(tokens):
                if any(self._token_matches_keyword(tok, tr) >= 0.8 for tr in triggers) and i + 1 < len(tokens):
                    return tokens[i + 1]
            return ""

        if rule.startswith("before:"):
            triggers = rule[7:].split("|")
            for i, tok in enumerate(tokens):
                if any(self._token_matches_keyword(tok, tr) >= 0.8 for tr in triggers):
                    return " ".join(tokens[:i]).strip()
            return ""

        # word_before: — returns only the SINGLE word immediately before the trigger.
        # Useful for extracting contact names: "darling to send hi" → "darling".
        if rule.startswith("word_before:"):
            triggers = rule[12:].split("|")
            for i, tok in enumerate(tokens):
                if any(self._token_matches_keyword(tok, tr) >= 0.8 for tr in triggers) and i > 0:
                    return tokens[i - 1]
            return ""

        if rule.startswith("regex:"):
            pattern = rule[6:]
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(0).strip() if m else ""

        return ""

    # ── Main parse ────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> ParsedIntent | None:
        """
        Parse any natural language text into a structured intent.

        Returns None only if nothing matched above the minimum score.
        """
        normalized = self._normalize(raw)
        tokens = normalized.split()

        best_intent: str | None = None
        best_score: float = 0.0
        best_params: dict[str, str] = {}

        for intent_name, cfg in INTENT_DEFS.items():
            keywords: set[str] = cfg["keywords"]
            min_score: float = cfg["min_score"]

            # Score = sum of best per-keyword matches / total keywords
            total_score = 0.0
            for kw in keywords:
                kw_best = max(
                    (self._token_matches_keyword(tok, kw) for tok in tokens),
                    default=0.0,
                )
                total_score += kw_best
            score = total_score / len(keywords)

            if score < min_score or score <= best_score:
                continue

            # Extract params
            params: dict[str, str] = {}
            for param_name, rule in cfg.get("extract", {}).items():
                value = self._extract(normalized, rule)
                if value:
                    params[param_name] = value

            best_score = score
            best_intent = intent_name
            best_params = params

        if best_intent is None:
            logger.warning(f"SmartParser: no intent matched for {raw!r}")
            return None

        result = ParsedIntent(
            intent=best_intent,
            params=best_params,
            confidence=best_score,
            raw_input=raw,
            normalized_input=normalized,
        )
        logger.info(f"SmartParser: {raw!r} → {result}")
        return result

    def parse_multi(self, raw: str) -> list[ParsedIntent]:
        """
        Split compound commands on 'and'/'then'/'aur'/'phir' and parse each part.
        Returns a list of intents (empty list only if raw has no non-empty parts).

        Clauses that match no known intent are NOT dropped — they're wrapped as
        a 'universal_fallback' intent so the layered fallback (CLI registry /
        AT-SPI navigator / Electron-CDP / binary launch) still gets a chance to
        handle them. This lets compound commands mix known + unknown actions,
        e.g. "install vlc and click the weird settings icon".
        """
        parts = re.split(r"\s+(?:and|then|aur|phir|also)\s+", raw, flags=re.IGNORECASE)
        results: list[ParsedIntent] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            intent = self.parse(part)
            if intent:
                results.append(intent)
            else:
                normalized = self._normalize(part)
                logger.info(f"SmartParser: {part!r} matched no intent — routing clause to universal_fallback")
                results.append(ParsedIntent(
                    intent="universal_fallback",
                    params={"raw_command": part, "normalized": normalized},
                    confidence=0.0,
                    raw_input=part,
                    normalized_input=normalized,
                ))
        return results


# Singleton — import and use directly.
smart_parser = SmartParser()

"""
LLMPlanner -- Tier 5: last-resort AI planner for complex, multi-step natural
language instructions that SmartParser's deterministic keyword matching
cannot reliably decompose.

Why this exists:
  SmartParser (core/smart_parser.py) splits compound commands on "and"/"then"
  and keyword-scores each clause independently. This works well for short,
  single-purpose commands ("install vlc", "take a screenshot") but silently
  mis-parses multi-sentence prose describing a workflow (e.g. "create a
  project folder, write a script that collects X, run it, back it up,
  checksum it, and verify it") into a pile of wrong/garbled intents.

  This tier does NOT try to out-parse SmartParser with more regex. Instead,
  when the deterministic parse looks unreliable for a given input (see
  `looks_unreliable()` and the call site in agent.run_command()), the WHOLE
  raw instruction is handed to an LLM with one job: write a single shell
  script that accomplishes it. That script is then executed through the
  EXISTING, already-tested tasks/run_command.py path -- so it still goes
  through core/safety_guard.py's dangerous-pattern checks. This tier adds
  planning intelligence; it does not add a new execution path or bypass any
  existing safety mechanism.

Credential scoping (important):
  This uses a SEPARATE credential from core/semantic_vision.py's Layer 4.
  The vision key (NVIDIA_API_KEY) is scoped for vision calls only. Text/
  planning calls made here use their own dedicated APINEX_API_KEY.
  Never reuse one for the other.

Activation:
  Fully inert unless APINEX_API_KEY is set -- with no key, `available()`
  is False and the system behaves exactly as it did before this module
  existed.

Env vars:
  APINEX_API_KEY    -- required to activate this tier
  APINEX_BASE_URL   -- default: https://api.apinex.bond/v1
  APINEX_TEXT_MODEL -- default: free/gpt-5.6-luna
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

SYSTEM_PROMPT_TEMPLATE = """You are a shell-scripting assistant for an Ubuntu desktop automation agent.
Given a natural-language instruction describing a (possibly multi-step) task, write a
single POSIX-compliant bash script that accomplishes the ENTIRE instruction, in order.

Hard rules:
- Output ONLY the script itself. No explanation, no markdown fences, no commentary.
- Start with a shebang line: #!/bin/bash
- Use `set -e` so the script stops on the first real error.
- NEVER use sudo, or apt/dnf/snap install/remove/upgrade, or any command that installs,
  removes, or upgrades a package -- UNLESS the instruction explicitly names a package
  and explicitly asks to install/remove/upgrade it.
- NEVER delete or overwrite anything outside of paths the instruction explicitly
  creates or names. Never run `rm -rf` on $HOME, /, or any suspiciously short path.
- Prefer $HOME-relative paths over hardcoded /home/<user> paths.
- If the instruction says "verify"/"check"/"confirm", include real shell checks
  (test -f, sha256sum comparisons, tar -tzf, grep, etc.) that print clear PASS/FAIL lines.
- If the instruction says not to delete/modify something, do not do it.
- End with a short plain-text PASS/FAIL summary section printed to stdout.

VISIBLE / GUI ACTIONS cannot be safely implemented by a generated shell
script on native Wayland. NEVER call this project's agent.py from the generated
script: recursive agent calls lose execution context and approval scope. If the
goal requires browser clicks, form selection, typing, or other GUI interaction,
output a script that prints "FAIL: GUI task requires structured automation" and
exits non-zero. The caller will then use the adaptive GUI fallback.

SCREENSHOT LOCATION (concrete fact, do not guess): screenshots taken via
'{python_bin} {agent_path} "take a screenshot"' are ALWAYS saved under
{screenshots_dir}/desktop_screenshot_<timestamp>.png -- use that exact
directory and filename pattern when verifying a screenshot was created.
Never assume $HOME/Pictures or any other location for this project.

NEVER FABRICATE GUI VERIFICATION (critical -- a past run got this wrong and
reported a false PASS): for any outcome that lives INSIDE a GUI app (a
calculator's displayed result, a form field's value, whether text actually
landed in the right window), do NOT compute the "expected" value yourself
(e.g. bash arithmetic) and then declare PASS just because the delegated
agent call returned exit code 0 or a screenshot file exists. A delegated
GUI step can silently do the wrong thing (miss the target window, mistype,
misclick) while still exiting 0 -- exit code only means "no crash", never
"correct on-screen result". The ONLY valid PASS/FAIL check for a GUI-driven
outcome is independently reading back what is ACTUALLY on screen afterward:
delegate a read-back step to the agent (e.g. a natural-language instruction
asking it to report what a field/display currently shows) and compare THAT
against the expected value -- never assume the GUI steps worked just because
they ran without error.
"""


def _default_system_prompt() -> str:
    python_bin = sys.executable
    project_root = Path(__file__).resolve().parent.parent
    agent_path = str(project_root / "agent.py")
    screenshots_dir = str(project_root / "logs" / "screenshots")
    return SYSTEM_PROMPT_TEMPLATE.format(
        python_bin=python_bin,
        agent_path=agent_path,
        screenshots_dir=screenshots_dir,
    )


@dataclass
class PlanResult:
    ok: bool
    script: str = ""
    reasoning: str = ""
    error: str = ""


def looks_unreliable(
    intents: list,
    raw: str,
    max_clauses: int = 2,
    max_unknown_ratio: float = 0.4,
    max_param_words: int = 6,
    min_margin: float = 0.08,
) -> bool:
    """
    Heuristic: decide whether SmartParser.parse_multi()'s result for `raw`
    is trustworthy enough to execute directly, or whether it should be
    treated as unreliable prose and escalated to the LLM planner instead.

    This is intentionally simple and conservative -- false positives just
    mean an extra (skippable, key-gated) AI call; false negatives mean a
    short legitimate compound command gets executed as before.
    """
    if not intents:
        # Zero deterministic intents is the CLEAREST case of "not trustworthy
        # enough to execute directly" there is -- there's nothing to execute.
        # Previously this fell through to universal_fallback's blind
        # binary-guessing without ever giving the LLM planner a chance, even
        # though that's exactly the situation it exists for.
        return True
    if len(raw.split()) > 40:
        return True
    if len(intents) > max_clauses:
        return True
    if intents:
        unknown = sum(1 for i in intents if getattr(i, "intent", None) == "universal_fallback")
        if len(intents) >= 2 and (unknown / len(intents)) > max_unknown_ratio:
            return True
        # Strong signal of misparse: a genuine app_name/folder_name/title/etc.
        # is a short identifier. A long, sentence-like value in that slot
        # means SmartParser grabbed a fragment of prose, not a real argument
        # (this is how e.g. "install_app" can get triggered with an app_name
        # like "ubuntu package manager state identify five installed packages").
        for intent in intents:
            for value in getattr(intent, "params", {}).values():
                if isinstance(value, str) and len(value.split()) > max_param_words:
                    return True
        # Ambiguous keyword race: two DIFFERENT intents scored almost the same
        # for this clause (e.g. "search cats in firefox" scores open_browser
        # 0.20 vs search_web 0.167 -- a 0.033 margin). SmartParser still has
        # to pick one, but a near-tie is a strong, general-purpose signal that
        # the pick may be wrong -- this is exactly how "search X in firefox"
        # silently became "open firefox to google.com" (the query got
        # dropped). Escalating on ANY close race, not just this one phrasing,
        # is what lets the adaptive/AI layers correct future unforeseen
        # ambiguous phrasings too, instead of only the ones we happened to
        # test by hand.
        for intent in intents:
            if getattr(intent, "margin", 1.0) < min_margin:
                return True
    return False


def is_confidently_resolvable_open_app(intents: list) -> bool:
    """
    Bypass for looks_unreliable(): a single "open <app>" intent whose
    app_name already resolves to a REAL installed application doesn't need
    AI-planner escalation just because SmartParser's keyword-overlap scoring
    happened to produce a close margin against some unrelated intent.

    Concrete example that motivated this: "open system monitor" ties
    open_app (matches "open") against system_info (matches "system") at the
    exact same keyword score purely because both intents' keyword sets
    happen to share a word -- margin=0.00 -- even though it's obviously an
    open_app request. Re-scoring intents smarter is a much bigger, riskier
    change; checking whether the extracted app_name is an actual installed
    application is a far stronger, ground-truth signal that's cheap and
    already available via core.app_registry / core.app_finder, and it only
    ever narrows the AI-escalation path -- it never widens what's trusted.
    """
    if len(intents) != 1:
        return False
    intent = intents[0]
    if getattr(intent, "intent", None) != "open_app":
        return False
    app_name = intent.params.get("app_name", "").strip()
    if not app_name:
        return False

    import shutil
    if shutil.which(app_name) or shutil.which(app_name.lower()):
        return True

    # A registry entry only counts if its command actually resolves to a
    # real binary on PATH -- APP_COMMANDS can list an app (e.g. "gimp") that
    # isn't actually installed on this machine, which must NOT count as
    # confidently resolvable.
    from core.app_registry import APP_COMMANDS
    registry_cmd = APP_COMMANDS.get(app_name.lower())
    if registry_cmd and shutil.which(registry_cmd.split()[0]):
        return True

    from core.app_finder import find_app
    found = find_app(app_name)
    return found is not None and bool(found.launch_command())


def looks_gui_shaped(raw: str) -> str:
    """
    Heuristic: does this instruction need live, multi-step interaction INSIDE
    a specific GUI app (clicking buttons, reading a display, filling a
    field) rather than being a pure file/shell/data task?

    Returns the matched app name (for use as core.action_loop's app_hint) if
    so, else "". Deliberately conservative: a false negative just means the
    upfront-script planner gets first try (today's behavior, unchanged); a
    false positive would route a perfectly fine shell task through the
    slower adaptive loop first for no benefit.

    Why this matters: a one-shot bash script that delegates a GUI step to
    `agent.py "..."` can only tell whether that call exited 0, not whether
    the click/type actually landed correctly -- confirmed live to produce a
    false PASS on a calculator task (see VERCEPT_LEVEL_ROADMAP.md). The
    adaptive loop re-observes after every single action and independently
    verifies before accepting "done", so GUI-shaped goals should reach it
    FIRST, not only as a fallback after the upfront script already failed.
    """
    from core.app_registry import APP_COMMANDS

    low = raw.lower()
    app_hint = ""
    for name in APP_COMMANDS:
        if re.search(rf"\b{re.escape(name)}\b", low):
            app_hint = name
            break
    if not app_hint:
        return ""

    # Needs more than just "open the app" -- some further interaction implied.
    interaction_words = (
        "click", "type", "press", "select", "compute", "calculate", "fill",
        "enter", "drag", "scroll", "check", "toggle", "read", "tell me",
        "result", "search", "find", "write", "save", "close", "switch",
    )
    if any(w in low for w in interaction_words):
        return app_hint
    return ""


class LLMPlanner:
    """Generates and runs a shell-script plan for complex instructions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("NIKKI_TEXT_API_KEY") or os.getenv("APINEX_API_KEY")
        self.base_url = base_url or os.getenv("NIKKI_TEXT_BASE_URL", os.getenv("APINEX_BASE_URL", "https://api.apinex.bond/v1"))
        self.model = model or os.getenv("NIKKI_TEXT_MODEL", os.getenv("APINEX_TEXT_MODEL", "free/gpt-5.6-luna"))
        self.timeout = timeout
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def _extract_script(self, text: str) -> str:
        """Pull the script out of a fenced code block if the model added one anyway."""
        m = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", text, re.DOTALL)
        script = m.group(1) if m else text
        script = script.strip()
        if not script.startswith("#!"):
            script = "#!/bin/bash\nset -e\n" + script
        return script

    def generate_script(self, raw_instruction: str) -> PlanResult:
        if not self.available():
            return PlanResult(ok=False, error="APINEX_API_KEY not set")
        try:
            client = self._get_client()
            stream = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _default_system_prompt()},
                    {"role": "user", "content": raw_instruction},
                ],
                temperature=0.2,
                top_p=0.95,
                max_tokens=2048,
                stream=True,
                timeout=self.timeout,
            )

            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                if delta.content:
                    content_parts.append(delta.content)

            text = "".join(content_parts)
            reasoning_text = "".join(reasoning_parts)
            script = self._extract_script(text)
            if not script.strip():
                return PlanResult(ok=False, error="Model returned an empty script", reasoning=reasoning_text)
            if re.search(r"(?:^|[\s/])agent\.py(?:\s|$)", script):
                return PlanResult(
                    ok=False,
                    error="Recursive agent.py calls are not permitted in generated plans",
                    reasoning=reasoning_text,
                )
            return PlanResult(ok=True, script=script, reasoning=reasoning_text)
        except Exception as exc:
            logger.error(f"LLMPlanner: generation failed: {exc}")
            return PlanResult(ok=False, error=str(exc))

    def save_script(self, script: str, reasoning: str = "") -> str:
        out_dir = Path("logs/ai_plans")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        path = out_dir / f"plan_{stamp}.sh"
        path.write_text(script)
        if reasoning:
            (out_dir / f"plan_{stamp}.reasoning.txt").write_text(reasoning)
        return str(path)

    def plan_and_execute(
        self,
        raw_instruction: str,
        exec_timeout: int = 90,
        *,
        authorized: bool = False,
    ) -> bool | None:
        """
        Generate a script for `raw_instruction` and execute it through the
        existing, safety-checked tasks/run_command.py path.

        Returns:
            True / False -- a plan was generated and executed (reflects its result)
            None         -- planner unavailable or generation failed; caller
                            should fall back to the deterministic pipeline
        """
        if not self.available():
            logger.debug("LLMPlanner: not available (no APINEX_API_KEY) -- skipping")
            return None
        if not authorized:
            logger.warning(
                "LLMPlanner: generated shell execution requires explicit --yes authorization"
            )
            return False

        logger.info(f"LLMPlanner: generating script for: {raw_instruction!r}")
        result = self.generate_script(raw_instruction)
        if not result.ok:
            logger.warning(f"LLMPlanner: could not generate a plan ({result.error}) -- falling back")
            return None

        script_path = self.save_script(result.script, result.reasoning)
        logger.info(f"LLMPlanner: script saved to {script_path}")
        print(f"\n🤖 AI-planned script ({script_path}):")
        print("─" * 60)
        print(result.script)
        print("─" * 60)

        from tasks.run_command import execute as run_command_execute
        ok = run_command_execute({
            "command": f"bash {script_path}",
            "timeout": exec_timeout,
            "authorized": authorized,
        }, {})
        return ok


# Singleton
llm_planner = LLMPlanner()

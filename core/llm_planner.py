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
  planning calls made here use their own dedicated NVIDIA_TEXT_API_KEY.
  Never reuse one for the other.

Activation:
  Fully inert unless NVIDIA_TEXT_API_KEY is set -- with no key, `available()`
  is False and the system behaves exactly as it did before this module
  existed.

Env vars:
  NVIDIA_TEXT_API_KEY -- required to activate this tier at all
  NVIDIA_BASE_URL     -- default: https://integrate.api.nvidia.com/v1 (shared
                         endpoint URL, not a secret -- fine to reuse)
  NVIDIA_TEXT_MODEL   -- default: nvidia/nemotron-3-ultra-550b-a55b
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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

VISIBLE / GUI ACTIONS (opening apps, clicking, typing into windows, window
management, hotkeys, screenshots): this system runs on native Wayland, where
tools like xdotool/wmctrl generally CANNOT reliably click or type into
arbitrary application windows. Do NOT use xdotool/wmctrl for clicking or
typing. Instead, for any step that needs real on-screen interaction, delegate
that single step to the existing, already-tested automation agent by calling:

    {python_bin} {agent_path} "<short natural-language command>"

which already knows how to open apps / click / type / manage windows /
take screenshots correctly on this system via AT-SPI (accessibility tree) and
uinput (hardware-level keyboard/mouse) -- e.g.:
    {python_bin} {agent_path} "open calculator"
    {python_bin} {agent_path} "take a screenshot"
    {python_bin} {agent_path} "minimize calculator"
Only use this delegation for GUI/visible steps. Use plain shell commands for
file operations, backend logic, and verification checks as usual.

SCREENSHOT LOCATION (concrete fact, do not guess): screenshots taken via
'{python_bin} {agent_path} "take a screenshot"' are ALWAYS saved under
{screenshots_dir}/desktop_screenshot_<timestamp>.png -- use that exact
directory and filename pattern when verifying a screenshot was created.
Never assume $HOME/Pictures or any other location for this project.
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
) -> bool:
    """
    Heuristic: decide whether SmartParser.parse_multi()'s result for `raw`
    is trustworthy enough to execute directly, or whether it should be
    treated as unreliable prose and escalated to the LLM planner instead.

    This is intentionally simple and conservative -- false positives just
    mean an extra (skippable, key-gated) AI call; false negatives mean a
    short legitimate compound command gets executed as before.
    """
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
    return False


class LLMPlanner:
    """Generates and runs a shell-script plan for complex instructions."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key or os.getenv("NVIDIA_TEXT_API_KEY")
        self.base_url = base_url or os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        self.model = model or os.getenv("NVIDIA_TEXT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
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
            return PlanResult(ok=False, error="NVIDIA_TEXT_API_KEY not set")
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
                max_tokens=16384,
                extra_body={"chat_template_kwargs": {"enable_thinking": True}},
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

    def plan_and_execute(self, raw_instruction: str, exec_timeout: int = 90) -> Optional[bool]:
        """
        Generate a script for `raw_instruction` and execute it through the
        existing, safety-checked tasks/run_command.py path.

        Returns:
            True / False -- a plan was generated and executed (reflects its result)
            None         -- planner unavailable or generation failed; caller
                            should fall back to the deterministic pipeline
        """
        if not self.available():
            logger.debug("LLMPlanner: not available (no NVIDIA_TEXT_API_KEY) -- skipping")
            return None

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
        ok = run_command_execute({"command": f"bash {script_path}", "timeout": exec_timeout}, {})
        return ok


# Singleton
llm_planner = LLMPlanner()

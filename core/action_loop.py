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
  (NVIDIA_TEXT_API_KEY) and asks for exactly ONE next action from a small,
  fixed vocabulary (click/type/key/wait/scroll/run_shell/done). One small
  decision at a time -- not a whole plan upfront -- is what lets it recover
  from surprises the way a human would.

Safety:
  The loop never invents its own execution mechanism. Every action still
  runs through existing, already-tested primitives:
    click/type   -> core.semantic_vision.SemanticVision (AT-SPI/OCR/vision tiers)
    key          -> core.gui_controller.GUIController
    run_shell    -> tasks.run_command (still goes through core.safety_guard)
  This module only decides WHAT to do next; it does not change HOW actions
  execute or bypass any existing safety mechanism.

Fully inert without NVIDIA_TEXT_API_KEY (the decision step needs it) --
with no key, `ActionLoop.available()` is False.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


DECISION_SYSTEM_PROMPT = """You are the decision-making step of a desktop automation loop on Ubuntu (GNOME/Wayland).
You are given: a GOAL, the CURRENT OBSERVATION (what's on screen right now), and the HISTORY of
actions already taken this run. Decide exactly ONE next action to make progress toward the goal.

Respond with ONLY a single JSON object (no markdown fences, no commentary), using one of these shapes:
  {"action": "click", "description": "short description of the element to click"}
  {"action": "type", "text": "text to type into the currently focused field"}
  {"action": "key", "key": "Return"}
  {"action": "wait", "seconds": 1.5}
  {"action": "scroll", "direction": "down", "amount_px": 600}
  {"action": "run_shell", "command": "a single safe read-only or file-scoped shell command"}
  {"action": "done", "success": true, "message": "why the goal is achieved"}
  {"action": "done", "success": false, "message": "why it can't proceed further"}

Rules:
- Pick "done" with success=false rather than looping on an action that isn't working.
- Never use run_shell for sudo, package install/remove/upgrade, or destructive file operations.
- Window focus for raw keyboard input ("type"/"key") cannot always be guaranteed on this
  system, so "click" on AT-SPI-visible buttons is the MORE reliable default action here,
  even though it takes more individual steps -- prefer it for calculators, dialogs, and
  anything with clickable digit/letter buttons.
- Only use "type" for free-text fields where no equivalent buttons exist (e.g. a search box
  or a text form field). After a "type" action, check the NEXT observation carefully to
  confirm the text actually landed in the right place before proceeding -- if it didn't
  appear, the window may not have been focused; try "click"-ing the field first instead.
"""


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


def _observe_atspi(app_hint: str = "") -> str:
    """
    Fast, free, local observation: list interactive elements from running
    apps via the AT-SPI accessibility tree. Reuses atspi_navigator's own
    walk/name/role helpers rather than re-implementing tree traversal.
    Returns "" if nothing usable was found (caller should fall back to vision).
    """
    try:
        import pyatspi
        from core.atspi_navigator import _walk, _name, _role  # reuse, don't duplicate

        desktop = pyatspi.Registry.getDesktop(0)
        lines: list[str] = []
        apps_seen: list[str] = []
        for app in desktop:
            if app is None:
                continue
            app_name = app.name or ""
            if app_hint and app_hint.lower() not in app_name.lower():
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
        from core.semantic_vision import semantic_vision
        from core.logger import take_screenshot
        if not semantic_vision.api_key:
            return ""
        import base64
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
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_steps: int = 12,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.getenv("NVIDIA_TEXT_API_KEY")
        self.base_url = base_url or os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        # IMPORTANT: this loop makes ONE fast tactical decision per step, unlike
        # llm_planner.py which makes ONE deep decision for a whole task upfront.
        # A 550B "ultra" reasoning model is the right tool for the latter and a
        # very wrong (50-90s/step) tool for the former. Use a small/fast model
        # here by default; override with NVIDIA_LOOP_MODEL if a faster option
        # is available on your key. Falls back to NVIDIA_TEXT_MODEL only if
        # NVIDIA_LOOP_MODEL isn't set, so existing setups keep working.
        self.model = (
            model
            or os.getenv("NVIDIA_LOOP_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
        )
        self.max_steps = max_steps
        self.timeout = timeout
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def _observe(self, app_hint: str = "") -> str:
        obs = _observe_atspi(app_hint)
        if obs:
            return obs
        obs = _observe_vision()
        return obs or "(no observation available -- screen may be empty or unreadable)"

    def _decide(self, goal: str, observation: str, history: list[LoopStep]) -> dict[str, Any]:
        history_text = "\n".join(
            f"{i+1}. did {s.decision.get('action')}({ {k: v for k, v in s.decision.items() if k != 'action'} }) -> {s.result}"
            for i, s in enumerate(history[-6:])  # last 6 steps is enough context
        ) or "(none yet)"

        user_msg = f"GOAL: {goal}\n\nCURRENT OBSERVATION:\n{observation}\n\nHISTORY:\n{history_text}"
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.1,
            timeout=self.timeout,
            # Tactical per-step decisions need speed, not chain-of-thought --
            # disable "thinking" mode (only relevant for models that support
            # it; harmless extra_body for ones that don't recognize it).
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        text = (resp.choices[0].message.content or "").strip()
        # Strip accidental markdown fences
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except Exception:
            logger.warning(f"ActionLoop: could not parse decision JSON: {text!r}")
            return {"action": "done", "success": False, "message": f"Model returned unparseable decision: {text[:200]}"}

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
            from core.atspi_navigator import wait_for_app, _try_focus_app
            app = wait_for_app([app_hint], timeout=2.0)
            if app is None:
                return False
            return _try_focus_app(app)
        except Exception as exc:
            logger.debug(f"ActionLoop: focus attempt failed: {exc}")
            return False

    def _act(self, decision: dict[str, Any], app_hint: str = "") -> str:
        action = decision.get("action")
        try:
            if action == "click":
                from core.semantic_vision import semantic_vision
                # Scope the AT-SPI walk to the target app -- without this it
                # searches EVERY running app's entire tree (confirmed live:
                # ~27s per click vs ~1-2s scoped), since a bare description
                # like "the 7 button" can keyword-match unrelated elements
                # across every other open window too.
                res = semantic_vision.find_and_click(decision.get("description", ""), app_name=app_hint)
                return f"clicked via {res.method}" if res.success else f"click failed: {res.error}"

            if action == "type":
                focused = self._ensure_focused(app_hint)
                from core.gui_controller import GUIController
                GUIController().type_text(decision.get("text", ""))
                return "typed" if focused or not app_hint else "typed (WARNING: could not confirm target app was focused)"

            if action == "key":
                self._ensure_focused(app_hint)
                from core.gui_controller import GUIController
                GUIController().hotkey(decision.get("key", ""))
                return "key pressed"

            if action == "wait":
                secs = float(decision.get("seconds", 1))
                time.sleep(min(secs, 10))
                return f"waited {secs}s"

            if action == "scroll":
                from core.gui_controller import GUIController
                GUIController().scroll(
                    clicks=int(decision.get("amount_px", 600)) // 100 or 1,
                    direction=decision.get("direction", "down"),
                )
                return "scrolled"

            if action == "run_shell":
                from tasks.run_command import execute as run_cmd
                ok = run_cmd({"command": decision.get("command", ""), "timeout": 20}, {})
                return "shell command succeeded" if ok else "shell command failed"

            return f"unknown action {action!r} -- ignored"
        except Exception as exc:
            return f"action raised exception: {exc}"

    def run(self, goal: str, app_hint: str = "") -> LoopResult:
        if not self.available():
            return LoopResult(success=False, message="NVIDIA_TEXT_API_KEY not set -- ActionLoop unavailable")

        steps: list[LoopStep] = []
        logger.info(f"ActionLoop: starting goal={goal!r}")
        for i in range(self.max_steps):
            observation = self._observe(app_hint)
            decision = self._decide(goal, observation, steps)
            logger.info(f"ActionLoop[{i+1}/{self.max_steps}]: decision={decision}")

            if decision.get("action") == "done":
                success = bool(decision.get("success", False))
                message = str(decision.get("message", ""))
                steps.append(LoopStep(observation, decision, "loop ended"))
                logger.info(f"ActionLoop: done (success={success}): {message}")
                return LoopResult(success=success, message=message, steps=steps)

            result = self._act(decision, app_hint)
            logger.info(f"ActionLoop[{i+1}/{self.max_steps}]: result={result}")
            steps.append(LoopStep(observation, decision, result))

        return LoopResult(success=False, message=f"max_steps ({self.max_steps}) reached without completion", steps=steps)


# Singleton
action_loop = ActionLoop()

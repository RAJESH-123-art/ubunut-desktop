"""One-shot structured planning and deterministic desktop/browser execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from loguru import logger

from core.action_policy import requires_approval
from core.atomic_write import atomic_write_json
from core.task_contract import ActionOutcome, SideEffect

_ALLOWED_ACTIONS = {
    "navigate",
    "click",
    "select_option",
    "fill",
    "press",
    "scroll",
    "drag_drop",
    "wait_for",
    "extract_text",
    "read_field",
    "check",
    "upload_file",
    "open_tab",
    "switch_tab",
    "close_tab",
    "download",
    "verify_file",
    "launch_app",
    "focus_app",
    "click_ui",
    "type_ui",
    "copy_ui",
    "paste_ui",
    "hotkey_ui",
    "wait_ui",
    "read_ui",
    "visual_click",
    "visual_drag",
    "transform_text",
    "assert_value",
    "wait",
    "task",
    "cap",
}
_BROWSER_ACTIONS = {
    "navigate",
    "click",
    "select_option",
    "fill",
    "press",
    "scroll",
    "drag_drop",
    "wait_for",
    "extract_text",
    "read_field",
    "check",
    "upload_file",
    "open_tab",
    "switch_tab",
    "close_tab",
    "download",
}
_NATIVE_ACTIONS = {
    "launch_app",
    "focus_app",
    "click_ui",
    "type_ui",
    "copy_ui",
    "paste_ui",
    "hotkey_ui",
    "wait_ui",
    "read_ui",
}
_VISUAL_ACTIONS = {"visual_click", "visual_drag"}
_LOCAL_ACTIONS = {"transform_text", "assert_value", "wait"}
_CONDITION_OPERATORS = {
    "equals", "not_equals", "contains", "not_contains", "truthy", "falsy",
}
_TRANSFORM_OPERATIONS = {
    "strip", "lower", "upper", "replace", "prefix", "suffix", "split", "join", "length",
}
_MAX_STEPS = 30
_MAX_STEP_REPAIRS = 3
_FORBIDDEN_TASK_ACTIONS = {"run_command", "universal_fallback", "browser_action"}

_PLAN_PROMPT = """You convert an Ubuntu desktop goal into one complete deterministic JSON plan.
CRITICAL: Begin your answer with the JSON object immediately. Do not perform extended preliminary analysis, restatement of the goal, or a verification pass — minimal thinking only. Your entire budget is for the JSON answer itself.
Return ONLY one JSON object with this shape:
{"summary":"short", "steps":[{"action":"...","args":{...},"expect":{...},"when":{...}}]}

Allowed actions and arguments:
- navigate: {"url":"https://..."}
- click: {"text":"visible text", "near":"optional nearby heading", "dialog":{"type":"alert|confirm|prompt|beforeunload","accept":true,"message_contains":"optional","prompt_text":"optional"}, "popup":{"url":"declared expected popup URL","activate":true,"title_contains":"optional"}}
- select_option: {"label":"dropdown label", "option":"visible option text"}
- fill: {"label":"field label", "text":"exact text"}
- press: {"key":"Enter"}
- scroll: {"direction":"down|up", "amount_px":600}
- drag_drop: {"source":"visible source text", "target":"visible destination text", "source_near":"optional", "target_near":"optional"}
- wait_for: {"text":"visible text", "timeout_seconds":10}
- extract_text: {"target":"visible text or element name", "near":"optional nearby heading", "attribute":"optional DOM attribute", "sensitive":false}
- read_field: {"label":"field label", "sensitive":false}
- check: {"label":"checkbox label", "checked":true}
- upload_file: {"label":"file input label", "path":"absolute existing file path"}
- open_tab: {"url":"https://..."}
- switch_tab: {"title_contains":"optional title", "url_contains":"optional URL fragment"}
- close_tab: {}
- download: {"text":"link/button text", "directory":"absolute directory", "near":"optional heading", "wait_for_completion":false, "filename_contains":"optional expected name fragment", "extensions":[".iso"], "source_domain":"optional expected link domain"}
- verify_file: {"directory":"absolute directory", "extensions":[".iso",".crdownload",".part"], "min_bytes":1}
- launch_app: {"app":"installed application name"}
- focus_app: {"app":"running application name"}
- click_ui: {"app":"running application name", "text":"accessible control name"}
- type_ui: {"app":"running application name", "field":"accessible field name", "text":"exact text"}
- copy_ui: {"app":"running application name", "target":"accessible readable field or text", "sensitive":false}
- paste_ui: {"app":"running application name", "field":"accessible editable field", "text":"exact clipboard text"}
- hotkey_ui: {"app":"running application name", "keys":["ctrl","s"]}
- wait_ui: {"app":"running application name", "text":"accessible text/control", "timeout_seconds":10}
- read_ui: {"app":"running application name", "target":"accessible field or text name", "sensitive":false}
- visual_click: {"app":"running application name", "target":"precise visible target description", "minimum_confidence":0.85}
- visual_drag: {"app":"running application name", "source":"precise visible source description", "target":"precise visible destination description", "minimum_confidence":0.90, "duration_seconds":0.8}
- transform_text: {"value":"text/list or ${step.N.field}", "operation":"strip|lower|upper|replace|prefix|suffix|split|join|length", "old":"for replace", "new":"replacement/prefix/suffix", "separator":"for split/join", "index":0}
- assert_value: {"value":"literal or ${step.N.field}","operator":"equals|not_equals|contains|not_contains|truthy|falsy","expected":"when required"}
- wait: {"seconds":1.0}
- task: {"name":"registered task module", "params":{...}}
- cap: {"cap":"exact capability name from UNIVERSAL CAPABILITY CATALOG", "args":{...capability inputs...}}
  Runs one universal OS capability (files, sheets, browser, research, calc,
  system, data, verify, ...) directly. "args" keys must match the listed
  inputs for that capability exactly. Prefer cap over task when both could
  serve; prefer it strongly for filesystem/spreadsheet/verify operations.

Evidence fields produced by earlier steps (reference as ${step.N.field}):
- extract_text, read_field, copy_ui, read_ui → text
- transform_text → value
- download → download (absolute file path)
- verify_file → file, bytes
- launch_app → launched; focus_app → focused
- task → the task's output_fields from REGISTERED TASK SCHEMAS
  (e.g. file_read → path/bytes/content, file_write → path/bytes/append)
  plus task (module name). Tasks without listed output_fields produce no
  referenceable data beyond task.
- cap → every key of the capability's returned data becomes a referenceable
  field (e.g. calc.evaluate → result, fs.write → path, system.get_username →
  username, sheet.append_rows → appended). Failed caps return no fields.

Rules:
- Produce the entire plan in one response. Do not ask for another LLM decision per step.
- Prefer browser DOM actions over scrolling. Use scroll only when needed.
- For native GTK/Qt apps, launch once and then use focus_app/click_ui/type_ui/
  hotkey_ui/wait_ui. Always name the target app; never type into unknown focus.
- For native click/type/hotkey actions, provide expect.ui_text when a visible
  state change should follow, or expect.app_running after launch.
- Do not use shell commands, coordinates, scripts, recursive agent calls, or invented actions.
- Use visual_click or visual_drag only for a canvas or visual surface where normal
  browser DOM or native accessibility controls are unavailable. Never provide coordinates.
- Every consequential final operation must be represented explicitly.
- Add wait_for after actions that asynchronously reveal new controls.
- For submissions, sends, purchases, or other final controls, provide a semantic
  expectation such as expect.text, expect.text_absent, expect.url_contains, or
  expect.url_changed.
- Downloads default to success once a real file/partial file starts growing. Set
  wait_for_completion=true only when later steps require the complete file.
- Add verify_file after downloads or file-producing tasks.
- Keep the plan concise and normally under 15 steps.
- Later args may reference verified earlier evidence as ${step.N.field}, for example
  ${step.2.download} or ${step.1.text}. Use references to transfer verified data
  between browser and native applications instead of asking the model again.
- A step may include when:{"value":"${step.N.field}","operator":"equals|not_equals|contains|not_contains|truthy|falsy","expected":"optional"} to skip that step deterministically. Conditions may reference only earlier steps.
- Never guess absolute filesystem paths or usernames. First run
  cap system.get_username (field: username), then build paths as
  /home/${step.1.username}/... — or pass a relative path and let the
  capability expand it."
- Use transform_text for local deterministic data preparation and assert_value for
  local verification; neither requires another model request.
- Only switch or close browser tabs owned by this execution.
"""

# Some free OpenAI-compatible models return an empty completion for the full
# policy prompt above.  This version retains the executable contract and lets
# ``validate_plan`` remain the final authority for every action and argument.
_COMPACT_PLAN_PROMPT = """Return only one JSON object, beginning immediately with minimal thinking:
{"summary":"short","steps":[{"action":"...","args":{},"expect":{},"when":{}}]}
Plan the user goal using ONLY these action names, exactly as written (never prefix them):
navigate(url), click(text), select_option(label,option), fill(label,text), press(key), scroll(direction,amount_px), wait_for(text), extract_text(target), read_field(label), check(label,checked), upload_file(paths), open_tab(url), switch_tab(index), close_tab(index), download(text,directory,wait_for_completion,filename_contains,extensions,source_domain), verify_file(directory,extensions,min_bytes),
launch_app(app), focus_app(app), click_ui(app,text), type_ui(app,field,text), copy_ui(app,target), paste_ui(app,field,text), hotkey_ui(app,keys), wait_ui(app,text), read_ui(app,target),
visual_click(app,target,minimum_confidence), visual_drag(app,source,target,minimum_confidence,duration_seconds),
transform_text(value,operation), assert_value(value,operator,expected), wait(seconds), task(task_name,args), cap(cap_name,args — a universal capability from the catalog, e.g. fs.write, sheet.append_rows, calc.evaluate).
Example step: {"action":"launch_app","args":{"app":"Firefox"}}.
Rules: action strings must be bare names like "launch_app", never "desktop.launch_app". Use no shell, scripts, coordinates, invented actions, or recursive agent calls. Keep under 15 steps. Evidence fields per action: extract_text/read_field/copy_ui/read_ui→text; transform_text→value; download→download; verify_file→file,bytes; task→see REGISTERED TASK SCHEMAS output_fields. Use ${step.N.field} only for earlier verified evidence. A step may include when:{"value":"${step.N.field}","operator":"equals|not_equals|contains|not_contains|truthy|falsy","expected":"optional string"} to skip that step deterministically; conditions may reference only earlier steps. Give each final action a relevant expect condition when possible."""


@dataclass(frozen=True)
class PlanStep:
    action: str
    args: dict[str, Any]
    expect: dict[str, Any] = field(default_factory=dict)
    when: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuredPlan:
    summary: str
    steps: tuple[PlanStep, ...]


@dataclass
class ExecutionResult:
    success: bool
    message: str
    completed_steps: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)


class UncertainStepOutcome(RuntimeError):
    """An action may have happened, so automatic replay is unsafe."""


class ActionNotDispatched(RuntimeError):
    """A target could not be resolved and no external action was issued."""


def _provider_text(value: Any) -> str:
    """Extract text from OpenAI-compatible provider response variants."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(_provider_text(item) for item in value).strip()
    if isinstance(value, dict):
        for name in ("text", "content", "value"):
            text = _provider_text(value.get(name))
            if text:
                return text
        return ""
    for name in ("text", "content", "value"):
        text = _provider_text(getattr(value, name, None))
        if text:
            return text
    return ""


def _completion_text(response: Any) -> str:
    """Read only the provider's final answer, never its private reasoning."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    # ``reasoning_content`` is a model scratchpad, not an answer.  Treating it
    # as a plan made a reasoning-only TokenRouter response look non-empty, then
    # caused JSON parsing to fail before the bounded compact-plan retry ran.
    for name in ("content", "text"):
        text = _provider_text(getattr(message, name, None))
        if text:
            return text
    return ""


def _json_payload_from_completion(text: str, *, label: str) -> dict[str, Any]:
    """Parse a provider reply while preserving strict plan validation later."""
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.IGNORECASE)
    if not normalized:
        raise ValueError(f"{label} returned an empty response")
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as initial_error:
        # Some compatible providers prepend a short reasoning sentence before
        # the JSON object.  Extract exactly one decodable object; validation
        # below still rejects unsupported actions and malformed fields.
        decoder = json.JSONDecoder()
        payload = None
        for match in re.finditer(r"\{", normalized):
            try:
                candidate, _end = decoder.raw_decode(normalized[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:
            raise ValueError(f"{label} returned invalid JSON: {initial_error}") from initial_error
    if not isinstance(payload, dict):
        raise TypeError(f"{label} response must be a JSON object")
    return payload


class StructuredPlanner:
    """Generate one complete action plan with one bounded LLM request."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("NIKKI_TEXT_API_KEY") or os.getenv("APINEX_API_KEY")
        self.base_url = base_url or os.getenv(
            "NIKKI_TEXT_BASE_URL", os.getenv("APINEX_BASE_URL", "https://api.apinex.bond/v1")
        )
        self.model = model or os.getenv(
            "NIKKI_TEXT_MODEL", os.getenv("APINEX_TEXT_MODEL", "free/gpt-5.6-luna")
        )
        # Free-tier reasoning models regularly spend 30-90s on the full plan
        # prompt even with thinking hints disabled.  NIKKI_TEXT_TIMEOUT
        # (seconds) tunes the planner HTTP budget; default 90s.
        _env_timeout = os.getenv("NIKKI_TEXT_TIMEOUT", "90")
        try:
            _default_timeout = float(_env_timeout)
        except ValueError:
            _default_timeout = 90.0
        self.timeout = timeout if timeout is not None else _default_timeout
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def _request_options(self) -> dict[str, Any]:
        """Require a machine-readable final response from compatible APIs."""
        options: dict[str, Any] = {"response_format": {"type": "json_object"}}
        if "tokenrouter.com" in self.base_url.lower():
            options["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }
        return options

    def _completion(self, system: str, user: str) -> str:
        # Reasoning-first models spend part of this budget on hidden
        # ``reasoning_content`` before the answer, so the plan budget must
        # cover both or the visible content arrives truncated/empty.
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=6000,
            timeout=self.timeout,
            **self._request_options(),
        )
        return _completion_text(response)

    def plan(self, goal: str) -> StructuredPlan:
        if not self.available():
            raise RuntimeError("APINEX_API_KEY is not configured")
        from tasks import task_catalog

        capabilities = json.dumps(task_catalog(), separators=(",", ":"))
        universal = _universal_catalog_compact()
        request_sections = [f"GOAL: {goal}", f"REGISTERED TASK SCHEMAS: {capabilities}"]
        if universal:
            request_sections.append(
                "UNIVERSAL CAPABILITY CATALOG (for cap steps; [c] = requires "
                "confirmation): " + universal
            )
        full_request = "\n".join(request_sections)
        text = self._completion(_PLAN_PROMPT, full_request)
        payload: dict[str, Any] | None = None
        parse_error: Exception | None = None
        if text:
            try:
                payload = _json_payload_from_completion(text, label="Planner")
            except (TypeError, ValueError) as exc:
                parse_error = exc
        # Free-tier models sometimes return a well-formed JSON object with
        # an empty/missing ``steps`` list (no browser/capability context was
        # consulted).  An empty-text retry is not enough -- retry the full
        # prompt once, then fall back to the bounded compact prompt.
        if payload is None or not isinstance(payload.get("steps"), list) or not payload.get("steps"):
            logger.warning(
                "Planner returned no usable steps (empty=%s, error=%s); retrying full prompt once",
                not text, parse_error,
            )
            text = self._completion(_PLAN_PROMPT, full_request)
            if not text:
                # Planning is read-only.  A single compact retry is bounded and
                # never bypasses plan validation or approval before execution.
                logger.warning("Planner returned an empty full-prompt response; using compact prompt once")
                text = self._completion(_COMPACT_PLAN_PROMPT, f"GOAL: {goal}")
            payload = _json_payload_from_completion(text, label="Planner")
        try:
            return validate_plan(payload)
        except (TypeError, ValueError) as exc:
            # A parsed plan that fails validation (invented action, invalid
            # condition, too many steps) previously aborted planning with no
            # retry.  Retry the bounded compact prompt once before giving up;
            # ``validate_plan`` remains the final authority for the retry too.
            logger.warning(
                "Planner output failed validation (%s); retrying compact prompt once", exc
            )
            text = self._completion(_COMPACT_PLAN_PROMPT, f"GOAL: {goal}")
            payload = _json_payload_from_completion(text, label="Planner")
            return validate_plan(payload)

    def repair_step(
        self,
        goal: str,
        original_plan: StructuredPlan,
        failed_step_number: int,
        failed_step: PlanStep,
        error: str,
        evidence: list[dict[str, Any]],
    ) -> PlanStep | None:
        """Request one bounded replacement for one failed, safe step."""
        if not self.available():
            return None
        from tasks import task_catalog

        request = {
            "goal": goal,
            "original_plan": plan_payload(original_plan),
            "failed_step_number": failed_step_number,
            "failed_step": {
                "action": failed_step.action,
                "args": failed_step.args,
                "expect": failed_step.expect,
                "when": failed_step.when,
            },
            "exact_error": error,
            "verified_evidence": _evidence_for_external_repair(evidence),
            "registered_task_schemas": task_catalog(),
        }
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _REPAIR_PROMPT},
                {"role": "user", "content": json.dumps(request, default=str)},
            ],
            temperature=0.0,
            max_tokens=900,
            timeout=min(self.timeout, 60.0),
            **self._request_options(),
        )
        payload = _json_payload_from_completion(
            _completion_text(response), label="Repair planner"
        )
        if payload.get("unsupported"):
            return None
        replacement = payload.get("replacement")
        if not isinstance(replacement, dict):
            raise TypeError("Repair response requires replacement or unsupported")
        return validate_plan({"summary": "single-step repair", "steps": [replacement]}).steps[0]


def _evidence_for_external_repair(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep structural evidence while withholding values marked sensitive."""
    filtered: list[dict[str, Any]] = []
    for item in evidence:
        if not item.get("sensitive"):
            filtered.append(dict(item))
            continue
        filtered.append({
            "step": item.get("step"),
            "action": item.get("action"),
            "sensitive": True,
            "value_withheld": True,
        })
    return filtered


_REPAIR_PROMPT = """Repair exactly one failed step in an immutable desktop automation plan.
Answer with the JSON object immediately, minimal thinking only.
Return ONLY one JSON object in one of these forms:
{"replacement":{"action":"...","args":{...},"expect":{...}}}
{"unsupported":"short reason"}

Rules:
- Replace only the supplied failed step; never return a complete new plan.
- Use only the same allowed actions and registered task schemas.
- Preserve the user's goal and verified prior evidence.
- Do not repeat or alter completed steps.
- Do not use shell commands, coordinates, scripts, or recursive agent calls.
- Return unsupported when no safe deterministic replacement exists.
"""

_STEP_REFERENCE_RE = re.compile(r"\$\{step\.(\d+)\.([a-zA-Z_][\w]*)\}")


def _resolve_step_references(value: Any, evidence: list[dict[str, Any]]) -> Any:
    """Resolve nested references while preserving exact referenced value types."""
    if isinstance(value, dict):
        return {key: _resolve_step_references(item, evidence) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_step_references(item, evidence) for item in value]
    if not isinstance(value, str):
        return value

    exact = _STEP_REFERENCE_RE.fullmatch(value)
    if exact:
        step_number, field = int(exact.group(1)), exact.group(2)
        match = next((item for item in evidence if item.get("step") == step_number), None)
        if match is None or field not in match:
            raise ValueError(f"Unresolved step reference {value!r}")
        return match[field]

    def replace(match: re.Match[str]) -> str:
        step_number, field = int(match.group(1)), match.group(2)
        item = next((entry for entry in evidence if entry.get("step") == step_number), None)
        if item is None or field not in item:
            raise ValueError(f"Unresolved step reference {match.group(0)!r}")
        return str(item[field])

    return _STEP_REFERENCE_RE.sub(replace, value)


def _condition_matches(condition: dict[str, Any]) -> bool:
    if not condition:
        return True
    value = condition.get("value")
    expected = condition.get("expected")
    operator = str(condition.get("operator", "equals"))
    if operator == "equals":
        return value == expected
    if operator == "not_equals":
        return value != expected
    if operator == "contains":
        return str(expected) in str(value)
    if operator == "not_contains":
        return str(expected) not in str(value)
    if operator == "truthy":
        return bool(value)
    if operator == "falsy":
        return not bool(value)
    raise ValueError(f"Unsupported condition operator {operator!r}")


def validate_plan(payload: object) -> StructuredPlan:
    """Validate untrusted model output before any action can execute."""
    if not isinstance(payload, dict):
        raise TypeError("Plan must be a JSON object")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Plan requires a non-empty steps list")
    if len(raw_steps) > _MAX_STEPS:
        raise ValueError(f"Plan exceeds maximum of {_MAX_STEPS} steps")

    steps: list[PlanStep] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise TypeError(f"Step {index + 1} must be an object")
        action = str(raw_step.get("action", "")).strip()
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"Step {index + 1} uses unsupported action {action!r}")
        args = raw_step.get("args", {})
        expect = raw_step.get("expect", {})
        when = raw_step.get("when", {})
        if not isinstance(args, dict) or not isinstance(expect, dict) or not isinstance(when, dict):
            raise TypeError(f"Step {index + 1} args/expect/when must be objects")
        if when:
            operator = str(when.get("operator", ""))
            if operator not in _CONDITION_OPERATORS or "value" not in when:
                raise ValueError(f"Step {index + 1} has an invalid condition")
            for reference in _STEP_REFERENCE_RE.finditer(json.dumps(when, default=str)):
                if int(reference.group(1)) >= index + 1:
                    raise ValueError(
                        f"Step {index + 1} condition must reference only earlier steps"
                    )
        if action in {"navigate", "open_tab"}:
            url = str(args.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Step {index + 1} has an invalid web URL")
        elif action in {"click", "download"} and not str(args.get("text", "")).strip():
            raise ValueError(f"Step {index + 1} requires visible text")
        elif action == "click":
            dialog = args.get("dialog")
            popup = args.get("popup")
            if dialog is not None and popup is not None:
                raise ValueError(f"Step {index + 1} cannot expect both dialog and popup")
            if dialog is not None:
                if not isinstance(dialog, dict):
                    raise TypeError(f"Step {index + 1} dialog must be an object")
                dialog_type = str(dialog.get("type", ""))
                if dialog_type not in {"alert", "confirm", "prompt", "beforeunload"}:
                    raise ValueError(f"Step {index + 1} has an invalid dialog type")
                if "accept" in dialog and not isinstance(dialog["accept"], bool):
                    raise TypeError(f"Step {index + 1} dialog accept must be boolean")
            if popup is not None:
                if not isinstance(popup, dict):
                    raise TypeError(f"Step {index + 1} popup must be an object")
                popup_url = str(popup.get("url", ""))
                parsed_popup = urlparse(popup_url)
                if parsed_popup.scheme not in {"http", "https"} or not parsed_popup.netloc:
                    raise ValueError(f"Step {index + 1} popup requires a declared web URL")
        elif action == "download":
            extensions = args.get("extensions", [])
            if not isinstance(extensions, list) or not all(
                isinstance(ext, str) and ext.startswith(".") for ext in extensions
            ):
                raise TypeError(
                    f"Step {index + 1} download extensions must be dot-prefixed strings"
                )
            source_domain = str(args.get("source_domain", "")).strip().lower()
            if source_domain and (
                "://" in source_domain or "/" in source_domain or "." not in source_domain
            ):
                raise ValueError(f"Step {index + 1} has an invalid source domain")
        elif action == "select_option" and not str(args.get("option", "")).strip():
            raise ValueError(f"Step {index + 1} requires an option")
        elif action == "drag_drop":
            if not str(args.get("source", "")).strip() or not str(args.get("target", "")).strip():
                raise ValueError(f"Step {index + 1} requires drag source and target text")
        elif action == "fill" and "text" not in args:
            raise ValueError(f"Step {index + 1} requires text")
        elif action == "extract_text" and not str(args.get("target", "")).strip():
            raise ValueError(f"Step {index + 1} requires an extraction target")
        elif action == "read_field" and not str(args.get("label", "")).strip():
            raise ValueError(f"Step {index + 1} requires a field label")
        elif action in {"extract_text", "read_field"} and (
            "sensitive" in args and not isinstance(args["sensitive"], bool)
        ):
            raise TypeError(f"Step {index + 1} sensitive must be boolean")
        elif action == "check":
            if not str(args.get("label", "")).strip():
                raise ValueError(f"Step {index + 1} requires a checkbox label")
            if "checked" in args and not isinstance(args["checked"], bool):
                raise TypeError(f"Step {index + 1} checked must be boolean")
        elif action == "upload_file":
            if not str(args.get("label", "")).strip() or "path" not in args:
                raise ValueError(f"Step {index + 1} requires file label and path")
            raw_path = str(args["path"])
            if not _STEP_REFERENCE_RE.fullmatch(raw_path) and not Path(raw_path).expanduser().is_absolute():
                raise ValueError(f"Step {index + 1} upload path must be absolute")
        elif action == "switch_tab" and not (
            str(args.get("title_contains", "")).strip()
            or str(args.get("url_contains", "")).strip()
        ):
            raise ValueError(f"Step {index + 1} requires a tab title or URL fragment")
        elif action in _NATIVE_ACTIONS:
            app = str(args.get("app", "")).strip()
            if not app:
                raise ValueError(f"Step {index + 1} requires a target app")
            if action == "click_ui" and not str(args.get("text", "")).strip():
                raise ValueError(f"Step {index + 1} requires accessible control text")
            if action == "type_ui" and (
                "text" not in args or not str(args.get("field", "")).strip()
            ):
                raise ValueError(f"Step {index + 1} requires field and text")
            if action == "copy_ui" and not str(args.get("target", "")).strip():
                raise ValueError(f"Step {index + 1} requires a readable UI target")
            if action == "paste_ui" and (
                "text" not in args or not str(args.get("field", "")).strip()
            ):
                raise ValueError(f"Step {index + 1} requires field and text")
            if action == "hotkey_ui" and not args.get("keys"):
                raise ValueError(f"Step {index + 1} requires keys")
            if action == "wait_ui" and not str(args.get("text", "")).strip():
                raise ValueError(f"Step {index + 1} requires UI text")
            if action == "read_ui" and not str(args.get("target", "")).strip():
                raise ValueError(f"Step {index + 1} requires a readable UI target")
            if action in {"read_ui", "copy_ui"} and (
                "sensitive" in args and not isinstance(args["sensitive"], bool)
            ):
                raise TypeError(f"Step {index + 1} sensitive must be boolean")
        elif action == "transform_text":
            operation = str(args.get("operation", ""))
            if "value" not in args or operation not in _TRANSFORM_OPERATIONS:
                raise ValueError(f"Step {index + 1} has an invalid text transformation")
            if operation == "replace" and "old" not in args:
                raise ValueError(f"Step {index + 1} replace requires old text")
            if "index" in args and (
                not isinstance(args["index"], int) or isinstance(args["index"], bool)
            ):
                raise TypeError(f"Step {index + 1} transform index must be integer")
        elif action == "assert_value":
            operator = str(args.get("operator", ""))
            if "value" not in args or operator not in _CONDITION_OPERATORS:
                raise ValueError(f"Step {index + 1} has an invalid assertion")
        elif action == "wait":
            seconds = args.get("seconds", 1.0)
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                raise TypeError(f"Step {index + 1} wait seconds must be numeric")
            if not 0.0 <= float(seconds) <= 10.0:
                raise ValueError(f"Step {index + 1} wait seconds must be between 0 and 10")
            args = {"seconds": float(seconds)}
        elif action in {"visual_click", "visual_drag"}:
            if not str(args.get("app", "")).strip():
                raise ValueError(f"Step {index + 1} requires a target app")
            required_targets = ("target",) if action == "visual_click" else ("source", "target")
            if any(not str(args.get(key, "")).strip() for key in required_targets):
                raise ValueError(f"Step {index + 1} requires precise visual target descriptions")
            if any(key in args for key in ("x", "y", "coordinates")):
                raise ValueError(f"Step {index + 1} cannot provide blind coordinates")
            confidence = float(args.get("minimum_confidence", 0.85))
            if not 0.5 <= confidence <= 0.99:
                raise ValueError(
                    f"Step {index + 1} minimum confidence must be between 0.5 and 0.99"
                )
            if action == "visual_drag":
                duration = float(args.get("duration_seconds", 0.8))
                if not 0.1 <= duration <= 5.0:
                    raise ValueError(
                        f"Step {index + 1} drag duration must be between 0.1 and 5 seconds"
                    )
        elif action == "task":
            name = str(args.get("name", "")).strip()
            if not name:
                raise ValueError(f"Step {index + 1} requires a task name")
            if name in _FORBIDDEN_TASK_ACTIONS:
                raise ValueError(f"Step {index + 1} cannot invoke task {name!r}")
            from core.task_contract import validate_task_params
            from tasks import get_task_spec

            spec = get_task_spec(name)
            if spec is None:
                raise ValueError(f"Step {index + 1} references unknown task {name!r}")
            validated_params = validate_task_params(spec, args.get("params", {}))
            args = {"name": name, "params": validated_params}
        elif action == "cap":
            cap_name = str(args.get("cap", "")).strip()
            if not cap_name:
                raise ValueError(f"Step {index + 1} requires a capability name")
            if cap_name in _FORBIDDEN_TASK_ACTIONS or cap_name.startswith((
                "structured.", "task:",
            )):
                raise ValueError(f"Step {index + 1} cannot invoke capability {cap_name!r}")
            if "args" in args and not isinstance(args["args"], dict):
                raise TypeError(f"Step {index + 1} capability args must be an object")
        steps.append(
            PlanStep(
                action=action,
                args=dict(args),
                expect=dict(expect),
                when=dict(when),
            )
        )

    return StructuredPlan(
        summary=str(payload.get("summary", "Structured automation plan")).strip(),
        steps=tuple(steps),
    )


_UNIVERSAL_CATALOG_CACHE: str | None = None
_UNIVERSAL_CONTRACT_REGISTRY = None


def _universal_contracts_registry():
    """Cached conservative registry for catalog/contract lookups.

    Built with approve_all=False so requires_confirmation stays True for the
    caps that declare it; actual dispatch registries are built per executor
    with that executor's approval policy.  Read-only use: contract metadata.
    """
    global _UNIVERSAL_CONTRACT_REGISTRY
    if _UNIVERSAL_CONTRACT_REGISTRY is None:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                from capabilities import build_registry

                _UNIVERSAL_CONTRACT_REGISTRY = build_registry()
            except Exception as exc:  # pragma: no cover - best-effort
                logger.warning(f"Universal capability registry unavailable: {exc}")
    return _UNIVERSAL_CONTRACT_REGISTRY


def _universal_catalog_compact() -> str:
    """Compact one-line-per-capability catalog for the planning prompt.

    Form: ``name(inputs)[c]`` where ``[c]`` marks confirmation-required caps.
    Kept small so free-tier planners can still emit complete plans in one
    response; full contracts stay in the registry.  Built once per process.
    """
    global _UNIVERSAL_CATALOG_CACHE
    if _UNIVERSAL_CATALOG_CACHE is not None:
        return _UNIVERSAL_CATALOG_CACHE
    registry = _universal_contracts_registry()
    if registry is None:
        _UNIVERSAL_CATALOG_CACHE = ""
        return ""
    lines = []
    for contract in registry.contracts():
        line = contract.name
        if contract.inputs:
            line += f"({','.join(contract.inputs)})"
        if contract.requires_confirmation:
            line += "[c]"
        lines.append(line)
    _UNIVERSAL_CATALOG_CACHE = ";".join(lines)
    return _UNIVERSAL_CATALOG_CACHE


def _universal_contract(name: str):
    """Return the CapabilityContract for one universal capability, or None."""
    if not name:
        return None
    registry = _universal_contracts_registry()
    if registry is None:
        return None
    capability = registry.get(name)
    return capability.contract if capability is not None else None


def plan_payload(plan: StructuredPlan) -> dict[str, Any]:
    """Convert a validated plan into JSON-safe checkpoint data."""
    return {
        "summary": plan.summary,
        "steps": [
            {
                "action": step.action,
                "args": step.args,
                "expect": step.expect,
                **({"when": step.when} if step.when else {}),
            }
            for step in plan.steps
        ],
    }


def plan_fingerprint(plan: StructuredPlan) -> str:
    """Bind one approval to the exact ordered plan and parameters."""
    canonical = json.dumps(
        plan_payload(plan), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def describe_plan(plan: StructuredPlan) -> str:
    """Return a concise redacted plan suitable for one-time confirmation."""
    lines = [plan.summary]
    for index, step in enumerate(plan.steps, 1):
        args = dict(step.args)
        for sensitive_key in ("text", "password", "token", "secret"):
            if sensitive_key in args:
                args[sensitive_key] = "<redacted>"
        if step.action == "cap" and isinstance(args.get("args"), dict):
            args["args"] = {
                key: "<redacted>" if key in ("text", "password", "token", "secret") else value
                for key, value in args["args"].items()
            }
        condition = f" when={json.dumps(step.when, default=str, sort_keys=True)}" if step.when else ""
        lines.append(
            f"{index}. {step.action}: {json.dumps(args, default=str, sort_keys=True)}{condition}"
        )
    return "\n".join(lines)


class StructuredExecutor:
    """Execute a validated plan locally without further LLM decisions."""

    def __init__(
        self,
        *,
        approve_all: bool = False,
        step_timeout: float = 15.0,
        total_timeout: float = 120.0,
        execution_id: str | None = None,
        repair_callback: Callable[
            [str, StructuredPlan, int, PlanStep, str, list[dict[str, Any]]],
            PlanStep | None,
        ] | None = None,
        repair_approval_callback: Callable[[StructuredPlan], bool] | None = None,
    ) -> None:
        self.approve_all = approve_all
        self.step_timeout = max(1.0, min(step_timeout, 60.0))
        self.total_timeout = max(self.step_timeout, min(total_timeout, 600.0))
        self.execution_id = execution_id or uuid.uuid4().hex
        self.repair_callback = repair_callback
        self.repair_approval_callback = repair_approval_callback
        self._goal_context = ""
        self._universal_registry_instance: Any = None
        self._allowed_hosts: set[str] = set()
        self._allowed_origins: set[str] = set()
        self._visual_locator = None
        self._owned_tab_counter = 0
        self._resume_tab_marker = ""
        self._owned_pages: list[Any] = []
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        from core.cdp_browser import CDP_URL, ensure_chrome_cdp
        ensure_chrome_cdp()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(CDP_URL)
        context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        marker = f"desktop-agent:{self.execution_id}"
        desired_marker = self._resume_tab_marker or marker
        for page in context.pages:
            try:
                if page.evaluate("window.name") == desired_marker:
                    self._page = page
                    break
            except Exception as exc:
                logger.debug(f"Could not inspect candidate owned browser tab: {exc}")
                continue
        if self._page is None:
            self._page = context.new_page()
            self._page.evaluate("marker => { window.name = marker; }", marker)
        self._page.bring_to_front()
        if self._page not in self._owned_pages:
            self._owned_pages.append(self._page)
        return self._page

    def _owned_page_candidates(self, current_page: Any) -> list[Any]:
        candidates = list(self._owned_pages)
        try:
            candidates.extend(current_page.context.pages)
        except Exception as exc:
            logger.debug(f"Could not enumerate current browser context pages: {exc}")
        if self._browser is not None:
            for context in self._browser.contexts:
                candidates.extend(context.pages)
        unique: list[Any] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique

    @staticmethod
    def _normalized(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    def _find_editable(self, label: str):
        """Resolve a labelled editable field without strict-mode failures.

        Label search can over-match (Google's page has six aria-labelled
        elements containing "Search").  Collect visible enabled editable
        candidates, narrow by label association, and only fail when the set
        is still ambiguous -- mirroring ``_find_control`` semantics.
        """
        page = self._ensure_page()
        wanted = self._normalized(label)
        query = "input:visible, textarea:visible, [role=combobox]:visible, [contenteditable=true]"
        candidates: list = []
        try:
            found = page.locator(query)
            for index in range(found.count()):
                candidates.append(found.nth(index))
        except Exception as exc:
            logger.debug(f"Could not enumerate editable candidates: {exc}")
        if not candidates:
            raise ActionNotDispatched(f"No visible editable field matching {label!r}")
        usable = []
        for candidate in candidates:
            try:
                if not candidate.is_visible() or not candidate.is_enabled():
                    continue
                editable = candidate.evaluate(
                    """el => {
                        if (el.tagName === 'TEXTAREA') return true;
                        if (el.getAttribute('contenteditable') === 'true') return true;
                        if (el.tagName !== 'INPUT') return false;
                        const t = (el.type || 'text').toLowerCase();
                        return ['text', 'search', 'email', 'password', 'url', 'tel', 'number', ''].includes(t);
                    }"""
                )
                if not editable:
                    continue
                identity = candidate.evaluate(
                    """el => [
                        ...((el.labels && Array.from(el.labels).map(i => i.innerText)) || []),
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('name') || '',
                        el.getAttribute('id') || '',
                        el.getAttribute('placeholder') || ''
                    ].join(' ')"""
                )
                if wanted and wanted not in self._normalized(identity):
                    continue
                usable.append(candidate)
            except Exception as exc:
                logger.debug(f"Could not inspect editable candidate: {exc}")
                continue
        if not usable:
            raise ActionNotDispatched(f"No visible enabled editable field matching {label!r}")
        if len(usable) > 1:
            # Prefer a real text input over combobox/contenteditable when the
            # label matched several control kinds.
            inputs = [c for c in usable if c.evaluate("el => el.tagName") in ("INPUT", "TEXTAREA")]
            if len(inputs) == 1:
                usable = inputs
        if len(usable) != 1:
            raise ActionNotDispatched(
                f"Ambiguous editable field {label!r}: matched {len(usable)} elements"
            )
        return usable[0]

    def _find_control(self, text: str, near: str = ""):
        page = self._ensure_page()
        candidates = page.get_by_role("button", name=text, exact=False)
        if candidates.count() == 0:
            candidates = page.get_by_role("link", name=text, exact=False)
        if candidates.count() == 0:
            candidates = page.get_by_text(text, exact=False)
        usable = []
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    usable.append(candidate)
            except Exception as exc:
                logger.debug(f"Could not inspect browser control candidate: {exc}")
                continue
        if not usable:
            raise ActionNotDispatched(f"No visible enabled control matching {text!r}")
        if near and len(usable) > 1:
            wanted = self._normalized(near)
            narrowed = []
            for candidate in usable:
                surrounding = candidate.evaluate(
                    """el => {
                        let node = el;
                        for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                            const text = (node.innerText || '').trim();
                            if (text.length > 30 && text.length < 5000) return text;
                        }
                        return '';
                    }"""
                )
                if wanted in self._normalized(surrounding):
                    narrowed.append(candidate)
            usable = narrowed
        if len(usable) != 1:
            raise ActionNotDispatched(
                f"Ambiguous control {text!r}: matched {len(usable)} visible enabled elements"
            )
        return usable[0]

    def _select(self, label: str, option: str) -> str:
        page = self._ensure_page()
        selects = page.locator("select:visible")
        wanted = self._normalized(option)
        best = None
        wanted_label = self._normalized(label)
        for index in range(selects.count()):
            select = selects.nth(index)
            if wanted_label:
                try:
                    label_text = str(select.evaluate(
                        """el => {
                            const labels = el.labels ? Array.from(el.labels) : [];
                            return [
                                ...labels.map(item => item.innerText || ''),
                                el.getAttribute('aria-label') || '',
                                el.getAttribute('name') || '',
                                el.getAttribute('id') || ''
                            ].join(' ');
                        }"""
                    ))
                except (AttributeError, TypeError):
                    label_text = " ".join(
                        str(select.get_attribute(name) or "")
                        for name in ("aria-label", "name", "id")
                    )
                if wanted_label not in self._normalized(label_text):
                    continue
            options = [text.strip() for text in select.locator("option").all_text_contents()]
            normalized = [self._normalized(text) for text in options]
            if any(wanted == item or wanted in item or item in wanted for item in normalized if item):
                best = (select, options, normalized)
                break
        if best is None:
            raise ActionNotDispatched(
                f"No visible dropdown contains option {option!r} ({label!r})"
            )
        select, options, normalized = best
        index = next(
            i for i, item in enumerate(normalized)
            if wanted == item or wanted in item or item in wanted
        )
        select.select_option(label=options[index], timeout=int(self.step_timeout * 1000))
        selected = select.locator("option:checked").inner_text().strip()
        if self._normalized(selected) != normalized[index]:
            raise RuntimeError(f"Dropdown did not retain selection {options[index]!r}")
        return selected

    @staticmethod
    def _page_fingerprint(page: Any) -> tuple[str, str, str]:
        """Cheap semantic snapshot used to detect no-op browser actions."""
        try:
            body = page.locator("body").inner_text(timeout=2_000)[:20_000]
            interactive = page.locator(
                "button, a, input, textarea, select, [role=button], [role=link]"
            ).evaluate_all(
                """els => els.slice(0, 200).map(el => [
                    el.innerText || '', el.getAttribute('aria-expanded') || '',
                    el.getAttribute('aria-selected') || '', el.value || ''
                ].join('|')).join('\\n')"""
            )
        except Exception:
            body = ""
            interactive = ""
        return page.url, page.title(), f"{body}\n{interactive}"

    @staticmethod
    def _host_matches(candidate: str, allowed: str) -> bool:
        return candidate == allowed or candidate.endswith(f".{allowed}")

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host or parsed.scheme not in {"http", "https"}:
            return ""
        default_port = 443 if parsed.scheme == "https" else 80
        port = parsed.port or default_port
        return f"{parsed.scheme}://{host}:{port}"

    def _assert_allowed_page_host(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if not host or not self._allowed_hosts:
            return
        if not any(self._host_matches(host, allowed) for allowed in self._allowed_hosts):
            raise PermissionError(
                f"Browser left plan-declared hosts at {host!r}; continuation refused"
            )
        if host in {"localhost", "127.0.0.1", "::1"}:
            origin = self._origin(url)
            if origin not in self._allowed_origins:
                raise PermissionError(
                    f"Browser left plan-declared loopback origins at {origin!r}; continuation refused"
                )

    def _verify_expectation(
        self,
        expect: dict[str, Any],
        *,
        before_url: str = "",
    ) -> None:
        if not expect:
            return
        page = self._ensure_page()
        timeout_ms = int(self.step_timeout * 1000)
        if text := str(expect.get("text", "")).strip():
            page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout_ms)
        if text_absent := str(expect.get("text_absent", "")).strip():
            page.get_by_text(text_absent, exact=False).first.wait_for(
                state="hidden", timeout=timeout_ms
            )
        if (
            fragment := str(expect.get("url_contains", "")).strip()
        ) and fragment not in page.url:
            raise RuntimeError(f"Current URL {page.url!r} does not contain {fragment!r}")
        if expect.get("url_changed") and page.url == before_url:
            raise RuntimeError("Expected browser URL to change, but it did not")

    def _execute_browser_step(self, step: PlanStep) -> dict[str, Any]:
        page = self._ensure_page()
        if page not in self._owned_pages:
            self._owned_pages.append(page)
        args = step.args
        timeout_ms = int(self.step_timeout * 1000)
        before = {"url": page.url, "title": page.title()}
        before_fingerprint = self._page_fingerprint(page)
        evidence: dict[str, Any]

        if step.action == "navigate":
            url = str(args["url"])
            if page.url.rstrip("/") != url.rstrip("/"):
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            evidence = {"url": page.url}
        elif step.action == "click":
            control = self._find_control(str(args["text"]), str(args.get("near", "")))
            dialog_spec = args.get("dialog")
            popup_spec = args.get("popup")
            if isinstance(dialog_spec, dict):
                dialog_state: dict[str, Any] = {}

                def handle_dialog(dialog: Any) -> None:
                    dialog_state.update({
                        "type": dialog.type,
                        "message": dialog.message,
                    })
                    expected_type = str(dialog_spec.get("type", ""))
                    expected_message = str(dialog_spec.get("message_contains", ""))
                    mismatch = (
                        dialog.type != expected_type
                        or (expected_message and expected_message not in dialog.message)
                    )
                    if mismatch:
                        dialog_state["error"] = "Dialog type or message did not match plan"
                        dialog.dismiss()
                    elif bool(dialog_spec.get("accept", True)):
                        dialog.accept(str(dialog_spec.get("prompt_text", "")))
                        dialog_state["accepted"] = True
                    else:
                        dialog.dismiss()
                        dialog_state["accepted"] = False

                page.once("dialog", handle_dialog)
                control.click(timeout=timeout_ms)
                deadline = time.monotonic() + self.step_timeout
                while not dialog_state and time.monotonic() < deadline:
                    page.wait_for_timeout(50)
                if not dialog_state:
                    raise UncertainStepOutcome("Click did not produce the declared dialog")
                if dialog_state.get("error"):
                    raise UncertainStepOutcome(str(dialog_state["error"]))
                evidence = {
                    "clicked": str(args["text"]),
                    "dialog": dialog_state,
                    "url": page.url,
                }
            elif isinstance(popup_spec, dict):
                with page.expect_popup(timeout=timeout_ms) as popup_info:
                    control.click(timeout=timeout_ms)
                popup = popup_info.value
                popup.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                declared_url = str(popup_spec["url"])
                if not popup.url.startswith(declared_url):
                    popup.close()
                    raise UncertainStepOutcome(
                        f"Popup URL {popup.url!r} did not match declared {declared_url!r}"
                    )
                self._owned_tab_counter += 1
                marker = f"desktop-agent:{self.execution_id}:{self._owned_tab_counter}"
                popup.evaluate("value => { window.name = value; }", marker)
                self._owned_pages.append(popup)
                title_fragment = str(popup_spec.get("title_contains", "")).lower()
                if title_fragment and title_fragment not in popup.title().lower():
                    popup.close()
                    self._owned_pages.remove(popup)
                    raise UncertainStepOutcome("Popup title did not match declared title")
                if bool(popup_spec.get("activate", True)):
                    popup.bring_to_front()
                    self._page = popup
                    page = popup
                active_page = self._page or page
                evidence = {
                    "clicked": str(args["text"]),
                    "popup_url": popup.url,
                    "popup_title": popup.title(),
                    "active_tab": marker if active_page is popup else str(active_page.evaluate("window.name")),
                }
            else:
                control.click(timeout=timeout_ms)
                evidence = {"clicked": str(args["text"]), "url": page.url}
        elif step.action == "select_option":
            selected = self._select(str(args.get("label", "")), str(args["option"]))
            evidence = {"selected": selected}
        elif step.action == "fill":
            label = str(args.get("label", "")).strip()
            locator = self._find_editable(label) if label else page.locator("input:visible, textarea:visible").first
            locator.fill(str(args["text"]), timeout=timeout_ms)
            if locator.input_value() != str(args["text"]):
                raise RuntimeError("Field value did not match requested text")
            evidence = {"filled": label or "focused field"}
        elif step.action == "press":
            page.keyboard.press(str(args.get("key", "Enter")))
            evidence = {"pressed": str(args.get("key", "Enter"))}
        elif step.action == "scroll":
            amount = max(1, min(int(args.get("amount_px", 600)), 5000))
            if str(args.get("direction", "down")).lower() == "up":
                amount = -amount
            page.mouse.wheel(0, amount)
            evidence = {"scrolled": amount}
        elif step.action == "drag_drop":
            source_text = str(args["source"])
            target_text = str(args["target"])
            source = self._find_control(source_text, str(args.get("source_near", "")))
            target = self._find_control(target_text, str(args.get("target_near", "")))
            source.drag_to(target, timeout=timeout_ms)
            evidence = {"dragged": source_text, "dropped_on": target_text}
        elif step.action == "wait_for":
            text = str(args.get("text", "")).strip()
            if not text:
                raise ValueError("wait_for requires text")
            seconds = max(0.1, min(float(args.get("timeout_seconds", self.step_timeout)), self.step_timeout))
            page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=int(seconds * 1000))
            evidence = {"visible": text}
        elif step.action == "extract_text":
            target = str(args["target"])
            locator = self._find_control(target, str(args.get("near", "")))
            attribute = str(args.get("attribute", "")).strip()
            value = (
                str(locator.get_attribute(attribute) or "")
                if attribute
                else str(locator.inner_text() or "").strip()
            )
            if not value:
                raise RuntimeError(f"Matched browser target {target!r} had no readable value")
            evidence = {
                "text": value,
                "target": target,
                "sensitive": bool(args.get("sensitive", False)),
            }
        elif step.action == "read_field":
            label = str(args["label"])
            locator = page.get_by_label(label, exact=False).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            value = str(locator.input_value()).strip()
            evidence = {
                "text": value,
                "field": label,
                "sensitive": bool(args.get("sensitive", False)),
            }
        elif step.action == "check":
            label = str(args["label"])
            desired = bool(args.get("checked", True))
            locator = page.get_by_label(label, exact=False).first
            if desired:
                locator.check(timeout=timeout_ms)
            else:
                locator.uncheck(timeout=timeout_ms)
            if bool(locator.is_checked()) != desired:
                raise RuntimeError(f"Checkbox {label!r} did not retain requested state")
            evidence = {"field": label, "checked": desired}
        elif step.action == "upload_file":
            label = str(args["label"])
            path = Path(str(args["path"])).expanduser()
            if not path.is_absolute() or not path.is_file():
                raise ValueError("upload_file requires an existing absolute file")
            locator = page.get_by_label(label, exact=False).first
            locator.set_input_files(str(path), timeout=timeout_ms)
            evidence = {"uploaded": str(path), "bytes": path.stat().st_size}
        elif step.action == "open_tab":
            self._owned_tab_counter += 1
            try:
                page = page.context.new_page()
            except Exception as context_error:
                browser = page.context.browser
                if browser is None:
                    raise RuntimeError(
                        f"Browser context cannot create another owned tab: {context_error}"
                    ) from context_error
                if hasattr(browser, "new_context"):
                    context = browser.new_context()
                    page = context.new_page()
                else:
                    page = browser.new_page()
            self._owned_pages.append(page)
            marker = f"desktop-agent:{self.execution_id}:{self._owned_tab_counter}"
            page.evaluate("value => { window.name = value; }", marker)
            page.goto(str(args["url"]), wait_until="domcontentloaded", timeout=timeout_ms)
            page.bring_to_front()
            self._page = page
            evidence = {
                "url": page.url,
                "opened_tab": marker,
                "active_tab": marker,
            }
        elif step.action == "switch_tab":
            title_fragment = str(args.get("title_contains", "")).lower()
            url_fragment = str(args.get("url_contains", "")).lower()
            prefix = f"desktop-agent:{self.execution_id}"
            matched = None
            for candidate in self._owned_page_candidates(page):
                try:
                    marker = str(candidate.evaluate("window.name"))
                    if not marker.startswith(prefix):
                        continue
                    if title_fragment and title_fragment not in candidate.title().lower():
                        continue
                    if url_fragment and url_fragment not in candidate.url.lower():
                        continue
                    matched = candidate
                    break
                except Exception as exc:
                    logger.debug(f"Could not inspect owned tab candidate: {exc}")
            if matched is None:
                raise RuntimeError("No owned browser tab matched the requested title or URL")
            matched.bring_to_front()
            self._page = matched
            page = matched
            evidence = {
                "url": page.url,
                "title": page.title(),
                "active_tab": str(page.evaluate("window.name")),
            }
        elif step.action == "close_tab":
            prefix = f"desktop-agent:{self.execution_id}"
            owned = []
            for candidate in self._owned_page_candidates(page):
                try:
                    if str(candidate.evaluate("window.name")).startswith(prefix):
                        owned.append(candidate)
                except Exception as exc:
                    logger.debug(f"Could not inspect tab ownership before close: {exc}")
            if page not in owned:
                raise PermissionError("Current browser tab is not owned by this execution")
            if len(owned) < 2:
                raise PermissionError("Refusing to close the execution's last owned tab")
            closed_page = page
            page.close()
            self._owned_pages = [
                candidate
                for candidate in self._owned_pages
                if candidate is not closed_page and not candidate.is_closed()
            ]
            remaining = [
                candidate
                for candidate in owned
                if candidate is not closed_page and not candidate.is_closed()
            ]
            if not remaining:
                self._page = None
                raise UncertainStepOutcome(
                    "Closing the tab also closed every remaining owned tab"
                )
            page = remaining[0]
            page.bring_to_front()
            self._page = page
            evidence = {
                "closed_owned_tab": True,
                "url": page.url,
                "active_tab": str(page.evaluate("window.name")),
            }
        elif step.action == "download":
            directory = Path(str(args.get("directory", Path.home() / "Downloads"))).expanduser()
            if not directory.is_absolute():
                raise ValueError("Download directory must be absolute")
            directory.mkdir(parents=True, exist_ok=True)
            baseline = {
                path.name: (path.stat().st_mtime_ns, path.stat().st_size)
                for path in directory.iterdir()
                if path.is_file()
            }
            session = page.context.new_cdp_session(page)
            session.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(directory),
                    "eventsEnabled": True,
                },
            )
            control = self._find_control(str(args["text"]), str(args.get("near", "")))
            source_domain = str(args.get("source_domain", "")).strip().lower()
            href = urljoin(page.url, str(control.get_attribute("href") or ""))
            href_host = (urlparse(href).hostname or "").lower()
            if source_domain and not self._host_matches(href_host, source_domain):
                raise ActionNotDispatched(
                    f"Download control domain {href_host!r} did not match {source_domain!r}"
                )
            control.click(timeout=timeout_ms)

            deadline = time.monotonic() + self.step_timeout
            changed: Path | None = None
            while time.monotonic() < deadline:
                for path in directory.iterdir():
                    if not path.is_file():
                        continue
                    state = (path.stat().st_mtime_ns, path.stat().st_size)
                    if state != baseline.get(path.name) and state[1] > 0:
                        changed = path
                        break
                if changed is not None:
                    break
                time.sleep(0.2)
            if changed is None:
                raise RuntimeError("Download click produced no new or growing file")

            if bool(args.get("wait_for_completion", False)):
                from core.download_watcher import watch_download

                watched = watch_download(
                    directory,
                    before={directory / name for name in baseline},
                    timeout=self.total_timeout,
                    stall_timeout=self.step_timeout,
                    poll_interval=0.5,
                    min_size=1,
                    filename_hint=str(args.get("filename_contains", "")),
                )
                if watched.path is None:
                    raise RuntimeError(
                        f"Download {watched.status}: no stable completed file after click"
                    )
                changed = watched.path
            filename_fragment = str(args.get("filename_contains", "")).strip().lower()
            if filename_fragment and filename_fragment not in changed.name.lower():
                raise RuntimeError(
                    f"Downloaded filename {changed.name!r} did not contain {filename_fragment!r}"
                )
            expected_extensions = tuple(
                str(ext).lower() for ext in args.get("extensions", [])
            )
            effective_name = changed.name.lower().removesuffix(".crdownload").removesuffix(".part")
            if expected_extensions and not effective_name.endswith(expected_extensions):
                raise RuntimeError(
                    f"Downloaded filename {changed.name!r} did not match expected extensions"
                )
            evidence = {
                "download": str(changed),
                "bytes": changed.stat().st_size,
                "source_domain": href_host,
                "status": "complete" if not changed.name.lower().endswith((".crdownload", ".part")) else "started",
            }
        else:
            raise ValueError(f"Unsupported browser action {step.action!r}")

        self._verify_expectation(step.expect, before_url=before["url"])
        self._assert_allowed_page_host(page.url)
        if (
            step.action in {"click", "press", "drag_drop"}
            and not step.expect
            and not step.args.get("dialog")
            and not step.args.get("popup")
        ):
            page.wait_for_timeout(250)
            if self._page_fingerprint(page) == before_fingerprint:
                raise RuntimeError(
                    f"{step.action} produced no observable page change; refusing blind continuation"
                )
        evidence["before"] = before
        evidence["after_url"] = page.url
        return evidence

    @staticmethod
    def _native_snapshot(app_name: str) -> tuple[str, ...]:
        """Capture accessible native UI state for no-op detection and evidence."""
        from core.atspi_navigator import _name, _role, _walk, wait_for_app

        app = wait_for_app([app_name], timeout=1.0)
        if app is None:
            return ()
        lines: list[str] = []
        for node in _walk(app):
            role = _role(node)
            name = _name(node)
            content = ""
            if role in {"entry", "text", "password text"}:
                try:
                    dynamic_node: Any = node
                    text = dynamic_node.queryText()
                    content = text.getText(0, text.characterCount).strip()
                except Exception:
                    content = ""
            if name or content:
                lines.append(f"{role}|{name}|{content}"[:500])
            if len(lines) >= 250:
                break
        return tuple(lines)

    def _await_native_change(
        self,
        app_name: str,
        before: tuple[str, ...],
    ) -> tuple[str, ...]:
        after = self._native_snapshot(app_name)
        deadline = time.monotonic() + min(1.5, self.step_timeout)
        while before == after and time.monotonic() < deadline:
            time.sleep(0.05)
            after = self._native_snapshot(app_name)
        if before == after:
            raise UncertainStepOutcome(
                "Native action was issued but produced no observable accessibility change"
            )
        return after

    def _verify_native_expectation(
        self,
        app_name: str,
        expect: dict[str, Any],
    ) -> dict[str, Any]:
        from core.atspi_navigator import wait_for_app, wait_for_node

        timeout = max(
            0.2,
            min(float(expect.get("timeout_seconds", self.step_timeout)), self.step_timeout),
        )
        if (
            expect.get("app_running")
            and wait_for_app([app_name], timeout=timeout) is None
        ):
            raise RuntimeError(f"Application {app_name!r} did not become accessible")
        if text := str(expect.get("ui_text", "")).strip():
            _app, node = wait_for_node(
                [app_name],
                name_contains=text,
                timeout=timeout,
            )
            if node is None:
                raise RuntimeError(
                    f"Expected UI text/control {text!r} did not appear in {app_name!r}"
                )
        return {"app": app_name, "accessible_nodes": len(self._native_snapshot(app_name))}

    def _execute_native_step(self, step: PlanStep) -> dict[str, Any]:
        from core.atspi_navigator import (
            _try_focus_app,
            click_by_intent,
            read_by_intent,
            type_by_intent,
            wait_for_app,
            wait_for_node,
        )

        args = step.args
        app_name = str(args["app"]).strip()
        before = self._native_snapshot(app_name)

        if step.action == "launch_app":
            from tasks.open_system_app import execute as open_app

            if not open_app({"app_name": app_name}, {}):
                raise RuntimeError(f"Could not launch {app_name!r}")
            if wait_for_app([app_name], timeout=self.step_timeout) is None:
                raise RuntimeError(f"Launched {app_name!r} but it was not accessible")
            evidence: dict[str, Any] = {"launched": app_name}
        elif step.action == "focus_app":
            app = wait_for_app([app_name], timeout=self.step_timeout)
            if app is None or not _try_focus_app(app):
                raise RuntimeError(f"Could not focus {app_name!r}")
            evidence = {"focused": app_name}
        elif step.action == "click_ui":
            text = str(args["text"])
            result = click_by_intent([app_name], text, app_timeout=self.step_timeout)
            if not result.ok:
                raise ActionNotDispatched(result.reason or f"Could not click {text!r}")
            evidence = {
                "clicked": text,
                "candidates_considered": result.candidates_considered,
            }
        elif step.action == "type_ui":
            field_name = str(args["field"])
            text = str(args["text"])
            result = type_by_intent(
                [app_name],
                field_name,
                text,
                app_timeout=self.step_timeout,
            )
            if not result.ok:
                raise ActionNotDispatched(result.reason or f"Could not type into {field_name!r}")
            evidence = {
                "typed_characters": len(text),
                "field": field_name,
                "candidates_considered": result.candidates_considered,
            }
        elif step.action == "copy_ui":
            target = str(args["target"])
            result = read_by_intent(
                [app_name], target, app_timeout=self.step_timeout
            )
            if not result.ok:
                raise RuntimeError(result.reason or f"Could not read {target!r} for copy")
            from core.system_utils import clipboard_get, clipboard_set

            if not clipboard_set(result.value) or clipboard_get() != result.value:
                raise RuntimeError("Desktop clipboard did not retain copied text")
            evidence = {
                "text": result.value,
                "target": target,
                "method": "accessibility_to_desktop_clipboard",
                "sensitive": bool(args.get("sensitive", False)),
                "candidates_considered": result.candidates_considered,
            }
        elif step.action == "paste_ui":
            field_name = str(args["field"])
            text = str(args["text"])
            from core.system_utils import clipboard_get, clipboard_set

            if not clipboard_set(text) or clipboard_get() != text:
                raise RuntimeError("Desktop clipboard did not retain paste text")
            # On this Wayland session, some native apps ignore injected
            # Ctrl+V even after focus confirmation.  Set the exact accessible
            # target from the verified clipboard value and retain the method
            # in evidence, so completion is never inferred from key dispatch.
            result = type_by_intent(
                [app_name], field_name, clipboard_get(), app_timeout=self.step_timeout
            )
            if not result.ok:
                raise ActionNotDispatched(result.reason or f"Could not paste into {field_name!r}")
            observed = ""
            try:
                dynamic_node: Any = result.node
                text_interface = dynamic_node.queryText()
                observed = text_interface.getText(0, text_interface.characterCount)
            except Exception:
                observed = ""
            if observed != text:
                raise RuntimeError("Accessible destination did not retain paste text")
            evidence = {
                "pasted_characters": len(text),
                "field": field_name,
                "method": "accessibility_clipboard_recovery",
                "candidates_considered": result.candidates_considered,
            }
        elif step.action == "hotkey_ui":
            app = wait_for_app([app_name], timeout=self.step_timeout)
            if app is None or not _try_focus_app(app):
                raise ActionNotDispatched(f"Could not focus {app_name!r} before hotkey")
            raw_keys = args["keys"]
            keys = raw_keys if isinstance(raw_keys, list) else str(raw_keys).split()
            if not keys or not all(isinstance(key, str) and key.strip() for key in keys):
                raise ValueError("hotkey_ui keys must be non-empty strings")
            from core.gui_controller import GUIController

            GUIController().hotkey(*keys)
            evidence = {"hotkey": "+".join(keys)}
        elif step.action == "wait_ui":
            text = str(args["text"])
            timeout = max(
                0.2,
                min(float(args.get("timeout_seconds", self.step_timeout)), self.step_timeout),
            )
            _app, node = wait_for_node([app_name], name_contains=text, timeout=timeout)
            if node is None:
                raise RuntimeError(f"UI text/control {text!r} did not appear in {app_name!r}")
            evidence = {"visible": text}
        elif step.action == "read_ui":
            target = str(args["target"])
            result = read_by_intent(
                [app_name], target, app_timeout=self.step_timeout
            )
            if not result.ok:
                raise RuntimeError(result.reason or f"Could not read {target!r}")
            evidence = {
                "text": result.value,
                "target": target,
                "sensitive": bool(args.get("sensitive", False)),
                "candidates_considered": result.candidates_considered,
            }
        else:
            raise ValueError(f"Unsupported native action {step.action!r}")

        if step.expect:
            evidence.update(self._verify_native_expectation(app_name, step.expect))
        if step.action in {"click_ui", "type_ui", "hotkey_ui"} and not step.expect:
            try:
                after = self._await_native_change(app_name, before)
            except UncertainStepOutcome as exc:
                raise UncertainStepOutcome(
                    f"{step.action} was issued but produced no observable accessibility change"
                ) from exc
        else:
            after = self._native_snapshot(app_name)
        evidence["before_nodes"] = len(before)
        evidence["after_nodes"] = len(after)
        return evidence

    @staticmethod
    def _visual_change_ratio(before_path: str, after_path: str) -> float:
        import cv2

        before = cv2.imread(before_path)
        after = cv2.imread(after_path)
        if before is None or after is None or before.shape != after.shape:
            raise RuntimeError("Could not compare visual action screenshots")
        difference = cv2.absdiff(before, after)
        changed = cv2.cvtColor(difference, cv2.COLOR_BGR2GRAY) > 12
        return float(changed.mean())

    def _locate_visual_target(
        self,
        screenshot: str,
        target: str,
        *,
        minimum_confidence: float,
    ) -> Any:
        """Find one target, retrying once before any UI input is dispatched."""
        from core.visual_locator import VisualLocator

        if self._visual_locator is None:
            self._visual_locator = VisualLocator(timeout=min(self.step_timeout, 15.0))
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return self._visual_locator.locate(
                    screenshot,
                    target,
                    minimum_confidence=minimum_confidence,
                )
            except (RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    logger.debug(
                        f"Visual localization rejected for {target!r}: {exc}; retrying once"
                    )
        assert last_error is not None
        raise last_error

    def _execute_visual_step(self, step: PlanStep) -> dict[str, Any]:
        from core.atspi_navigator import _try_focus_app, click_by_intent, wait_for_app
        from core.gui_controller import GUIController
        from core.logger import LOGS_DIR, take_screenshot

        app_name = str(step.args["app"]).strip()
        target = str(step.args["target"]).strip()
        app = wait_for_app([app_name], timeout=self.step_timeout)
        if app is None or not _try_focus_app(app):
            raise RuntimeError(f"Could not focus {app_name!r} before visual localization")

        browser_viewport = self._page is not None
        if browser_viewport:
            before_path = str(
                LOGS_DIR / f"structured_visual_{self.execution_id}_before_viewport.png"
            )
            self._page.screenshot(path=before_path)
        else:
            before_path = take_screenshot(
                name=f"structured_visual_{self.execution_id}_before"
            )
        method = ""
        confidence: float | None = None
        bounds: dict[str, int] | None = None

        accessible = click_by_intent([app_name], target, app_timeout=1.0)
        if accessible.ok:
            method = "atspi"
        elif self._page is not None:
            try:
                control = self._find_control(target)
                control.click(timeout=int(self.step_timeout * 1000))
                method = "browser_dom"
            except Exception as exc:
                logger.debug(f"DOM fallback unavailable for visual target {target!r}: {exc}")

        if not method:
            minimum_confidence = float(step.args.get("minimum_confidence", 0.85))
            located = self._locate_visual_target(
                before_path,
                target,
                minimum_confidence=minimum_confidence,
            )
            located = self._visual_locator.refine_high_contrast_target(
                before_path, located
            )
            x, y = located.center
            if browser_viewport:
                # Page screenshots use viewport coordinates.  This sends the
                # same browser input a human click would generate, without
                # mixing viewport coordinates with desktop/window chrome.
                self._page.mouse.click(x, y)
                method = "vision_browser_viewport"
            else:
                GUIController().click(x, y)
                method = "vision"
            confidence = located.confidence
            bounds = {
                "x": located.x,
                "y": located.y,
                "width": located.width,
                "height": located.height,
            }

        time.sleep(0.5)
        if browser_viewport:
            after_path = str(
                LOGS_DIR / f"structured_visual_{self.execution_id}_after_viewport.png"
            )
            self._page.screenshot(path=after_path)
        else:
            after_path = take_screenshot(
                name=f"structured_visual_{self.execution_id}_after"
            )
        change_ratio = self._visual_change_ratio(before_path, after_path)
        required_change = float(step.expect.get("minimum_change_ratio", 0.001))
        if step.expect.get("visual_change", True) and change_ratio < required_change:
            raise UncertainStepOutcome(
                f"Visual click occurred but produced insufficient screen change ({change_ratio:.4f})"
            )
        if text := str(step.expect.get("ui_text", "")).strip():
            self._verify_native_expectation(app_name, {"ui_text": text})
        return {
            "method": method,
            "target": target,
            "confidence": confidence,
            "bounds": bounds,
            "before_screenshot": before_path,
            "after_screenshot": after_path,
            "visual_change_ratio": change_ratio,
        }

    def _execute_visual_drag_step(self, step: PlanStep) -> dict[str, Any]:
        """Drag between two independently confidence-gated visual targets."""
        from core.atspi_navigator import _try_focus_app, wait_for_app
        from core.gui_controller import GUIController
        from core.logger import LOGS_DIR, take_screenshot

        app_name = str(step.args["app"]).strip()
        browser_viewport = self._page is not None
        if not browser_viewport:
            app = wait_for_app([app_name], timeout=self.step_timeout)
            if app is None or not _try_focus_app(app):
                raise ActionNotDispatched(
                    f"Could not focus {app_name!r} before visual drag localization"
                )

        if browser_viewport:
            before_path = str(
                LOGS_DIR / f"structured_visual_drag_{self.execution_id}_before_viewport.png"
            )
            self._page.screenshot(path=before_path)
        else:
            before_path = take_screenshot(
                name=f"structured_visual_drag_{self.execution_id}_before"
            )
        minimum_confidence = float(step.args.get("minimum_confidence", 0.9))
        source = self._locate_visual_target(
            before_path,
            str(step.args["source"]),
            minimum_confidence=minimum_confidence,
        )
        target = self._locate_visual_target(
            before_path,
            str(step.args["target"]),
            minimum_confidence=minimum_confidence,
        )
        source = self._visual_locator.refine_high_contrast_target(before_path, source)
        target = self._visual_locator.refine_high_contrast_target(before_path, target)
        duration = float(step.args.get("duration_seconds", 0.8))
        if browser_viewport:
            # Page screenshots and Playwright mouse coordinates both use the
            # viewport.  Do not send viewport coordinates to the desktop.
            self._page.mouse.move(*source.center)
            self._page.mouse.down()
            self._page.mouse.move(
                *target.center,
                steps=max(2, round(duration * 20)),
            )
            self._page.mouse.up()
            method = "vision_browser_viewport"
        else:
            gui = GUIController()
            gui.move_to(*source.center)
            gui.drag_to(*target.center, duration=duration)
            method = "vision"

        time.sleep(0.5)
        if browser_viewport:
            after_path = str(
                LOGS_DIR / f"structured_visual_drag_{self.execution_id}_after_viewport.png"
            )
            self._page.screenshot(path=after_path)
        else:
            after_path = take_screenshot(
                name=f"structured_visual_drag_{self.execution_id}_after"
            )
        change_ratio = self._visual_change_ratio(before_path, after_path)
        required_change = float(step.expect.get("minimum_change_ratio", 0.001))
        if change_ratio < required_change:
            raise UncertainStepOutcome(
                f"Visual drag occurred but produced insufficient screen change ({change_ratio:.4f})"
            )
        return {
            "method": method,
            "source": str(step.args["source"]),
            "target": str(step.args["target"]),
            "source_bounds": {
                "x": source.x,
                "y": source.y,
                "width": source.width,
                "height": source.height,
            },
            "target_bounds": {
                "x": target.x,
                "y": target.y,
                "width": target.width,
                "height": target.height,
            },
            "confidence": min(source.confidence, target.confidence),
            "before_screenshot": before_path,
            "after_screenshot": after_path,
            "visual_change_ratio": change_ratio,
        }

    @staticmethod
    def _verify_file(args: dict[str, Any]) -> dict[str, Any]:
        directory = Path(str(args.get("directory", ""))).expanduser()
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("verify_file requires an existing absolute directory")
        extensions = tuple(str(ext).lower() for ext in args.get("extensions", []))
        min_bytes = max(0, int(args.get("min_bytes", 1)))
        matches = [
            path for path in directory.iterdir()
            if path.is_file()
            and (not extensions or path.name.lower().endswith(extensions))
            and path.stat().st_size >= min_bytes
        ]
        if not matches:
            raise RuntimeError(f"No matching file evidence found in {directory}")
        newest = max(matches, key=lambda path: path.stat().st_mtime)
        return {"file": str(newest), "bytes": newest.stat().st_size}

    def _execute_task(self, args: dict[str, Any]) -> dict[str, Any]:
        from core.automation_service import ExecutionRequest, automation_service
        from tasks import get_task_spec

        name = str(args.get("name", ""))
        if name in _FORBIDDEN_TASK_ACTIONS:
            raise PermissionError(f"Structured plans cannot invoke task {name!r}")
        spec = get_task_spec(name)
        if spec is None:
            raise ValueError(f"Unknown task {name!r}")
        params = args.get("params", {})
        if not isinstance(params, dict):
            raise TypeError(f"Task {name!r} params must be an object")
        if requires_approval(name, params) and not self.approve_all:
            raise PermissionError(f"Task {name!r} requires explicit plan approval")
        result = automation_service.execute(
            ExecutionRequest(
                task=name,
                params=params,
                approved=self.approve_all,
                source="structured_plan",
                execution_id=f"{self.execution_id}:task:{name}",
            )
        )
        if not result.success:
            raise RuntimeError(result.error or f"Task {name!r} returned failure")
        return {
            "task": name,
            **result.data,
            "task_evidence": result.evidence,
            "warnings": result.warnings,
        }

    def _execute_capability_step(self, step: PlanStep) -> dict[str, Any]:
        """Dispatch one universal capability through the merged runtime registry.

        Mirrors the adaptive loop's proven cap dispatch: fresh RuntimeState,
        contract-enforced registry execution, ActionOutcome evidence.  The
        registry carries approve_all so registered executors observe the same
        approval policy as every other structured step; requires_confirmation
        caps additionally raise belt-and-suspenders PermissionError here.
        """
        from core.task_runtime import RuntimeAction, RuntimeState

        cap_name = str(step.args.get("cap", "")).strip()
        if not cap_name:
            raise ActionNotDispatched("cap step requires a capability name")
        cap_args = step.args.get("args", {})
        if not isinstance(cap_args, dict):
            raise ActionNotDispatched("cap args must be an object")
        registry = self._universal_registry()
        capability = registry.get(cap_name)
        if capability is None:
            raise ActionNotDispatched(f"Unknown capability {cap_name!r}")
        contract = capability.contract
        if contract.requires_confirmation and not self.approve_all:
            raise PermissionError(
                f"Capability {cap_name!r} requires explicit plan approval"
            )
        outcome = registry.execute(
            RuntimeAction(cap_name, dict(cap_args)),
            RuntimeState(goal=self._goal_context),
        )
        if outcome.state == "failed":
            raise RuntimeError(outcome.error or f"Capability {cap_name!r} failed")
        if outcome.state == "blocked":
            raise PermissionError(outcome.error or f"Capability {cap_name!r} was blocked")
        if outcome.state != "succeeded":
            raise UncertainStepOutcome(
                outcome.error or f"Capability {cap_name!r} returned {outcome.state}"
            )
        data = outcome.data if isinstance(outcome.data, dict) else {"value": outcome.data}
        item: dict[str, Any] = {"cap": cap_name, **data}
        if outcome.warnings:
            item["warnings"] = outcome.warnings
        return item

    @staticmethod
    def _execute_local_step(step: PlanStep) -> dict[str, Any]:
        args = step.args
        if step.action == "wait":
            seconds = float(args.get("seconds", 1.0))
            time.sleep(seconds)
            return {"waited_seconds": seconds}
        if step.action == "assert_value":
            condition = {
                "value": args.get("value"),
                "operator": args.get("operator"),
                "expected": args.get("expected"),
            }
            if not _condition_matches(condition):
                raise RuntimeError(
                    f"Value assertion failed for operator {args.get('operator')!r}"
                )
            return {"asserted": True, "operator": args.get("operator")}

        value = args.get("value")
        operation = str(args["operation"])
        if operation == "strip":
            result: Any = str(value).strip()
        elif operation == "lower":
            result = str(value).lower()
        elif operation == "upper":
            result = str(value).upper()
        elif operation == "replace":
            result = str(value).replace(str(args["old"]), str(args.get("new", "")))
        elif operation == "prefix":
            result = f"{args.get('new', '')}{value}"
        elif operation == "suffix":
            result = f"{value}{args.get('new', '')}"
        elif operation == "split":
            parts = str(value).split(str(args.get("separator", " ")))
            if "index" in args:
                try:
                    result = parts[int(args["index"])]
                except IndexError as exc:
                    raise ValueError("Transform split index is out of range") from exc
            else:
                result = parts
        elif operation == "join":
            if not isinstance(value, list):
                raise TypeError("Transform join requires a list value")
            result = str(args.get("separator", " ")).join(str(item) for item in value)
        elif operation == "length":
            if value is None:
                result = 0
            else:
                try:
                    result = len(value)
                except TypeError:
                    result = len(str(value))
        else:
            raise ValueError(f"Unsupported transformation {operation!r}")
        return {
            "value": result,
            "operation": operation,
            "sensitive": bool(args.get("sensitive", False)),
        }

    def _repair_observation(self, step: PlanStep) -> dict[str, Any]:
        """Capture fresh semantic state without exposing input field values."""
        if bool(step.args.get("sensitive", False)):
            return {"observation_withheld": True}
        if step.action in _BROWSER_ACTIONS and self._page is not None:
            try:
                controls = self._page.locator(
                    "button, a, select, [role=button], [role=link], [role=combobox]"
                ).evaluate_all(
                    """els => els.filter(el => {
                        const style = getComputedStyle(el);
                        return style.visibility !== 'hidden' && style.display !== 'none';
                    }).slice(0, 80).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || '',
                        name: (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 160)
                    }))"""
                )
                return {
                    "kind": "browser_semantics",
                    "url": self._page.url,
                    "title": self._page.title(),
                    "controls": controls,
                }
            except Exception as exc:
                return {"kind": "browser_semantics", "observation_error": str(exc)}
        if step.action in _NATIVE_ACTIONS:
            app = str(step.args.get("app", ""))
            try:
                snapshot = self._native_snapshot(app)
                return {
                    "kind": "native_accessibility",
                    "app": app,
                    "nodes": [str(item)[:160] for item in snapshot[:80]],
                }
            except Exception as exc:
                return {"kind": "native_accessibility", "observation_error": str(exc)}
        return {"kind": "structured", "action": step.action}

    def _dispatch_step(self, step: PlanStep) -> dict[str, Any]:
        if step.action in _BROWSER_ACTIONS:
            return self._execute_browser_step(step)
        if step.action in _NATIVE_ACTIONS:
            return self._execute_native_step(step)
        if step.action == "visual_drag":
            return self._execute_visual_drag_step(step)
        if step.action in _VISUAL_ACTIONS:
            return self._execute_visual_step(step)
        if step.action in _LOCAL_ACTIONS:
            return self._execute_local_step(step)
        if step.action == "verify_file":
            return self._verify_file(step.args)
        if step.action == "task":
            return self._execute_task(step.args)
        if step.action == "cap":
            return self._execute_capability_step(step)
        raise ValueError(f"Unsupported action {step.action!r}")

    def _universal_registry(self):
        """Lazily build the merged runtime registry (universal + structured + tasks).

        Cached on the executor so one plan reuses browser ownership and
        approvals across cap steps; mirrors the adaptive loop's dispatch.
        """
        if self._universal_registry_instance is None:
            from core.runtime_adapters import build_runtime_registry

            self._universal_registry_instance = build_runtime_registry(
                executor=self,
                page=self._page,
                goal=self._goal_context,
                approve_all=self.approve_all,
            )
        return self._universal_registry_instance

    def attach_page(self, page: Any) -> None:
        """Reuse an externally managed Playwright page without owning its lifecycle."""
        self._page = page
        if page is not None and page not in self._owned_pages:
            self._owned_pages.append(page)

    def close(self) -> None:
        """Release browser handles created by this executor."""
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception as exc:
                logger.debug(f"Structured browser disconnect failed: {exc}")
            finally:
                self._pw = None

    @staticmethod
    def _step_side_effect(step: PlanStep) -> SideEffect:
        if step.action in {
            "wait", "wait_for", "extract_text", "read_field", "read_ui",
            "verify_file", "transform_text", "assert_value",
        }:
            return "read"
        if step.action == "download":
            return "local_write"
        if step.action == "cap":
            contract = _universal_contract(str(step.args.get("cap", "")))
            if contract is not None:
                return contract.side_effect
            return "read"
        return "external"

    @staticmethod
    def _one_step_plan(step: PlanStep, summary: str = "Adaptive structured step") -> StructuredPlan:
        return validate_plan({
            "summary": summary,
            "steps": [{
                "action": step.action,
                "args": step.args,
                "expect": step.expect,
                **({"when": step.when} if step.when else {}),
            }],
        })

    def _approve_one_step(
        self,
        plan: StructuredPlan,
        approval_callback: Callable[[StructuredPlan], bool] | None,
    ) -> bool:
        if self.approve_all or not self._step_is_consequential(plan.steps[0]):
            return True
        if approval_callback is None:
            return False
        fingerprint = plan_fingerprint(plan)
        return bool(approval_callback(plan)) and plan_fingerprint(plan) == fingerprint

    def _allow_one_step_hosts(self, step: PlanStep) -> None:
        raw_urls = []
        if step.action in {"navigate", "open_tab"}:
            raw_urls.append(str(step.args.get("url", "")))
        popup = step.args.get("popup")
        if isinstance(popup, dict):
            raw_urls.append(str(popup.get("url", "")))
        for url in raw_urls:
            host = (urlparse(url).hostname or "").lower()
            origin = self._origin(url)
            if host:
                self._allowed_hosts.add(host)
            if origin:
                self._allowed_origins.add(origin)

    def execute_step(
        self,
        step: PlanStep,
        *,
        goal: str = "",
        evidence: list[dict[str, Any]] | None = None,
        approval_callback: Callable[[StructuredPlan], bool] | None = None,
        page: Any = None,
    ) -> ActionOutcome:
        """Validate and execute one step while retaining this executor's live state."""
        prior_evidence = list(evidence or ())
        self._goal_context = goal or self._goal_context
        try:
            plan = self._one_step_plan(step)
            candidate = plan.steps[0]
            candidate = PlanStep(
                action=candidate.action,
                args=_resolve_step_references(candidate.args, prior_evidence),
                expect=_resolve_step_references(candidate.expect, prior_evidence),
                when=_resolve_step_references(candidate.when, prior_evidence),
            )
        except Exception as exc:
            return ActionOutcome(
                state="failed",
                error=f"Invalid structured step: {exc}",
                dispatch_status="not_attempted",
                side_effect="read",
            )

        if not _condition_matches(candidate.when):
            return ActionOutcome(
                state="skipped",
                data={"action": candidate.action, "condition_met": False},
                dispatch_status="not_attempted",
                side_effect="read",
            )
        approved_plan = StructuredPlan(plan.summary, (candidate,))
        if not self._approve_one_step(approved_plan, approval_callback):
            return ActionOutcome(
                state="blocked",
                error=f"Structured step ({candidate.action}) requires exact one-step approval",
                dispatch_status="not_attempted",
                side_effect="external",
            )

        if page is not None:
            self.attach_page(page)
        self._allow_one_step_hosts(candidate)
        repair_count = 0
        original = candidate
        while True:
            try:
                item = self._dispatch_step(candidate)
                evidence_item = {"action": candidate.action, **item}
                if repair_count:
                    evidence_item["repaired_from_action"] = original.action
                return ActionOutcome(
                    state="succeeded",
                    data=evidence_item,
                    evidence=[evidence_item],
                    dispatch_status="confirmed",
                    side_effect=self._step_side_effect(candidate),
                    metadata={"repairs": repair_count},
                )
            except Exception as exc:
                consequential = self._step_is_consequential(candidate)
                not_dispatched = isinstance(exc, ActionNotDispatched)
                uncertain = isinstance(exc, UncertainStepOutcome) or (
                    consequential and not not_dispatched
                )
                if uncertain:
                    return ActionOutcome(
                        state="uncertain",
                        error=str(exc),
                        uncertainty=[str(exc)],
                        dispatch_status="unknown",
                        side_effect="external",
                        metadata={"action": candidate.action, "repairs": repair_count},
                    )
                if self.repair_callback is None or repair_count >= _MAX_STEP_REPAIRS:
                    return ActionOutcome(
                        state="failed",
                        error=str(exc),
                        dispatch_status="not_dispatched" if not_dispatched else "dispatched",
                        side_effect=self._step_side_effect(candidate),
                        metadata={"action": candidate.action, "repairs": repair_count},
                    )
                repair_evidence = list(prior_evidence)
                repair_evidence.append({
                    "action": "live_observation",
                    **self._repair_observation(candidate),
                })
                try:
                    replacement = self.repair_callback(
                        goal,
                        approved_plan,
                        1,
                        candidate,
                        str(exc),
                        repair_evidence,
                    )
                    if replacement is None:
                        raise RuntimeError("No safe failed-step replacement was available")
                    replacement_plan = self._one_step_plan(
                        replacement, "Adaptive structured step repair"
                    )
                    replacement = replacement_plan.steps[0]
                    changed = replacement != candidate
                    callback = approval_callback or self.repair_approval_callback
                    if changed and not self.approve_all:
                        fingerprint = plan_fingerprint(replacement_plan)
                        if callback is None or not callback(replacement_plan):
                            raise PermissionError(
                                "Changed failed-step replacement was not explicitly approved"
                            )
                        if plan_fingerprint(replacement_plan) != fingerprint:
                            raise PermissionError("Replacement changed after approval")
                    elif self._step_is_consequential(replacement) and not self._approve_one_step(
                        replacement_plan, callback
                    ):
                        raise PermissionError(
                            "Consequential failed-step replacement was not explicitly approved"
                        )
                except Exception as repair_exc:
                    return ActionOutcome(
                        state="failed",
                        error=f"{exc}; failed-step repair unsuccessful: {repair_exc}",
                        dispatch_status="not_dispatched" if not_dispatched else "dispatched",
                        side_effect=self._step_side_effect(candidate),
                        metadata={"action": candidate.action, "repairs": repair_count},
                    )
                repair_count += 1
                candidate = replacement
                self._allow_one_step_hosts(candidate)

    @staticmethod
    def _step_is_consequential(step: PlanStep) -> bool:
        if step.action in {
            "click", "select_option", "fill", "press", "check", "upload_file",
            "download", "close_tab", "drag_drop", "click_ui", "type_ui", "hotkey_ui",
            "copy_ui", "paste_ui", "visual_click", "visual_drag",
        }:
            return True
        if step.action == "task":
            name = str(step.args.get("name", ""))
            params = dict(step.args.get("params", {}) or {})
            return requires_approval(name, params)
        if step.action == "cap":
            contract = _universal_contract(str(step.args.get("cap", "")))
            if contract is None:
                return True
            return contract.requires_confirmation or contract.side_effect in {
                "external", "destructive",
            }
        return False

    def _approval_error(self, plan: StructuredPlan) -> str:
        if self.approve_all:
            return ""
        for index, step in enumerate(plan.steps, 1):
            if self._step_is_consequential(step):
                return f"Structured step {index} ({step.action}) requires explicit plan approval"
        return ""

    def _approve_replacement(
        self,
        plan: StructuredPlan,
        index: int,
        original: PlanStep,
        replacement: PlanStep,
    ) -> bool:
        changed_target = original.action != replacement.action or original.args != replacement.args
        needs_approval = (
            changed_target
            or self._step_is_consequential(original)
            or self._step_is_consequential(replacement)
        )
        if not needs_approval:
            return True
        if self.repair_approval_callback is None:
            return self.approve_all
        amended_steps = list(plan.steps)
        amended_steps[index - 1] = replacement
        amended = StructuredPlan(
            summary=f"{plan.summary} (repair step {index})",
            steps=tuple(amended_steps),
        )
        fingerprint = plan_fingerprint(amended)
        return (
            self.repair_approval_callback(amended)
            and plan_fingerprint(amended) == fingerprint
        )

    def _save_checkpoint(
        self,
        path: Path | None,
        plan: StructuredPlan,
        evidence: list[dict[str, Any]],
        completed: int,
        *,
        in_progress: int | None = None,
        phase: str = "",
        complete: bool = False,
        uncertain: bool = False,
        error: str = "",
        goal: str = "",
    ) -> None:
        if path is None:
            return
        atomic_write_json(path, {
            "version": 2,
            "goal": goal,
            "execution_id": self.execution_id,
            "plan": plan_payload(plan),
            "fingerprint": plan_fingerprint(plan),
            "completed_steps": completed,
            "in_progress": in_progress,
            "phase": phase,
            "complete": complete,
            "uncertain": uncertain,
            "error": error,
            "evidence": evidence,
            "updated_at": time.time(),
        })

    def execute(
        self,
        plan: StructuredPlan,
        *,
        checkpoint_path: Path | None = None,
        resume: bool = False,
        goal: str = "",
    ) -> ExecutionResult:
        declared_urls = [
            str(
                step.args.get("url", "")
                or (
                    step.args.get("popup", {}).get("url", "")
                    if isinstance(step.args.get("popup"), dict)
                    else ""
                )
            )
            for step in plan.steps
            if step.action in {"navigate", "open_tab"} or step.args.get("popup")
        ]
        self._goal_context = goal or self._goal_context
        self._allowed_origins = {
            origin for url in declared_urls for origin in [self._origin(url)] if origin
        }
        self._allowed_hosts = {
            host
            for step in plan.steps
            if step.action in {"navigate", "open_tab"} or step.args.get("popup")
            for host in [(
                urlparse(
                    str(
                        step.args.get("url", "")
                        or (
                            step.args.get("popup", {}).get("url", "")
                            if isinstance(step.args.get("popup"), dict)
                            else ""
                        )
                    )
                ).hostname
                or ""
            ).lower()]
            if host
        }
        approval_error = self._approval_error(plan)
        if approval_error:
            return ExecutionResult(False, approval_error)

        evidence: list[dict[str, Any]] = []
        completed = 0
        if resume and checkpoint_path is not None and checkpoint_path.is_file():
            try:
                saved = json.loads(checkpoint_path.read_text())
                if saved.get("fingerprint") != plan_fingerprint(plan):
                    return ExecutionResult(False, "Checkpoint plan fingerprint does not match")
                if saved.get("uncertain"):
                    return ExecutionResult(
                        False,
                        "Interrupted consequential step has uncertain outcome; automatic replay refused",
                        int(saved.get("completed_steps", 0)),
                        list(saved.get("evidence", [])),
                    )
                interrupted = saved.get("in_progress")
                phase = str(saved.get("phase", ""))
                if interrupted is not None:
                    interrupted_index = int(interrupted)
                    if not 1 <= interrupted_index <= len(plan.steps):
                        return ExecutionResult(False, "Checkpoint in-progress step is invalid")
                    interrupted_step = plan.steps[interrupted_index - 1]
                    dispatched_or_legacy = phase == "dispatched" or not phase
                    if dispatched_or_legacy and self._step_is_consequential(interrupted_step):
                        message = (
                            "Interrupted consequential step may already have executed; "
                            "automatic replay refused"
                        )
                        self._save_checkpoint(
                            checkpoint_path,
                            plan,
                            list(saved.get("evidence", [])),
                            int(saved.get("completed_steps", 0)),
                            in_progress=interrupted_index,
                            phase="dispatched",
                            uncertain=True,
                            error=message,
                            goal=goal,
                        )
                        return ExecutionResult(
                            False,
                            message,
                            int(saved.get("completed_steps", 0)),
                            list(saved.get("evidence", [])),
                        )
                completed = int(saved.get("completed_steps", 0))
                evidence = list(saved.get("evidence", []))
                self.execution_id = str(saved.get("execution_id") or self.execution_id)
                self._resume_tab_marker = next(
                    (
                        str(item["active_tab"])
                        for item in reversed(evidence)
                        if item.get("active_tab")
                    ),
                    "",
                )
            except (OSError, ValueError, TypeError) as exc:
                return ExecutionResult(False, f"Could not resume structured checkpoint: {exc}")

        self._save_checkpoint(
            checkpoint_path, plan, evidence, completed, goal=goal
        )
        started = time.monotonic()
        try:
            for index, step in enumerate(plan.steps, 1):
                if index <= completed:
                    continue
                if time.monotonic() - started > self.total_timeout:
                    message = (
                        f"Structured execution exceeded {self.total_timeout:.0f}s total budget"
                    )
                    self._save_checkpoint(
                        checkpoint_path,
                        plan,
                        evidence,
                        completed,
                        error=message,
                        goal=goal,
                    )
                    return ExecutionResult(False, message, completed, evidence)
                try:
                    resolved_step = PlanStep(
                        action=step.action,
                        args=_resolve_step_references(step.args, evidence),
                        expect=_resolve_step_references(step.expect, evidence),
                        when=_resolve_step_references(step.when, evidence),
                    )
                except Exception as exc:
                    message = f"Step {index} references unavailable evidence: {exc}"
                    self._save_checkpoint(
                        checkpoint_path,
                        plan,
                        evidence,
                        completed,
                        error=message,
                        goal=goal,
                    )
                    return ExecutionResult(False, message, completed, evidence)
                if not _condition_matches(resolved_step.when):
                    evidence.append({
                        "step": index,
                        "action": step.action,
                        "skipped": True,
                        "condition_met": False,
                    })
                    completed = index
                    self._save_checkpoint(
                        checkpoint_path, plan, evidence, completed, goal=goal
                    )
                    continue
                safe_args = dict(resolved_step.args)
                for sensitive_key in ("text", "password", "token", "secret"):
                    if sensitive_key in safe_args:
                        safe_args[sensitive_key] = "<redacted>"
                logger.info(
                    f"StructuredExecutor[{index}/{len(plan.steps)}]: {step.action} {safe_args}"
                )
                self._save_checkpoint(
                    checkpoint_path,
                    plan,
                    evidence,
                    completed,
                    in_progress=index,
                    phase="prepared",
                    goal=goal,
                )
                candidate = resolved_step
                repair_count = 0
                while True:
                    last_error: Exception | None = None
                    attempt_count = 1 if self._step_is_consequential(candidate) else 2
                    for attempt in range(attempt_count):
                        try:
                            self._save_checkpoint(
                                checkpoint_path,
                                plan,
                                evidence,
                                completed,
                                in_progress=index,
                                phase="dispatched",
                                goal=goal,
                            )
                            item = self._dispatch_step(candidate)
                            evidence_item = {
                                "step": index,
                                "action": candidate.action,
                                **item,
                            }
                            if repair_count:
                                evidence_item["repaired_from_action"] = step.action
                            evidence.append(evidence_item)
                            completed = index
                            self._save_checkpoint(
                                checkpoint_path, plan, evidence, completed, goal=goal
                            )
                            break
                        except Exception as exc:
                            last_error = exc
                            uncertain = isinstance(exc, UncertainStepOutcome)
                            consequential = self._step_is_consequential(candidate)
                            not_dispatched = isinstance(exc, ActionNotDispatched)
                            if uncertain or (consequential and not not_dispatched):
                                if uncertain:
                                    message = (
                                        f"Step {index} ({candidate.action}) has uncertain "
                                        f"outcome: {exc}"
                                    )
                                else:
                                    message = f"Step {index} ({candidate.action}) failed: {exc}"
                                self._save_checkpoint(
                                    checkpoint_path,
                                    plan,
                                    evidence,
                                    completed,
                                    in_progress=index,
                                    phase="dispatched",
                                    uncertain=True,
                                    error=message,
                                    goal=goal,
                                )
                                return ExecutionResult(False, message, completed, evidence)
                            if attempt + 1 < attempt_count:
                                logger.warning(
                                    f"Structured step {index} failed once; "
                                    f"retrying locally: {exc}"
                                )
                                time.sleep(0.5)
                    else:
                        retry_suffix = " after one retry" if attempt_count > 1 else ""
                        message = (
                            f"Step {index} ({candidate.action}) failed"
                            f"{retry_suffix}: {last_error}"
                        )
                        if self.repair_callback is None:
                            self._save_checkpoint(
                                checkpoint_path,
                                plan,
                                evidence,
                                completed,
                                error=message,
                                goal=goal,
                            )
                            return ExecutionResult(False, message, completed, evidence)
                        if repair_count >= _MAX_STEP_REPAIRS:
                            message = (
                                f"{message}; exhausted maximum of "
                                f"{_MAX_STEP_REPAIRS} failed-step repairs"
                            )
                            self._save_checkpoint(
                                checkpoint_path,
                                plan,
                                evidence,
                                completed,
                                error=message,
                                goal=goal,
                            )
                            return ExecutionResult(False, message, completed, evidence)

                        try:
                            repair_evidence = list(evidence)
                            repair_evidence.append({
                                "step": index,
                                "action": "live_observation",
                                **self._repair_observation(candidate),
                            })
                            replacement = self.repair_callback(
                                goal,
                                plan,
                                index,
                                candidate,
                                str(last_error),
                                repair_evidence,
                            )
                        except Exception as exc:
                            logger.warning(f"Failed-step repair request failed: {exc}")
                            message = f"{message}; repair request failed: {exc}"
                            self._save_checkpoint(
                                checkpoint_path,
                                plan,
                                evidence,
                                completed,
                                error=message,
                                goal=goal,
                            )
                            return ExecutionResult(False, message, completed, evidence)

                        if replacement is None:
                            self._save_checkpoint(
                                checkpoint_path,
                                plan,
                                evidence,
                                completed,
                                error=message,
                                goal=goal,
                            )
                            return ExecutionResult(False, message, completed, evidence)

                        try:
                            replacement = validate_plan({
                                "summary": "single-step repair",
                                "steps": [{
                                    "action": replacement.action,
                                    "args": replacement.args,
                                    "expect": replacement.expect,
                                    **({"when": replacement.when} if replacement.when else {}),
                                }],
                            }).steps[0]
                            replacement = PlanStep(
                                action=replacement.action,
                                args=_resolve_step_references(replacement.args, evidence),
                                expect=_resolve_step_references(replacement.expect, evidence),
                                when={},
                            )
                            if not self._approve_replacement(
                                plan, index, candidate, replacement
                            ):
                                raise PermissionError(
                                    "Failed-step replacement was not explicitly approved"
                                )
                        except Exception as exc:
                            message = f"{message}; failed-step repair unsuccessful: {exc}"
                            self._save_checkpoint(
                                checkpoint_path,
                                plan,
                                evidence,
                                completed,
                                error=message,
                                goal=goal,
                            )
                            return ExecutionResult(False, message, completed, evidence)

                        repair_count += 1
                        candidate = replacement
                        logger.info(
                            f"Executing repaired step {index}: {candidate.action}"
                        )
                        continue
                    break
            self._save_checkpoint(
                checkpoint_path,
                plan,
                evidence,
                completed,
                complete=True,
                goal=goal,
            )
            return ExecutionResult(True, f"Completed {completed} structured steps", completed, evidence)
        finally:
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception as exc:
                    logger.debug(f"Structured browser disconnect failed: {exc}")


def _checkpoint_path_for_goal(goal: str) -> Path:
    goal_hash = hashlib.sha256(goal.strip().encode("utf-8")).hexdigest()[:20]
    return Path(__file__).resolve().parents[1] / "logs" / "structured_sessions" / f"{goal_hash}.json"


def _runtime_checkpoint_path_for_goal(goal: str) -> Path:
    goal_hash = hashlib.sha256(goal.strip().encode("utf-8")).hexdigest()[:20]
    return Path(__file__).resolve().parents[1] / "logs" / "runtime_sessions" / f"{goal_hash}.json"


def plan_and_execute_runtime(
    goal: str,
    *,
    approve_all: bool = False,
    approval_callback: Callable[[StructuredPlan], bool] | None = None,
) -> ExecutionResult | None:
    """Use the authoritative runtime for newly planned structured goals.

    Older structured-session checkpoints remain resumable through
    :func:`plan_and_execute`; new sessions store the exact validated plan in
    the runtime checkpoint so verified steps are never replanned or replayed.
    """
    from core.runtime_adapters import (
        execute_structured_plan_runtime,
        resume_structured_plan_runtime,
    )

    checkpoint_path = _runtime_checkpoint_path_for_goal(goal)
    planner = StructuredPlanner()
    executor: StructuredExecutor | None = None
    try:
        if checkpoint_path.is_file():
            saved = json.loads(checkpoint_path.read_text())
            if saved.get("goal") == goal and saved.get("status") in {"running", "paused_for_human"}:
                facts = saved.get("verified_facts", {})
                runtime_plan = validate_plan(
                    facts.get("structured_plan") if isinstance(facts, dict) else None
                )
                executor = StructuredExecutor(
                    approve_all=approve_all,
                    repair_callback=planner.repair_step if planner.available() else None,
                    repair_approval_callback=approval_callback,
                )
                approval_error = executor._approval_error(runtime_plan)
                if approval_error and not approve_all:
                    if approval_callback is None:
                        return ExecutionResult(False, approval_error)
                    fingerprint = plan_fingerprint(runtime_plan)
                    if not approval_callback(runtime_plan):
                        return ExecutionResult(False, "Structured plan was not approved")
                    if plan_fingerprint(runtime_plan) != fingerprint:
                        return ExecutionResult(False, "Structured plan changed after approval")
                    executor.approve_all = True
                result = resume_structured_plan_runtime(
                    goal,
                    checkpoint_path,
                    executor=executor,
                    approve_all=approve_all,
                    approval_callback=approval_callback,
                )
                return _runtime_execution_result(result)

        if not planner.available():
            return None
        try:
            plan = planner.plan(goal)
        except Exception as exc:
            # A planning failure means no structured run happened at all.
            # Return None so callers fall through to their other executors
            # (adaptive loop, AI planner) instead of terminating the goal.
            logger.warning(f"Structured planning failed: {exc}")
            return None

        executor = StructuredExecutor(
            approve_all=approve_all,
            repair_callback=planner.repair_step,
            repair_approval_callback=approval_callback,
        )
        approval_error = executor._approval_error(plan)
        if approval_error and not approve_all:
            if approval_callback is None:
                return ExecutionResult(False, approval_error)
            fingerprint = plan_fingerprint(plan)
            if not approval_callback(plan):
                return ExecutionResult(False, "Structured plan was not approved")
            if plan_fingerprint(plan) != fingerprint:
                return ExecutionResult(False, "Structured plan changed after approval")
            executor.approve_all = True

        result = execute_structured_plan_runtime(
            plan,
            goal,
            executor=executor,
            checkpoint_path=checkpoint_path,
            approve_all=approve_all,
            approval_callback=approval_callback,
        )
        return _runtime_execution_result(result)
    except (OSError, ValueError, TypeError) as exc:
        return ExecutionResult(False, f"Runtime structured session failed: {exc}")
    finally:
        if executor is not None:
            executor.close()


def _runtime_execution_result(result: Any) -> ExecutionResult:
    facts = result.state.verified_facts
    evidence = list(facts.get("structured_evidence", ()))
    return ExecutionResult(
        result.success,
        result.state.message,
        int(facts.get("structured_next_step", 0)),
        evidence,
    )


def plan_and_execute(
    goal: str,
    *,
    approve_all: bool = False,
    approval_callback: Callable[[StructuredPlan], bool] | None = None,
) -> ExecutionResult | None:
    """Plan once, obtain exact-plan approval if needed, then execute locally."""
    checkpoint_path = _checkpoint_path_for_goal(goal)
    planner = StructuredPlanner()
    resume = False
    execution_id: str | None = None
    plan: StructuredPlan | None = None
    if checkpoint_path.is_file():
        try:
            saved = json.loads(checkpoint_path.read_text())
            if saved.get("goal") == goal and not saved.get("complete", False):
                plan = validate_plan(saved.get("plan"))
                execution_id = str(saved.get("execution_id") or "") or None
                resume = True
                logger.info(
                    f"Resuming structured plan at step "
                    f"{int(saved.get('completed_steps', 0)) + 1} without replanning"
                )
        except (OSError, ValueError, TypeError) as exc:
            return ExecutionResult(False, f"Invalid structured checkpoint: {exc}")

    if plan is None:
        if not planner.available():
            return None
        try:
            plan = planner.plan(goal)
        except Exception as exc:
            # Planning produced no structured run; let callers fall through
            # to their other executors instead of failing the whole goal.
            logger.warning(f"Structured planning failed: {exc}")
            return None
    logger.info(f"Structured plan: {plan.summary} ({len(plan.steps)} steps)")
    executor = StructuredExecutor(
        approve_all=approve_all,
        execution_id=execution_id,
        repair_callback=planner.repair_step if planner.available() else None,
        repair_approval_callback=approval_callback,
    )
    approval_error = executor._approval_error(plan)
    if approval_error and not approve_all:
        if approval_callback is None:
            return ExecutionResult(False, approval_error)
        fingerprint = plan_fingerprint(plan)
        if not approval_callback(plan):
            return ExecutionResult(False, "Structured plan was not approved")
        if plan_fingerprint(plan) != fingerprint:
            return ExecutionResult(False, "Structured plan changed after approval")
        executor.approve_all = True
    return executor.execute(
        plan,
        checkpoint_path=checkpoint_path,
        resume=resume,
        goal=goal,
    )

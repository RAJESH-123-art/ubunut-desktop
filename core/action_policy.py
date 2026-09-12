"""Central classification and approval tokens for consequential actions."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

CONSEQUENTIAL_ACTIONS: dict[str, str] = {
    "whatsapp_send": "Send a WhatsApp message",
    "file_delete": "Delete a file",
    "file_operations": "Modify, move, or delete a file",
    "folder_delete": "Delete a folder",
    "folder_operations": "Modify or delete a folder",
    "file_write": "Write or overwrite a file",
    "create_folder": "Create a folder",
    "install_app": "Install an application",
    "organize_downloads": "Move or delete files in Downloads",
    "system_power": "Change system power state",
    "process_kill": "Terminate a process",
    "run_command": "Run a shell command",
    "type_text": "Type into another application",
    "hotkey": "Send a keyboard shortcut to another application",
    "system_hotkey": "Send a system keyboard shortcut",
    "window_close": "Close an application window",
    "window_management": "Change or close an application window",
    "universal_fallback": "Run adaptive desktop automation",
}


def requires_approval(intent: str, params: dict[str, Any] | None = None) -> bool:
    """Classify an intent/task, including operation-specific module aliases."""
    params = params or {}
    if intent == "file_operations":
        return str(params.get("operation", "")).lower() in {"delete", "remove", "move", "rename"}
    if intent == "folder_operations":
        return str(params.get("operation", "")).lower() in {"create", "delete"}
    if intent == "window_management":
        return str(params.get("operation", "")).lower() in {"close", "minimize", "maximize"}
    if intent == "organize_downloads":
        return bool(
            params.get("move_videos", True)
            or params.get("create_subfolders")
            or params.get("delete_junk")
            or params.get("delete_older_than_days")
        )
    return intent in CONSEQUENTIAL_ACTIONS


def describe_action(intent: str, params: dict[str, Any] | None = None) -> str:
    """Return a human-readable description for confirmation interfaces."""
    if intent in CONSEQUENTIAL_ACTIONS:
        return CONSEQUENTIAL_ACTIONS[intent]
    return f"Execute {intent}"


def action_token(intent: str, params: dict[str, Any]) -> str:
    """Return a stable token binding approval to one exact resolved action."""
    payload = json.dumps(
        {"intent": intent, "params": params},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approved(intent: str, params: dict[str, Any], tokens: Iterable[str] | None) -> bool:
    return tokens is not None and action_token(intent, params) in set(tokens)

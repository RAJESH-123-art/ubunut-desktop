"""
Tiny task-memory / cache store.

Persists learned facts and task results as JSON so repeated tasks skip
re-discovery (e.g., last Klavaro window position, WhatsApp contact hints).

Usage:
    from core.memory import memory
    memory.set("klavaro", "last_score", {"wpm": 2394, "accuracy": 100.0})
    memory.get("klavaro", "last_score")
"""

import json
import threading
import time
from pathlib import Path
from typing import cast

from loguru import logger

# Stored values are heterogeneous by nature (JSON-shaped).
StoredValue = str | int | float | bool | dict[str, object] | list[object] | None

_STORE_PATH: Path = Path.home() / ".config" / "desktop_automation" / "task_memory.json"
_lock: threading.Lock = threading.Lock()


def _load() -> dict[str, dict[str, object]]:
    try:
        data = cast(object, json.loads(_STORE_PATH.read_text()))
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict):
        result: dict[str, dict[str, object]] = {}
        for ns, entries in cast(dict[str, object], data).items():
            if isinstance(entries, dict):
                result[str(ns)] = cast(dict[str, object], entries)
        return result
    return {}


def _save(data: dict[str, dict[str, object]]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ = _STORE_PATH.write_text(json.dumps(data, indent=2, default=str))


class Memory:
    """Namespaced key-value store with optional TTL."""

    def get(self, namespace: str, key: str, max_age: float | None = None) -> StoredValue:
        with _lock:
            data = _load()
        ns_dict = data.get(namespace)
        if not isinstance(ns_dict, dict):
            return None
        entry_raw = ns_dict.get(key)
        if not isinstance(entry_raw, dict):
            return None
        entry = cast(dict[str, object], entry_raw)
        ts = entry.get("ts", 0)
        ts_num = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0
        if max_age is not None and time.time() - ts_num > max_age:
            return None
        val = entry.get("value")
        if isinstance(val, (str, int, float, bool, list, dict)) or val is None:
            return cast(StoredValue, val)
        return None

    def set(self, namespace: str, key: str, value: object) -> None:
        with _lock:
            data = _load()
            data.setdefault(namespace, {})[key] = {"value": value, "ts": time.time()}
            _save(data)
        logger.debug(f"memory: {namespace}.{key} saved")

    def delete(self, namespace: str, key: str) -> None:
        with _lock:
            data = _load()
            if namespace in data:
                _ = data[namespace].pop(key, None)
                _save(data)

    def clear_namespace(self, namespace: str) -> None:
        with _lock:
            data = _load()
            _ = data.pop(namespace, None)
            _save(data)


memory = Memory()

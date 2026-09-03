"""
CLI Registry — Layer 1 of the layered automation architecture.

See ARCHITECTURE_EVOLUTION.md and TASK_KNOWLEDGE_BASE.md Part 4/6.

Loads config/cli_registry.yaml (pattern -> shell command template) and
matches user commands against it BEFORE the blind binary-guessing in
tasks/universal_fallback.py runs. This turns "unknown command -> guess a
random token is a binary -> probably fail" into "unknown command -> check
a real knowledge base of proven command templates -> run it properly."

Adding a new capability = add one YAML entry, no Python code needed.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from core.logger import notify
from core.safety_guard import is_dangerous_shell_command

_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "cli_registry.yaml"
_DEFAULT_TIMEOUT = 20.0

_entries_cache: list[dict[str, Any]] | None = None


def _load_entries(force: bool = False) -> list[dict[str, Any]]:
    global _entries_cache
    if _entries_cache is not None and not force:
        return _entries_cache

    try:
        raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        logger.warning(f"cli_registry: could not read {_REGISTRY_PATH}: {exc}")
        raw = {}

    compiled: list[dict[str, Any]] = []
    for entry in raw.get("entries", []):
        name = entry.get("name", "?")
        try:
            pattern = re.compile(entry["match"], re.IGNORECASE)
            command = entry["command"]
        except (re.error, KeyError) as exc:
            logger.warning(f"cli_registry: skipping malformed entry {name!r}: {exc}")
            continue
        compiled.append({
            "name": name,
            "pattern": pattern,
            "command": command,
            "defaults": entry.get("defaults", {}),
            "fallback": entry.get("fallback"),
            "timeout": float(entry.get("timeout", _DEFAULT_TIMEOUT)),
        })

    _entries_cache = compiled
    logger.info(f"cli_registry: loaded {len(compiled)} entries from {_REGISTRY_PATH.name}")
    return compiled


def match(text: str) -> dict[str, Any] | None:
    """
    Try to match `text` against every registered pattern.
    Returns a dict with name/command/fallback/timeout on match, else None.
    """
    for entry in _load_entries():
        m = entry["pattern"].search(text)
        if not m:
            continue

        params: dict[str, str] = dict(entry["defaults"])
        captured = {k: v for k, v in m.groupdict().items() if v is not None}
        # Shell-quote only values captured from user text — defaults are
        # static/trusted, coming from the YAML file itself.
        for k, v in captured.items():
            params[k] = shlex.quote(str(v).strip())

        try:
            command = entry["command"].format(**params)
        except (KeyError, IndexError) as exc:
            logger.debug(f"cli_registry: '{entry['name']}' matched but missing param: {exc}")
            continue

        fallback = entry.get("fallback")
        if fallback:
            try:
                fallback = fallback.format(**params)
            except (KeyError, IndexError):
                fallback = None

        return {
            "name": entry["name"],
            "command": command,
            "fallback": fallback,
            "timeout": entry["timeout"],
        }
    return None


def _run_one(command: str, timeout: float) -> bool:
    # SAFETY: defense-in-depth against a future registry entry (or a bad
    # param substitution) producing a catastrophic command — checked here
    # too, not just at the call sites, since this is the actual execution
    # point for every entry in the registry.
    if is_dangerous_shell_command(command):
        logger.error(f"cli_registry: refusing dangerous command: {command!r}")
        return False
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            if proc.stdout.strip():
                print(proc.stdout.strip())
            return True
        logger.warning(f"cli_registry: {command!r} exited {proc.returncode}: {proc.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning(f"cli_registry: {command!r} timed out after {timeout}s")
    except OSError as exc:
        logger.warning(f"cli_registry: {command!r} failed to launch: {exc}")
    return False


def try_run(text: str) -> bool:
    """
    Match `text` against the registry and execute the resulting command
    (with fallback) if found. Returns False immediately (no side effect)
    if nothing matches, so callers can safely chain further fallback
    strategies afterward.
    """
    result = match(text)
    if result is None:
        return False

    name, command, fallback, timeout = (
        result["name"], result["command"], result["fallback"], result["timeout"],
    )
    logger.info(f"cli_registry: matched '{name}' -> {command!r}")

    if _run_one(command, timeout):
        logger.info(f"cli_registry: '{name}' succeeded")
        notify(f"Ran: {name}")
        return True

    if fallback:
        logger.info(f"cli_registry: '{name}' trying fallback -> {fallback!r}")
        if _run_one(fallback, timeout):
            logger.info(f"cli_registry: '{name}' fallback succeeded")
            notify(f"Ran: {name} (fallback)")
            return True

    logger.warning(f"cli_registry: '{name}' matched but command failed")
    return False


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) or "check disk space"
    result = match(query)
    print(f"Query: {query!r}")
    print(f"Match: {result}")

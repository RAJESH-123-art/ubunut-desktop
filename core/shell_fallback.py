"""
Ordered shell-command fallback runner.

Deduplicates the "_run() helper + shutil.which() gate + ordered candidate
list" pattern found nearly identically in system_power.py, lock_screen.py,
volume_control.py and brightness_control.py — see TASK_KNOWLEDGE_BASE.md
Part 3. New tasks (and the CLI registry) should use this instead of
re-implementing the same loop.
"""
from __future__ import annotations

import shutil
import subprocess

from loguru import logger


def try_commands(candidates: list[list[str]], timeout: float = 10.0) -> bool:
    """
    Try each command (given as an argv list, no shell) in order.
    Skips any candidate whose binary (first token) isn't on PATH.
    Returns True on the first candidate that runs and exits 0.
    """
    for cmd in candidates:
        if not cmd:
            continue
        binary = cmd[0]
        if not shutil.which(binary):
            logger.debug(f"try_commands: '{binary}' not on PATH — skipping {cmd!r}")
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                   check=False)  # returncode checked below
            if result.returncode == 0:
                logger.debug(f"try_commands: succeeded: {cmd!r}")
                return True
            logger.debug(f"try_commands: {cmd!r} exited {result.returncode}: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logger.debug(f"try_commands: {cmd!r} timed out after {timeout}s")
        except OSError as exc:
            logger.debug(f"try_commands: {cmd!r} failed to launch: {exc}")
    return False


def try_shell_commands(candidates: list[str], timeout: float = 10.0) -> bool:
    """
    Like try_commands, but for shell-string candidates (needed when pipes
    or redirects are required). Uses shell=True — only pass trusted, static
    templates, never raw user input.
    """
    for cmd in candidates:
        if not cmd:
            continue
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                                   check=False)  # returncode checked below
            if result.returncode == 0:
                logger.debug(f"try_shell_commands: succeeded: {cmd!r}")
                return True
            logger.debug(f"try_shell_commands: {cmd!r} exited {result.returncode}: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logger.debug(f"try_shell_commands: {cmd!r} timed out after {timeout}s")
        except OSError as exc:
            logger.debug(f"try_shell_commands: {cmd!r} failed to launch: {exc}")
    return False

"""
Safety guard — last line of defense against catastrophic destructive actions.

Every automation layer in this project ultimately resolves natural language
into a path or shell command. A single misparsed/misresolved value fed into
`shutil.rmtree()` or `subprocess.run(..., shell=True)` can destroy far more
than intended — e.g. an empty, "~", or "/" folder name resolving to the
filesystem root or the user's entire home directory, or a fallback path
blindly running "rm -rf ..." because its first token happened to be a real
binary on PATH.

This module does NOT replace the existing pre-check/post-verify pattern
already used throughout tasks/*.py (see TASK_KNOWLEDGE_BASE.md Part 2) — it
adds one more, specific check: "is the target itself sane at all", run
BEFORE any destructive operation, independent of whether that target exists.

Design intent: be conservative. It is always better to refuse a legitimate
but unusual request and let the user rephrase, than to silently destroy
data or system state that can never be recovered.
"""
from __future__ import annotations

import re
from pathlib import Path

# Absolute paths that must NEVER be recursively deleted, no matter what a
# parser/user/LLM asked for.
_FORBIDDEN_ROOTS = {
    "/", "/root", "/home", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/var", "/opt", "/srv", "/sys", "/proc", "/dev", "/run",
    "/media", "/mnt", "/snap", "/tmp",
}


class UnsafeActionError(RuntimeError):
    """Raised when an action is judged too dangerous to perform."""


def assert_safe_to_delete(path: Path, *, restrict_to_home: bool = False) -> None:
    """
    Raise UnsafeActionError if `path` is too dangerous to recursively
    delete. Call this BEFORE shutil.rmtree() / unlink() on anything derived
    from user input, NL parsing, or any other untrusted/auto-resolved source.

    Args:
        path: the target about to be deleted (need not exist yet).
        restrict_to_home: if True, ALSO refuse anything outside the user's
            home directory — use this for tasks that should conceptually
            never touch system-wide locations (e.g. organize_downloads).
    """
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()

    if resolved == Path("/") or str(resolved) in _FORBIDDEN_ROOTS:
        raise UnsafeActionError(f"Refusing to delete a protected system path: {resolved}")

    if resolved == home:
        raise UnsafeActionError(f"Refusing to delete the entire home directory: {resolved}")

    is_inside_home = True
    try:
        resolved.relative_to(home)
    except ValueError:
        is_inside_home = False

    if restrict_to_home and not is_inside_home:
        raise UnsafeActionError(
            f"Refusing to delete outside the home directory: {resolved}"
        )

    # Refuse suspiciously shallow paths outside the home directory
    # (e.g. "/mnt/data" = 2 parts, allowed; "/mnt" alone = 1 part, refused).
    # Paths inside the home directory are exempt since "~/Desktop" etc. are
    # intentionally shallow and legitimate.
    if not is_inside_home and len(resolved.parts) <= 2:
        raise UnsafeActionError(
            f"Refusing to delete a suspiciously shallow system path: {resolved}"
        )


# ── Dangerous shell command detection ────────────────────────────────────────

_DANGEROUS_SHELL_PATTERNS = [
    r"rm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+/\*?(?:\s|$)",  # rm -rf /  or  rm -rf /*
    r"rm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+~(?:\s|$|/\s*$)",  # rm -rf ~  or  rm -rf ~/
    r"rm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+\*(?:\s|$)",  # rm -rf *
    r"rm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+\.\.",         # rm -rf ..
    r"rm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+\$HOME",       # rm -rf $HOME
    r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;\s*:",           # classic fork bomb
    r"\bmkfs(\.\w+)?\b",                                  # format a filesystem
    r"\bdd\b[^|;&]*\bof=/dev/",                          # dd ... of=/dev/sdX
    r">\s*/dev/(sd|nvme|hd)\w*",                         # redirect into a raw disk device
    r"\bchmod\s+-R\s+000\s+/",                           # lock out the whole filesystem
    r"\bchown\s+-R\b.*\s+/(?:\s|$)",                     # chown -R ... /
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_SHELL_PATTERNS), re.IGNORECASE)


def is_dangerous_shell_command(command: str) -> bool:
    """
    Best-effort detector for obviously catastrophic shell command patterns.
    NOT a security sandbox (a determined user can always bypass this) — it
    exists purely as a sanity net against a parser/fallback path accidentally
    constructing/running something like "rm -rf /" from a bad extraction.
    """
    return bool(_DANGEROUS_RE.search(command))


def assert_safe_shell_command(command: str) -> None:
    """Raise UnsafeActionError if `command` matches a known-catastrophic pattern."""
    if is_dangerous_shell_command(command):
        raise UnsafeActionError(
            f"Refusing to run a command that matches a known-dangerous pattern: {command!r}"
        )

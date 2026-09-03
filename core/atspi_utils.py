"""
Shared AT-SPI accessibility tree utilities.

Centralises helpers used by both tasks/install_app.py and tasks/atspi_install.py
so the same code isn't duplicated in two places.

All functions return None / False on failure rather than raising, so callers
can fall back to alternative strategies without extra try/except wrappers.
"""
from __future__ import annotations

import sys
import time

from loguru import logger

# Make system pyatspi available (installed as a system package, not via pip)
sys.path.insert(0, "/usr/lib/python3/dist-packages")


def find_node(root: object, role: object = None,
              name_contains: str | None = None,
              depth: int = 0, max_depth: int = 15) -> object | None:
    """
    Recursively walk an AT-SPI subtree and return the first matching node.

    Args:
        root:          AT-SPI node to start from.
        role:          pyatspi role constant (e.g. pyatspi.ROLE_PUSH_BUTTON).
                       None = any role.
        name_contains: Case-insensitive substring the node's name must contain.
                       None = any name.
        depth / max_depth: Guard against infinite recursion on deep trees.
    """
    if depth > max_depth:
        return None
    try:
        role_ok = role is None or root.getRole() == role          # type: ignore[union-attr]
        name_ok = (name_contains is None
                   or name_contains.lower() in (root.name or "").lower())  # type: ignore[union-attr]
        if role_ok and name_ok:
            return root
        for i in range(root.childCount):                           # type: ignore[union-attr]
            found = find_node(root.getChildAtIndex(i), role, name_contains,  # type: ignore[union-attr]
                              depth + 1, max_depth)
            if found:
                return found
    except Exception:
        pass
    return None


def do_action(node: object,
              preferred: tuple[str, ...] = ("click", "press", "activate")) -> bool:
    """
    Trigger the best available action on an AT-SPI node.
    Tries each name in *preferred* before falling back to action index 0.
    """
    try:
        act = node.queryAction()                                    # type: ignore[union-attr]
        n   = act.nActions
        if n == 0:
            # Some GTK apps expose duplicate/decoy accessible nodes with a
            # "clickable" role but zero actions (confirmed live against
            # Nautilus's "main menu" button) — explicit check instead of
            # letting doAction(0) raise, so callers get a clean False.
            return False
        names = [act.getName(i).lower() for i in range(n)]
        for pref in preferred:
            if pref in names:
                act.doAction(names.index(pref))
                return True
        act.doAction(0)
        return True
    except Exception as exc:
        logger.debug(f"AT-SPI do_action failed: {exc}")
        return False


def set_text(node: object, text: str) -> bool:
    """Write *text* into an editable AT-SPI field."""
    try:
        node.queryEditableText().setTextContents(text)             # type: ignore[union-attr]
        return True
    except Exception as exc:
        logger.debug(f"AT-SPI set_text failed: {exc}")
        return False


def wait_for_app(name_fragments: list[str], timeout: int = 15) -> object | None:
    """
    Poll the AT-SPI desktop until an application whose name contains any of
    *name_fragments* appears, then return that application node.

    Returns None if the timeout expires without a match.
    """
    try:
        import pyatspi
    except ImportError:
        logger.warning("pyatspi not available (not installed as system package)")
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                n = (app.name or "").lower()
                if any(frag in n for frag in name_fragments):
                    logger.info(f"AT-SPI: found app '{app.name}'")
                    return app
        except Exception:
            pass
        time.sleep(1)

    logger.warning(f"AT-SPI: app {name_fragments} not found after {timeout}s")
    return None

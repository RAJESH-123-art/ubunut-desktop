"""
AT-SPI Universal Navigator — Layer 2 of the layered automation architecture.

See ARCHITECTURE_EVOLUTION.md ("THE MISSING PIECE — core/atspi_navigator.py")
and TASK_KNOWLEDGE_BASE.md Part 3/6.

This generalizes two proven patterns already working in this codebase,
instead of inventing something new from scratch:
  - atspi_install.py's `_await_node()` — bounded polling for an AT-SPI
    element to appear, used instead of blind fixed sleeps.
  - klavaro_automation.py's structural tree walk — identifying nodes by
    role/position rather than requiring an exact name match.

Purpose: let the agent operate on ANY already-running GTK/Qt app's UI by
reading its live accessibility tree, instead of needing a dedicated task
file written in advance for that specific app.

Scope of this first version: works on apps that are ALREADY RUNNING
(launch them first with tasks/open_system_app.py). Scoring is a simple,
transparent keyword-overlap heuristic — good enough for common actions
("create folder", "close dialog", "search box") without needing an LLM.
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass

from loguru import logger

from core.atspi_utils import do_action, set_text

_POLL_INTERVAL = 0.25
_DEFAULT_TIMEOUT = 6.0
_MAX_TREE_DEPTH = 20

# AT-SPI role names (lowercase, as returned by getRoleName()) worth treating
# as "clickable" vs "text entry" when scoring candidate nodes.
CLICKABLE_ROLES = {
    "push button", "button", "menu item", "check box", "radio button",
    "toggle button", "list item", "tab", "icon",
}
TEXT_ENTRY_ROLES = {"entry", "text", "password text"}
DIALOG_ROLES = {"dialog", "alert", "file chooser"}

_STOPWORDS = {"the", "a", "an", "to", "in", "on", "of", "and", "with", "for", "into"}

# Different apps/toolkits label the same concept differently ("New Folder"
# vs "Create Directory", "Delete" vs "Remove", "Search" vs "Find"). Each
# group below is treated as interchangeable during scoring — mitigates
# Problem 5 (naming differences) from TASK_KNOWLEDGE_BASE.md Part 9, though
# it can never cover every possible label an app might use.
_SYNONYM_GROUPS: list[set[str]] = [
    {"new", "create", "add"},
    {"folder", "directory"},
    {"delete", "remove", "trash", "discard"},
    {"close", "exit", "quit", "dismiss"},
    {"search", "find", "lookup"},
    {"open", "launch", "load"},
    {"save", "export"},
    {"rename", "edit"},
    {"settings", "preferences", "config"},
    {"ok", "confirm", "apply", "done", "accept"},
    {"cancel", "abort"},
    {"back", "previous", "prev"},
    {"next", "continue", "forward"},
    {"menu", "options", "more"},
    {"type", "write", "enter", "input"},
    {"select", "choose", "pick"},
    {"copy", "duplicate"},
    {"refresh", "reload", "sync"},
    {"help", "about"},
    {"pause", "stop"},
]
# Build word -> synonym-set lookup. Uses union-merge (not plain overwrite)
# so a word appearing in two groups keeps synonyms from BOTH — a plain
# dict overwrite would silently drop one group's synonyms for any shared
# word (found and fixed during this review; no such collision remains in
# the list above, but the merge is defensive against future additions).
_SYNONYM_OF: dict[str, set[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYM_OF.setdefault(_word, set()).update(_group)


@dataclass
class NavResult:
    """Outcome of a navigator operation — always returned, never raises."""
    ok: bool
    node: object | None = None
    app: object | None = None
    reason: str = ""
    candidates_considered: int = 0


# ── Tree walking ─────────────────────────────────────────────────────────────

def _walk(node: object, depth: int = 0) -> Iterator[object]:
    """Depth-first walk of an AT-SPI subtree, yielding every node."""
    if node is None or depth > _MAX_TREE_DEPTH:
        return
    yield node
    try:
        child_count = node.childCount  # type: ignore[union-attr]
    except Exception:
        return
    for i in range(child_count):
        try:
            child = node.getChildAtIndex(i)  # type: ignore[union-attr]
        except Exception:
            continue
        yield from _walk(child, depth + 1)


def _role(node: object) -> str:
    try:
        return (node.getRoleName() or "").lower()  # type: ignore[union-attr]
    except Exception:
        return ""


def _name(node: object) -> str:
    try:
        return (node.name or "").lower()  # type: ignore[union-attr]
    except Exception:
        return ""


# ── Waiting for an app to appear ─────────────────────────────────────────────

def wait_for_app(app_frags: list[str], timeout: float = _DEFAULT_TIMEOUT) -> object | None:
    """
    Poll the AT-SPI desktop every _POLL_INTERVAL seconds until an app whose
    name contains any of `app_frags` appears. Returns the app node, or None
    on timeout. This is the same bounded-poll shape as atspi_install.py's
    `_await_node()` and atspi_utils.py's `wait_for_app()` — reused/aligned
    here rather than re-invented.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                if any(frag in (app.name or "").lower() for frag in app_frags):
                    return app
        except Exception:
            pass
        time.sleep(_POLL_INTERVAL)
    return None


def wait_for_node(
    app_frags: list[str],
    role_filter: set[str] | None = None,
    name_contains: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[object | None, object | None]:
    """
    Generalized version of atspi_install.py's `_await_node()`: poll until an
    app matching `app_frags` exists AND (if role_filter/name_contains given)
    has a matching descendant node. Returns (app, node) — node is the app
    root itself if no filters were given. Returns (None, None) on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                if not any(frag in (app.name or "").lower() for frag in app_frags):
                    continue
                if role_filter is None and name_contains is None:
                    return app, app
                for node in _walk(app):
                    if role_filter is not None and _role(node) not in role_filter:
                        continue
                    if name_contains is not None and name_contains.lower() not in _name(node):
                        continue
                    return app, node
        except Exception:
            pass
        time.sleep(_POLL_INTERVAL)
    return None, None


# ── Scoring: matching a plain-language phrase against tree nodes ────────────

# Pure-symbol button labels (calculator operators, close-icon "x", etc.)
# have NO letters/digits at all, so word-overlap scoring can never match
# them on its own — found live testing gnome-calculator's "+"/"×"/"÷"
# buttons. This maps the common ones to a matchable word.
_SYMBOL_ALIASES: dict[str, str] = {
    "+": "plus", "-": "minus", "−": "minus", "×": "multiply", "*": "multiply",
    "÷": "divide", "/": "divide", "=": "equals", "%": "percent", "√": "sqrt",
}


def _keywords(phrase: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    # NOTE: length filter is >=1, not >=2 — a >=2 filter silently dropped
    # single-digit search phrases like "5" (confirmed live against
    # gnome-calculator's digit buttons, which are genuinely single chars).
    return {w for w in words if w not in _STOPWORDS and len(w) >= 1}


def _expand_synonyms(keywords: set[str]) -> set[str]:
    """Add known synonyms of each keyword (see _SYNONYM_GROUPS above)."""
    expanded = set(keywords)
    for kw in keywords:
        expanded |= _SYNONYM_OF.get(kw, set())
    return expanded


def _score_node(node: object, keywords: set[str]) -> float:
    """
    Score a node's relevance to `keywords` (higher = better match).
    Pure keyword-overlap heuristic — same spirit as smart_parser.py's
    intent scoring, applied to AT-SPI node names instead of user input.
    Synonym-expanded so "create new folder" also matches a button literally
    labelled "Add Directory" (see _SYNONYM_GROUPS) — mitigation, not a
    guarantee, since not every possible label variant is covered.
    """
    text = _name(node)
    if not text or not keywords:
        return 0.0
    node_words = set(re.findall(r"[a-z0-9]+", text))
    alias = _SYMBOL_ALIASES.get(text.strip())
    if alias:
        node_words.add(alias)
    expanded_keywords = _expand_synonyms(keywords)
    overlap = len(expanded_keywords & node_words)
    if overlap == 0:
        # Substring fallback for compound labels e.g. "New Folder", "Save As"
        overlap = sum(1 for kw in expanded_keywords if len(kw) >= 3 and kw in text)
    if overlap == 0:
        return 0.0
    # Score against the ORIGINAL keyword count (not the expanded set) so a
    # synonym match doesn't outscore a direct match on a shorter phrase.
    score = overlap / max(len(keywords), 1)
    if _role(node) in CLICKABLE_ROLES:
        score += 0.25  # prefer genuinely actionable elements over static labels
    return score


def _has_action(node: object) -> bool:
    """
    Return True if `node` exposes at least one AT-SPI action, i.e. can
    actually be triggered. Real GTK apps commonly expose duplicate/decoy
    accessible nodes with the same name and a "clickable" role but ZERO
    actions (confirmed live against Nautilus's "main menu" button, which
    has a push-button decoy alongside the real toggle-button control) —
    without this check, scoring can pick the decoy purely on name overlap.
    """
    try:
        return node.queryAction().nActions > 0  # type: ignore[union-attr]
    except Exception:
        return False


def find_best_match(
    app: object,
    phrase: str,
    role_filter: set[str] | None = None,
    require_actionable: bool = False,
) -> tuple[object | None, int]:
    """
    Walk `app`'s AT-SPI tree and return the node whose name best matches
    `phrase` (optionally restricted to roles in `role_filter`), plus the
    number of candidate nodes considered (0 score included) for diagnostics.
    Returns (None, count) if nothing scored above zero.

    If `require_actionable` is True, nodes with zero AT-SPI actions are
    skipped entirely rather than scored — use this whenever the caller's
    next step is do_action() (see `_has_action`'s docstring for why).
    """
    keywords = _keywords(phrase)
    if not keywords:
        return None, 0
    best_node, best_score, considered = None, 0.0, 0
    for node in _walk(app):
        if role_filter is not None and _role(node) not in role_filter:
            continue
        if require_actionable and not _has_action(node):
            continue
        considered += 1
        score = _score_node(node, keywords)
        if score > best_score:
            best_node, best_score = node, score
    return (best_node if best_score > 0 else None), considered


# ── Best-effort focus (Problem 2 mitigation, NOT a full solution) ───────────

def _try_focus_app(app: object, timeout: float = 1.0) -> bool:
    """
    Best-effort attempt to bring `app`'s top-level frame to the foreground
    before interacting with it. Some GTK popovers/menus only render on a
    genuinely focused window (confirmed live — see TASK_KNOWLEDGE_BASE.md
    Part 9's Nautilus test) — trying this first can only help, never hurt,
    even though grabFocus() is known to fail in some sandboxed/headless
    display setups. Failure here is silent-and-continue, not fatal: the
    caller proceeds to search/click regardless, since plenty of actions
    (direct AT-SPI actions on already-visible controls) work fine even
    when focus couldn't be confirmed.
    """
    try:
        for i in range(app.childCount):  # type: ignore[union-attr]
            frame = app.getChildAtIndex(i)  # type: ignore[union-attr]
            if frame is None:
                continue
            if _role(frame) not in ("frame", "dialog", "window", "alert"):
                continue
            try:
                frame.queryComponent().grabFocus()  # type: ignore[union-attr]
            except Exception:
                return False
            import pyatspi
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if frame.getState().contains(pyatspi.STATE_ACTIVE):  # type: ignore[union-attr]
                        return True
                except Exception:
                    return False
                time.sleep(0.05)
            return False
    except Exception:
        pass
    return False


# ── High-level actions ───────────────────────────────────────────────────────

def click_by_intent(
    app_frags: list[str],
    phrase: str,
    app_timeout: float = _DEFAULT_TIMEOUT,
) -> NavResult:
    """
    Find an already-running app matching `app_frags`, find the clickable
    element whose name best matches `phrase`, and click it.

    Caller must launch the app first (e.g. tasks/open_system_app.py) —
    this only navigates within an app that's already on screen. Makes a
    best-effort focus attempt first (see `_try_focus_app`) since some
    menus/popovers need real focus to render — this is a mitigation, not
    a guarantee, per TASK_KNOWLEDGE_BASE.md Part 9's Problem 2.
    """
    app = wait_for_app(app_frags, timeout=app_timeout)
    if app is None:
        return NavResult(False, reason=f"app not found within {app_timeout}s: {app_frags}")

    _try_focus_app(app)  # best-effort; deliberately not checked for success

    node, considered = find_best_match(
        app, phrase, role_filter=CLICKABLE_ROLES, require_actionable=True,
    )
    if node is None:
        return NavResult(
            False, app=app, candidates_considered=considered,
            reason=f"no clickable element matched {phrase!r} ({considered} candidates checked)",
        )

    if do_action(node):
        logger.info(f"atspi_navigator: clicked {_name(node)!r} for intent {phrase!r}")
        return NavResult(True, node=node, app=app, candidates_considered=considered)
    return NavResult(
        False, node=node, app=app, candidates_considered=considered,
        reason="matched element found but do_action failed",
    )


def type_by_intent(
    app_frags: list[str],
    phrase: str,
    text: str,
    app_timeout: float = _DEFAULT_TIMEOUT,
) -> NavResult:
    """
    Find the text-entry field whose name/placeholder best matches `phrase`
    within an already-running app, and type `text` into it. Falls back to
    the first text-entry field found if no name match scores above zero
    (many dialogs have exactly one input with an unhelpful/blank label).
    """
    app = wait_for_app(app_frags, timeout=app_timeout)
    if app is None:
        return NavResult(False, reason=f"app not found within {app_timeout}s: {app_frags}")

    _try_focus_app(app)  # best-effort; deliberately not checked for success

    node, considered = find_best_match(app, phrase, role_filter=TEXT_ENTRY_ROLES)
    if node is None:
        for candidate in _walk(app):
            if _role(candidate) in TEXT_ENTRY_ROLES:
                node = candidate
                break

    if node is None:
        return NavResult(
            False, app=app, candidates_considered=considered,
            reason=f"no text field matched {phrase!r} and none found as fallback",
        )

    if set_text(node, text):
        logger.info(f"atspi_navigator: typed into {_name(node)!r}")
        return NavResult(True, node=node, app=app, candidates_considered=considered)
    return NavResult(
        False, node=node, app=app, candidates_considered=considered,
        reason="matched field found but set_text failed",
    )


def wait_for_dialog(app_frags: list[str], timeout: float = _DEFAULT_TIMEOUT) -> tuple[object | None, object | None]:
    """Convenience wrapper: wait for a dialog/alert to appear within an app."""
    return wait_for_node(app_frags, role_filter=DIALOG_ROLES, timeout=timeout)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 -m core.atspi_navigator <app_fragment> <intent phrase>")
        print('Example: python3 -m core.atspi_navigator nautilus "create new folder"')
        sys.exit(1)
    result = click_by_intent([sys.argv[1]], " ".join(sys.argv[2:]), app_timeout=8.0)
    print(result)

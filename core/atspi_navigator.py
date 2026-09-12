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
from typing import Any

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
READABLE_ROLES = TEXT_ENTRY_ROLES | {
    "label", "static", "status bar", "table cell", "list item", "heading",
    "paragraph", "document text",
}
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
    {"clear", "reset", "c", "ac"},
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
    value: str = ""


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
        except Exception as child_err:
            logger.debug(f"_walk: child {i} unreadable: {child_err}")
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


def _text_content(node: object) -> str:
    """Return live accessible text for unnamed displays and document nodes."""
    try:
        dynamic_node: Any = node
        text = dynamic_node.queryText()
        return str(text.getText(0, text.characterCount) or "").strip().lower()
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
    # Normalize away spaces/hyphens/underscores before comparing: the AT-SPI
    # app.name for a running process is very often its hyphenated binary
    # name (e.g. "gnome-text-editor"), while callers naturally pass the
    # human .desktop Name= ("text editor", with a space) -- a naive
    # substring check between those never matches. Confirmed live: opening
    # "Text Editor" and calling wait_for_app(["text editor"]) returned None
    # even though the app was genuinely running and visible in AT-SPI as
    # 'gnome-text-editor', silently breaking type_text.py's app-targeted
    # focus step (it fell back to typing into whatever else had focus).
    def _norm(s: str) -> str:
        return s.lower().replace(" ", "").replace("-", "").replace("_", "")

    norm_frags = [_norm(f) for f in app_frags]
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                norm_name = _norm(app.name or "")
                if any(frag in norm_name for frag in norm_frags):
                    return app
        except Exception as poll_err:
            logger.debug(f"wait_for_app: AT-SPI poll failed: {poll_err}")
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
                    if name_contains is not None:
                        searchable = f"{_name(node)} {_text_content(node)}".strip()
                        if name_contains.lower() not in searchable:
                            continue
                    return app, node
        except Exception as poll_err:
            logger.debug(f"wait_for_node: AT-SPI poll failed: {poll_err}")
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
    text = f"{_name(node)} {_text_content(node)}".strip()
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
        # Keep all Wayland focus mechanics in one place.  The window task
        # refreshes stale AT-SPI frame objects, uses safe frame activation,
        # and never treats a dialog's default button as a focus control.
        from tasks.window_management import _focus_frame

        for i in range(app.childCount):  # type: ignore[union-attr]
            frame = app.getChildAtIndex(i)  # type: ignore[union-attr]
            if frame is None or _role(frame) not in ("frame", "dialog", "window", "alert"):
                continue
            if _focus_frame(frame, timeout=timeout):
                return True
    except Exception as exc:
        logger.debug(f"AT-SPI application focus failed: {exc}")
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


def read_by_intent(
    app_frags: list[str],
    phrase: str,
    app_timeout: float = _DEFAULT_TIMEOUT,
) -> NavResult:
    """Read a specifically matched accessible value without changing UI state."""
    app = wait_for_app(app_frags, timeout=app_timeout)
    if app is None:
        return NavResult(False, reason=f"app not found within {app_timeout}s: {app_frags}")
    node, considered = find_best_match(app, phrase, role_filter=READABLE_ROLES)
    if node is None:
        return NavResult(
            False,
            app=app,
            candidates_considered=considered,
            reason=f"no readable element matched {phrase!r}",
        )
    value = ""
    try:
        dynamic_node: Any = node
        text = dynamic_node.queryText()
        value = text.getText(0, text.characterCount).strip()
    except Exception:
        try:
            value = str(node.name or "").strip()  # type: ignore[union-attr]
        except Exception:
            value = ""
    if not value:
        return NavResult(
            False,
            node=node,
            app=app,
            candidates_considered=considered,
            reason=f"matched readable element for {phrase!r} had no value",
        )
    return NavResult(
        True,
        node=node,
        app=app,
        candidates_considered=considered,
        value=value,
    )


def wait_for_dialog(app_frags: list[str], timeout: float = _DEFAULT_TIMEOUT) -> tuple[object | None, object | None]:
    """Convenience wrapper: wait for a dialog/alert to appear within an app."""
    return wait_for_node(app_frags, role_filter=DIALOG_ROLES, timeout=timeout)


# ── ATSPINavigator — object API over the helpers above ─────────────────────

class ATSPIElement:
    """Stable wrapper over a pyatspi node: never raises on property access.

    Capabilities access `.name`, `.role` and `.extents` (with `.x/.y/.width/`
    `.height` in screen coordinates), so defunct/remote nodes must degrade
    to empty values instead of raising.
    """

    __slots__ = ("_node",)

    def __init__(self, node: object) -> None:
        self._node = node

    @property
    def name(self) -> str:
        try:
            return str(self._node.name or "")  # type: ignore[union-attr]
        except Exception:
            return ""

    @property
    def role(self) -> str:
        return _role(self._node)

    @property
    def extents(self) -> object | None:
        """Screen-coordinate bounding box, or None if unavailable."""
        try:
            component = self._node.queryComponent()  # type: ignore[union-attr]
            box = component.getExtents(1)  # 1 == XY_SCREEN in pyatspi
            # Normalise to a plain object with x/y/width/height so callers
            # can use getattr without knowing pyatspi's BoundingBox type.
            return type("Extents", (), {
                "x": int(box.x), "y": int(box.y),
                "width": int(box.width), "height": int(box.height),
            })()
        except Exception:
            return None

    def text(self) -> str:
        """Live accessible text content (queryText), may be empty."""
        return _text_content(self._node)

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"ATSPIElement(name={self.name!r}, role={self.role!r})"


class ATSPINavigator:
    """Read/query side of the AT-SPI tree for the capability layer.

    Writing (click/type) goes through click_by_intent()/type_by_intent()
    above; this class covers the observation API the capabilities expect:
    find_elements, find_element, get_all_text, dump_visible_text,
    read_field_value, get_desktop_summary.

    Every method degrades gracefully: on AT-SPI unavailability or a defunct
    node they return empty results rather than raising, so capability
    wrappers can report "not found" instead of crashing.
    """

    def __init__(self, app_timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.app_timeout = app_timeout

    # ── app iteration ─────────────────────────────────────────────────────

    def _iter_apps(self, app_name: str | None) -> list[object]:
        """All desktop apps, or those whose normalised name contains
        `app_name` (same normalisation as wait_for_app)."""
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            apps = [a for a in desktop if a is not None]
        except Exception as exc:
            logger.debug(f"ATSPINavigator: desktop unavailable: {exc}")
            return []
        if not app_name:
            return apps
        needle = app_name.lower().replace(" ", "").replace("-", "").replace("_", "")
        matched = []
        for app in apps:
            hay = str(app.name or "").lower().replace(" ", "").replace("-", "").replace("_", "")
            if needle and needle in hay:
                matched.append(app)
        return matched

    # ── search API ────────────────────────────────────────────────────────

    def find_elements(
        self,
        name: str | None = None,
        role: str | None = None,
        app_name: str | None = None,
        limit: int = 50,
    ) -> list[ATSPIElement]:
        """Elements whose accessible name contains `name` (case-insensitive)
        and whose role equals `role`, across all apps or one app."""
        results: list[ATSPIElement] = []
        needle = (name or "").lower()
        for app in self._iter_apps(app_name):
            for node in _walk(app):
                if needle and needle not in _name(node):
                    continue
                if role and _role(node) != role.lower():
                    continue
                results.append(ATSPIElement(node))
                if len(results) >= limit:
                    return results
        return results

    def find_element(self, app_name: str | None = None, text: str | None = None) -> ATSPIElement | None:
        """First element whose name OR live text content contains `text`."""
        if not text:
            return None
        needle = text.lower()
        for app in self._iter_apps(app_name):
            for node in _walk(app):
                if needle in _name(node) or needle in _text_content(node):
                    return ATSPIElement(node)
        return None

    # ── text dump API ────────────────────────────────────────────────────

    def get_all_text(self, app_name: str | None = None) -> list[str]:
        """Readable text values across the desktop (or one app)."""
        texts: list[str] = []
        for app in self._iter_apps(app_name):
            for node in _walk(app):
                if _role(node) not in READABLE_ROLES:
                    continue
                value = _text_content(node) or _name(node)
                if value:
                    texts.append(value)
        return texts

    def dump_visible_text(self, app_name: str | None = None) -> str | None:
        """Joined readable text, or None when nothing was readable.

        None (not "") signals "text dump unavailable" so verification
        callers can distinguish a genuinely empty screen from a failure."""
        texts = self.get_all_text(app_name=app_name)
        return "\n".join(texts) if texts else None

    def read_field_value(self, app_name: str | None = None, field_name: str | None = None) -> str | None:
        """Value of the text-entry field best matching `field_name`."""
        apps = self._iter_apps(app_name)
        if not apps:
            return None
        # Prefer a named match, fall back to the first entry field.
        fallback: object | None = None
        for app in apps:
            for node in _walk(app):
                if _role(node) not in TEXT_ENTRY_ROLES:
                    continue
                if fallback is None:
                    fallback = node
                if field_name and field_name.lower() in _name(node):
                    value = _text_content(node)
                    if value:
                        return value
        if fallback is not None:
            value = _text_content(fallback)
            return value or None
        return None

    # ── desktop summary API ──────────────────────────────────────────────

    def get_desktop_summary(self, per_app_node_limit: int = 40) -> dict[str, Any]:
        """Bounded per-app summary: app name, frame count, and a sample of
        element names/roles — enough to reason about the screen without
        dumping the entire tree."""
        summary: dict[str, Any] = {"apps": []}
        for app in self._iter_apps(None):
            app_entry: dict[str, Any] = {
                "name": str(app.name or ""),
                "elements": [],
            }
            count = 0
            for node in _walk(app):
                count += 1
                if len(app_entry["elements"]) < per_app_node_limit:
                    role = _role(node)
                    name = _name(node)
                    if role or name:
                        app_entry["elements"].append({"role": role, "name": name[:80]})
            app_entry["node_count"] = count
            summary["apps"].append(app_entry)
        summary["app_count"] = len(summary["apps"])
        return summary

    # ── active window (Wayland-safe) ─────────────────────────────────────

    def get_active_window(self) -> dict[str, Any] | None:
        """Best-effort active window via AT-SPI ACTIVE/FOCUSED frame state.

        xdotool's _NET_ACTIVE_WINDOW query fails on native Wayland (it can
        only see XWayland clients), so this walks the desktop for a frame/
        window/dialog node holding the ACTIVE or FOCUSED state."""
        try:
            import pyatspi
            state_active = pyatspi.STATE_ACTIVE
            state_focused = pyatspi.STATE_FOCUSED
        except Exception:
            return None
        best: dict[str, Any] | None = None
        for app in self._iter_apps(None):
            for node in _walk(app):
                if _role(node) not in ("frame", "window", "dialog", "alert"):
                    continue
                try:
                    # pyatspi API is getState().contains() — getStateSet()
                    # does not exist on Atspi.Accessible.
                    states = node.getState()  # type: ignore[union-attr]
                    if states.contains(state_active):
                        return {"app": str(app.name or ""), "title": _name(node), "state": "active"}
                    if states.contains(state_focused) and best is None:
                        best = {"app": str(app.name or ""), "title": _name(node), "state": "focused"}
                except Exception as state_err:
                    logger.debug(f"get_active_window: state query failed: {state_err}")
                    continue
        return best


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 -m core.atspi_navigator <app_fragment> <intent phrase>")
        print('Example: python3 -m core.atspi_navigator nautilus "create new folder"')
        sys.exit(1)
    result = click_by_intent([sys.argv[1]], " ".join(sys.argv[2:]), app_timeout=8.0)
    print(result)

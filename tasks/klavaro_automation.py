#!/usr/bin/env python3
"""
Klavaro Velocity Typing Test Automation.

Strategy:
  1. Read exercise text via AT-SPI (direct from Klavaro's GTK widget memory)
  2. Sanity-check the extracted text before touching the keyboard
  3. Focus the input field via AT-SPI (coordinate-free, Wayland-safe)
  4. Type EXACTLY what AT-SPI returned — including punctuation — at 1800 CPM
  5. Screenshot the results

AT-SPI is the ground truth: it reads what Klavaro has in its GtkTextBuffer,
which is literally what the screen shows. No OCR, no coordinate guessing.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

import evdev
from loguru import logger

from core.uinput_keyboard import VirtualKeyboard
from core.wait_until import wait_until

sys.path.insert(0, "/usr/lib/python3/dist-packages")

_klavaro_proc = None

# Minimum words per paragraph to consider the extraction valid
_MIN_WORDS = 5
# Minimum ratio of real English-looking tokens to total tokens
_MIN_REAL_WORD_RATIO = 0.60


# ═══════════════════════════════════════════════════════════════════════════
# LAUNCH HELPERS
# ═══════════════════════════════════════════════════════════════════════════

# Klavaro exercise index → (pref value, keyboard key, label)
# Indices are 0-based in prefs file; key = UI number key to press
# Confirmed key mapping (probed 2026-08-31):
#   KEY_1 → Basic Course
#   KEY_2 → Adaptability   (nonsense words, mixed case + symbols, ¶ markers)
#   KEY_3 → Velocity       (real words, ¶ markers)
#   KEY_4 → Fluidness      (sentences, press-key-to-start)
EXERCISE_MAP = {
    "basic":        (0, evdev.ecodes.KEY_1, "Basic Course",  False),
    "adaptability": (1, evdev.ecodes.KEY_2, "Adaptability",  True),   # needs ENTER to start
    "velocity":     (2, evdev.ecodes.KEY_3, "Velocity",      False),
    "fluidness":    (3, evdev.ecodes.KEY_4, "Fluidness",     True),   # needs ENTER to start
}


def _set_exercise_mode(mode: str = "adaptability") -> tuple[int, bool]:
    """Write prefs and return (nav_keycode, needs_enter_to_start)."""
    entry     = EXERCISE_MAP.get(mode, EXERCISE_MAP["adaptability"])
    pref_val, keycode, label, needs_enter = entry
    pref_dir  = Path.home() / ".config" / "klavaro"
    pref_dir.mkdir(parents=True, exist_ok=True)
    pref_file = pref_dir / "preferences.ini"
    if pref_file.exists():
        lines, found = [], False
        for line in pref_file.read_text().splitlines():
            if line.startswith("exercise="):
                lines.append(f"exercise={pref_val}"); found = True
            elif line.startswith("speech="):
                lines.append("speech=false")
            else:
                lines.append(line)
        if not found:
            lines.append(f"exercise={pref_val}")
        pref_file.write_text("\n".join(lines) + "\n")
    else:
        pref_file.write_text(f"[Preferences]\nexercise={pref_val}\nspeech=false\n")
    logger.info(f"⚙️  Exercise mode: {label} (pref={pref_val}, needs_enter={needs_enter})")
    return keycode, needs_enter


def _launch_detached() -> subprocess.Popen:
    import shutil
    prefix = (
        ["env", "GDK_BACKEND=x11", "GTK_MODULES=atk-bridge", "NO_AT_BRIDGE=0"]
        if shutil.which("xdotool") else []
    )
    proc = subprocess.Popen(
        prefix + ["klavaro"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
    )
    logger.info(f"Klavaro launched — PID {proc.pid}")
    return proc


def _focus_window() -> None:
    subprocess.run(["wmctrl", "-a", "Klavaro"], capture_output=True, check=False)
    # Poll for AT-SPI STATE_ACTIVE instead of blindly sleeping a fixed 0.3s —
    # same pattern as window_management.py's _focus_frame(). Timeout is more
    # generous than the original so slow window managers still get enough time.
    wait_until(_frame_active, timeout=1.5, interval=0.1)


# ═══════════════════════════════════════════════════════════════════════════
# AT-SPI — TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

def _klavaro_app():
    """Return the Klavaro AT-SPI application node, or None."""
    try:
        import pyatspi
        for app in pyatspi.Registry.getDesktop(0):
            if app and "klavaro" in (app.name or "").lower():
                return app
    except Exception as exc:
        logger.debug(f"AT-SPI desktop query failed: {exc}")
    return None


def _walk(node, depth: int = 0):
    if depth > 15:
        return
    yield depth, node
    try:
        for i in range(node.childCount):
            yield from _walk(node.getChildAtIndex(i), depth + 1)
    except Exception as exc:
        logger.debug(f"AT-SPI walk error: {exc}")


def _read_exercise_text(timeout: float = 12.0) -> str:
    """
    Read the full exercise string from Klavaro's GtkTextView via AT-SPI.

    The exercise text lives in a [text] node whose parent is a [scroll pane].
    It contains ¶ paragraph markers (e.g. "word word word.¶\\nword...").

    Returns the raw string exactly as Klavaro stores it, or "" on failure.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        app = _klavaro_app()
        if app:
            for _, node in _walk(app):
                try:
                    if node.getRoleName() != "text":
                        continue
                    # Must be inside a scroll pane (the exercise display widget)
                    if (node.parent or object()).getRoleName() != "scroll pane":
                        continue
                    t    = node.queryText()
                    text = t.getText(0, t.characterCount)
                    if "¶" in text and len(text.strip()) > 30:
                        return text
                except Exception as exc:
                    logger.debug(f"Text node query failed: {exc}")
        time.sleep(0.4)
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# SANITY CHECK  — decide whether extracted text is safe to type
# ═══════════════════════════════════════════════════════════════════════════

def _is_real_word(token: str) -> bool:
    """
    True if the token is a typeable word.
    Accepts: English words, Adaptability nonsense words (mixed case + symbols),
    and anything that contains at least one letter.
    Rejects: pure whitespace, pure numbers, empty strings.
    """
    return bool(token) and any(c.isalpha() for c in token)


def _sanity_check(paras: list[str]) -> tuple[bool, str]:
    """
    Verify extracted paragraphs are safe to type.
    Returns (ok, reason).
    """
    if not paras:
        return False, "no paragraphs extracted"

    total_words = sum(len(p.split()) for p in paras)
    if total_words < _MIN_WORDS:
        return False, f"too few words ({total_words})"

    all_tokens = [t for p in paras for t in p.split()]
    real = sum(1 for t in all_tokens if _is_real_word(t))
    ratio = real / len(all_tokens) if all_tokens else 0.0

    if ratio < _MIN_REAL_WORD_RATIO:
        return False, (
            f"too many non-word tokens "
            f"({real}/{len(all_tokens)} = {ratio:.0%} real words — "
            f"expected ≥{_MIN_REAL_WORD_RATIO:.0%})"
        )

    return True, "ok"


def _split_paragraphs(raw: str) -> list[str]:
    """
    Split raw AT-SPI text on ¶ markers.
    Preserves ALL punctuation verbatim (Klavaro expects exact match).
    Works for both Velocity (real words) and Adaptability (nonsense words).
    """
    parts = re.split(r"¶\n?", raw)
    return [p.strip() for p in parts if p.strip() and len(p.split()) >= 2]


# ═══════════════════════════════════════════════════════════════════════════
# AT-SPI — INPUT FIELD FOCUS
# ═══════════════════════════════════════════════════════════════════════════

def _focus_input(timeout: float = 6.0) -> bool:
    """
    Focus the Klavaro typing input box via AT-SPI grabFocus().
    The input [text] node is identified by NOT being inside a scroll pane.
    Coordinate-free — immune to Wayland/X11 offset issues.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        app = _klavaro_app()
        if app:
            for _, node in _walk(app):
                try:
                    if node.getRoleName() != "text":
                        continue
                    if (node.parent or object()).getRoleName() == "scroll pane":
                        continue          # skip exercise display; want input box
                    node.queryComponent().grabFocus()
                    return True
                except Exception as exc:
                    logger.debug(f"grabFocus failed: {exc}")
        time.sleep(0.3)
    return False


# ═══════════════════════════════════════════════════════════════════════════
# AT-SPI — READINESS POLLS
#
# These back the wait_until() calls in execute(), replacing what used to be
# a stack of blind time.sleep(N) guesses. Each one checks one real, narrow
# AT-SPI condition — same structural-disambiguation technique already used
# by _read_exercise_text/_focus_input (identify nodes by role + parent role,
# not by name).
# ═══════════════════════════════════════════════════════════════════════════

def _frame_active() -> bool:
    """
    True once Klavaro's top-level frame reports AT-SPI STATE_ACTIVE.
    Same technique as window_management.py's _active_window/_has_state —
    a real post-focus signal instead of guessing a fixed settle time.
    """
    try:
        import pyatspi
        app = _klavaro_app()
        if not app:
            return False
        for i in range(app.childCount):
            frame = app.getChildAtIndex(i)
            if frame is None:
                continue
            if frame.getRoleName() not in ("frame", "dialog", "window", "alert"):
                continue
            if frame.getState().contains(pyatspi.STATE_ACTIVE):
                return True
    except Exception as exc:
        logger.debug(f"AT-SPI active-frame check failed: {exc}")
    return False


def _atspi_tree_populated() -> bool:
    """
    True once Klavaro's AT-SPI tree exposes at least one [text] node.

    wmctrl seeing the window in the taskbar only means the window manager
    mapped a window — it says nothing about whether GTK has finished
    constructing widgets or whether the AT-SPI bridge has caught up. This
    checks the actual thing we're about to depend on.
    """
    app = _klavaro_app()
    if not app:
        return False
    for _, node in _walk(app):
        try:
            if node.getRoleName() == "text":
                return True
        except Exception:
            continue
    return False


def _scroll_pane_text() -> str:
    """
    Read whatever text currently sits in the exercise scroll-pane [text]
    node, with no length/¶ requirement.

    Deliberately looser than _read_exercise_text() (which requires ¶ + a
    minimum length before trusting the text enough to type it) — this is
    only used to detect "has the pane repainted with *something* new",
    a cheaper/earlier signal on the way to that stricter condition.
    """
    app = _klavaro_app()
    if not app:
        return ""
    for _, node in _walk(app):
        try:
            if node.getRoleName() != "text":
                continue
            if (node.parent or object()).getRoleName() != "scroll pane":
                continue
            t = node.queryText()
            return t.getText(0, t.characterCount)
        except Exception:
            continue
    return ""


def _input_text() -> str | None:
    """
    Read the current text content of Klavaro's typing input box — the
    [text] node NOT inside a scroll pane, same disambiguation _focus_input
    uses. Returns None if the node can't be found at all (e.g. a results
    dialog has replaced it), or "" if found but empty.
    """
    app = _klavaro_app()
    if not app:
        return None
    for _, node in _walk(app):
        try:
            if node.getRoleName() != "text":
                continue
            if (node.parent or object()).getRoleName() == "scroll pane":
                continue
            t = node.queryText()
            return t.getText(0, t.characterCount)
        except Exception:
            continue
    return None


def _input_focused() -> bool:
    """
    True once the input [text] node itself reports AT-SPI STATE_FOCUSED —
    confirms the grabFocus() call inside _focus_input() actually landed,
    instead of assuming success as soon as grabFocus() didn't raise.
    """
    try:
        import pyatspi
        app = _klavaro_app()
        if not app:
            return False
        for _, node in _walk(app):
            try:
                if node.getRoleName() != "text":
                    continue
                if (node.parent or object()).getRoleName() == "scroll pane":
                    continue
                return bool(node.getState().contains(pyatspi.STATE_FOCUSED))
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"AT-SPI focus check failed: {exc}")
    return False


def _dialog_visible() -> bool:
    """
    True if Klavaro has raised a top-level dialog/alert (its end-of-exercise
    results popup uses one) alongside the main window — same structural
    role-filtering technique window_management.py uses to enumerate windows.
    """
    app = _klavaro_app()
    if not app:
        return False
    try:
        for i in range(app.childCount):
            child = app.getChildAtIndex(i)
            if child is not None and child.getRoleName() in ("dialog", "alert"):
                return True
    except Exception as exc:
        logger.debug(f"AT-SPI dialog check failed: {exc}")
    return False


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EXECUTE
# ═══════════════════════════════════════════════════════════════════════════

def execute(args: dict[str, object], _resources: dict[str, object]) -> bool:
    """
    Keyboard automation for Klavaro exercise.

    Decision flow:
      ① Set exercise mode (default: adaptability)
      ② Launch Klavaro, navigate to chosen exercise
      ③ Read exercise text via AT-SPI
      ④ Sanity-check the text (real words, proper length)
         → FAIL → ABORT (never type garbage)
         → PASS → proceed
      ⑤ Focus input field via AT-SPI (coordinate-free)
      ⑥ Type EXACTLY what AT-SPI returned at 1800 CPM (no re-clicks)
      ⑦ Screenshot results
    """
    global _klavaro_proc

    mode  = str(args.get("exercise", "adaptability"))
    label = EXERCISE_MAP.get(mode, EXERCISE_MAP["adaptability"])[2]

    logger.info("=" * 60)
    logger.info(f"  KLAVARO AUTOMATION  —  {label}")
    logger.info("=" * 60)

    nav_key, needs_enter = _set_exercise_mode(mode)
    ss_dir  = Path(__file__).parent.parent / "logs" / "screenshots"
    ss_dir.mkdir(parents=True, exist_ok=True)

    vk = VirtualKeyboard()

    try:
        # ── ① Launch Klavaro ───────────────────────────────────────────────
        logger.info("STEP 1 ── Launching Klavaro…")
        _klavaro_proc = _launch_detached()

        for attempt in range(40):
            r = subprocess.run(["wmctrl", "-l"], capture_output=True,
                               text=True, check=False)
            if "klavaro" in r.stdout.lower():
                logger.info(f"         Window ready (attempt {attempt+1})")
                break
            time.sleep(0.5)
        else:
            logger.warning("         wmctrl did not see Klavaro — continuing anyway")

        # wmctrl seeing the window doesn't mean GTK/AT-SPI are ready yet —
        # poll for the AT-SPI tree to actually expose widgets. Timeout is
        # more generous than the original fixed 2.0s.
        wait_until(_atspi_tree_populated, timeout=4.0, interval=0.2)

        # ── Navigate to chosen exercise ─────────────────────────────────────────
        logger.info(f"STEP 2 ── Navigating to {label} exercise…")
        _focus_window()
        vk.press_key(nav_key, delay=0.08)
        # Poll for the exercise scroll-pane to show *something* new rather
        # than guessing how long the screen switch takes.
        wait_until(lambda: len(_scroll_pane_text().strip()) > 5, timeout=5.0, interval=0.25)

        if needs_enter:
            # Adaptability/Fluidness shows intro text first;
            # press Enter to dismiss and load the actual exercise text
            logger.info("         Pressing Enter to start exercise…")
            vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
            # Adaptability/Fluidness intro text has no ¶ marker; poll for the
            # real exercise text (which always does) to replace it.
            wait_until(lambda: "¶" in _scroll_pane_text(), timeout=4.0, interval=0.25)

        # "Final render wait": poll the exact ¶ + min-length condition
        # _read_exercise_text() itself requires below, instead of guessing —
        # STEP 3 succeeds immediately once this is already true.
        wait_until(
            lambda: "¶" in _scroll_pane_text() and len(_scroll_pane_text().strip()) > 30,
            timeout=3.0, interval=0.2,
        )

        # ── ② Read exercise text via AT-SPI ─────────────────────────────
        logger.info("STEP 3 ── Reading exercise text via AT-SPI…")
        raw_text = _read_exercise_text(timeout=10)

        if not raw_text:
            logger.error("         AT-SPI returned empty string — aborting")
            return False

        paras = _split_paragraphs(raw_text)

        # ── ③ Sanity-check extracted text ────────────────────────────────
        ok, reason = _sanity_check(paras)

        logger.info("STEP 4 ── Sanity check…")
        for i, p in enumerate(paras):
            words = p.split()
            real  = sum(1 for w in words if _is_real_word(w))
            logger.info(f"         P{i+1} ({len(words)} words, "
                        f"{real}/{len(words)} real): {p[:70]!r}")

        if not ok:
            logger.error(f"         SANITY CHECK FAILED: {reason}")
            logger.error("         Refusing to type — extracted text is not trustworthy.")
            return False

        logger.info(f"         ✅ Sanity check passed ({reason})")

        # ── ④ Focus input field ──────────────────────────────────────────
        logger.info("STEP 5 ── Focusing Klavaro input field via AT-SPI…")
        if not _focus_input(timeout=6):
            logger.error("         Could not focus input field — aborting")
            return False
        logger.info("         Input field focused ✅")
        # _focus_input() only checks that grabFocus() didn't raise; poll for
        # STATE_FOCUSED to confirm the focus genuinely landed before typing.
        wait_until(_input_focused, timeout=1.5, interval=0.1)

        # ── ⑤ Type exactly what AT-SPI returned ──────────────────────────
        # ONE focus at the start; no re-clicks between paragraphs.
        # Klavaro keeps the input field active after each Enter.
        logger.info("STEP 6 ── Typing at 1800 CPM…")
        t0 = time.time()

        for i, para in enumerate(paras):
            # No word cap — type the full paragraph exactly as AT-SPI gave it
            # (Velocity ~20 words, Adaptability ~22 words, Fluidness ~75+ words)
            text = para

            logger.info(f"         ▶ Para {i+1}/{len(paras)} "
                        f"({len(words)} words, {len(text)} chars): "
                        f"'{text[:60]}'")

            vk.type_text(text, cpm=1800)

            logger.info(f"           ✅ typed in {time.time()-t0:.1f}s total")

            # Enter after each paragraph (advances Klavaro to next paragraph)
            time.sleep(0.2)
            vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
            # Poll for the input box to go empty again — Klavaro clears it
            # once it has accepted the line and advanced. This is the most
            # reliable observable signal available (the exercise scroll-pane
            # text itself doesn't necessarily change per paragraph — Klavaro
            # tracks the current line internally rather than repainting).
            wait_until(lambda: (_input_text() or "") == "", timeout=3.0, interval=0.15)

        total = time.time() - t0
        logger.info(f"         ⚡ All {len(paras)} paragraphs typed in {total:.2f}s")

        # ── ⑥ Screenshot results ──────────────────────────────────────────
        logger.info("STEP 7 ── Capturing results screenshot…")
        # Poll for Klavaro's results dialog/alert to actually appear via
        # AT-SPI instead of guessing a fixed wait. Bounded generously since
        # dialog render time can vary with system load.
        wait_until(_dialog_visible, timeout=6.0, interval=0.3)
        _focus_window()
        # _focus_window() already polls for STATE_ACTIVE above, so focus
        # itself is confirmed. There's no further AT-SPI-observable signal
        # for "the compositor has finished painting the frame" though, so
        # this stays a small bounded blind wait as a safety margin before
        # the screenshot is taken.
        time.sleep(0.5)

        from core.logger import take_screenshot
        path = take_screenshot(name="klavaro_results")
        logger.info(f"         Screenshot: {path}")

        logger.info("=" * 60)
        logger.info("  DONE — Klavaro left open on screen")
        logger.info("=" * 60)
        return True

    except Exception as exc:
        import traceback
        logger.error(f"Automation failed: {exc}")
        logger.error(traceback.format_exc())
        return False

    finally:
        try:
            vk.close()
        except Exception as exc:
            logger.debug(f"VirtualKeyboard close (non-fatal): {exc}")


def setup() -> dict[str, object]:
    return {}


def cleanup(_resources: dict[str, object]) -> None:
    logger.info("Klavaro intentionally left open.")

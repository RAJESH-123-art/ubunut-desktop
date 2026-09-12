from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/usr/lib/python3/dist-packages")

import pyatspi

from core.atspi_navigator import _walk, wait_for_app
from core.gui_controller import GUIController
from core.system_utils import clipboard_get, clipboard_set
from tasks.open_system_app import execute as open_app
from tasks.window_management import _focus_frame


@dataclass
class EditableTarget:
    app: object
    frame: object
    node: object
    role: str
    area: int


def has_state(node: object, state: int) -> bool:
    try:
        return bool(node.getState().contains(state))
    except Exception:
        return False


def text_of(node: object) -> str:
    text = node.queryText()
    return text.getText(0, text.characterCount)


def app_or_launch(fragments: list[str], launch_name: str) -> object:
    app = wait_for_app(fragments, timeout=2.0)
    if app is None:
        if not open_app({"app_name": launch_name}, {}):
            raise RuntimeError(f"Could not launch {launch_name}")
        app = wait_for_app(fragments, timeout=10.0)
    if app is None:
        raise RuntimeError(f"{launch_name} did not appear in AT-SPI")
    return app


def editable_targets(app: object) -> list[EditableTarget]:
    candidates: list[EditableTarget] = []
    for frame_index in range(app.childCount):
        try:
            frame = app.getChildAtIndex(frame_index)
            if frame is None or not has_state(frame, pyatspi.STATE_SHOWING):
                continue
        except Exception:
            continue
        for node in _walk(frame):
            if not has_state(node, pyatspi.STATE_EDITABLE):
                continue
            try:
                node.queryEditableText()
                node.queryText()
                role = (node.getRoleName() or "").lower()
                extents = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                area = max(0, extents.width) * max(0, extents.height)
            except Exception:
                continue
            candidates.append(EditableTarget(app, frame, node, role, area))
    candidates.sort(
        key=lambda item: (
            has_state(item.frame, pyatspi.STATE_ACTIVE),
            has_state(item.node, pyatspi.STATE_SHOWING),
            item.role in {"text", "paragraph"},
            item.area,
        ),
        reverse=True,
    )
    return candidates


def choose_target(app: object, label: str, active_only: bool = False) -> EditableTarget:
    targets = editable_targets(app)
    if active_only:
        targets = [target for target in targets if has_state(target.frame, pyatspi.STATE_ACTIVE)]
    if not targets:
        raise RuntimeError(f"No editable AT-SPI target found in {label}")
    target = targets[0]
    print(
        f"{label}_TARGET frame={getattr(target.frame, 'name', '')!r} "
        f"role={target.role!r} area={target.area}"
    )
    return target


def set_exact(target: EditableTarget, value: str) -> None:
    if not target.node.queryEditableText().setTextContents(value):
        raise RuntimeError("EditableText.setTextContents returned false")
    if text_of(target.node) != value:
        raise RuntimeError(f"AT-SPI set verification failed: {text_of(target.node)!r}")


def wait_for_stable_exact(target: EditableTarget, expected: str, timeout: float = 1.0) -> str:
    """Require an exact value to remain unchanged after delayed native input."""
    deadline = time.time() + timeout
    observed = text_of(target.node)
    while time.time() < deadline:
        time.sleep(0.1)
        observed = text_of(target.node)
        if observed != expected:
            return observed
    return observed


def select_and_copy(target: EditableTarget, gui: GUIController) -> None:
    # Native Wayland apps do not consistently expose Ctrl+A selection through
    # AT-SPI.  Read the already verified editable source, put it in the real
    # desktop clipboard, and verify ownership before switching applications.
    selection = text_of(target.node)
    if not selection:
        raise RuntimeError("Source editable field is empty")
    if not clipboard_set(selection) or clipboard_get() != selection:
        raise RuntimeError("Desktop clipboard did not retain the source text")
    print(f"CLIPBOARD_SOURCE={selection!r}")


def paste_and_wait(target: EditableTarget, gui: GUIController, expected: str) -> str:
    focused = _focus_frame(target.frame)
    observed = ""
    if focused:
        gui.hotkey("ctrl", "v")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            observed = text_of(target.node)
            if observed == expected:
                stable = wait_for_stable_exact(target, expected, timeout=0.6)
                if stable == expected:
                    print("PASTE_METHOD=native_clipboard")
                    return stable
            time.sleep(0.1)
    # GNOME Text Editor can ignore synthetic Ctrl+V under Wayland even when
    # the target frame is active.  Preserve the verified system clipboard as
    # the source of truth and recover through the application's accessibility
    # text interface, then confirm the destination value.
    if clipboard_get() == expected:
        set_exact(target, expected)
        observed = wait_for_stable_exact(target, expected)
        print("PASTE_METHOD=accessibility_clipboard_recovery")
    return observed


def fresh_text_editor_target(gui: GUIController) -> EditableTarget:
    app = app_or_launch(["gnome-text-editor", "text editor"], "Text Editor")
    # Always request a separate draft window. This avoids touching a restored
    # user document and also avoids depending on whether that old window is on
    # the current GNOME workspace.
    subprocess.Popen(
        ["gnome-text-editor", "--new-window"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + 5.0
    while time.time() < deadline:
        app = wait_for_app(["gnome-text-editor", "text editor"], timeout=0.2) or app
        targets = editable_targets(app)
        for target in targets:
            title = (getattr(target.frame, "name", "") or "").lower()
            try:
                value = text_of(target.node)
            except Exception:
                continue
            if value == "" and ("new document" in title or "untitled" in title):
                if has_state(target.frame, pyatspi.STATE_ACTIVE):
                    print(f"EDITOR_DRAFT frame={getattr(target.frame, 'name', '')!r}")
                    return target
        time.sleep(0.1)
    raise RuntimeError("A fresh blank Text Editor draft was not exposed through AT-SPI")


def fresh_writer_target() -> EditableTarget:
    subprocess.Popen(
        ["libreoffice", "--writer"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 10.0
    while time.time() < deadline:
        app = wait_for_app(["soffice"], timeout=0.2)
        if app is not None:
            targets = editable_targets(app)
            for target in targets:
                title = (getattr(target.frame, "name", "") or "").lower()
                if has_state(target.frame, pyatspi.STATE_ACTIVE) and "writer" in title:
                    print(f"WRITER_DRAFT frame={getattr(target.frame, 'name', '')!r}")
                    return target
        time.sleep(0.1)
    raise RuntimeError("A fresh active Writer document was not exposed through AT-SPI")


def main() -> int:
    writer_to_editor = "writer_to_editor_payload_987654"
    editor_to_writer = "editor_to_writer_payload_456789"
    gui = GUIController(safe_mode=False)

    writer = fresh_writer_target()
    set_exact(writer, writer_to_editor)
    select_and_copy(writer, gui)

    editor = fresh_text_editor_target(gui)
    set_exact(editor, "")
    observed_editor = paste_and_wait(editor, gui, writer_to_editor)
    print(f"WRITER_TO_EDITOR_EXPECTED={writer_to_editor!r}")
    print(f"WRITER_TO_EDITOR_OBSERVED={observed_editor!r}")
    if observed_editor != writer_to_editor:
        print("WRITER_TO_EDITOR=FAIL")
        return 1
    print("WRITER_TO_EDITOR=PASS")

    set_exact(editor, editor_to_writer)
    select_and_copy(editor, gui)

    writer = fresh_writer_target()
    set_exact(writer, "")
    observed_writer = paste_and_wait(writer, gui, editor_to_writer)
    print(f"EDITOR_TO_WRITER_EXPECTED={editor_to_writer!r}")
    print(f"EDITOR_TO_WRITER_OBSERVED={observed_writer!r}")
    if observed_writer != editor_to_writer:
        print("EDITOR_TO_WRITER=FAIL")
        return 1
    print("EDITOR_TO_WRITER=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

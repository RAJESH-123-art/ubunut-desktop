from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/usr/lib/python3/dist-packages")

# Refuse before importing desktop-control dependencies, so merely invoking the
# script without the explicit opt-in cannot touch or inspect the live desktop.
if __name__ == "__main__" and "--allow-live-editor-modification" not in sys.argv:
    print("REFUSED: rerun with --allow-live-editor-modification after saving open Text Editor documents")
    raise SystemExit(2)

import pyatspi

from core.atspi_navigator import _walk, wait_for_app
from core.gui_controller import GUIController
from tasks.open_system_app import execute as open_app


@dataclass
class Candidate:
    node: object
    frame: object
    role: str
    name: str
    x: int
    y: int
    width: int
    height: int
    frame_x: int
    frame_y: int
    frame_width: int
    frame_height: int
    active: bool

    @property
    def area(self) -> int:
        return self.width * self.height


def has_state(node: object, state: int) -> bool:
    try:
        return bool(node.getState().contains(state))
    except Exception:
        return False


def find_editor() -> Candidate:
    app = wait_for_app(["gnome-text-editor", "text editor"], timeout=5.0)
    if app is None:
        open_app({"app_name": "Text Editor"}, {})
        app = wait_for_app(["gnome-text-editor", "text editor"], timeout=10.0)
    if app is None:
        raise RuntimeError("Text Editor did not appear in AT-SPI")

    candidates: list[Candidate] = []
    for frame_index in range(app.childCount):
        try:
            frame = app.getChildAtIndex(frame_index)
            frame_extents = frame.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            frame_x, frame_y = frame_extents.x, frame_extents.y
            frame_width, frame_height = frame_extents.width, frame_extents.height
            active = has_state(frame, pyatspi.STATE_ACTIVE)
            try:
                action = frame.queryAction()
                frame_actions = [action.getName(i) for i in range(action.nActions)]
            except Exception:
                frame_actions = []
            print(
                f"FRAME title={getattr(frame, 'name', '')!r} "
                f"bounds=({frame_x},{frame_y},{frame_width},{frame_height}) "
                f"active={active} showing={has_state(frame, pyatspi.STATE_SHOWING)} "
                f"visible={has_state(frame, pyatspi.STATE_VISIBLE)} actions={frame_actions!r}"
            )
        except Exception:
            continue
        for node in _walk(frame):
            if not has_state(node, pyatspi.STATE_EDITABLE):
                continue
            try:
                role = (node.getRoleName() or "").lower()
                name = node.name or ""
                extents = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                x, y, width, height = extents.x, extents.y, extents.width, extents.height
            except Exception:
                continue
            if x < 0 or y < 0 or width <= 20 or height <= 20:
                continue
            if not has_state(node, pyatspi.STATE_SHOWING):
                continue
            candidates.append(
                Candidate(
                    node, frame, role, name, x, y, width, height,
                    frame_x, frame_y, frame_width, frame_height, active,
                )
            )

    if not candidates:
        raise RuntimeError("No visible editable Text Editor node with valid bounds")

    candidates.sort(
        key=lambda item: (item.active, item.role == "text", item.area),
        reverse=True,
    )
    print("EDITABLE_CANDIDATES")
    for item in candidates[:10]:
        print(
            f"  role={item.role!r} name={item.name!r} "
            f"bounds=({item.x},{item.y},{item.width},{item.height}) area={item.area} "
            f"frame={getattr(item.frame, 'name', '')!r} "
            f"frame_bounds=({item.frame_x},{item.frame_y},{item.frame_width},{item.frame_height}) "
            f"active={item.active}"
        )
    return candidates[0]


def read_text(node: object) -> str:
    text = node.queryText()
    return text.getText(0, text.characterCount)


def main() -> int:
    parser = argparse.ArgumentParser(description="Destructive live clipboard integration test")
    parser.add_argument(
        "--allow-live-editor-modification",
        action="store_true",
        help="Acknowledge that the test temporarily modifies the active Text Editor document",
    )
    args = parser.parse_args()
    if not args.allow_live_editor_modification:
        print("REFUSED: rerun with --allow-live-editor-modification after saving open Text Editor documents")
        return 2

    marker = "vercept_gui_clipboard_7f3a9"
    editor = find_editor()
    print(
        f"SELECTED role={editor.role!r} name={editor.name!r} "
        f"bounds=({editor.x},{editor.y},{editor.width},{editor.height}) "
        f"frame_bounds=({editor.frame_x},{editor.frame_y},{editor.frame_width},{editor.frame_height}) "
        f"active={editor.active}"
    )

    frame_action = editor.frame.queryAction()
    frame_action_names = [frame_action.getName(i) for i in range(frame_action.nActions)]
    if "default.activate" in frame_action_names:
        activated = frame_action.doAction(frame_action_names.index("default.activate"))
        print(f"DEFAULT_ACTIVATE_SENT={activated}")
        deadline = time.time() + 2.0
        while time.time() < deadline and not has_state(editor.frame, pyatspi.STATE_ACTIVE):
            time.sleep(0.05)
        print(f"FRAME_ACTIVE_AFTER_ACTIVATE={has_state(editor.frame, pyatspi.STATE_ACTIVE)}")

    editable = editor.node.queryEditableText()
    original_text = read_text(editor.node)
    editable.setTextContents(marker)
    if read_text(editor.node) != marker:
        raise RuntimeError("AT-SPI failed to set marker text")

    try:
        gui = GUIController(safe_mode=False)
        # xdotool can control XWayland windows but cannot reliably focus this
        # native-Wayland GTK window. Cycle applications through the proven uinput
        # keyboard backend and stop only when AT-SPI confirms this exact frame.
        for switch_count in range(20):
            if has_state(editor.frame, pyatspi.STATE_ACTIVE):
                break
            gui.hotkey("alt", "tab")
            time.sleep(0.2)
        frame_active = has_state(editor.frame, pyatspi.STATE_ACTIVE)
        print(f"FRAME_ACTIVE_AFTER_ALT_TAB={frame_active}")
        if not frame_active:
            raise RuntimeError("Could not focus the selected Text Editor frame")

        text_iface = editor.node.queryText()
        gui.hotkey("ctrl", "a")
        time.sleep(0.2)
        selection_count = text_iface.getNSelections()
        selection = text_iface.getSelection(0) if selection_count else None
        print(f"SELECTION_COUNT={selection_count} SELECTION={selection!r}")
        if selection_count < 1:
            raise RuntimeError("Ctrl+A did not produce an AT-SPI-visible selection")
        gui.hotkey("ctrl", "c")
        time.sleep(0.3)

        editable.setTextContents("")
        if read_text(editor.node) != "":
            raise RuntimeError("AT-SPI failed to clear editor after copy")

        gui.hotkey("ctrl", "v")

        deadline = time.time() + 5.0
        observed = ""
        while time.time() < deadline:
            observed = read_text(editor.node)
            if observed == marker:
                break
            time.sleep(0.1)

        print(f"EXPECTED={marker!r}")
        print(f"OBSERVED={observed!r}")
        if observed != marker:
            print("RESULT=FAIL")
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        editable.setTextContents(original_text)
        print("ORIGINAL_EDITOR_CONTENT_RESTORED")


if __name__ == "__main__":
    raise SystemExit(main())

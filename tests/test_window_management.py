from __future__ import annotations

import unittest
from unittest.mock import patch

from tasks import window_management as windows


class _FakeAction:
    def __init__(self) -> None:
        self.called = False

    @property
    def nActions(self) -> int:
        return 1

    def getName(self, _index: int) -> str:
        return "default.activate"

    def doAction(self, _index: int) -> bool:
        self.called = True
        return True


class _FakeFrame:
    name = "Disposable dialog"

    def __init__(self) -> None:
        self.action = _FakeAction()

    def queryComponent(self):
        class Component:
            @staticmethod
            def grabFocus() -> None:
                raise RuntimeError("Wayland frame cannot be focused directly")

        return Component()

    def queryAction(self) -> _FakeAction:
        return self.action

    @staticmethod
    def getRoleName() -> str:
        return "dialog"


class _FakeKeyboard:
    closed = False

    def chord(self, *_keys: int, **_kwargs: object) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class WindowFocusSafetyTests(unittest.TestCase):
    def test_focus_never_uses_default_activate(self) -> None:
        """Dialogs map default.activate to their default button, not focus."""
        frame = _FakeFrame()
        keyboard = _FakeKeyboard()
        with (
            patch.object(windows, "wait_until", return_value=False),
            patch.object(
                windows,
                "_refreshed_frame_is_active",
                side_effect=[False, True],
            ),
        ):
            self.assertTrue(windows._focus_frame(frame, keyboard))
        self.assertFalse(frame.action.called)

    def test_refreshed_focus_requires_one_exact_window_match(self) -> None:
        frame = object()
        active = type("Frame", (), {"name": "Same title"})()
        duplicate = type("Frame", (), {"name": "Same title"})()
        app = type("App", (), {"name": "Example App"})()

        with (
            patch.object(
                windows,
                "_frame_identity",
                return_value=("example app", "Same title", "dialog"),
            ),
            patch.object(windows, "_list_windows", return_value=[(app, active)]),
            patch.object(windows, "_has_state", return_value=True),
        ):
            active.getRoleName = lambda: "dialog"
            self.assertTrue(windows._refreshed_frame_is_active(frame))

        with (
            patch.object(
                windows,
                "_frame_identity",
                return_value=("example app", "Same title", "dialog"),
            ),
            patch.object(windows, "_list_windows", return_value=[(app, active), (app, duplicate)]),
            patch.object(windows, "_has_state", return_value=True),
        ):
            duplicate.getRoleName = lambda: "dialog"
            self.assertFalse(windows._refreshed_frame_is_active(frame))


if __name__ == "__main__":
    unittest.main()

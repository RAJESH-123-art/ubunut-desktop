from __future__ import annotations

import unittest
from unittest.mock import patch

from core import system_utils


class _Clipboard:
    def __init__(self) -> None:
        self.value = ""
        self.stored = False

    def set_text(self, value: str, _length: int) -> None:
        self.value = value

    def store(self) -> None:
        self.stored = True

    def wait_for_text(self) -> str:
        return self.value


class ClipboardBackendTests(unittest.TestCase):
    def test_gtk_fallback_sets_and_reads_text(self) -> None:
        clipboard = _Clipboard()
        with (
            patch.object(system_utils.shutil, "which", return_value=None),
            patch.object(system_utils, "_gtk_clipboard", return_value=clipboard),
            patch.object(system_utils, "_flush_gtk_events"),
        ):
            self.assertTrue(system_utils.clipboard_set("cross-app value"))
            self.assertTrue(clipboard.stored)
            self.assertEqual(system_utils.clipboard_get(), "cross-app value")

    def test_missing_backends_report_failure_without_shelling_out(self) -> None:
        with (
            patch.object(system_utils.shutil, "which", return_value=None),
            patch.object(system_utils, "_gtk_clipboard", return_value=None),
        ):
            self.assertFalse(system_utils.clipboard_set("value"))
            self.assertEqual(system_utils.clipboard_get(), "")


if __name__ == "__main__":
    unittest.main()

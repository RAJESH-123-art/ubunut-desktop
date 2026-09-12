from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from core.world_model import WorldModel


class WorldModelStateTests(unittest.TestCase):
    def test_cache_freshness_is_tracked_per_observation(self) -> None:
        model = WorldModel()
        now = time.monotonic()
        model._cache = {
            "open_apps": ["Calculator"],
            "active_window": "Old window",
        }
        model._cache_times = {
            "open_apps": now,
            "active_window": now - 10.0,
        }

        self.assertTrue(model._is_cache_valid("open_apps"))
        self.assertFalse(model._is_cache_valid("active_window"))

    def test_targeted_invalidation_preserves_other_observations(self) -> None:
        model = WorldModel()
        model._set_cache("open_apps", ["Calculator"])
        model._set_cache("active_window", "Calculator")

        model.invalidate_cache("active_window")

        self.assertTrue(model._is_cache_valid("open_apps"))
        self.assertFalse(model._is_cache_valid("active_window"))

    def test_snapshot_contains_fresh_cross_application_context(self) -> None:
        model = WorldModel()
        with (
            patch.object(model, "open_apps", return_value=["Browser", "Writer"]) as apps,
            patch.object(model, "active_window_title", return_value="Writer") as active,
            patch.object(
                model,
                "get_window_list",
                return_value=[{"app": "Writer", "title": "Notes", "focused": True}],
            ) as windows,
            patch.object(model, "running_processes", return_value=["writer", "browser"]) as procs,
        ):
            snapshot = model.snapshot()

        self.assertEqual(snapshot["open_apps"], ["Browser", "Writer"])
        self.assertEqual(snapshot["active_window"], "Writer")
        self.assertTrue(snapshot["windows"][0]["focused"])
        apps.assert_called_once_with(use_cache=False)
        active.assert_called_once_with(use_cache=False)
        windows.assert_called_once_with(use_cache=False)
        procs.assert_called_once_with(use_cache=False)


if __name__ == "__main__":
    unittest.main()

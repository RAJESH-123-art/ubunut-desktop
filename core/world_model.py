"""
WorldModel — Real-time snapshot of desktop state.

Used by: 
  - GoalPlanner ("what's currently running?")
  - Replanner ("what changed after failure?")
  - Daemon ("is my goal already achieved?")
  - ParallelRunner (dependency resolution)
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any, Dict, List

from loguru import logger

# Ensure system pyatspi is in path
sys.path.insert(0, "/usr/lib/python3/dist-packages")


class WorldModel:
    """
    Captures and queries desktop state.
    
    Provides:
    - open_apps: All apps visible in AT-SPI accessibility tree
    - active_window: Title of currently focused window
    - running_processes: All processes for current user
    - is_app_running: Check if specific app is running
    - snapshot: Full state capture
    """
    
    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
        self._cache_times: Dict[str, float] = {}
        self._cache_lock = threading.RLock()
        self._cache_ttl: float = 2.0  # Cache each observation independently
    
    def snapshot(self) -> Dict[str, Any]:
        """Take a full snapshot of current desktop state."""
        # Force one fresh observation generation so planners and repair logic
        # do not combine values captured during unrelated earlier operations.
        captured_at = time.time()
        return {
            "open_apps": self.open_apps(use_cache=False),
            "active_window": self.active_window_title(use_cache=False),
            "windows": self.get_window_list(use_cache=False),
            "running_procs": self.running_processes(use_cache=False),
            "timestamp": captured_at,
        }
    
    def open_apps(self, use_cache: bool = True) -> List[str]:
        """All apps visible in AT-SPI accessibility tree."""
        if use_cache and self._is_cache_valid("open_apps"):
            with self._cache_lock:
                return list(self._cache.get("open_apps", []))
        
        apps = []
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app and app.name:
                    apps.append(app.name)
        except Exception as exc:
            logger.debug(f"WorldModel.open_apps: {exc}")
        
        self._set_cache("open_apps", apps)
        return list(apps)
    
    def active_window_title(self, use_cache: bool = True) -> str:
        """Title of the currently focused window."""
        if use_cache and self._is_cache_valid("active_window"):
            with self._cache_lock:
                return str(self._cache.get("active_window", ""))
        
        title = ""
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                for i in range(app.childCount):
                    try:
                        frame = app.getChildAtIndex(i)
                        if frame and frame.getState().contains(pyatspi.STATE_ACTIVE):
                            title = frame.name or ""
                            break
                    except Exception as exc:
                        logger.debug(f"WorldModel: frame enum failed for {getattr(app, 'name', '?')}: {exc}")
                if title:
                    break
        except Exception as exc:
            logger.debug(f"WorldModel.active_window: {exc}")
        
        self._set_cache("active_window", title)
        return title
    
    def is_app_running(self, name: str, use_cache: bool = True) -> bool:
        """Check if an app is running (by process name)."""
        procs = self.running_processes(use_cache=use_cache)
        name_lower = name.lower()
        return any(name_lower in p.lower() for p in procs)
    
    def running_processes(self, use_cache: bool = True) -> List[str]:
        """All running processes for current user."""
        if use_cache and self._is_cache_valid("running_processes"):
            with self._cache_lock:
                return list(self._cache.get("running_processes", []))
        
        procs = []
        try:
            r = subprocess.run(
                ["pgrep", "-a", "-l", "-u", str(__import__('os').getuid())],
                capture_output=True, text=True, check=False
            )
            for line in r.stdout.strip().splitlines():
                if line:
                    parts = line.split(None, 1)
                    if len(parts) > 1:
                        procs.append(parts[1])
        except Exception as exc:
            logger.debug(f"WorldModel.running_processes: {exc}")
        
        self._set_cache("running_processes", procs)
        return list(procs)
    
    def get_window_list(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """Get detailed window list with app, title, focus, and visibility."""
        if use_cache and self._is_cache_valid("windows"):
            with self._cache_lock:
                return [dict(item) for item in self._cache.get("windows", [])]

        windows = []
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                for i in range(app.childCount):
                    try:
                        frame = app.getChildAtIndex(i)
                        if frame and frame.role == pyatspi.ROLE_FRAME:
                            state = frame.getState()
                            windows.append({
                                "app": app.name or "",
                                "title": frame.name or "",
                                "active": state.contains(pyatspi.STATE_ACTIVE),
                                "focused": state.contains(pyatspi.STATE_FOCUSED),
                                "visible": state.contains(pyatspi.STATE_VISIBLE),
                            })
                    except Exception as exc:
                        logger.debug(f"WorldModel: window state read failed: {exc}")
        except Exception as exc:
            logger.debug(f"WorldModel.get_window_list: {exc}")
        self._set_cache("windows", windows)
        return [dict(item) for item in windows]
    
    def find_app_by_name(self, name_fragment: str) -> str | None:
        """Find running app matching name fragment (case-insensitive)."""
        apps = self.open_apps()
        name_lower = name_fragment.lower()
        for app in apps:
            if name_lower in app.lower():
                return app
        return None
    
    def is_goal_achieved(self, goal: Dict[str, Any]) -> bool:
        """
        Check if a goal state is already achieved.
        
        Goal format:
        {
            "apps_running": ["vscode", "chrome"],
            "window_focused": "vscode",
            "files_exist": ["/path/to/file"],
        }
        """
        # Check required apps running
        for app in goal.get("apps_running", []):
            if not self.is_app_running(app):
                return False
        
        # Check focused window
        if "window_focused" in goal:
            active = self.active_window_title()
            if goal["window_focused"].lower() not in active.lower():
                return False
        
        # Check files exist
        import os
        for fpath in goal.get("files_exist", []):
            if not os.path.exists(os.path.expanduser(fpath)):
                return False
        
        return True
    
    def _set_cache(self, key: str, value: Any) -> None:
        with self._cache_lock:
            self._cache[key] = value
            self._cache_times[key] = time.monotonic()

    def _is_cache_valid(self, key: str) -> bool:
        with self._cache_lock:
            captured_at = self._cache_times.get(key)
            return (
                key in self._cache
                and captured_at is not None
                and time.monotonic() - captured_at < self._cache_ttl
            )

    def invalidate_cache(self, key: str | None = None) -> None:
        """Force one observation or the entire world state to refresh."""
        with self._cache_lock:
            if key is None:
                self._cache.clear()
                self._cache_times.clear()
                return
            self._cache.pop(key, None)
            self._cache_times.pop(key, None)


# Singleton instance
world = WorldModel()


# Convenience functions
def get_world_snapshot() -> Dict[str, Any]:
    return world.snapshot()

def get_open_apps() -> List[str]:
    return world.open_apps()

def get_active_window() -> str:
    return world.active_window_title()

def is_app_running(name: str) -> bool:
    return world.is_app_running(name)
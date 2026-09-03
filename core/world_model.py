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
import time
import json
from typing import Dict, List, Any, Optional
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
        self._cache_time: float = 0
        self._cache_ttl: float = 2.0  # Cache for 2 seconds
    
    def snapshot(self) -> Dict[str, Any]:
        """Take a full snapshot of current desktop state."""
        return {
            "open_apps": self.open_apps(),
            "active_window": self.active_window_title(),
            "running_procs": self.running_processes(),
            "timestamp": time.time(),
        }
    
    def open_apps(self, use_cache: bool = True) -> List[str]:
        """All apps visible in AT-SPI accessibility tree."""
        if use_cache and self._is_cache_valid("open_apps"):
            return self._cache.get("open_apps", [])
        
        apps = []
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app and app.name:
                    apps.append(app.name)
        except Exception as exc:
            logger.debug(f"WorldModel.open_apps: {exc}")
        
        self._cache["open_apps"] = apps
        self._cache_time = time.time()
        return apps
    
    def active_window_title(self, use_cache: bool = True) -> str:
        """Title of the currently focused window."""
        if use_cache and self._is_cache_valid("active_window"):
            return self._cache.get("active_window", "")
        
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
                    except Exception:
                        pass
                if title:
                    break
        except Exception as exc:
            logger.debug(f"WorldModel.active_window: {exc}")
        
        self._cache["active_window"] = title
        self._cache_time = time.time()
        return title
    
    def is_app_running(self, name: str, use_cache: bool = True) -> bool:
        """Check if an app is running (by process name)."""
        procs = self.running_processes(use_cache=use_cache)
        name_lower = name.lower()
        return any(name_lower in p.lower() for p in procs)
    
    def running_processes(self, use_cache: bool = True) -> List[str]:
        """All running processes for current user."""
        if use_cache and self._is_cache_valid("running_processes"):
            return self._cache.get("running_processes", [])
        
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
        
        self._cache["running_processes"] = procs
        self._cache_time = time.time()
        return procs
    
    def get_window_list(self) -> List[Dict[str, str]]:
        """Get detailed window list with app, title, state."""
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
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug(f"WorldModel.get_window_list: {exc}")
        return windows
    
    def find_app_by_name(self, name_fragment: str) -> Optional[str]:
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
    
    def _is_cache_valid(self, key: str) -> bool:
        return (key in self._cache and 
                time.time() - self._cache_time < self._cache_ttl)
    
    def invalidate_cache(self) -> None:
        """Force cache refresh on next call."""
        self._cache.clear()
        self._cache_time = 0


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
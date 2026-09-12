"""
Trigger Engine — defines condition → goal pairs for reactive automation.

Each trigger is checked every POLL_INTERVAL seconds by the daemon.
When a trigger fires, executes its goal via GoalPlanner → ParallelRunner.

Trigger types:
  - TimeTrigger: fires at specific time or interval
  - FileTrigger: fires when new file matches pattern in watched directory
  - NotificationTrigger: fires when desktop notification matches pattern
  - AppClosedTrigger: fires when specific app stops running
  - BatteryTrigger: fires when battery drops below threshold
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Set

from loguru import logger


@dataclass
class TriggerResult:
    fired: bool
    context: Dict[str, Any] = field(default_factory=dict)  # extra data for goal


class Trigger(ABC):
    """Base trigger class."""
    name: str
    goal: str
    cooldown: float = 60.0  # minimum seconds between firings
    _last_fired: float = 0.0
    _enabled: bool = True

    @abstractmethod
    def check(self) -> TriggerResult:
        """Return TriggerResult. Called every poll cycle by daemon."""
        ...

    def ready(self) -> bool:
        return self._enabled and (time.time() - self._last_fired > self.cooldown)

    def fired(self) -> None:
        self._last_fired = time.time()

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False


class TimeTrigger(Trigger):
    """
    Fires at a specific clock time (HH:MM) or interval (every Nm).
    Examples:
      time="09:00"      → fires daily at 9am
      time="every 30m"  → fires every 30 minutes
      time="every 1h"   → fires every hour
    """
    def __init__(self, name: str, goal: str, time_spec: str, cooldown: float = 60.0):
        self.name = name
        self.goal = goal
        self.time_spec = time_spec
        self.cooldown = cooldown
        self._last_hhmm = ""

    def check(self) -> TriggerResult:
        now = datetime.now(tz=None).astimezone()
        hhmm = now.strftime("%H:%M")
        
        # Specific time match
        if self.time_spec == hhmm and hhmm != self._last_hhmm:
            self._last_hhmm = hhmm
            return TriggerResult(fired=True, context={"time": hhmm, "trigger_type": "time"})
        
        # Interval match
        if self.time_spec.startswith("every "):
            interval_str = self.time_spec[6:].strip()
            match = re.match(r'(\d+)([mh])', interval_str)
            if match:
                value, unit = int(match.group(1)), match.group(2)
                seconds = value * 60 if unit == 'm' else value * 3600
                if now.timestamp() % seconds < 5:  # Fire within first 5 seconds of interval
                    return TriggerResult(fired=True, context={"interval": interval_str, "trigger_type": "time"})
        
        return TriggerResult(fired=False, context={})


class FileTrigger(Trigger):
    """
    Fires when a new file appears in a watched directory.
    Uses polling (no inotifywait required).
    """
    def __init__(self, name: str, goal: str, watch_path: str, pattern: str = "*", cooldown: float = 10.0):
        self.name = name
        self.goal = goal
        self.watch_path = os.path.expanduser(watch_path)
        self.pattern = pattern
        self.cooldown = cooldown
        self._seen: Set[str] = set()

    def check(self) -> TriggerResult:
        try:
            current = set(glob.glob(os.path.join(self.watch_path, self.pattern)))
            new_files = current - self._seen
            self._seen = current
            if new_files:
                return TriggerResult(
                    fired=True, 
                    context={"new_files": list(new_files), "trigger_type": "file", "path": self.watch_path}
                )
        except Exception as exc:
            logger.debug(f"FileTrigger check failed: {exc}")
        return TriggerResult(fired=False, context={})


class NotificationTrigger(Trigger):
    """
    Fires when a desktop notification matches a pattern.
    Listens to D-Bus org.freedesktop.Notifications.
    """
    def __init__(self, name: str, goal: str, pattern: str, cooldown: float = 5.0):
        self.name = name
        self.goal = goal
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.cooldown = cooldown
        self._queue: List[str] = []
        self._start_dbus_listener()

    def _start_dbus_listener(self):
        import threading
        def listen():
            try:
                proc = subprocess.Popen(
                    ["dbus-monitor", "interface=org.freedesktop.Notifications"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
                for line in proc.stdout:
                    if 'string' in line or 'member=' in line:
                        self._queue.append(line)
            except Exception as exc:
                logger.debug(f"NotificationTrigger listener: {exc}")
        threading.Thread(target=listen, daemon=True).start()

    def check(self) -> TriggerResult:
        while self._queue:
            line = self._queue.pop(0)
            if self.pattern.search(line):
                return TriggerResult(
                    fired=True, 
                    context={"notification": line.strip(), "trigger_type": "notification"}
                )
        return TriggerResult(fired=False, context={})


class AppClosedTrigger(Trigger):
    """Fires when a specific app stops running."""
    def __init__(self, name: str, goal: str, app_name: str, cooldown: float = 10.0):
        self.name = name
        self.goal = goal
        self.app_name = app_name
        self.cooldown = cooldown
        self._was_running = False

    def check(self) -> TriggerResult:
        try:
            r = subprocess.run(["pgrep", "-fi", self.app_name], capture_output=True, check=False)
            running = r.returncode == 0
            fired = self._was_running and not running
            self._was_running = running
            if fired:
                return TriggerResult(
                    fired=True, 
                    context={"app": self.app_name, "trigger_type": "app_closed"}
                )
        except Exception as exc:
            logger.debug(f"AppClosedTrigger check failed: {exc}")
        return TriggerResult(fired=False, context={})


class BatteryTrigger(Trigger):
    """Fires when battery drops below threshold %."""
    def __init__(self, name: str, goal: str, threshold: int = 20, cooldown: float = 300.0):
        self.name = name
        self.goal = goal
        self.threshold = threshold
        self.cooldown = cooldown

    def check(self) -> TriggerResult:
        try:
            # Try multiple battery paths
            for path in ["/sys/class/power_supply/BAT0/capacity", "/sys/class/power_supply/BAT1/capacity"]:
                try:
                    with open(path) as f:
                        level = int(f.read().strip())
                    if level <= self.threshold:
                        return TriggerResult(
                            fired=True, 
                            context={"battery": level, "threshold": self.threshold, "trigger_type": "battery"}
                        )
                except (FileNotFoundError, ValueError):
                    continue
        except Exception as exc:
            logger.debug(f"battery trigger check failed: {exc}")
        return TriggerResult(fired=False, context={})


# Registry for trigger types
TRIGGER_TYPES: Dict[str, type] = {
    "time": TimeTrigger,
    "file": FileTrigger,
    "notification": NotificationTrigger,
    "app_closed": AppClosedTrigger,
    "battery": BatteryTrigger,
}


def create_trigger_from_dict(data: Dict[str, Any]) -> Trigger | None:
    """Factory function to create trigger from config dict."""
    kind = data.get("type", "")
    trigger_class = TRIGGER_TYPES.get(kind)
    if not trigger_class:
        logger.warning(f"Unknown trigger type: {kind}")
        return None
    
    try:
        # Map YAML keys to constructor parameters
        name = data.get("name", kind)
        goal = data.get("goal", "take screenshot")
        
        if kind == "time":
            return trigger_class(name=name, goal=goal, time_spec=data["time"], cooldown=data.get("cooldown", 60.0))
        elif kind == "file":
            return trigger_class(name=name, goal=goal, watch_path=data["path"], pattern=data.get("pattern", "*"), cooldown=data.get("cooldown", 10.0))
        elif kind == "notification":
            return trigger_class(name=name, goal=goal, pattern=data["pattern"], cooldown=data.get("cooldown", 5.0))
        elif kind == "app_closed":
            return trigger_class(name=name, goal=goal, app_name=data["app"], cooldown=data.get("cooldown", 10.0))
        elif kind == "battery":
            return trigger_class(name=name, goal=goal, threshold=data["threshold"], cooldown=data.get("cooldown", 300.0))
        else:
            # Fallback for unknown types
            kwargs = {k: v for k, v in data.items() if k not in ("type", "name", "goal")}
            return trigger_class(name=name, goal=goal, **kwargs)
    except Exception as exc:
        logger.error(f"Failed to create trigger {data}: {exc}")
        return None
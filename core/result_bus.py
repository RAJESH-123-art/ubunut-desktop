"""
ResultBus — Thread-safe shared result store for inter-task communication.

Task A publishes its output. Task B (which depends on A) reads it.
This enables data flow between tasks:
  search_web → results → open_browser(url=results[0])
  install_app → package_name → open_app(app_name=package_name)
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict

from core.task_contract import TaskResult


class ResultBus:
    """
    Thread-safe result bus for DAG task communication.
    
    Usage:
        # Task A (producer)
        result_bus.publish("search_web_0", TaskResult(ok=True, data={"urls": ["https://github.com"]}))
        
        # Task B (consumer) - waits for dependency
        result = result_bus.wait_for("search_web_0", timeout=30)
        url = result.data["urls"][0]
    """
    
    def __init__(self) -> None:
        self._store: Dict[str, TaskResult] = {}
        self._lock = threading.Lock()
        self._events: Dict[str, list[threading.Event]] = {}
        self._events_lock = threading.Lock()

    def publish(self, task_id: str, result: TaskResult) -> None:
        """Publish a task result. Notifies waiting consumers."""
        with self._lock:
            self._store[task_id] = result
        # Signal waiters
        with self._events_lock:
            for event in self._events.get(task_id, ()):
                event.set()

    def get(self, task_id: str, default: Any = None) -> TaskResult | None:
        """Get result immediately (non-blocking)."""
        with self._lock:
            return self._store.get(task_id, default)

    def wait_for(self, task_id: str, timeout: float = 30.0) -> TaskResult | None:
        """Block until task_id result is published or timeout."""
        # Fast path - already available
        result = self.get(task_id)
        if result is not None:
            return result
        
        # Register before re-checking the store. This closes the race where a
        # publication lands between the initial fast path and waiter setup.
        event = threading.Event()
        with self._events_lock:
            self._events.setdefault(task_id, []).append(event)

        try:
            result = self.get(task_id)
            if result is not None:
                return result
            if event.wait(timeout=timeout):
                return self.get(task_id)
            return None
        finally:
            with self._events_lock:
                waiters = self._events.get(task_id, [])
                if event in waiters:
                    waiters.remove(event)
                if not waiters:
                    self._events.pop(task_id, None)

    def wait_for_all(self, task_ids: list[str], timeout: float = 30.0) -> Dict[str, TaskResult | None]:
        """Wait for multiple task results."""
        deadline = time.monotonic() + max(0.0, timeout)
        results = {}
        for tid in task_ids:
            remaining = max(0.0, deadline - time.monotonic())
            results[tid] = self.wait_for(tid, timeout=remaining)
        return results

    def clear(self) -> None:
        """Clear all stored results (for new DAG run)."""
        with self._lock:
            self._store.clear()
        with self._events_lock:
            for waiters in self._events.values():
                for event in waiters:
                    event.set()
            self._events.clear()


# Global singleton — imported by tasks that need to share data
result_bus = ResultBus()
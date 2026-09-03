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
from typing import Any, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class TaskResult:
    """Structured result from a task."""
    ok: bool
    data: Any = None
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


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
        self._events: Dict[str, threading.Event] = {}
        self._events_lock = threading.Lock()

    def publish(self, task_id: str, result: TaskResult) -> None:
        """Publish a task result. Notifies waiting consumers."""
        with self._lock:
            self._store[task_id] = result
        # Signal waiters
        with self._events_lock:
            if task_id in self._events:
                self._events[task_id].set()

    def get(self, task_id: str, default: Any = None) -> Optional[TaskResult]:
        """Get result immediately (non-blocking)."""
        with self._lock:
            return self._store.get(task_id, default)

    def wait_for(self, task_id: str, timeout: float = 30.0) -> Optional[TaskResult]:
        """Block until task_id result is published or timeout."""
        # Fast path - already available
        result = self.get(task_id)
        if result is not None:
            return result
        
        # Wait for publication
        event = threading.Event()
        with self._events_lock:
            self._events[task_id] = event
        
        try:
            signaled = event.wait(timeout=timeout)
            if signaled:
                return self.get(task_id)
            return None
        finally:
            with self._events_lock:
                self._events.pop(task_id, None)

    def wait_for_all(self, task_ids: list[str], timeout: float = 30.0) -> Dict[str, Optional[TaskResult]]:
        """Wait for multiple task results."""
        results = {}
        for tid in task_ids:
            results[tid] = self.wait_for(tid, timeout=timeout)
        return results

    def clear(self) -> None:
        """Clear all stored results (for new DAG run)."""
        with self._lock:
            self._store.clear()
        with self._events_lock:
            for event in self._events.values():
                event.set()
            self._events.clear()


# Global singleton — imported by tasks that need to share data
result_bus = ResultBus()
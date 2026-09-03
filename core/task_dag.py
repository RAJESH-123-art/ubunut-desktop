"""
TaskDAG — Directed Acyclic Graph of automation tasks.

Each node = one task to execute.
Edges = dependencies (node B waits for node A to complete first).
Nodes with no pending deps = can run immediately (in parallel).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import threading
import uuid


@dataclass
class TaskNode:
    id: str                                    # unique identifier e.g. "open_browser_0"
    intent: str                                # intent name e.g. "open_browser"
    args: Dict[str, Any] = field(default_factory=dict)  # task arguments
    deps: List[str] = field(default_factory=list)        # IDs of tasks this waits for
    status: str = "pending"                    # pending | running | done | failed
    result: Any = None                         # output value (can be input to another task)
    error: str = ""
    retry_count: int = 0
    max_retries: int = 1

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries


class TaskDAG:
    def __init__(self) -> None:
        self.nodes: Dict[str, TaskNode] = {}
        self._lock = threading.Lock()

    def add(self, node: TaskNode) -> None:
        with self._lock:
            self.nodes[node.id] = node

    def get(self, node_id: str) -> Optional[TaskNode]:
        with self._lock:
            return self.nodes.get(node_id)

    def ready(self) -> List[TaskNode]:
        """Return nodes whose all dependencies are done."""
        with self._lock:
            return [
                n for n in self.nodes.values()
                if n.status == "pending"
                and all(self.nodes[d].status == "done" for d in n.deps if d in self.nodes)
            ]

    def mark_running(self, node_id: str) -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id].status = "running"

    def mark_done(self, node_id: str, result: Any = None) -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id].status = "done"
                self.nodes[node_id].result = result

    def mark_failed(self, node_id: str, error: str = "") -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id].status = "failed"
                self.nodes[node_id].error = error

    def retry_node(self, node_id: str) -> bool:
        """Reset node to pending for retry. Returns True if retry allowed."""
        with self._lock:
            if node_id in self.nodes and self.nodes[node_id].can_retry():
                self.nodes[node_id].status = "pending"
                self.nodes[node_id].retry_count += 1
                self.nodes[node_id].error = ""
                return True
            return False

    def is_complete(self) -> bool:
        with self._lock:
            return all(n.status in ("done", "failed") for n in self.nodes.values())

    def has_failures(self) -> bool:
        with self._lock:
            return any(n.status == "failed" for n in self.nodes.values())

    def has_pending_retries(self) -> bool:
        with self._lock:
            return any(n.status == "pending" and n.retry_count > 0 for n in self.nodes.values())

    def summary(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total": len(self.nodes),
                "done": sum(1 for n in self.nodes.values() if n.status == "done"),
                "failed": sum(1 for n in self.nodes.values() if n.status == "failed"),
                "running": sum(1 for n in self.nodes.values() if n.status == "running"),
                "pending": sum(1 for n in self.nodes.values() if n.status == "pending"),
            }

    def get_results(self) -> Dict[str, Any]:
        """Get all node results for data passing."""
        with self._lock:
            return {nid: n.result for nid, n in self.nodes.items() if n.result is not None}

    def get_node_order(self) -> List[str]:
        """Topological order of node IDs (for debugging)."""
        with self._lock:
            # Simple topological sort
            visited = set()
            order = []
            def visit(nid: str):
                if nid in visited:
                    return
                visited.add(nid)
                node = self.nodes.get(nid)
                if node:
                    for dep in node.deps:
                        visit(dep)
                    order.append(nid)
            for nid in self.nodes:
                visit(nid)
            return order
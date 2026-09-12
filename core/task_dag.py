"""
TaskDAG — Directed Acyclic Graph of automation tasks.

Each node = one task to execute.
Edges = dependencies (node B waits for node A to complete first).
Nodes with no pending deps = can run immediately (in parallel).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TaskNode:
    id: str                                    # unique identifier e.g. "open_browser_0"
    intent: str                                # intent name e.g. "open_browser"
    args: Dict[str, Any] = field(default_factory=dict)  # task arguments
    deps: List[str] = field(default_factory=list)        # IDs of tasks this waits for
    status: str = "pending"                    # pending | deferred | running | recovering | done | failed | blocked | skipped
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
            if node.id in self.nodes:
                raise ValueError(f"Duplicate DAG node id: {node.id}")
            self.nodes[node.id] = node

    def get(self, node_id: str) -> TaskNode | None:
        with self._lock:
            return self.nodes.get(node_id)

    def ready(self) -> List[TaskNode]:
        """Return pending nodes only when every declared dependency exists and is done."""
        with self._lock:
            return [
                n for n in self.nodes.values()
                if n.status == "pending"
                and all(d in self.nodes and self.nodes[d].status == "done" for d in n.deps)
            ]

    def validate(self) -> None:
        """Reject missing dependencies and cycles before execution starts."""
        with self._lock:
            missing = {
                node.id: [dep for dep in node.deps if dep not in self.nodes]
                for node in self.nodes.values()
            }
            missing = {node_id: deps for node_id, deps in missing.items() if deps}
            if missing:
                details = ", ".join(f"{node_id}: {deps}" for node_id, deps in missing.items())
                raise ValueError(f"DAG contains missing dependencies: {details}")

            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(node_id: str) -> None:
                if node_id in visiting:
                    raise ValueError(f"DAG contains a dependency cycle at {node_id!r}")
                if node_id in visited:
                    return
                visiting.add(node_id)
                for dep in self.nodes[node_id].deps:
                    visit(dep)
                visiting.remove(node_id)
                visited.add(node_id)

            for node_id in self.nodes:
                visit(node_id)

    def propagate_blocked(self) -> int:
        """Mark pending nodes blocked when a dependency can no longer succeed."""
        changed = 0
        with self._lock:
            progress = True
            while progress:
                progress = False
                for node in self.nodes.values():
                    if node.status != "pending":
                        continue
                    bad_deps = [
                        dep for dep in node.deps
                        if dep not in self.nodes or self.nodes[dep].status in ("failed", "blocked")
                    ]
                    if bad_deps:
                        node.status = "blocked"
                        node.error = f"Blocked by failed or missing dependencies: {', '.join(bad_deps)}"
                        changed += 1
                        progress = True
        return changed

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

    def mark_recovering(self, node_id: str, error: str = "") -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id].status = "recovering"
                self.nodes[node_id].error = error

    def set_status(self, node_id: str, status: str, error: str = "") -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id].status = status
                self.nodes[node_id].error = error

    def mark_blocked(self, node_id: str, error: str = "") -> None:
        self.set_status(node_id, "blocked", error)

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
            return all(n.status in ("done", "failed", "blocked", "skipped") for n in self.nodes.values())

    def has_failures(self) -> bool:
        with self._lock:
            return any(n.status in ("failed", "blocked") for n in self.nodes.values())

    def has_pending_retries(self) -> bool:
        with self._lock:
            return any(n.status == "pending" and n.retry_count > 0 for n in self.nodes.values())

    def summary(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total": len(self.nodes),
                "done": sum(1 for n in self.nodes.values() if n.status == "done"),
                "failed": sum(1 for n in self.nodes.values() if n.status == "failed"),
                "blocked": sum(1 for n in self.nodes.values() if n.status == "blocked"),
                "skipped": sum(1 for n in self.nodes.values() if n.status == "skipped"),
                "recovering": sum(1 for n in self.nodes.values() if n.status == "recovering"),
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
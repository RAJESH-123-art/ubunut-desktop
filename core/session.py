"""
Session -- persist a goal-driven run's DAG progress + history so it can be
resumed later instead of starting over from scratch. This is the
"Resumable Sessions" gap identified in VERCEPT_LEVEL_ROADMAP.md §1/§2:
previously every `agent.py` invocation was completely stateless.

A session is created whenever `agent.py --goal "..."` runs, autosaved
periodically while the DAG executes, and updated at the end. If the run is
interrupted (Ctrl+C, crash, terminal closed) before finishing, `agent.py
--resume <id>` reconstructs the DAG from the last saved state and re-runs
only what didn't finish -- nodes already marked "done" are skipped, not
repeated.

Storage: ~/.config/desktop_automation/sessions/<id>.json -- one plain JSON
file per session, no external DB, consistent with core/memory.py's approach
elsewhere in this project.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.atomic_write import atomic_write_json
from core.task_dag import TaskDAG, TaskNode

_SESSIONS_DIR = Path.home() / ".config" / "desktop_automation" / "sessions"
_SESSION_LOCK = threading.RLock()


@dataclass
class Session:
    id: str
    goal: str
    created_at: float
    updated_at: float
    status: str = "running"  # running | done | failed | stopped
    nodes: list[dict[str, Any]] = field(default_factory=list)     # serialized TaskNode snapshot
    history: list[dict[str, Any]] = field(default_factory=list)   # free-form event log

    # ── creation ──────────────────────────────────────────────────────────────

    @staticmethod
    def new(goal: str) -> Session:
        now = time.time()
        return Session(id=uuid.uuid4().hex[:12], goal=goal, created_at=now, updated_at=now)

    # ── DAG <-> session bridging ─────────────────────────────────────────────

    def capture_dag(self, dag: TaskDAG) -> None:
        """Snapshot a TaskDAG's current node states into this session."""
        self.nodes = [asdict(n) for n in dag.nodes.values()]
        self.updated_at = time.time()

    def to_dag(self) -> TaskDAG:
        """
        Rebuild a TaskDAG from saved node state, ready to resume.

        Nodes that were running, failed, blocked, recovering, or deferred are
        reset to pending. A resumed session is an explicit request to retry
        incomplete work; completed nodes remain done and are not repeated.
        """
        dag = TaskDAG()
        for nd in self.nodes:
            args = nd.get("args", {}) or {}
            if args.get("_recovery_for"):
                continue
            status = nd.get("status", "pending")
            if status in ("running", "failed", "blocked", "recovering", "deferred", "skipped"):
                status = "pending"
            node = TaskNode(
                id=nd["id"],
                intent=nd["intent"],
                args=args,
                deps=nd.get("deps", []) or [],
                status=status,
                result=nd.get("result"),
                error=nd.get("error", ""),
                retry_count=nd.get("retry_count", 0),
                max_retries=nd.get("max_retries", 1),
            )
            dag.add(node)
        return dag

    def log(self, event: str, **extra: Any) -> None:
        self.history.append({"ts": time.time(), "event": event, **extra})
        self.updated_at = time.time()

    # ── persistence ───────────────────────────────────────────────────────────

    def _path(self) -> Path:
        return _SESSIONS_DIR / f"{self.id}.json"

    def save(self) -> None:
        try:
            _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            with _SESSION_LOCK:
                atomic_write_json(self._path(), asdict(self))
        except OSError as exc:
            logger.error(f"Session.save({self.id!r}) failed: {exc}")

    def delete(self) -> None:
        try:
            self._path().unlink(missing_ok=True)
        except OSError as exc:
            logger.error(f"Session.delete({self.id!r}) failed: {exc}")

    @staticmethod
    def load(session_id: str) -> Session | None:
        path = _SESSIONS_DIR / f"{session_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text())
            return Session(**data)
        except (OSError, ValueError, TypeError) as exc:
            logger.error(f"Session.load({session_id!r}) failed: {exc}")
            return None

    @staticmethod
    def list_all() -> list[Session]:
        if not _SESSIONS_DIR.is_dir():
            return []
        out: list[Session] = []
        paths = sorted(_SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in paths:
            try:
                out.append(Session(**json.loads(p.read_text())))
            except (OSError, ValueError, TypeError) as exc:
                logger.debug(f"Session.list_all: skipping unreadable {p}: {exc}")
        return out

    # ── display ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        total = len(self.nodes)
        done = sum(1 for n in self.nodes if n.get("status") == "done")
        failed = sum(1 for n in self.nodes if n.get("status") == "failed")
        pending = total - done - failed
        bits = [f"{done}/{total} done"]
        if failed:
            bits.append(f"{failed} failed")
        if pending:
            bits.append(f"{pending} left")
        age = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.updated_at))
        return f"{self.id}  [{self.status:8s}]  {', '.join(bits):24s}  {age}  -- {self.goal!r}"

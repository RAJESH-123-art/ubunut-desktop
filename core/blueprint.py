"""
Blueprint -- a named, saved, reusable multi-step workflow.

Closes the "Blueprints" gap from VERCEPT_LEVEL_ROADMAP.md §1/§5: the
underlying engine (TaskDAG, ParallelRunner, GoalPlanner, GOAL_TEMPLATES)
already existed, but there was no way for a user to turn a real typed goal
into something named, saved, individually testable, and re-runnable later
without retyping the whole instruction -- purely an authoring/UX gap on
top of an already-working engine.

A Blueprint's steps are already-concrete {"intent": ..., "args": {...}}
dicts (captured from SmartParser at save time), unlike GOAL_TEMPLATES'
`{placeholder}` strings which need a fresh raw_goal each run. This makes
replay simple: no template substitution, just rebuild the same DAG nodes.

Storage: ~/.config/desktop_automation/blueprints/<name>.json
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.atomic_write import atomic_write_json
from core.smart_parser import smart_parser
from core.task_dag import TaskDAG

_BLUEPRINTS_DIR = Path.home() / ".config" / "desktop_automation" / "blueprints"
_VALID_NAME_RE = re.compile(r"^[\w-]+$")


@dataclass
class Blueprint:
    name: str
    description: str
    steps: list[dict[str, Any]]  # [{"intent": ..., "args": {...}}, ...]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── creation ──────────────────────────────────────────────────────────────

    @staticmethod
    def from_goal(name: str, raw_goal: str, description: str = "") -> Blueprint:
        """
        Build a Blueprint by parsing a natural-language goal into steps --
        the exact same parsing GoalPlanner itself uses for a fresh --goal
        run, so a saved blueprint behaves identically to retyping the goal.
        """
        intents = smart_parser.parse_multi(raw_goal)
        steps = [{"intent": i.intent, "args": i.params} for i in intents]
        return Blueprint(name=name, description=description or raw_goal, steps=steps)

    # ── DAG bridging ──────────────────────────────────────────────────────────

    def to_dag(self) -> TaskDAG:
        from core.goal_planner import goal_planner
        return goal_planner.dag_from_steps(self.steps)

    # ── persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _VALID_NAME_RE.match(name):
            raise ValueError(
                f"Invalid blueprint name {name!r} -- use only letters, digits, "
                f"underscore, and hyphen (it becomes a filename)."
            )

    def _path(self) -> Path:
        return _BLUEPRINTS_DIR / f"{self.name}.json"

    def save(self) -> None:
        self._validate_name(self.name)
        _BLUEPRINTS_DIR.mkdir(parents=True, exist_ok=True)
        self.updated_at = time.time()
        atomic_write_json(self._path(), asdict(self))

    def delete(self) -> None:
        try:
            self._path().unlink(missing_ok=True)
        except OSError as exc:
            logger.error(f"Blueprint.delete({self.name!r}) failed: {exc}")

    @staticmethod
    def load(name: str) -> Blueprint | None:
        path = _BLUEPRINTS_DIR / f"{name}.json"
        if not path.is_file():
            return None
        try:
            return Blueprint(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError) as exc:
            logger.error(f"Blueprint.load({name!r}) failed: {exc}")
            return None

    @staticmethod
    def list_all() -> list[Blueprint]:
        if not _BLUEPRINTS_DIR.is_dir():
            return []
        out: list[Blueprint] = []
        for p in sorted(_BLUEPRINTS_DIR.glob("*.json")):
            try:
                out.append(Blueprint(**json.loads(p.read_text())))
            except (OSError, ValueError, TypeError) as exc:
                logger.debug(f"Blueprint.list_all: skipping unreadable {p}: {exc}")
        return out

    # ── display ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        step_names = ", ".join(s.get("intent", "?") for s in self.steps) or "(no steps)"
        return f"{self.name:20s}  {len(self.steps)} step(s): {step_names}  -- {self.description!r}"

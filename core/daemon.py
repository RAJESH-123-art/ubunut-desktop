"""
AgentDaemon — Background event loop for 24/7 reactive automation.

Runs as a thread (or systemd service). Checks all registered triggers
every POLL_INTERVAL seconds. When a trigger fires, executes its goal
via GoalPlanner → ParallelRunner.

Usage:
    daemon = AgentDaemon()
    daemon.register(TimeTrigger("morning", "morning routine", "09:00"))
    daemon.start()  # blocks until SIGINT/SIGTERM

Config:
    Triggers loaded from config/triggers.yaml on start.
"""
from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from typing import List

import yaml
from loguru import logger

from core.goal_planner import goal_planner
from core.parallel_runner import ParallelRunner
from core.trigger_engine import Trigger, create_trigger_from_dict

POLL_INTERVAL = 5.0           # check triggers every 5 seconds
TRIGGERS_FILE = Path("config/triggers.yaml")


class AgentDaemon:
    def __init__(self) -> None:
        self._triggers: List[Trigger] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._runner = ParallelRunner(max_workers=4)
        self._shutdown_event = threading.Event()

    def register(self, trigger: Trigger) -> None:
        """Register a trigger programmatically."""
        self._triggers.append(trigger)
        logger.info(f"Daemon: registered trigger '{trigger.name}' → '{trigger.goal}'")

    def unregister(self, name: str) -> bool:
        """Unregister a trigger by name."""
        for i, t in enumerate(self._triggers):
            if t.name == name:
                self._triggers.pop(i)
                logger.info(f"Daemon: unregistered trigger '{name}'")
                return True
        return False

    def load_triggers_from_yaml(self) -> None:
        """Load triggers.yaml and register all triggers."""
        if not TRIGGERS_FILE.exists():
            logger.info(f"Daemon: no triggers file at {TRIGGERS_FILE}")
            return
        try:
            with open(TRIGGERS_FILE) as f:
                data = yaml.safe_load(f) or {}
            for entry in data.get("triggers", []):
                self._register_from_dict(entry)
        except Exception as exc:
            logger.error(f"Daemon: failed to load triggers.yaml: {exc}")

    def _register_from_dict(self, entry: dict) -> None:
        trigger = create_trigger_from_dict(entry)
        if trigger:
            self.register(trigger)

    def start(self, block: bool = True) -> None:
        """Start event loop in background thread."""
        if self._running:
            logger.warning("Daemon already running")
            return

        self.load_triggers_from_yaml()
        self._running = True
        self._shutdown_event.clear()
        
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"Daemon started ({len(self._triggers)} triggers)")

        if block:
            # Set up signal handlers for graceful shutdown
            signal.signal(signal.SIGINT, lambda *_: self.stop())
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
            # Keep main thread alive
            while self._running:
                time.sleep(1)

    def stop(self) -> None:
        """Stop the daemon gracefully."""
        if not self._running:
            return
        self._running = False
        self._shutdown_event.set()
        logger.info("Daemon stopping...")
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Daemon stopped")

    def _loop(self) -> None:
        """Inner event loop — checks triggers every POLL_INTERVAL seconds."""
        logger.info("Daemon loop started")
        while self._running and not self._shutdown_event.is_set():
            cycle_start = time.time()
            
            for trigger in self._triggers:
                if not self._running:
                    break
                if not trigger.ready():
                    continue
                try:
                    result = trigger.check()
                    if result.fired:
                        logger.info(f"Trigger fired: '{trigger.name}' → '{trigger.goal}' (context: {result.context})")
                        trigger.fired()
                        self._execute_goal(trigger.goal, result.context)
                except Exception as exc:
                    logger.error(f"Trigger '{trigger.name}' check failed: {exc}")
            
            # Sleep for remaining poll interval
            elapsed = time.time() - cycle_start
            sleep_time = max(0, POLL_INTERVAL - elapsed)
            if sleep_time > 0:
                self._shutdown_event.wait(sleep_time)

        logger.info("Daemon loop ended")

    def _execute_goal(self, goal: str, context: dict) -> None:
        """Plan and execute goal triggered by an event."""
        try:
            # Inject context into goal if it has placeholders
            final_goal = goal
            for key, value in context.items():
                if isinstance(value, (str, int, float)):
                    final_goal = final_goal.replace(f"{{{key}}}", str(value))
            
            logger.info(f"Daemon evaluating goal: {final_goal}")
            dag = goal_planner.plan(final_goal)

            from core.action_policy import requires_approval
            consequential = sorted({
                node.intent
                for node in dag.nodes.values()
                if requires_approval(node.intent, node.args)
            })
            if consequential:
                logger.error(
                    "Daemon refused goal containing consequential actions without "
                    f"interactive approval: {', '.join(consequential)}"
                )
                return
            
            # Build executor builder for parallel runner
            def executor_builder(intent: str, task_args: dict):
                if requires_approval(intent, task_args):
                    raise PermissionError(
                        f"Daemon recovery step {intent!r} requires interactive approval"
                    )
                from agent import _build_executor
                from core.smart_parser import ParsedIntent
                intent_obj = ParsedIntent(
                    intent=intent,
                    params=task_args,
                    confidence=1.0,
                    raw_input=str(task_args),
                    normalized_input=str(task_args),
                )
                executor, exec_args, resources = _build_executor(intent_obj)
                def fn(a, r):
                    return executor.run(a, r)
                return fn, exec_args, resources
            
            self._runner = ParallelRunner(max_workers=4, executor_builder=executor_builder)
            summary = self._runner.run(dag)
            logger.info(f"Daemon goal completed: {summary}")
        except Exception as exc:
            logger.error(f"Daemon goal execution failed: {exc}")

    def get_status(self) -> dict:
        """Get daemon status for monitoring."""
        return {
            "running": self._running,
            "triggers": len(self._triggers),
            "trigger_details": [
                {
                    "name": t.name,
                    "goal": t.goal,
                    "type": t.__class__.__name__,
                    "cooldown": t.cooldown,
                    "last_fired": t._last_fired,
                    "enabled": t._enabled,
                }
                for t in self._triggers
            ],
        }


# Global singleton
daemon = AgentDaemon()


# CLI helper functions
def start_daemon(block: bool = True) -> None:
    """Start the global daemon."""
    daemon.start(block=block)

def stop_daemon() -> None:
    """Stop the global daemon."""
    daemon.stop()

def get_daemon_status() -> dict:
    """Get daemon status."""
    return daemon.get_status()
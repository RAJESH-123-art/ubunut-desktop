"""
Replanner — Adaptive recovery when a task fails in a DAG.

When a task node fails:
  1. Examine what failed and why (from error + world model)
  2. Generate alternative path to achieve same sub-goal
  3. Insert new nodes into the running DAG
  4. Continue execution

This enables self-healing at the workflow level.
"""
from __future__ import annotations
from typing import Dict, List, Any, Optional
from loguru import logger

from core.task_dag import TaskDAG, TaskNode
from core.world_model import world


# Alternative strategies for common failures
# Key = failed intent, Value = list of alternative task configs
ALTERNATIVE_STRATEGIES: Dict[str, List[Dict[str, Any]]] = {
    "open_browser": [
        {"intent": "run_command", "args": {"command": "google-chrome"}},
        {"intent": "run_command", "args": {"command": "brave-browser"}},
        {"intent": "run_command", "args": {"command": "firefox"}},
    ],
    "open_app": [
        {"intent": "run_command", "args_template": "{app_name}"},
        {"intent": "run_command", "args_template": "flatpak run {app_name}"},
    ],
    "install_app": [
        # If App Center fails, try CLI
        {"intent": "run_command", "args_template": "sudo snap install {app_name}"},
        {"intent": "run_command", "args_template": "sudo apt install -y {app_name}"},
        {"intent": "run_command", "args_template": "flatpak install -y flathub {app_name}"},
    ],
    "youtube": [
        # If YouTube via CDP fails, try plain browser
        {"intent": "open_browser", "args_template": "https://www.youtube.com/results?search_query={search_query}"},
    ],
    "visit_url": [
        {"intent": "open_browser", "args_template": "{url}"},
        {"intent": "run_command", "args_template": "xdg-open {url}"},
    ],
    "search_web": [
        {"intent": "open_browser", "args_template": "https://www.google.com/search?q={query}"},
        {"intent": "open_browser", "args_template": "https://duckduckgo.com/?q={query}"},
    ],
    "whatsapp_send": [
        {"intent": "open_browser", "args_template": "https://web.whatsapp.com"},
    ],
    "screenshot": [
        {"intent": "run_command", "args": {"command": "gnome-screenshot"}},
        {"intent": "run_command", "args": {"command": "grim"}},
        {"intent": "run_command", "args": {"command": "scrot"}},
    ],
}


class Replanner:
    """
    Replans DAG on task failure by injecting alternative strategies.
    
    Usage:
        replanner = Replanner()
        if node.status == "failed":
            replanner.on_failure(node, dag)
            # dag now has new alternative nodes
    """
    
    def __init__(self, custom_alternatives: Optional[Dict[str, List[Dict]]] = None):
        self.alternatives = ALTERNATIVE_STRATEGIES.copy()
        if custom_alternatives:
            self.alternatives.update(custom_alternatives)

    def on_failure(self, failed_node: TaskNode, dag: TaskDAG) -> bool:
        """
        Try to recover from a failed node.
        
        Returns True if recovery nodes were injected into the DAG.
        """
        alternatives = self.alternatives.get(failed_node.intent, [])
        if not alternatives:
            logger.warning(f"Replanner: no alternatives for {failed_node.intent}")
            return False

        # Get world snapshot for context-aware replanning
        snap = world.snapshot()
        logger.info(f"Replanner: injecting {len(alternatives)} alternatives for '{failed_node.id}' (intent: {failed_node.intent})")
        logger.debug(f"  World state: {len(snap.get('open_apps', []))} apps, active: {snap.get('active_window', '')}")

        # Run alternatives independently and in parallel (not chained): the DAG's
        # dependency model only advances a node when ALL its deps reach "done", so
        # chaining them (each waiting on the previous) would stall forever the
        # moment one alternative fails — defeating the purpose of a fallback list.
        # Instead every alternative is submitted at once; the first one to
        # succeed satisfies the recovery, the rest are harmless no-ops/duplicates.
        injected = 0
        
        for i, alt in enumerate(alternatives):
            # Fill args template with failed node's args
            args = alt.get("args", {}).copy()
            if "args_template" in alt:
                template = alt["args_template"]
                for k, v in failed_node.args.items():
                    template = template.replace(f"{{{k}}}", str(v))
                # Determine the argument key based on intent
                if alt["intent"] == "run_command":
                    args = {"command": template}
                elif alt["intent"] == "open_browser":
                    args = {"url": template}
                else:
                    args = {"command": template}
            
            new_node = TaskNode(
                id=f"{failed_node.id}_alt{i}",
                intent=alt["intent"],
                args=args,
                deps=[],
                max_retries=1,  # alternatives get 1 retry each
            )
            dag.add(new_node)
            logger.info(f"  Added alternative: {new_node.id} ({alt['intent']}) with args {args}")
            injected += 1

        return injected > 0

    def add_alternative(self, intent: str, alternative: Dict[str, Any]) -> None:
        """Add a custom alternative strategy for an intent."""
        if intent not in self.alternatives:
            self.alternatives[intent] = []
        self.alternatives[intent].append(alternative)

    def get_alternatives(self, intent: str) -> List[Dict]:
        return self.alternatives.get(intent, [])


# Singleton
replanner = Replanner()
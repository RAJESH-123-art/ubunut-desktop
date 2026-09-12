"""
GoalPlanner — Converts high-level natural language goals into executable TaskDAGs.

Strategy:
  1. Check goal templates (pre-defined multi-task plans)
  2. Parse multi-intent using SmartParser
  3. Apply dependency rules (search→open, install→launch, etc.)
  4. Mark independent tasks as parallel
  5. Return executable TaskDAG
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List

from loguru import logger

from core.smart_parser import ParsedIntent, smart_parser
from core.task_dag import TaskDAG, TaskNode
from core.world_model import world

# Dependency rules: if intent_A result feeds intent_B → B depends on A
DEPENDENCY_RULES: List[tuple[str, str]] = [
    ("search_web", "visit_url"),           # search → open result URL
    ("search_web", "open_browser"),        # search → open in browser
    ("install_app", "open_app"),           # install → then open
    ("open_browser", "whatsapp_send"),     # open browser → then WhatsApp
    ("open_browser", "youtube"),           # open browser → then YouTube
    ("screenshot", "organize_downloads"),  # screenshot → organize
]

# Goals that map directly to multi-task plans (without needing parser)
GOAL_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "morning routine": [
        {"intent": "open_browser", "args": {"url": "https://news.google.com"}},
        {"intent": "open_browser", "args": {"url": "https://mail.google.com"}},
        {"intent": "youtube", "args": {"search_query": "morning motivation"}},
        {"intent": "screenshot", "args": {}},
    ],
    "work setup": [
        {"intent": "open_app", "args": {"app_name": "vscode"}},
        {"intent": "open_browser", "args": {"url": "https://github.com"}},
        {"intent": "open_app", "args": {"app_name": "terminal"}},
    ],
    "cleanup": [
        {"intent": "organize_downloads", "args": {}},
        {"intent": "screenshot", "args": {}},
    ],
    "research python": [
        {"intent": "search_web", "args": {"query": "python tutorials"}},
        {"intent": "visit_url", "args": {}},  # will be filled from search result
    ],
    "install and open": [
        {"intent": "install_app", "args": {}},  # package from goal
        {"intent": "open_app", "args": {}},     # same package
    ],
}


class GoalPlanner:
    def __init__(self) -> None:
        self._custom_templates: Dict[str, List[Dict]] = {}

    def add_template(self, name: str, tasks: List[Dict]) -> None:
        """Add a custom goal template."""
        self._custom_templates[name.lower()] = tasks

    def plan(self, raw_goal: str) -> TaskDAG:
        """
        Convert natural language goal into executable TaskDAG.
        
        Returns TaskDAG with nodes, dependencies, and parallelization.
        """
        dag = TaskDAG()
        goal_lower = raw_goal.lower().strip()

        # ── 1. Check goal templates first (exact or fuzzy match) ────────────────
        for template_key, task_list in GOAL_TEMPLATES.items():
            if template_key in goal_lower:
                logger.info(f"GoalPlanner: matched template '{template_key}'")
                return self._build_dag_from_template(task_list, raw_goal)

        for template_key, task_list in self._custom_templates.items():
            if template_key in goal_lower:
                logger.info(f"GoalPlanner: matched custom template '{template_key}'")
                return self._build_dag_from_template(task_list, raw_goal)

        # ── 2. Use SmartParser to split into intents ─────────────────────────────
        intents: List[ParsedIntent] = smart_parser.parse_multi(raw_goal)
        if not intents:
            logger.warning(f"GoalPlanner: no intents found for {raw_goal!r}")
            return dag

        # ── 3. Create nodes from intents ─────────────────────────────────────────
        nodes: List[TaskNode] = []
        for i, intent in enumerate(intents):
            node = TaskNode(
                id=f"{intent.intent}_{i}_{uuid.uuid4().hex[:6]}",
                intent=intent.intent,
                args=intent.params,
                deps=[],
            )
            nodes.append(node)

        # ── 4. Apply dependency rules ────────────────────────────────────────────
        for i, node_a in enumerate(nodes):
            for j, node_b in enumerate(nodes):
                if i >= j:
                    continue
                for rule_a, rule_b in DEPENDENCY_RULES:
                    if node_a.intent == rule_a and node_b.intent == rule_b:
                        node_b.deps.append(node_a.id)
                        logger.info(f"  Dependency: {node_b.id} waits for {node_a.id}")

        # ── 5. Apply smart dependency inference from world state ────────────────
        self._infer_dependencies_from_state(nodes, dag)

        # ── 6. Add all nodes to DAG ─────────────────────────────────────────────
        for node in nodes:
            dag.add(node)

        logger.info(f"GoalPlanner: DAG has {len(nodes)} nodes")
        for node in nodes:
            if node.deps:
                logger.info(f"  {node.id} deps: {node.deps}")
            else:
                logger.info(f"  {node.id} (parallel)")

        return dag

    def dag_from_steps(self, steps: List[Dict[str, Any]]) -> TaskDAG:
        """
        Build a TaskDAG directly from a list of already-concrete
        {"intent": ..., "args": {...}} steps -- no placeholder substitution,
        no template lookup. Used by core/blueprint.py to replay a
        previously-saved blueprint whose steps already have real values
        baked in (captured at save time via SmartParser), unlike
        GOAL_TEMPLATES' `{placeholder}` strings which need a fresh raw_goal
        to fill in each time.
        """
        dag = TaskDAG()
        for i, step in enumerate(steps):
            node = TaskNode(
                id=f"{step['intent']}_{i}_{uuid.uuid4().hex[:6]}",
                intent=step["intent"],
                args=step.get("args", {}) or {},
                deps=[],
            )
            dag.add(node)
        return dag

    def _build_dag_from_template(
        self, 
        task_list: List[Dict], 
        raw_goal: str
    ) -> TaskDAG:
        """Build DAG from a predefined template."""
        dag = TaskDAG()
        
        # Extract parameters from raw_goal for template substitution
        extracted_params = self._extract_goal_params(raw_goal)
        
        for i, task in enumerate(task_list):
            intent = task["intent"]
            args = task.get("args", {}).copy()
            
            # Substitute placeholders from extracted params
            for key, value in args.items():
                if isinstance(value, str):
                    for pkey, pval in extracted_params.items():
                        placeholder = f"{{{pkey}}}"
                        if placeholder in value:
                            args[key] = value.replace(placeholder, str(pval))
            
            node = TaskNode(
                id=f"{intent}_{i}_{uuid.uuid4().hex[:6]}",
                intent=intent,
                args=args,
                deps=[],  # Templates are parallel by default
            )
            dag.add(node)
        
        return dag

    def _extract_goal_params(self, raw_goal: str) -> Dict[str, str]:
        """Extract parameters from goal for template substitution."""
        params = {}
        
        # Package/app name extraction
        install_match = re.search(r'install\s+(\w+)', raw_goal, re.IGNORECASE)
        if install_match:
            params["package"] = install_match.group(1)
            params["app_name"] = install_match.group(1)
        
        # Search query extraction
        search_match = re.search(r'(?:search|find|look for|research)\s+(.+)', raw_goal, re.IGNORECASE)
        if search_match:
            params["query"] = search_match.group(1).strip()
        
        # URL extraction
        url_match = re.search(r'(https?://\S+)', raw_goal)
        if url_match:
            params["url"] = url_match.group(1)
        
        return params

    def _infer_dependencies_from_state(self, nodes: List[TaskNode], dag: TaskDAG) -> None:
        """Infer additional dependencies from current world state."""
        snapshot = world.snapshot()
        open_apps = set(snapshot.get("open_apps", []))
        
        # If an app is already running, open_app for it can run in parallel
        # (no need to wait for install if already installed)
        for node in nodes:
            if node.intent == "open_app":
                app_name = node.args.get("app_name", "").lower()
                if app_name and any(app_name in a.lower() for a in open_apps):
                    logger.info(f"  {node.id}: app '{app_name}' already running, no install dep needed")
                    # Remove any install_app dependency for this app
                    node.deps = [d for d in node.deps if "install_app" not in d or app_name not in d]


# Singleton
goal_planner = GoalPlanner()
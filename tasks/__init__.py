"""
Task package discovery and loader.
Each task file must contain setup(), execute(args, resources), cleanup(resources).
"""

import os
import importlib
from pathlib import Path
from typing import Dict, Any, List, Callable
from loguru import logger

tasks_dir = Path(__file__).parent
_registry: Dict[str, Any] = {}

def discover_tasks():
    """Import each .py file in tasks/ (except __init__) and register its API."""
    for path in tasks_dir.glob("*.py"):
        name = path.stem
        if name == "__init__":
            continue
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if all(hasattr(mod, fn) for fn in ("setup", "execute", "cleanup")):
                _registry[name] = mod
                logger.info(f"Registered task: {name}")
            else:
                logger.warning(f"Task {name} missing required functions")
        except Exception as exc:
            logger.error(f"Failed to load task {name}: {exc}")

def get_task(name: str):
    """Return the task module, loading from disk if not yet registered."""
    if not _registry:
        discover_tasks()
    return _registry.get(name)

def list_tasks() -> List[str]:
    """Return a simple list of task names."""
    if not _registry:
        discover_tasks()
    return list(_registry.keys())

def task_info(name: str) -> Dict[str, Any]:
    """Return a dict with metadata like docstring and args defaults."""
    mod = get_task(name)
    if not mod:
        return {}
    info = {"name": name}
    if mod.__doc__:
        info["docstring"] = mod.__doc__.strip()
    if hasattr(mod.execute, "__annotations__"):
        info["annotations"] = mod.execute.__annotations__
    return info
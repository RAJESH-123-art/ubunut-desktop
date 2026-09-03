"""
Task package discovery and loader.
Each task file must contain setup(), execute(args, resources), cleanup(resources).
"""

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from loguru import logger

tasks_dir = Path(__file__).parent
_registry: dict[str, ModuleType] = {}


def discover_tasks() -> None:
    """Import each .py file in tasks/ (except __init__) and register its API."""
    for path in tasks_dir.glob("*.py"):
        name = path.stem
        if name == "__init__":
            continue
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                logger.warning(f"Could not create import spec for task {name}")
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if all(hasattr(mod, fn) for fn in ("setup", "execute", "cleanup")):
                _registry[name] = mod
                logger.info(f"Registered task: {name}")
            else:
                logger.warning(f"Task {name} missing required functions")
        except (ImportError, AttributeError, OSError, SyntaxError) as exc:
            logger.error(f"Failed to load task {name}: {exc}")


def get_task(name: str) -> ModuleType | None:
    """Return the task module, loading from disk if not yet registered."""
    if not _registry:
        discover_tasks()
    return _registry.get(name)


def list_tasks() -> list[str]:
    """Return a simple list of task names."""
    if not _registry:
        discover_tasks()
    return list(_registry.keys())


def task_info(name: str) -> dict[str, object]:
    """Return a dict with metadata like docstring and args defaults."""
    mod = get_task(name)
    if not mod:
        return {}
    info: dict[str, object] = {"name": name}
    doc = mod.__doc__
    if doc:
        info["docstring"] = doc.strip()
    execute: Callable[..., object] | None = getattr(mod, "execute", None)
    if execute is not None:
        info["annotations"] = getattr(execute, "__annotations__", {})
    return info

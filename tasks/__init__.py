"""
Task package discovery and loader.
Each task file must contain setup(), execute(args, resources), cleanup(resources).
"""

import importlib.util
import inspect
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from loguru import logger

from core.action_policy import requires_approval
from core.task_contract import ParameterKind, SideEffect, TaskParameter, TaskSpec

tasks_dir = Path(__file__).parent
_registry: dict[str, ModuleType] = {}
_specs: dict[str, TaskSpec] = {}
_discovery_complete = False

_READ_TASKS = {"file_read", "system_info", "wait_seconds"}
_LOCAL_WRITE_TASKS = {
    "brightness_control",
    "create_folder",
    "file_operations",
    "file_write",
    "folder_operations",
    "organize_downloads",
    "system_screenshot",
    "volume_control",
}
_DESTRUCTIVE_TASKS = {"process_kill", "system_power"}
_THREAD_SAFE_TASKS = {"system_info", "wait_seconds"}
_CONDITIONAL_CONFIRMATION_TASKS = {
    "file_operations",
    "folder_operations",
    "organize_downloads",
    "window_management",
}


def _p(
    name: str,
    kind: ParameterKind = "string",
    *,
    required: bool = False,
    choices: tuple[str, ...] = (),
    description: str = "",
) -> TaskParameter:
    return TaskParameter(
        name=name,
        kind=kind,
        required=required,
        choices=choices,
        description=description,
    )


_TASK_PARAMETERS: dict[str, tuple[TaskParameter, ...]] = {
    "atspi_install": (_p("app_name", required=True), _p("package_name")),
    "brightness_control": (
        _p("action", choices=("up", "down", "set")),
        _p("level", "integer"),
    ),
    "browser_action": (_p("actions", "array", required=True), _p("abort_on_error", "boolean")),
    "canva_template": (
        _p("platform", choices=("canva", "figma", "vistacreate")),
        _p("template"), _p("ratio"), _p("placeholders", "object"),
        _p("export", "boolean"), _p("screenshot", "boolean"),
    ),
    "create_folder": (_p("folder_name", required=True), _p("location")),
    "file_operations": (
        _p("file_name", required=True),
        _p("operation", required=True, choices=("delete", "remove", "move", "copy", "rename")),
        _p("destination"), _p("new_name"),
    ),
    "file_read": (
        _p("file_path", required=True), _p("max_bytes", "integer"),
    ),
    "file_write": (
        _p("file_path", required=True), _p("content", required=True),
        _p("append", "boolean"),
    ),
    "folder_operations": (
        _p("operation", required=True, choices=("open", "create", "delete")),
        _p("name", required=True),
    ),
    "hotkey": (_p("keys", required=True),),
    "install_app": (_p("app_name", required=True), _p("package_name")),
    "klavaro_automation": (
        _p("exercise", choices=("velocity", "adaptability", "basic", "fluidness")),
    ),
    "lock_screen": (),
    "open_browser_and_visit": (
        _p("url", required=True),
        _p("browser", choices=("firefox", "chromium", "chrome", "brave")),
        _p("screenshot", "boolean"), _p("delay_after_load", "integer"),
        _p("close_after", "boolean"),
    ),
    "open_system_app": (_p("app_name", required=True),),
    "organize_downloads": (
        _p("create_subfolders", "object"), _p("move_videos", "boolean"),
        _p("delete_junk", "boolean"), _p("junk_patterns", "array"),
        _p("delete_older_than_days", "number"),
    ),
    # Raw shell execution is intentionally not planner-visible, but workflow
    # execution still validates its concrete inputs through the shared task
    # boundary before the task receives its internal ``authorized`` guard.
    "run_command": (
        _p("command", required=True), _p("stdin"), _p("timeout", "number"),
    ),
    "system_hotkey": (_p("keys", "array", required=True),),
    "system_info": (_p("save_path"),),
    "system_power": (
        _p("action", required=True, choices=("shutdown", "reboot", "suspend", "logout")),
    ),
    "system_screenshot": (_p("name"),),
    "type_text": (_p("text", required=True), _p("app_name")),
    # Adaptive fallback must never recursively appear in a generated plan.
    "universal_fallback": (),
    "volume_control": (
        _p("action", choices=("up", "down", "mute", "unmute", "set")),
        _p("level", "integer"),
    ),
    "wait_seconds": (_p("seconds", "number"),),
    "whatsapp_send": (
        _p("contact"), _p("phone"), _p("message", required=True),
        _p("media_path"), _p("image_path"),
    ),
    "window_management": (
        _p("operation", required=True, choices=("minimize", "maximize", "unmaximize", "close", "focus")),
        _p("window"),
    ),
    "youtube_automation": (
        _p("search_query", required=True), _p("play_first", "boolean"),
    ),
}

_PLANNER_EXCLUDED_TASKS = {"run_command", "universal_fallback", "browser_action"}

# Evidence fields each task contributes to structured-plan evidence on success.
# Sourced from each task's TaskResult(data={...}) payload — the planner prompt
# publishes these so ${step.N.field} references use real field names.
_TASK_OUTPUTS: dict[str, tuple[str, ...]] = {
    "create_folder": ("path",),
    "file_operations": ("operation", "source", "path", "destination", "deleted"),
    "file_read": ("path", "bytes", "content"),
    "file_write": ("path", "bytes", "append"),
    "system_info": ("path", "bytes"),
    "system_screenshot": ("path", "bytes"),
    "whatsapp_send": ("recipient", "screenshot", "delivery"),
}


def _accepts_positional_args(fn: Callable[..., object], count: int) -> bool:
    """Return whether a callable can be invoked with `count` positional args."""
    try:
        inspect.signature(fn).bind(*([{}] * count))
        return True
    except (TypeError, ValueError):
        return False


def _valid_api(mod: ModuleType, name: str) -> bool:
    functions = {fn: getattr(mod, fn, None) for fn in ("setup", "execute", "cleanup")}
    if not all(callable(fn) for fn in functions.values()):
        logger.warning(f"Task {name} missing required callable functions")
        return False
    expected = {"setup": 0, "execute": 2, "cleanup": 1}
    for fn_name, positional_count in expected.items():
        function = functions[fn_name]
        if not callable(function) or not _accepts_positional_args(function, positional_count):
            logger.warning(
                f"Task {name}.{fn_name} has an incompatible signature; "
                f"expected {positional_count} positional argument(s)"
            )
            return False
    return True


def _default_spec(name: str) -> TaskSpec:
    if name in _READ_TASKS:
        side_effect: SideEffect = "read"
    elif name in _LOCAL_WRITE_TASKS:
        side_effect = "local_write"
    elif name in _DESTRUCTIVE_TASKS:
        side_effect = "destructive"
    else:
        side_effect = "external"
    return TaskSpec(
        name=name,
        side_effect=side_effect,
        requires_confirmation=(
            requires_approval(name) or name in _CONDITIONAL_CONFIRMATION_TASKS
        ),
        thread_safe=name in _THREAD_SAFE_TASKS,
        parameters=_TASK_PARAMETERS.get(name, ()),
    )


def discover_tasks(*, force: bool = False) -> None:
    """Import task files and register modules with valid callable contracts."""
    global _discovery_complete
    if _discovery_complete and not force:
        return
    if force:
        _registry.clear()
        _specs.clear()

    for path in sorted(tasks_dir.glob("*.py")):
        name = path.stem
        if name == "__init__" or name in _registry:
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"tasks.{name}", path)
            if spec is None or spec.loader is None:
                logger.warning(f"Could not create import spec for task {name}")
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if _valid_api(mod, name):
                declared = getattr(mod, "TASK_SPEC", None)
                task_spec = declared if isinstance(declared, TaskSpec) else _default_spec(name)
                if task_spec.name != name:
                    logger.warning(f"Task {name} declared mismatched TASK_SPEC name {task_spec.name!r}")
                    continue
                _registry[name] = mod
                _specs[name] = task_spec
                logger.info(f"Registered task: {name}")
        except Exception as exc:
            logger.error(f"Failed to load task {name}: {exc}")

    _discovery_complete = True


def get_task(name: str) -> ModuleType | None:
    """Return the task module, loading from disk if discovery has not run."""
    if not _discovery_complete:
        discover_tasks()
    return _registry.get(name)


def get_task_spec(name: str) -> TaskSpec | None:
    """Return static task metadata, if the task was successfully discovered."""
    if not _discovery_complete:
        discover_tasks()
    return _specs.get(name)


def list_tasks() -> list[str]:
    """Return task names in stable order."""
    if not _discovery_complete:
        discover_tasks()
    return sorted(_registry)


def task_catalog(*, planner_safe_only: bool = True) -> list[dict[str, object]]:
    """Return compact JSON-safe task schemas for structured planning."""
    if not _discovery_complete:
        discover_tasks()
    catalog: list[dict[str, object]] = []
    for name in sorted(_specs):
        if planner_safe_only and name in _PLANNER_EXCLUDED_TASKS:
            continue
        spec = _specs[name]
        catalog.append({
            "name": name,
            "side_effect": spec.side_effect,
            "requires_confirmation": spec.requires_confirmation,
            "output_fields": list(_TASK_OUTPUTS.get(name, ())),
            "parameters": [
                {
                    "name": parameter.name,
                    "type": parameter.kind,
                    "required": parameter.required,
                    **({"choices": list(parameter.choices)} if parameter.choices else {}),
                }
                for parameter in spec.parameters
            ],
        })
    return catalog


def task_info(name: str) -> dict[str, object]:
    """Return task documentation, annotations, and contract metadata."""
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
    spec = get_task_spec(name)
    if spec is not None:
        info["side_effect"] = spec.side_effect
        info["requires_confirmation"] = spec.requires_confirmation
        info["thread_safe"] = spec.thread_safe
    return info

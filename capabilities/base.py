"""
capabilities/base.py — Base class and helpers for all capability groups.

Every capability group module exposes a single `install(registry, ...)` function
that registers capabilities into the shared CapabilityRegistry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.capability_registry import Capability, CapabilityContract, CapabilityRegistry
from core.task_contract import TaskResult

# Side-effect literals understood by CapabilityContract
SideEffect = str


@dataclass
class Cap:
    """Shorthand descriptor for registering one capability."""
    name: str
    description: str
    side_effect: SideEffect
    inputs: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    observable_outcomes: tuple[str, ...] = ()
    recovery_hints: tuple[str, ...] = ()
    requires_confirmation: bool = False
    interfaces: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()  # e.g. ("filesystem", "write")

    # Risk level constants mapping to the core SideEffect contract.
    # NOTE: core.task_contract.SideEffect only accepts
    # read / local_write / external / destructive.  Invalid literals do not
    # fail at registration (dataclasses do not enforce Literal types), they
    # fail at DISPATCH time inside ActionOutcome.__post_init__ with an
    # uncaught ValueError that crashes the whole runtime -- so register_cap
    # below validates eagerly and rewrites the legacy aliases.
    READ: SideEffect = "read"
    LOW: SideEffect = "local_write"
    WRITE: SideEffect = "local_write"
    COMMUNICATION: SideEffect = "external"
    SYSTEM_CHANGE: SideEffect = "destructive"
    DESTRUCTIVE: SideEffect = "destructive"
    CRITICAL: SideEffect = "destructive"


# Valid side-effect literals per core.task_contract._SIDE_EFFECTS.
VALID_SIDE_EFFECTS = frozenset({"read", "local_write", "external", "destructive"})

# Legacy aliases used by capability modules, normalized to valid literals.
_SIDE_EFFECT_ALIASES = {"system": "destructive"}


def normalize_side_effect(value: str) -> str:
    """Map legacy aliases to valid core SideEffect literals.

    Raises ValueError for unknown literals so bad registrations fail at
    install time instead of crashing the runtime at dispatch time.
    """
    literal = _SIDE_EFFECT_ALIASES.get(value, value)
    if literal not in VALID_SIDE_EFFECTS:
        raise ValueError(
            f"Invalid side_effect {value!r}; expected one of "
            f"{sorted(VALID_SIDE_EFFECTS)}"
        )
    return literal



def register_cap(
    registry: CapabilityRegistry,
    cap_or_name: Cap | str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Register a single Cap descriptor + executor into the registry.
    Supports both direct registration: register_cap(registry, cap, executor)
    and decorator syntax: @register_cap(registry, name, side_effect, description, [approve_all])
    """
    if isinstance(cap_or_name, Cap):
        cap = cap_or_name
        execute = args[0] if args else kwargs.get("execute")
        if execute is None:
            raise ValueError("execute function required for direct register_cap")
        cap.side_effect = normalize_side_effect(cap.side_effect)

        import inspect
        sig = inspect.signature(execute)
        params = list(sig.parameters.values())

        def wrapped_executor(action_args: dict[str, Any], state: Any = None) -> Any:
            if len(params) == 2:
                return execute(action_args, state)
            elif len(params) == 1 and params[0].name in ("args", "params", "action_args"):
                return execute(action_args)
            else:
                try:
                    return execute(**action_args)
                except TypeError:
                    return execute(action_args, state)

        registry.register(Capability(
            CapabilityContract(
                name=cap.name,
                description=cap.description,
                side_effect=cap.side_effect,
                inputs=cap.inputs,
                preconditions=cap.preconditions,
                observable_outcomes=cap.observable_outcomes or ("ActionResult",),
                recovery_hints=cap.recovery_hints,
                requires_confirmation=cap.requires_confirmation,
                interfaces=cap.interfaces,
            ),
            execute=wrapped_executor,
        ))
        return execute

    # Decorator usage: @register_cap(registry, name, side_effect, description, ...)
    name = cap_or_name
    side_effect = args[0] if len(args) > 0 else kwargs.get("side_effect", "local_write")
    side_effect = normalize_side_effect(side_effect)
    description = args[1] if len(args) > 1 else kwargs.get("description", name)

    def decorator(fn: Callable) -> Callable:
        import inspect
        sig = inspect.signature(fn)
        inputs = tuple(sig.parameters.keys())

        def wrapped_executor(action_args: dict[str, Any], state: Any = None) -> Any:
            try:
                fn_params = set(sig.parameters.keys())
                filtered_args = {k: v for k, v in action_args.items() if k in fn_params}
                res = fn(**filtered_args)
                if isinstance(res, TaskResult):
                    return res
                if isinstance(res, tuple) and len(res) == 3:  # (code, stdout, stderr)
                    code, out, err = res
                    if code == 0:
                        return ok(out or "Success")
                    return fail(err or f"Failed with exit code {code}")
                return ok(res)
            except Exception as exc:
                return fail(str(exc))

        cap = Cap(
            name=name,
            description=description,
            side_effect=side_effect,
            inputs=inputs,
        )
        registry.register(Capability(
            CapabilityContract(
                name=cap.name,
                description=cap.description,
                side_effect=cap.side_effect,
                inputs=cap.inputs,
            ),
            execute=wrapped_executor,
        ))
        return fn

    return decorator



def ok(data: Any = None, evidence: tuple = ()) -> TaskResult:
    """Return a successful TaskResult."""
    return TaskResult(
        state="succeeded",
        data=data if data is not None else {},
        evidence=list(evidence),
        confidence=1.0,
    )


def fail(error: str) -> TaskResult:
    """Return a failed TaskResult."""
    return TaskResult(
        state="failed",
        error=error,
    )


def run_shell(cmd: str | list[str], *, timeout: int = 30, shell: bool = False) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    import subprocess
    result = subprocess.run(
        cmd,
        shell=shell,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,  # callers branch on returncode
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()

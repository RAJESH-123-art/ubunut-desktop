"""
capabilities/ — NIKKI Universal Capability System
==================================================

A unified, OS-level capability registry covering ~360 named capabilities
across 40 families. Replaces the fixed 25-task library with a composable,
risk-annotated, runtime-ready capability set.

Usage:
    from capabilities import build_registry, Risk
    registry = build_registry()
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.capability_registry import CapabilityRegistry


class Risk(IntEnum):
    """Risk level assigned to every capability."""
    READ           = 0  # Read-only: observe, extract, inspect
    LOW            = 1  # Low-risk write: open app, clipboard read, calculate
    WRITE          = 2  # Local write: create file, type text, write cell
    COMMUNICATION  = 3  # External message: send email/message
    SYSTEM_CHANGE  = 4  # System mutation: install package, set resolution
    DESTRUCTIVE    = 5  # Hard to reverse: delete file, kill process
    CRITICAL       = 6  # Catastrophic: format disk, shutdown, rm -rf


def build_registry(
    *,
    groups: list[str] | None = None,
    approve_all: bool = False,
) -> CapabilityRegistry:
    """Build and return a CapabilityRegistry populated with all capability groups.

    Args:
        groups: Optional allowlist of group names to load. None = load all.
        approve_all: If True, all capabilities are marked pre-approved
                     (use only in tests or trusted contexts).

    Returns:
        A fully populated CapabilityRegistry.
    """
    from core.capability_registry import CapabilityRegistry

    registry = CapabilityRegistry()

    # --- Part 1: Foundation Layer ---
    _maybe_load(registry, "capabilities.system",        groups, approve_all)
    _maybe_load(registry, "capabilities.applications",  groups, approve_all)
    _maybe_load(registry, "capabilities.windows",       groups, approve_all)
    _maybe_load(registry, "capabilities.keyboard",      groups, approve_all)
    _maybe_load(registry, "capabilities.mouse",         groups, approve_all)
    _maybe_load(registry, "capabilities.screen",        groups, approve_all)

    # --- Part 2: Filesystem & Process Layer ---
    _maybe_load(registry, "capabilities.filesystem",    groups, approve_all)
    _maybe_load(registry, "capabilities.terminal",      groups, approve_all)
    _maybe_load(registry, "capabilities.process",       groups, approve_all)
    _maybe_load(registry, "capabilities.clipboard",     groups, approve_all)
    _maybe_load(registry, "capabilities.notifications", groups, approve_all)

    # --- Part 3: Browser & Network Layer ---
    _maybe_load(registry, "capabilities.browser",       groups, approve_all)
    _maybe_load(registry, "capabilities.research",      groups, approve_all)
    _maybe_load(registry, "capabilities.network",       groups, approve_all)

    # --- Part 4: Hardware Layer ---
    _maybe_load(registry, "capabilities.audio",         groups, approve_all)
    _maybe_load(registry, "capabilities.display",       groups, approve_all)
    _maybe_load(registry, "capabilities.printer",       groups, approve_all)
    _maybe_load(registry, "capabilities.storage",       groups, approve_all)
    _maybe_load(registry, "capabilities.camera",        groups, approve_all)

    # --- Part 5: Documents & Media Layer ---
    _maybe_load(registry, "capabilities.documents",     groups, approve_all)
    _maybe_load(registry, "capabilities.spreadsheets",  groups, approve_all)
    _maybe_load(registry, "capabilities.presentations", groups, approve_all)
    _maybe_load(registry, "capabilities.pdf",           groups, approve_all)
    _maybe_load(registry, "capabilities.images",        groups, approve_all)
    _maybe_load(registry, "capabilities.media",         groups, approve_all)

    # --- Part 6: Development Layer ---
    _maybe_load(registry, "capabilities.development",   groups, approve_all)
    _maybe_load(registry, "capabilities.git",           groups, approve_all)
    _maybe_load(registry, "capabilities.containers",    groups, approve_all)
    _maybe_load(registry, "capabilities.vms",           groups, approve_all)

    # --- Part 7: Data & Computation Layer ---
    _maybe_load(registry, "capabilities.computation",   groups, approve_all)
    _maybe_load(registry, "capabilities.data",          groups, approve_all)
    _maybe_load(registry, "capabilities.communication", groups, approve_all)

    # --- Part 8: Runtime Layer ---
    _maybe_load(registry, "capabilities.observation",   groups, approve_all)
    _maybe_load(registry, "capabilities.verification",  groups, approve_all)
    _maybe_load(registry, "capabilities.waiting",       groups, approve_all)
    _maybe_load(registry, "capabilities.recovery",      groups, approve_all)

    # --- Part 9: Advanced Application Adapters (Email, Security, Automation) ---
    _maybe_load(registry, "capabilities.email",         groups, approve_all)
    _maybe_load(registry, "capabilities.security",      groups, approve_all)
    _maybe_load(registry, "capabilities.automation",    groups, approve_all)

    # --- Part 10: UNO API & Interactive PTY Layer ---
    _maybe_load(registry, "capabilities.uno_doc",       groups, approve_all)
    _maybe_load(registry, "capabilities.pty_terminal",   groups, approve_all)

    # --- Part 11: New Families (Calendar, Voice, Translation, QR, Scheduling, Remote) ---
    _maybe_load(registry, "capabilities.calendar",       groups, approve_all)
    _maybe_load(registry, "capabilities.voice",         groups, approve_all)
    _maybe_load(registry, "capabilities.translation",   groups, approve_all)
    _maybe_load(registry, "capabilities.qrcode",        groups, approve_all)
    _maybe_load(registry, "capabilities.scheduling",    groups, approve_all)
    _maybe_load(registry, "capabilities.remote",        groups, approve_all)

    return registry



def _maybe_load(
    registry: CapabilityRegistry,
    module_path: str,
    groups: list[str] | None,
    approve_all: bool,
) -> None:
    """Load a capability group module if it exists and is in the allowlist."""
    short_name = module_path.split(".")[-1]
    if groups is not None and short_name not in groups:
        return
    try:
        import importlib
        mod = importlib.import_module(module_path)
        if hasattr(mod, "install"):
            mod.install(registry, approve_all=approve_all)
    except ModuleNotFoundError:
        pass  # Part not built yet — silently skip
    except Exception as exc:
        import warnings
        warnings.warn(f"capabilities/{short_name}: failed to load: {exc}", stacklevel=2)


__all__ = ["Risk", "build_registry"]

"""
capabilities/security.py — Security, Credentials Vault, and Permission capabilities.

Covers NIKKI capability family: 33 (SECURITY)
"""
from __future__ import annotations

import os
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.safety_guard import is_dangerous_shell_command


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _security_vault_get(args: dict[str, Any], state: Any = None) -> Any:
        key = args.get("key", "")
        if not key:
            return fail("key is required")
        env_val = os.getenv(key.upper())
        if env_val:
            # NEVER return the secret value in capability results: results flow
            # into LLM context, plans, and logs. Presence + length only.
            return ok({"key": key, "found": True, "value_length": len(env_val)})
        return fail(f"Credential key '{key}' not found in environment or vault")

    register_cap(registry, Cap("security.vault_get", "Check credential key exists (presence only — never reveals values)", Cap.READ, ("key",)), _security_vault_get)

    def _security_audit_command(args: dict[str, Any], state: Any = None) -> Any:
        cmd = args.get("command", "")
        dangerous = is_dangerous_shell_command(cmd)
        return ok({"command": cmd, "safe": not dangerous, "dangerous_pattern": dangerous})

    register_cap(registry, Cap("security.audit_safety", "Check if a shell command passes safety audit", Cap.READ, ("command",)), _security_audit_command)

    def _security_vault_set(args: dict[str, Any], state: Any = None) -> Any:
        key = args.get("key", "")
        value = args.get("value", "")
        if not key or not value:
            return fail("key and value are required")
        try:

            os.environ[key.upper()] = value
            return ok({"key": key, "stored": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("security.vault_set", "Store credential key-value in session vault", Cap.WRITE, ("key", "value")), _security_vault_set)

    def _security_vault_list(args: dict[str, Any], state: Any = None) -> Any:
        try:

            vault_keys = [k for k in os.environ if k.startswith(("VAULT_", "API_", "SECRET_"))]
            return ok({"keys": vault_keys, "count": len(vault_keys)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("security.vault_list", "List stored credential key names without revealing values", Cap.READ, ()), _security_vault_list)

    def _security_vault_delete(args: dict[str, Any], state: Any = None) -> Any:
        key = args.get("key", "")
        if not key:
            return fail("key is required")
        try:
            if key.upper() in os.environ:
                del os.environ[key.upper()]
                return ok({"key": key, "deleted": True})
            return fail(f"Key '{key}' not found in vault")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("security.vault_delete", "Delete credential key from session vault", Cap.DESTRUCTIVE, ("key",)), _security_vault_delete)

    def _security_generate_password(args: dict[str, Any], state: Any = None) -> Any:
        import secrets
        import string
        length = int(args.get("length", 16))
        include_special = args.get("special", True)

        chars = string.ascii_letters + string.digits
        if include_special:
            chars += "!@#$%^&*()-_=+"

        pwd = "".join(secrets.choice(chars) for _ in range(length))
        return ok({"password": pwd, "length": length})

    register_cap(registry, Cap("security.generate_password", "Generate secure random password or secret key", Cap.READ, ("length",)), _security_generate_password)


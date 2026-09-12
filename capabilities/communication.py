"""
capabilities/communication.py — Messaging and Communication capabilities.

Covers NIKKI capability family: 31 (MESSAGING & COMMUNICATION)
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, register_cap


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _comm_send_whatsapp(args: dict[str, Any], state: Any = None) -> Any:
        phone = args.get("phone", "")
        message = args.get("message", "")
        try:
            from tasks.whatsapp_send import execute as send_wa
            res = send_wa({"phone": phone, "message": message})
            return res
        except Exception as e:
            return fail(f"WhatsApp send failed: {e}")

    register_cap(registry, Cap("comm.send_whatsapp", "Send WhatsApp message via Web/URI", Cap.COMMUNICATION, ("phone", "message")), _comm_send_whatsapp)

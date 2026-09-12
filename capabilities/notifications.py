"""
capabilities/notifications.py — Desktop Notifications capabilities.

Covers NIKKI capability family: 14 (NOTIFICATIONS)
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.system_utils import notify_send


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _notification_send(args: dict[str, Any], state: Any = None) -> Any:
        title = args.get("title", "Notification")
        message = args.get("message", "")
        urgency = args.get("urgency", "normal")
        timeout = str(args.get("timeout", 5000))
        try:
            sent = notify_send(message, title=title, urgency=urgency, timeout=timeout)
            if sent:
                return ok({"sent": True, "title": title, "message": message})
            return fail("notify-send failed or not installed")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("notification.send", "Send desktop notification", Cap.COMMUNICATION, ("title", "message", "urgency")), _notification_send)

    def _notification_read(args: dict[str, Any], state: Any = None) -> Any:
        """Read pending notifications via dbus/gdbus."""
        try:
            import subprocess
            out = subprocess.check_output(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                 "--object-path", "/org/freedesktop/Notifications", "--method",
                 "org.freedesktop.Notifications.GetCapabilities"],
                text=True, timeout=5
            )
            return ok({"capabilities": out.strip(), "note": "Use notification manager to read pending"})
        except Exception as e:
            return ok({"note": f"Cannot read notifications via dbus: {e}"})

    register_cap(registry, Cap("notification.read", "Read pending desktop notifications", Cap.READ, ()), _notification_read)

    def _notification_dismiss(args: dict[str, Any], state: Any = None) -> Any:
        nid = args.get("id", 0)
        try:
            import subprocess
            subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                 "--object-path", "/org/freedesktop/Notifications", "--method",
                 "org.freedesktop.Notifications.CloseNotification", str(nid)],
                timeout=5, capture_output=True, check=False
            )
            return ok({"dismissed": True, "id": nid})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("notification.dismiss", "Dismiss a desktop notification by ID", Cap.LOW, ("id",)), _notification_dismiss)

    def _notification_monitor(args: dict[str, Any], state: Any = None) -> Any:
        """Return a snapshot of the notification daemon status."""
        try:
            import subprocess
            out = subprocess.check_output(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                 "--object-path", "/org/freedesktop/Notifications", "--method",
                 "org.freedesktop.Notifications.GetServerInformation"],
                text=True, timeout=5
            )
            return ok({"server_info": out.strip(), "monitoring": True})
        except Exception as e:
            return ok({"monitoring": False, "note": str(e)})

    register_cap(registry, Cap("notification.monitor", "Monitor notification daemon status", Cap.READ, ()), _notification_monitor)


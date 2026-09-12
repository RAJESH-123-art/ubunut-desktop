"""
capabilities/system.py — System, Power, Bluetooth, and Settings capabilities.

Covers NIKKI capability families: 1 (SYSTEM), 17 (POWER), 18 (BLUETOOTH), 34 (SETTINGS)

Risk levels:
    READ          — get_info, get_uptime, get_battery, get_setting, bluetooth_status
    LOW           — lock_screen
    SYSTEM_CHANGE — suspend, hibernate, logout, set_setting, install_package, bluetooth_connect
    DESTRUCTIVE   — remove_package
    CRITICAL      — shutdown, restart (requires_confirmation=True)
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell

# ─── helpers ─────────────────────────────────────────────────────────────────

def _gui() -> Any:
    """Lazy singleton GUIController."""
    from core.gui_controller import GUIController
    return GUIController()


def _run(cmd: list[str] | str, shell: bool = False, timeout: int = 30) -> tuple[int, str, str]:
    return run_shell(cmd, timeout=timeout, shell=shell)


# ─── install ──────────────────────────────────────────────────────────────────

def install(registry: Any, *, approve_all: bool = False) -> None:

    # ── system.get_info ──────────────────────────────────────────────────────
    def _get_info(args: dict, state: Any) -> Any:
        try:
            import socket
            rc, uptime_raw, _ = _run(["cat", "/proc/uptime"])
            uptime_secs = float(uptime_raw.split()[0]) if rc == 0 and uptime_raw.split() else 0
            hours, rem = divmod(int(uptime_secs), 3600)
            minutes = rem // 60

            _rc2, mem_raw, _ = _run(["free", "-m"])
            mem_lines = mem_raw.splitlines()
            mem_info: dict = {}
            if len(mem_lines) > 1:
                parts = mem_lines[1].split()
                if len(parts) >= 3:
                    mem_info = {"total_mb": int(parts[1]), "used_mb": int(parts[2])}

            rc3, cpu_raw, _ = _run(["nproc"])
            cpu_count = int(cpu_raw.strip()) if rc3 == 0 and cpu_raw.strip().isdigit() else 0

            rc4, disk_raw, _ = _run(["df", "-h", "/"])
            disk_info: dict = {}
            if rc4 == 0:
                lines = disk_raw.splitlines()
                if len(lines) > 1:
                    parts = lines[1].split()
                    if len(parts) >= 5:
                        disk_info = {"total": parts[1], "used": parts[2], "available": parts[3], "use_pct": parts[4]}

            return ok({
                "hostname": socket.gethostname(),
                "os": platform.platform(),
                "kernel": platform.release(),
                "cpu_cores": cpu_count,
                "uptime": f"{hours}h {minutes}m",
                "memory": mem_info,
                "disk": disk_info,
                "username": os.environ.get("USER", ""),
            })
        except Exception as e:
            return fail(f"get_info failed: {e}")

    register_cap(registry, Cap(
        name="system.get_info",
        description="Return hostname, OS version, uptime, CPU cores, RAM, and disk usage.",
        side_effect="read",
        observable_outcomes=("System info dict",),
    ), _get_info)

    # ── system.get_uptime ────────────────────────────────────────────────────
    def _get_uptime(args: dict, state: Any) -> Any:
        try:
            rc, out, _ = _run(["uptime", "-p"])
            return ok({"uptime": out.strip() if rc == 0 else "unknown"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.get_uptime",
        description="Return human-readable system uptime.",
        side_effect="read",
    ), _get_uptime)

    # ── system.get_username ──────────────────────────────────────────────────
    def _get_username(args: dict, state: Any) -> Any:
        try:
            rc, out, _ = _run(["whoami"])
            return ok({"username": out.strip() if rc == 0 else os.environ.get("USER", "")})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.get_username",
        description="Return the current logged-in username.",
        side_effect="read",
    ), _get_username)

    # ── system.shutdown ──────────────────────────────────────────────────────
    def _shutdown(args: dict, state: Any) -> Any:
        try:
            delay = str(args.get("delay_minutes", 0))
            cmd = ["sudo", "shutdown", f"+{delay}"] if delay != "0" else ["sudo", "shutdown", "now"]
            rc, _, err = _run(cmd, timeout=10)
            if rc != 0:
                return fail(f"shutdown failed: {err}")
            return ok({"message": "Shutdown initiated"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.shutdown",
        description="Shut down the system. CRITICAL — requires confirmation.",
        side_effect="system",
        inputs=("delay_minutes",),
        requires_confirmation=True,
        recovery_hints=("Cancel with: sudo shutdown -c",),
    ), _shutdown)

    # ── system.restart ───────────────────────────────────────────────────────
    def _restart(args: dict, state: Any) -> Any:
        try:
            rc, _, err = _run(["sudo", "reboot"], timeout=10)
            if rc != 0:
                return fail(f"reboot failed: {err}")
            return ok({"message": "Restart initiated"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.restart",
        description="Restart the system. CRITICAL — requires confirmation.",
        side_effect="system",
        requires_confirmation=True,
    ), _restart)

    # ── system.suspend ───────────────────────────────────────────────────────
    def _suspend(args: dict, state: Any) -> Any:
        try:
            rc, _, err = _run(["systemctl", "suspend"], timeout=10)
            return ok({"message": "Suspended"}) if rc == 0 else fail(err)
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.suspend",
        description="Suspend (sleep) the system.",
        side_effect="system",
        requires_confirmation=True,
    ), _suspend)

    # ── system.hibernate ────────────────────────────────────────────────────
    def _hibernate(args: dict, state: Any) -> Any:
        try:
            rc, _, err = _run(["systemctl", "hibernate"], timeout=10)
            return ok({"message": "Hibernating"}) if rc == 0 else fail(err)
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.hibernate",
        description="Hibernate the system.",
        side_effect="system",
        requires_confirmation=True,
    ), _hibernate)

    # ── system.lock_screen ───────────────────────────────────────────────────
    def _lock_screen(args: dict, state: Any) -> Any:
        try:
            for cmd in [["loginctl", "lock-session"], ["gnome-screensaver-command", "-l"], ["xdg-screensaver", "lock"]]:
                if shutil.which(cmd[0]):
                    rc, _, _ = _run(cmd, timeout=5)
                    if rc == 0:
                        return ok({"message": "Screen locked"})
            return fail("No lock screen command found (tried loginctl, gnome-screensaver-command, xdg-screensaver)")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.lock_screen",
        description="Lock the screen immediately.",
        side_effect="system",
    ), _lock_screen)

    # ── system.logout ────────────────────────────────────────────────────────
    def _logout(args: dict, state: Any) -> Any:
        try:
            for cmd in [["gnome-session-quit", "--logout", "--no-prompt"], ["loginctl", "terminate-session", ""]]:
                if shutil.which(cmd[0]):
                    rc, _, _err = _run(cmd, timeout=10)
                    if rc == 0:
                        return ok({"message": "Logout initiated"})
            return fail("No logout command available")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.logout",
        description="Log out of the current session.",
        side_effect="system",
        requires_confirmation=True,
    ), _logout)

    # ── system.get_setting ───────────────────────────────────────────────────
    def _get_setting(args: dict, state: Any) -> Any:
        try:
            schema = str(args.get("schema", ""))
            key = str(args.get("key", ""))
            if not schema or not key:
                return fail("'schema' and 'key' are required")
            rc, out, err = _run(["gsettings", "get", schema, key], timeout=10)
            if rc != 0:
                return fail(f"gsettings get failed: {err}")
            return ok({"schema": schema, "key": key, "value": out.strip()})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.get_setting",
        description="Read a GNOME gsettings value. Inputs: schema, key.",
        side_effect="read",
        inputs=("schema", "key"),
    ), _get_setting)

    # ── system.set_setting ───────────────────────────────────────────────────
    def _set_setting(args: dict, state: Any) -> Any:
        try:
            schema = str(args.get("schema", ""))
            key = str(args.get("key", ""))
            value = str(args.get("value", ""))
            if not schema or not key:
                return fail("'schema' and 'key' are required")
            rc, _, err = _run(["gsettings", "set", schema, key, value], timeout=10)
            if rc != 0:
                return fail(f"gsettings set failed: {err}")
            return ok({"message": f"Set {schema} {key} = {value}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.set_setting",
        description="Write a GNOME gsettings value. Inputs: schema, key, value.",
        side_effect="system",
        inputs=("schema", "key", "value"),
        requires_confirmation=True,
    ), _set_setting)

    # ── system.install_package ───────────────────────────────────────────────
    def _install_package(args: dict, state: Any) -> Any:
        try:
            package = str(args.get("package", "")).strip()
            if not package:
                return fail("'package' name is required")
            env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            result = subprocess.run(
                ["sudo", "apt-get", "install", "-y", package],
                capture_output=True, text=True, timeout=300, env=env,
                check=False,  # callers branch on returncode
            )
            if result.returncode != 0:
                return fail(f"apt install failed: {result.stderr.strip()}")
            return ok({"message": f"Installed {package}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.install_package",
        description="Install a system package via apt-get. Inputs: package.",
        side_effect="system",
        inputs=("package",),
        requires_confirmation=True,
    ), _install_package)

    # ── system.remove_package ────────────────────────────────────────────────
    def _remove_package(args: dict, state: Any) -> Any:
        try:
            package = str(args.get("package", "")).strip()
            if not package:
                return fail("'package' name is required")
            env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            result = subprocess.run(
                ["sudo", "apt-get", "remove", "-y", package],
                capture_output=True, text=True, timeout=120, env=env,
                check=False,  # callers branch on returncode
            )
            if result.returncode != 0:
                return fail(f"apt remove failed: {result.stderr.strip()}")
            return ok({"message": f"Removed {package}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.remove_package",
        description="Remove a system package via apt-get. Inputs: package.",
        side_effect="system",
        inputs=("package",),
        requires_confirmation=True,
    ), _remove_package)

    # ── system.list_packages ─────────────────────────────────────────────────
    def _list_packages(args: dict, state: Any) -> Any:
        try:
            query = str(args.get("query", "")).strip()
            if query:
                _rc, out, _ = _run(["dpkg", "-l", f"*{query}*"])
            else:
                _rc, out, _ = _run(["dpkg", "-l"])
            packages = []
            for line in out.splitlines():
                if line.startswith("ii"):
                    parts = line.split()
                    if len(parts) >= 3:
                        packages.append({"name": parts[1], "version": parts[2]})
            return ok({"packages": packages, "count": len(packages)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.list_packages",
        description="List installed packages, optionally filtered by query. Inputs: query.",
        side_effect="read",
        inputs=("query",),
    ), _list_packages)

    # ── system.get_battery ───────────────────────────────────────────────────
    def _get_battery(args: dict, state: Any) -> Any:
        try:
            # Try upower first
            if shutil.which("upower"):
                rc, out, _ = _run(["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"], timeout=5)
                if rc == 0:
                    data: dict = {}
                    for line in out.splitlines():
                        if "percentage" in line:
                            data["percentage"] = line.split(":")[1].strip()
                        elif "state" in line and "power supply" not in line:
                            data["state"] = line.split(":")[1].strip()
                        elif "time to" in line:
                            data["time_remaining"] = line.split(":")[1].strip()
                    return ok(data or {"message": "Battery info not available"})
            # Fallback: /sys/class/power_supply
            bat_path = Path("/sys/class/power_supply/BAT0")
            if bat_path.exists():
                capacity = (bat_path / "capacity").read_text().strip()
                status = (bat_path / "status").read_text().strip()
                return ok({"percentage": f"{capacity}%", "state": status})
            return ok({"message": "No battery detected (desktop system)"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="system.get_battery",
        description="Return battery status: percentage, charging state, time remaining.",
        side_effect="read",
    ), _get_battery)

    # ── bluetooth.status ─────────────────────────────────────────────────────
    def _bt_status(args: dict, state: Any) -> Any:
        try:
            rc, out, _ = _run(["rfkill", "list", "bluetooth"], timeout=5)
            if rc != 0:
                return fail("rfkill not available")
            soft_blocked = "Soft blocked: yes" in out
            hard_blocked = "Hard blocked: yes" in out
            return ok({"enabled": not soft_blocked and not hard_blocked, "soft_blocked": soft_blocked, "hard_blocked": hard_blocked})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="bluetooth.status",
        description="Return Bluetooth enabled/blocked status.",
        side_effect="read",
    ), _bt_status)

    # ── bluetooth.scan ───────────────────────────────────────────────────────
    def _bt_scan(args: dict, state: Any) -> Any:
        try:
            timeout_s = int(args.get("timeout", 5))
            proc = subprocess.Popen(
                ["bluetoothctl", "scan", "on"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            time.sleep(timeout_s)
            proc.terminate()
            # List devices
            _rc, out, _ = _run(["bluetoothctl", "devices"], timeout=5)
            devices = []
            for line in out.splitlines():
                parts = line.strip().split(" ", 2)
                if len(parts) >= 3:
                    devices.append({"address": parts[1], "name": parts[2]})
            return ok({"devices": devices})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="bluetooth.scan",
        description="Scan for nearby Bluetooth devices for a few seconds. Inputs: timeout.",
        side_effect="read",
        inputs=("timeout",),
    ), _bt_scan)

    # ── bluetooth.connect ────────────────────────────────────────────────────
    def _bt_connect(args: dict, state: Any) -> Any:
        try:
            address = str(args.get("address", "")).strip()
            if not address:
                return fail("'address' (Bluetooth MAC) is required")
            rc, out, err = _run(["bluetoothctl", "connect", address], timeout=15)
            if rc != 0 or "Failed" in out:
                return fail(f"Bluetooth connect failed: {err or out}")
            return ok({"message": f"Connected to {address}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="bluetooth.connect",
        description="Connect to a Bluetooth device by MAC address. Inputs: address.",
        side_effect="system",
        inputs=("address",),
        requires_confirmation=True,
    ), _bt_connect)

    # ── bluetooth.disconnect ─────────────────────────────────────────────────
    def _bt_disconnect(args: dict, state: Any) -> Any:
        try:
            address = str(args.get("address", "")).strip()
            if not address:
                return fail("'address' is required")
            rc, _, err = _run(["bluetoothctl", "disconnect", address], timeout=10)
            return ok({"message": f"Disconnected {address}"}) if rc == 0 else fail(err)
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="bluetooth.disconnect",
        description="Disconnect a Bluetooth device. Inputs: address.",
        side_effect="system",
        inputs=("address",),
    ), _bt_disconnect)

    # ── bluetooth.pair ───────────────────────────────────────────────────────
    def _bt_pair(args: dict, state: Any) -> Any:
        try:
            address = str(args.get("address", "")).strip()
            if not address:
                return fail("'address' is required")
            rc, out, err = _run(["bluetoothctl", "pair", address], timeout=30)
            if rc != 0 or "Failed" in out:
                return fail(f"Pairing failed: {err or out}")
            return ok({"message": f"Paired with {address}"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="bluetooth.pair",
        description="Pair with a Bluetooth device. Inputs: address.",
        side_effect="system",
        inputs=("address",),
        requires_confirmation=True,
    ), _bt_pair)

"""
capabilities/network.py — Network and WiFi capabilities.
"""
from __future__ import annotations

import time
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def install(registry: Any, *, approve_all: bool = False) -> None:
    def check_status(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            rc, _out, _err = run_shell(["ping", "-c", "1", "8.8.8.8"])
            connected = (rc == 0)
            return ok({"connected": connected})
        except Exception as e:
            return fail(str(e))

    def list_interfaces(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _rc, out, _err = run_shell(["ip", "link", "show"])
            return ok({"interfaces": out})
        except Exception as e:
            return fail(str(e))

    def get_ip(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            iface = args.get("interface")
            cmd = ["ip", "addr", "show"]
            if iface:
                cmd.append(iface)
            _rc, out, _err = run_shell(cmd)
            return ok({"ip_info": out})
        except Exception as e:
            return fail(str(e))

    def get_dns(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _rc, out, _err = run_shell(["cat", "/etc/resolv.conf"])
            return ok({"dns": out})
        except Exception as e:
            return fail(str(e))

    def ping(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            host = args["host"]
            count = str(args.get("count", 4))
            rc, out, _err = run_shell(["ping", "-c", count, host])
            return ok({"output": out, "success": rc == 0})
        except Exception as e:
            return fail(str(e))

    def test_connection(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            url = args["url"]
            import requests
            res = requests.get(url, timeout=10)
            return ok({"status_code": res.status_code, "connected": res.status_code < 400})
        except ImportError:
            rc, out, _err = run_shell(["curl", "-I", "-s", url])
            return ok({"output": out, "success": rc == 0})
        except Exception as e:
            return fail(str(e))

    def download(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            url = args["url"]
            dest = args["destination"]
            rc, out, err = run_shell(["wget", "-O", dest, url])
            return ok({"status": "downloaded" if rc == 0 else "failed", "output": err or out})
        except Exception as e:
            return fail(str(e))

    def upload(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            url = args["url"]
            path = args["file_path"]
            rc, out, _err = run_shell(["curl", "-F", f"file=@{path}", url])
            return ok({"output": out, "success": rc == 0})
        except Exception as e:
            return fail(str(e))

    def wifi_list(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _rc, out, _err = run_shell(["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,SECURITY", "dev", "wifi"])
            return ok({"networks": out})
        except Exception as e:
            return fail(str(e))

    def wifi_connect(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            ssid = args["ssid"]
            password = args.get("password", "")
            cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            if password:
                cmd.extend(["password", password])
            rc, out, _err = run_shell(cmd)
            return ok({"success": rc == 0, "output": out})
        except Exception as e:
            return fail(str(e))

    def wifi_disconnect(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            iface = args.get("interface", "wlan0")
            rc, out, _err = run_shell(["nmcli", "dev", "disconnect", iface])
            return ok({"success": rc == 0, "output": out})
        except Exception as e:
            return fail(str(e))

    def wifi_status(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _rc, out, _err = run_shell(["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID,SIGNAL", "dev", "wifi"])
            return ok({"status": out})
        except Exception as e:
            return fail(str(e))

    def wifi_scan(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            rc, _out, _err = run_shell(["nmcli", "dev", "wifi", "rescan"])
            return ok({"success": rc == 0})
        except Exception as e:
            return fail(str(e))

    def network_monitor(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            dur = int(args.get("duration", 5))
            time.sleep(dur) # Mocking monitor
            return ok({"status": "monitored", "duration": dur})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("network.status", "Check status", "read"), check_status)
    register_cap(registry, Cap("network.list_interfaces", "List interfaces", "read"), list_interfaces)
    register_cap(registry, Cap("network.get_ip", "Get IP", "read", ("interface",)), get_ip)
    register_cap(registry, Cap("network.get_dns", "Get DNS", "read"), get_dns)
    register_cap(registry, Cap("network.ping", "Ping host", "read", ("host", "count")), ping)
    register_cap(registry, Cap("network.test_connection", "Test connection", "read", ("url",)), test_connection)
    register_cap(registry, Cap("network.download", "Download", "local_write", ("url", "destination")), download)
    register_cap(registry, Cap("network.upload", "Upload", "external", ("url", "file_path")), upload)
    register_cap(registry, Cap("wifi.list", "List WiFi", "read"), wifi_list)
    register_cap(registry, Cap("wifi.connect", "Connect WiFi", "system", ("ssid", "password"), requires_confirmation=True), wifi_connect)
    register_cap(registry, Cap("wifi.disconnect", "Disconnect WiFi", "system", ("interface",)), wifi_disconnect)
    register_cap(registry, Cap("wifi.status", "WiFi status", "read"), wifi_status)
    register_cap(registry, Cap("wifi.scan", "WiFi scan", "read"), wifi_scan)
    register_cap(registry, Cap("network.monitor", "Monitor network", "read", ("duration",)), network_monitor)

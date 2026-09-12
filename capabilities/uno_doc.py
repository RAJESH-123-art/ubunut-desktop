"""
capabilities/uno_doc.py — LibreOffice UNO API Adapter capabilities.

Covers NIKKI capability family: 37 (APPLICATION ADAPTERS / LIBREOFFICE UNO)
"""
from __future__ import annotations

import shutil
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _uno_status(args: dict[str, Any], state: Any = None) -> Any:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        return ok({"available": bool(soffice), "binary": soffice or ""})

    register_cap(registry, Cap("uno.status", "Check if LibreOffice UNO runtime is available", Cap.READ, ()), _uno_status)

    def _uno_connect(args: dict[str, Any], state: Any = None) -> Any:
        port = int(args.get("port", 2002))
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            return fail("LibreOffice not installed")
        # Start a real headless UNO listener and verify it is accepting
        import subprocess as sp
        try:
            proc = sp.Popen(
                [soffice, "--headless", "--invisible", "--norestore",
                 f"--accept=socket,host=localhost,port={port};urp;"],
                stdout=sp.DEVNULL, stderr=sp.DEVNULL,
            )
        except Exception as e:
            return fail(f"failed to launch soffice UNO listener: {e}")
        # Verify the socket is actually accepting before claiming connected
        import socket
        import time
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("localhost", port), timeout=1):
                    return ok({"connected": True, "port": port, "mode": "uno_socket", "pid": proc.pid})
            except OSError:
                time.sleep(0.5)
        proc.terminate()
        return fail(f"soffice UNO listener did not accept connections on port {port} within 15s")

    register_cap(registry, Cap("uno.connect", "Start LibreOffice headless UNO socket server and verify it accepts connections", Cap.LOW, ("port",)), _uno_connect)

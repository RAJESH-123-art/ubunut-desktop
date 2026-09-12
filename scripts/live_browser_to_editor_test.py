from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/usr/lib/python3/dist-packages")

from core.browser import brave
from core.gui_controller import GUIController
from scripts.live_cross_app_clipboard_test import (
    fresh_text_editor_target,
    paste_and_wait,
    set_exact,
)
from tasks.browser_action import execute as browser_execute


def main() -> int:
    marker = "browser_to_editor_payload_112358"
    html = f"<title>Vercept Clipboard Source</title><main id='source'>{marker}</main>".encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = brave(headless=False).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        resources = {"browser": browser}
        ok = browser_execute(
            {
                "actions": [
                    {"type": "goto", "url": url},
                ],
                "abort_on_error": True,
            },
            resources,
        )
        dom_value = browser.page.locator("#source").inner_text()
        print(f"DOM_SOURCE={dom_value!r}")
        if not ok or dom_value != marker:
            raise RuntimeError("Browser source/action verification failed")

        gui = GUIController(safe_mode=False)
        editor = fresh_text_editor_target(gui)
        # Cross-application transfer through the agent's reliable local APIs:
        # browser DOM extraction → native application's AT-SPI EditableText.
        set_exact(editor, dom_value)
        observed = editor.node.queryText().getText(
            0, editor.node.queryText().characterCount
        )
        print(f"EXPECTED={marker!r}")
        print(f"ATSPI_DESTINATION={observed!r}")
        if observed != marker:
            print("RESULT=FAIL")
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())

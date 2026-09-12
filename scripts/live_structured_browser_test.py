from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.structured_automation import StructuredExecutor, validate_plan


def main() -> int:
    source_value = "structured_live_value_161803"
    first_page = f"""<!doctype html>
        <title>Structured Source</title>
        <main>
          <p id="source">{source_value}</p>
          <label>Destination <input aria-label="Destination"></label>
          <label>Confirm <input type="checkbox" aria-label="Confirm"></label>
          <label>Billing country <select><option>Choose</option><option>Canada</option></select></label>
          <label>Shipping country <select><option>Choose</option><option>Canada</option></select></label>
          <button onclick="if(confirm('Proceed with local test?')) document.querySelector('#dialog-status').textContent='Dialog confirmed'">Confirm dialog</button>
          <span id="dialog-status">Dialog pending</span>
          <button onclick="window.open('/second', '_blank')">Open popup</button>
        </main>
    """.encode()
    second_page = b"<!doctype html><title>Structured Second</title><p>Second page marker</p>"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = second_page if self.path.startswith("/second") else first_page
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = brave(headless=True).start()
    execution_id = "live-structured-browser"
    try:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        browser.page.evaluate(
            "value => { window.name = value; }",
            f"desktop-agent:{execution_id}",
        )
        plan = validate_plan({
            "summary": "Exercise generic structured browser primitives",
            "steps": [
                {"action": "navigate", "args": {"url": origin}},
                {"action": "extract_text", "args": {"target": source_value}},
                {
                    "action": "transform_text",
                    "args": {"value": "${step.2.text}", "operation": "upper"},
                },
                {
                    "action": "assert_value",
                    "args": {
                        "value": "${step.3.value}",
                        "operator": "equals",
                        "expected": source_value.upper(),
                    },
                },
                {
                    "action": "click",
                    "args": {"text": "This control does not exist"},
                    "when": {
                        "value": "${step.2.text}",
                        "operator": "equals",
                        "expected": "never-match",
                    },
                },
                {
                    "action": "fill",
                    "args": {"label": "Destination", "text": "${step.2.text}"},
                },
                {"action": "read_field", "args": {"label": "Destination"}},
                {"action": "check", "args": {"label": "Confirm", "checked": True}},
                {
                    "action": "select_option",
                    "args": {"label": "Shipping country", "option": "Canada"},
                },
                {
                    "action": "click",
                    "args": {
                        "text": "Confirm dialog",
                        "dialog": {
                            "type": "confirm",
                            "accept": True,
                            "message_contains": "local test",
                        },
                    },
                    "expect": {"text": "Dialog confirmed"},
                },
                {
                    "action": "click",
                    "args": {
                        "text": "Open popup",
                        "popup": {
                            "url": f"{origin}/second",
                            "activate": True,
                            "title_contains": "Structured Second",
                        },
                    },
                },
                {"action": "extract_text", "args": {"target": "Second page marker"}},
                {
                    "action": "switch_tab",
                    "args": {"title_contains": "Structured Source"},
                },
                {"action": "read_field", "args": {"label": "Destination"}},
                {"action": "close_tab", "args": {}},
            ],
        })
        executor = StructuredExecutor(
            approve_all=True,
            execution_id=execution_id,
            step_timeout=5.0,
            total_timeout=30.0,
        )
        executor._page = browser.page
        result = executor.execute(plan, goal="live generic browser primitive test")
        print(f"SUCCESS={result.success}")
        print(f"COMPLETED={result.completed_steps}")
        print(f"MESSAGE={result.message}")
        extracted = result.evidence[1].get("text") if len(result.evidence) > 1 else None
        transformed = result.evidence[2].get("value") if len(result.evidence) > 2 else None
        asserted = result.evidence[3].get("asserted") if len(result.evidence) > 3 else None
        skipped = result.evidence[4].get("skipped") if len(result.evidence) > 4 else None
        first_read = result.evidence[6].get("text") if len(result.evidence) > 6 else None
        checked = result.evidence[7].get("checked") if len(result.evidence) > 7 else None
        selected = result.evidence[8].get("selected") if len(result.evidence) > 8 else None
        dialog_accepted = (
            result.evidence[9].get("dialog", {}).get("accepted")
            if len(result.evidence) > 9
            else None
        )
        popup_url = result.evidence[10].get("popup_url") if len(result.evidence) > 10 else None
        second_read = result.evidence[13].get("text") if len(result.evidence) > 13 else None
        print(f"EXTRACTED={extracted!r}")
        print(f"TRANSFORMED={transformed!r}")
        print(f"ASSERTED={asserted!r}")
        print(f"SKIPPED_FALSE_BRANCH={skipped!r}")
        print(f"FIRST_READ={first_read!r}")
        print(f"SECOND_READ={second_read!r}")
        print(f"CHECKED={checked!r}")
        print(f"SELECTED={selected!r}")
        print(f"DIALOG_ACCEPTED={dialog_accepted!r}")
        print(f"POPUP_URL={popup_url!r}")
        if not result.success:
            return 1
        if (
            extracted,
            transformed,
            asserted,
            skipped,
            first_read,
            second_read,
            checked,
            selected,
            dialog_accepted,
            popup_url,
        ) != (
            source_value,
            source_value.upper(),
            True,
            True,
            source_value,
            source_value,
            True,
            "Canada",
            True,
            f"{origin}/second",
        ):
            raise RuntimeError("Structured browser evidence did not match expected values")
        print("RESULT=PASS")
        return 0
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())

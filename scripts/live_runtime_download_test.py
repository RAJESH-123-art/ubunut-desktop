"""Live local proof of browser download followed by filesystem verification."""
from __future__ import annotations

import http.server
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.runtime_adapters import execute_structured_plan_runtime
from core.runtime_observer import RuntimeObserver
from core.structured_automation import StructuredExecutor, validate_plan


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = ("runtime download proof\n" * 120).encode()

    def do_GET(self) -> None:
        if self.path == "/artifact.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", "attachment; filename=runtime-artifact.txt")
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)
            return
        body = b"<!doctype html><a href='/artifact.txt'>Download runtime artifact</a>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def main() -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = brave(headless=True).start()
    executor = StructuredExecutor(approve_all=True, total_timeout=30.0, step_timeout=8.0)
    executor.attach_page(browser.page)
    try:
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            plan = validate_plan({
                "summary": "Download and verify one local browser artifact",
                "steps": [
                    {"action": "navigate", "args": {"url": origin}},
                    {"action": "download", "args": {
                        "text": "Download runtime artifact",
                        "directory": str(directory),
                        "wait_for_completion": True,
                        "filename_contains": "runtime-artifact",
                        "extensions": [".txt"],
                        "source_domain": "127.0.0.1",
                    }},
                    {"action": "verify_file", "args": {
                        "directory": str(directory),
                        "extensions": [".txt"],
                        "min_bytes": len(_Handler.payload),
                    }},
                ],
            })
            observer = RuntimeObserver(page=browser.page, directories=[directory])
            result = execute_structured_plan_runtime(
                plan,
                "download and verify one local browser artifact",
                executor=executor,
                page=browser.page,
                observe=lambda _runtime: observer.observe(),
            )
            files = list(directory.glob("*.txt"))
            print(f"SUCCESS={result.success}")
            print(f"MESSAGE={result.state.message}")
            print(f"SUBGOALS={[(key, item.status) for key, item in result.state.subgoals.items()]}")
            print(f"FILES={[(path.name, path.stat().st_size) for path in files]}")
            if not result.success or len(files) != 1 or files[0].read_bytes() != _Handler.payload:
                return 1
        print("RESULT=PASS")
        return 0
    finally:
        executor.close()
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())

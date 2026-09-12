from __future__ import annotations

import http.server
import socketserver
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/usr/lib/python3/dist-packages")

import uno
from com.sun.star.beans import PropertyValue

from core.browser import brave


def property_value(name: str, value: object) -> PropertyValue:
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def connect_uno(port: int) -> object:
    local_ctx = uno.getComponentContext()
    resolver = local_ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_ctx
    )
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            return resolver.resolve(
                f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
            )
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Could not connect to isolated LibreOffice UNO listener")


def main() -> int:
    values = ["browser_calc_label_271828", "314159"]
    html = (
        "<title>Vercept Calc Source</title>"
        f"<div id='label'>{values[0]}</div><div id='value'>{values[1]}</div>"
    ).encode()

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
    browser = brave(headless=True).start()
    office: subprocess.Popen[bytes] | None = None
    document = None
    output = Path("/tmp/vercept-browser-to-calc.ods")
    try:
        browser.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        observed = [
            browser.page.locator("#label").inner_text(),
            browser.page.locator("#value").inner_text(),
        ]
        print(f"DOM_SOURCE={observed!r}")
        if observed != values:
            raise RuntimeError("Browser DOM source mismatch")

        port = 20873
        office = subprocess.Popen(
            [
                "libreoffice",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ctx = connect_uno(port)
        desktop = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx
        )
        document = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, ())
        sheet = document.Sheets.getByIndex(0)
        sheet.getCellByPosition(0, 0).String = observed[0]
        sheet.getCellByPosition(1, 0).String = observed[1]
        if output.exists():
            output.unlink()
        document.storeAsURL(
            uno.systemPathToFileUrl(str(output)),
            (property_value("FilterName", "calc8"),),
        )
        document.close(True)
        document = None

        if not output.is_file():
            raise RuntimeError("ODS output was not created")
        with zipfile.ZipFile(output) as archive:
            mime = archive.read("mimetype")
            content = archive.read("content.xml").decode("utf-8")
        print(f"ODS_MIME={mime!r}")
        print(f"ODS_BYTES={output.stat().st_size}")
        for value in values:
            if value not in content:
                raise RuntimeError(f"ODS content.xml missing {value!r}")
        if mime != b"application/vnd.oasis.opendocument.spreadsheet":
            raise RuntimeError("Unexpected ODS MIME")
        print(f"ODS_VALUES={values!r}")
        print("RESULT=PASS")
        return 0
    finally:
        if document is not None:
            try:
                document.close(True)
            except Exception:
                pass
        if office is not None and office.poll() is None:
            office.terminate()
            try:
                office.wait(timeout=5)
            except subprocess.TimeoutExpired:
                office.kill()
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())

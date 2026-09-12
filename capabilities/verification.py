"""
capabilities/verification.py — Real verification capabilities.

Every verify.* cap MUST observe actual system state before reporting.
NO hardcoded verified:true. Evidence is always attached.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from capabilities.base import Cap, fail, ok, register_cap, run_shell
from core.capability_registry import CapabilityRegistry


def _verify_result(verified: bool, data: dict[str, Any], evidence_detail: str) -> Any:
    """Failed verification MUST return a failed TaskResult so the runtime
    triggers recovery/replan instead of continuing on an unverified state.
    The observed data is preserved on the failure for diagnosis."""
    if verified:
        return ok(data=data, evidence=(evidence_detail,))
    reason = data.get("reason") or f"verification failed: {evidence_detail}"
    result = fail(reason)
    result.data = data  # preserve observations for diagnosis
    return result


def _fail_unverified(reason: str, **extra: Any) -> Any:
    """Not-verified outcome: failed TaskResult carrying the observation."""
    data = {"verified": False, "reason": reason}
    data.update(extra)
    result = fail(reason)
    result.data = data
    return result


def install(registry: CapabilityRegistry, *, approve_all: bool = False) -> None:
    """Install Verification capabilities (read-only, evidence-backed)."""

    def _evidence(name: str, detail: str) -> list[dict[str, Any]]:
        return [{"check": name, "evidence": detail}]

    def ver_app_open(kwargs: dict[str, Any], state: Any) -> Any:
        name = str(kwargs.get("name", "")).strip()
        if not name:
            return fail("'name' is required")
        rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=5)
        running = rc == 0 and bool(out.strip())
        return _verify_result(
            running,
            {"verified": running, "name": name, "matching_pids": out.splitlines()},
            f"pgrep -fi {name!r} -> rc={rc}, matches={len(out.splitlines())}",
        )
    register_cap(registry, Cap(name="verify.app_open", description="verify app is running via pgrep", side_effect="read", inputs=("name",)), ver_app_open)

    def ver_window(kwargs: dict[str, Any], state: Any) -> Any:
        # Best available under Wayland: pgrep on the app + title via gdbus if available
        name = str(kwargs.get("name", kwargs.get("title", ""))).strip()
        if not name:
            return fail("'name' or 'title' is required")
        rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=5)
        running = rc == 0 and bool(out.strip())
        if not running and shutil.which("gdbus"):
            rc2, out2, _ = run_shell(
                ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                 "--object-path", "/org/gnome/Shell/Extensions/Windows",
                 "--method", "org.gnome.Shell.Extensions.Windows.List"],
                timeout=5,
            )
            running = rc2 == 0 and name.lower() in out2.lower()
        return _verify_result(running, {"verified": running, "name": name},
                               f"process/windows match for {name!r}: {running}")
    register_cap(registry, Cap(name="verify.window", description="verify window/app exists", side_effect="read", inputs=("name", "title")), ver_window)

    def ver_element(kwargs: dict[str, Any], state: Any) -> Any:
        # AT-SPI element lookup — the strongest available semantic source
        app = str(kwargs.get("app", "")).strip()
        query = str(kwargs.get("query", kwargs.get("text", ""))).strip()
        if not query:
            return fail("'query' or 'text' is required")
        try:
            from core.atspi_navigator import ATSPINavigator
            nav = ATSPINavigator()
            element = nav.find_element(app_name=app or None, text=query) if hasattr(nav, "find_element") else None
            found = element is not None
            return _verify_result(found, {"verified": found, "query": query, "app": app},
                                  f"AT-SPI lookup {query!r} in {app or 'any app'}: found={found}")
        except Exception as e:
            return _fail_unverified(f"AT-SPI lookup failed: {e}", query=query)
    register_cap(registry, Cap(name="verify.element", description="verify UI element exists via AT-SPI", side_effect="read", inputs=("query", "app", "text")), ver_element)

    def ver_text_visible(kwargs: dict[str, Any], state: Any) -> Any:
        text = str(kwargs.get("text", "")).strip()
        if not text:
            return fail("'text' is required")
        app = str(kwargs.get("app", "")).strip()
        try:
            from core.atspi_navigator import ATSPINavigator
            nav = ATSPINavigator()
            dump = nav.dump_visible_text(app_name=app or None) if hasattr(nav, "dump_visible_text") else None
            if dump is None:
                return _fail_unverified("AT-SPI text dump unavailable", text=text)
            found = text.lower() in str(dump).lower()
            return _verify_result(found, {"verified": found, "text": text},
                                  f"text {text!r} visible on screen: {found}")
        except Exception as e:
            return _fail_unverified(f"AT-SPI text check failed: {e}", text=text)
    register_cap(registry, Cap(name="verify.text_visible", description="verify text visible on screen via AT-SPI", side_effect="read", inputs=("text", "app")), ver_text_visible)

    def ver_click(kwargs: dict[str, Any], state: Any) -> Any:
        # Click effect = expected text/state must now be observable
        expected = str(kwargs.get("expected_change", kwargs.get("expected", ""))).strip()
        if not expected:
            return fail("'expected_change' is required — a click cannot be verified without an expected observable")
        return ver_text_visible({"text": expected, "app": kwargs.get("app", "")}, state)
    register_cap(registry, Cap(name="verify.click", description="verify click had observable effect (checks expected text via AT-SPI)", side_effect="read", inputs=("expected_change", "app")), ver_click)

    def ver_input_value(kwargs: dict[str, Any], state: Any) -> Any:
        app = str(kwargs.get("app", "")).strip()
        field = str(kwargs.get("field", kwargs.get("selector", ""))).strip()
        expected = kwargs.get("expected")
        if not field:
            return fail("'field' is required")
        try:
            from core.atspi_navigator import ATSPINavigator
            nav = ATSPINavigator()
            value = nav.read_field_value(app_name=app or None, field_name=field) if hasattr(nav, "read_field_value") else None
            if value is None:
                return _fail_unverified(f"could not read field {field!r} via AT-SPI", field=field)
            verified = expected is None or str(expected) == str(value)
            return _verify_result(verified, {"verified": verified, "field": field, "value": str(value), "expected": expected},
                                  f"field {field!r} = {value!r}")
        except Exception as e:
            return _fail_unverified(f"AT-SPI field read failed: {e}", field=field)
    register_cap(registry, Cap(name="verify.input_value", description="verify input field value via AT-SPI", side_effect="read", inputs=("field", "expected", "app", "selector")), ver_input_value)

    def ver_file_exists(kwargs: dict[str, Any], state: Any) -> Any:
        path = str(kwargs.get("path", "")).strip()
        if not path:
            return fail("'path' is required")
        exists = os.path.exists(os.path.expanduser(path))
        size = os.path.getsize(os.path.expanduser(path)) if exists else 0
        min_bytes = kwargs.get("min_bytes", 0)
        verified = exists and size >= int(min_bytes or 0)
        return _verify_result(
            verified,
            {"verified": verified, "path": path, "exists": exists, "size_bytes": size},
            f"os.path.exists({path!r})={exists}, size={size}",
        )
    register_cap(registry, Cap(name="verify.file_exists", description="verify file exists (with optional min_bytes)", side_effect="read", inputs=("path", "min_bytes")), ver_file_exists)

    def ver_folder_exists(kwargs: dict[str, Any], state: Any) -> Any:
        path = str(kwargs.get("path", "")).strip()
        if not path:
            return fail("'path' is required")
        expanded = os.path.expanduser(path)
        exists = os.path.isdir(expanded)
        count = len(os.listdir(expanded)) if exists else 0
        return _verify_result(
            exists,
            {"verified": exists, "path": path, "is_dir": exists, "item_count": count},
            f"os.path.isdir({path!r})={exists}",
        )
    register_cap(registry, Cap(name="verify.folder_exists", description="verify folder exists and is a directory", side_effect="read", inputs=("path",)), ver_folder_exists)

    def ver_download_complete(kwargs: dict[str, Any], state: Any) -> Any:
        folder = os.path.expanduser(str(kwargs.get("folder", kwargs.get("directory", ""))).strip() or "~/Downloads")
        filename = str(kwargs.get("filename", "")).strip()
        contains = str(kwargs.get("filename_contains", "")).strip()
        kwargs.get("extensions") or [".part", ".crdownload"]
        if not filename and not contains:
            return fail("'filename' or 'filename_contains' is required")
        target_dir = Path(folder).expanduser()
        if not target_dir.is_dir():
            return _fail_unverified(f"download folder {folder!r} does not exist", folder=folder)
        candidates = list(target_dir.glob(filename)) if filename else list(target_dir.iterdir())
        if contains:
            candidates = [f for f in candidates if contains.lower() in f.name.lower()]
        # Exclude temp/partial download suffixes
        partial_exts = {".part", ".crdownload", ".tmp"}
        complete_files = [f for f in candidates if f.is_file() and f.suffix.lower() not in partial_exts]
        if not complete_files:
            # Check whether a partial file exists (download in progress or stalled)
            partials = [f for f in candidates if f.is_file() and f.suffix.lower() in partial_exts]
            if partials:
                return _fail_unverified(
                    "download incomplete: only partial temp files found",
                    folder=folder, partial_files=[f.name for f in partials],
                )
            return _fail_unverified("no matching download file found", folder=folder)
        f = complete_files[0]
        min_bytes = int(kwargs.get("min_bytes", 1) or 1)
        size = f.stat().st_size
        verified = size >= min_bytes
        return _verify_result(
            verified,
            {"verified": verified, "path": str(f), "filename": f.name, "size_bytes": size, "min_bytes": min_bytes},
            f"complete file {f.name!r} size={size} >= {min_bytes}: {verified}",
        )
    register_cap(registry, Cap(name="verify.download_complete", description="verify download finished (complete file exists, no .part/.crdownload, size >= min_bytes)", side_effect="read", inputs=("filename", "folder", "filename_contains", "extensions", "min_bytes")), ver_download_complete)

    def ver_upload_complete(kwargs: dict[str, Any], state: Any) -> Any:
        # Uploads are verified by the receiving page state; check AT-SPI text if provided
        expected = str(kwargs.get("expected_text", kwargs.get("expected_change", ""))).strip()
        if not expected:
            return fail("'expected_text' is required — upload success must be observed on the receiving page")
        return ver_text_visible({"text": expected, "app": kwargs.get("app", "")}, state)
    register_cap(registry, Cap(name="verify.upload_complete", description="verify upload complete via expected page/screen text", side_effect="read", inputs=("expected_text", "app")), ver_upload_complete)

    def ver_message_sent(kwargs: dict[str, Any], state: Any) -> Any:
        # Verify the outgoing message bubble now exists in the conversation view
        text = str(kwargs.get("text", "")).strip()
        app = str(kwargs.get("app", "")).strip()
        if not text:
            return fail("'text' is required")
        return ver_text_visible({"text": text, "app": app}, state)
    register_cap(registry, Cap(name="verify.message_sent", description="verify message visible in conversation (outgoing bubble) via AT-SPI", side_effect="read", inputs=("text", "app")), ver_message_sent)

    def ver_document(kwargs: dict[str, Any], state: Any) -> Any:
        path = Path(str(kwargs.get("path", ""))).expanduser()
        if not path.is_file():
            return _fail_unverified(f"document {path} not found", path=str(path))
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if suffix == ".docx":
            try:
                import docx as _docx
                d = _docx.Document(str(path))
                paras = len(d.paragraphs)
                return ok(data={"verified": True, "path": str(path), "size_bytes": size, "paragraphs": paras},
                          evidence=(f"opened docx with {paras} paragraphs",))
            except Exception as e:
                return _fail_unverified(f"docx unreadable: {e}", path=str(path))
        if size < 32:
            return _fail_unverified(f"document {path} suspiciously small ({size} bytes)", path=str(path))
        return ok(data={"verified": True, "path": str(path), "size_bytes": size},
                  evidence=(f"{path.name} exists, {size} bytes",))
    register_cap(registry, Cap(name="verify.document", description="verify document exists and is parseable", side_effect="read", inputs=("path",)), ver_document)

    def ver_spreadsheet(kwargs: dict[str, Any], state: Any) -> Any:
        path = Path(str(kwargs.get("path", ""))).expanduser()
        if not path.is_file():
            return _fail_unverified(f"spreadsheet {path} not found", path=str(path))
        suffix = path.suffix.lower()
        cell = str(kwargs.get("cell", "")).strip()
        expected = kwargs.get("expected")
        if suffix == ".xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(str(path), read_only=True)
                result: dict[str, Any] = {"verified": True, "path": str(path), "sheets": wb.sheetnames}
                if cell:
                    ws = wb.active
                    value = ws[cell].value
                    if expected is not None:
                        result["verified"] = str(value) == str(expected)
                    result["cell"] = cell
                    result["value"] = str(value)
                wb.close()
                return _verify_result(bool(result["verified"]), result,
                                      f"xlsx sheets={result.get('sheets')}, cell {cell or '(none)'} checked")
            except Exception as e:
                return _fail_unverified(f"xlsx unreadable: {e}", path=str(path))
        if suffix == ".csv":
            try:
                import csv as _csv
                with open(path, newline="", encoding="utf-8") as fh:
                    rows = list(_csv.reader(fh))
                result = {"verified": len(rows) > 0, "path": str(path), "row_count": len(rows)}
                if cell:
                    # A1 notation (e.g. "B2") -> row/col indexes
                    def _a1_to_idx(a1: str) -> tuple[int, int]:
                        letters = "".join(ch for ch in a1 if ch.isalpha()).upper()
                        digits = "".join(ch for ch in a1 if ch.isdigit())
                        col_idx = 0
                        for ch in letters:
                            col_idx = col_idx * 26 + (ord(ch) - 64)
                        return int(digits) - 1, col_idx - 1
                    try:
                        r, c = _a1_to_idx(cell)
                        value = rows[r][c] if r < len(rows) and c < len(rows[r]) else None
                        result["cell"] = cell
                        result["value"] = value
                        if expected is not None:
                            result["verified"] = str(value) == str(expected)
                    except Exception as cell_err:
                        logger.debug(f"verify.spreadsheet: cell {cell!r} unreadable: {cell_err}")
                    return _verify_result(bool(result["verified"]), result, f"parsed CSV rows={len(rows)}")
            except Exception as e:
                return _fail_unverified(f"CSV unreadable: {e}", path=str(path))
        return _fail_unverified(f"unsupported spreadsheet format {suffix!r}", path=str(path))
    register_cap(registry, Cap(name="verify.spreadsheet", description="verify spreadsheet exists and cell content matches (xlsx via openpyxl, CSV via csv module)", side_effect="read", inputs=("path", "cell", "expected")), ver_spreadsheet)

    def ver_browser_url(kwargs: dict[str, Any], state: Any) -> Any:
        expected = str(kwargs.get("expected_url", kwargs.get("url", ""))).strip()
        partial = bool(kwargs.get("partial", True))
        if not expected:
            return fail("'expected_url' is required")
        try:
            from capabilities.browser import _browser
            if not _browser:
                return _fail_unverified("browser not open — no URL to verify", expected_url=expected)
            page = _browser.page
            current = str(page.url)
            verified = (expected in current) if partial else (current.rstrip('/') == expected.rstrip('/'))
            return _verify_result(verified, {"verified": verified, "current_url": current, "expected_url": expected},
                                  f"current={current!r} expected~{expected!r} match={verified}")
        except Exception as e:
            return _fail_unverified(f"browser URL check failed: {e}", expected_url=expected)
    register_cap(registry, Cap(name="verify.browser_url", description="verify current browser page URL matches expected", side_effect="read", inputs=("expected_url", "url", "partial")), ver_browser_url)

    def ver_process_running(kwargs: dict[str, Any], state: Any) -> Any:
        name = str(kwargs.get("name", "")).strip()
        if not name:
            return fail("'name' is required")
        rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=5)
        running = rc == 0 and bool(out.strip())
        return _verify_result(running, {"verified": running, "name": name, "pids": out.splitlines()},
                              f"pgrep -fi {name!r}: {running}")
    register_cap(registry, Cap(name="verify.process_running", description="verify process running via pgrep", side_effect="read", inputs=("name",)), ver_process_running)

    def ver_network_connected(kwargs: dict[str, Any], state: Any) -> Any:
        rc, _out, _err = run_shell(["ping", "-c", "1", "-W", "3", "8.8.8.8"], timeout=6)
        connected = rc == 0
        if not connected and shutil.which("curl"):
            rc2, _, _ = run_shell(["curl", "-s", "-o", "/dev/null", "-m", "5", "https://www.google.com"], timeout=8)
            connected = rc2 == 0
        return _verify_result(connected, {"verified": connected, "method": "ping 8.8.8.8" + ("" if connected else " + curl fallback")},
                              f"ping rc={rc}, connected={connected}")
    register_cap(registry, Cap(name="verify.network_connected", description="verify internet connectivity (ping + curl fallback)", side_effect="read", inputs=()), ver_network_connected)

    def ver_result(kwargs: dict[str, Any], state: Any) -> Any:
        # Generic comparison — deterministic local check, never auto-true
        actual = kwargs.get("value", kwargs.get("actual"))
        expected = kwargs.get("expected")
        operator = str(kwargs.get("operator", "equals")).lower()
        if expected is None and operator not in ("truthy", "falsy"):
            return fail("'expected' (or value+operator) is required")
        ops = {
            "equals": lambda a, e: str(a) == str(e),
            "not_equals": lambda a, e: str(a) != str(e),
            "contains": lambda a, e: str(e) in str(a),
            "not_contains": lambda a, e: str(e) not in str(a),
            "greater_than": lambda a, e: float(a) > float(e),
            "less_than": lambda a, e: float(a) < float(e),
        }
        if operator in ("truthy", "falsy"):
            verified = bool(actual) if operator == "truthy" else not bool(actual)
        elif operator not in ops:
            return fail(f"unknown operator {operator!r}; supported: {sorted(ops) | {'truthy', 'falsy'}}")
        else:
            try:
                verified = ops[operator](actual, expected)
            except (TypeError, ValueError):
                return fail(f"cannot compare {actual!r} {operator} {expected!r}")
        return _verify_result(verified, {"verified": verified, "actual": actual, "expected": expected, "operator": operator},
                              f"{actual!r} {operator} {expected!r} -> {verified}")
    register_cap(registry, Cap(name="verify.result", description="generic deterministic comparison (equals/contains/gt/lt/truthy)", side_effect="read", inputs=("value", "expected", "operator", "actual")), ver_result)

    def ver_final_state(kwargs: dict[str, Any], state: Any) -> Any:
        """Verify final state = a set of named conditions all hold."""
        conditions = kwargs.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            return fail("'conditions' list is required (e.g. [{'type': 'file_exists', 'path': ...}, ...])")
        results = []
        all_ok = True
        for cond in conditions:
            if not isinstance(cond, dict):
                results.append({"error": "condition must be an object", "verified": False})
                all_ok = False
                continue
            ctype = str(cond.get("type", "")).strip()
            cargs = {k: v for k, v in cond.items() if k != "type"}
            checks = {
                "file_exists": lambda a: ver_file_exists(a, state),
                "folder_exists": lambda a: ver_folder_exists(a, state),
                "app_open": lambda a: ver_app_open(a, state),
                "process_running": lambda a: ver_process_running(a, state),
                "network_connected": lambda a: ver_network_connected(a, state),
                "browser_url": lambda a: ver_browser_url(a, state),
                "spreadsheet": lambda a: ver_spreadsheet(a, state),
                "document": lambda a: ver_document(a, state),
                "text_visible": lambda a: ver_text_visible(a, state),
                "download_complete": lambda a: ver_download_complete(a, state),
            }
            handler = checks.get(ctype)
            if handler is None:
                results.append({"type": ctype, "verified": False, "reason": f"unknown condition type {ctype!r}"})
                all_ok = False
                continue
            try:
                outcome = handler(cargs)
                data = outcome.data if hasattr(outcome, "data") else {}
                cond_ok = bool(data.get("verified"))
                results.append({"type": ctype, **{k: v for k, v in data.items() if k != "verified"}, "verified": cond_ok})
                all_ok = all_ok and cond_ok
            except Exception as e:
                results.append({"type": ctype, "verified": False, "reason": str(e)})
                all_ok = False
        return _verify_result(all_ok, {"verified": all_ok, "results": results},
                              f"{sum(1 for r in results if r.get('verified'))}/{len(results)} conditions verified")
    register_cap(registry, Cap(name="verify.final_state", description="verify multiple named conditions all hold (file/folder/app/process/network/url/spreadsheet/document/text/download)", side_effect="read", inputs=("conditions",)), ver_final_state)

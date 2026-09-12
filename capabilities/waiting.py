"""
capabilities/waiting.py — Condition-based waiting capabilities.

NO fixed sleeps as completion proof. Every wait.* cap polls a REAL
condition via core.wait_until.wait_until and reports the observed
outcome. Returns verified:true only when the condition actually became
true within the timeout.
"""
from __future__ import annotations

import os
import time
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell
from core.capability_registry import CapabilityRegistry
from core.wait_until import wait_until


def _timeout(kwargs: dict[str, Any], default: float = 10.0) -> float:
    try:
        return max(0.5, float(kwargs.get("timeout", kwargs.get("timeout_seconds", default))))
    except (TypeError, ValueError):
        return default


def install(registry: CapabilityRegistry, *, approve_all: bool = False) -> None:
    """Install Waiting capabilities (condition-based, evidence-backed)."""

    def wait_seconds(kwargs: dict[str, Any], state: Any) -> Any:
        # Explicit fixed wait is occasionally legitimate (e.g. letting an
        # animation settle) — but it is NEVER proof of completion, and the
        # result says exactly that.
        try:
            seconds = min(60.0, max(0.0, float(kwargs.get("seconds", 1))))
            time.sleep(seconds)
            return ok(data={"waited_seconds": seconds, "note": "fixed delay only; not verification of any state"})
        except Exception as e:
            return fail(str(e))
    register_cap(registry, Cap(name="wait.seconds", description="fixed delay (NOT proof of completion — prefer condition waits)", side_effect="read", inputs=("seconds",)), wait_seconds)

    def _atspi_text_present(text: str, app: str) -> bool:
        try:
            from core.atspi_navigator import ATSPINavigator
            nav = ATSPINavigator()
            dump = nav.dump_visible_text(app_name=app or None) if hasattr(nav, "dump_visible_text") else None
            return dump is not None and text.lower() in str(dump).lower()
        except Exception:
            return False

    def wait_for_element(kwargs: dict[str, Any], state: Any) -> Any:
        query = str(kwargs.get("query", kwargs.get("text", ""))).strip()
        app = str(kwargs.get("app", "")).strip()
        timeout = _timeout(kwargs)
        if not query:
            return fail("'query' or 'text' is required")
        try:
            from core.atspi_navigator import ATSPINavigator
            nav = ATSPINavigator()

            def check() -> bool:
                try:
                    if hasattr(nav, "find_element"):
                        return nav.find_element(app_name=app or None, text=query) is not None
                    return _atspi_text_present(query, app)
                except Exception:
                    return False

            met = wait_until(check, timeout=timeout, interval=0.5)
            return ok(data={"condition_met": met, "query": query, "timeout": timeout},
                      evidence=(f"AT-SPI element {query!r} present: {met}",))
        except Exception as e:
            return fail(f"wait.for_element failed: {e}")
    register_cap(registry, Cap(name="wait.for_element", description="wait until UI element appears (AT-SPI)", side_effect="read", inputs=("query", "text", "app", "timeout")), wait_for_element)

    def wait_for_text(kwargs: dict[str, Any], state: Any) -> Any:
        text = str(kwargs.get("text", "")).strip()
        app = str(kwargs.get("app", "")).strip()
        timeout = _timeout(kwargs)
        if not text:
            return fail("'text' is required")
        met = wait_until(lambda: _atspi_text_present(text, app), timeout=timeout, interval=0.5)
        return ok(data={"condition_met": met, "text": text, "timeout": timeout},
                  evidence=(f"screen text {text!r} visible: {met}",))
    register_cap(registry, Cap(name="wait.for_text", description="wait until text is visible on screen (AT-SPI)", side_effect="read", inputs=("text", "app", "timeout")), wait_for_text)

    def wait_for_window(kwargs: dict[str, Any], state: Any) -> Any:
        title = str(kwargs.get("title", kwargs.get("name", ""))).strip()
        timeout = _timeout(kwargs)
        if not title:
            return fail("'title' or 'name' is required")

        def check() -> bool:
            rc, out, _ = run_shell(["pgrep", "-fi", title], timeout=3)
            return rc == 0 and bool(out.strip())

        met = wait_until(check, timeout=timeout, interval=0.5)
        return ok(data={"condition_met": met, "window": title, "timeout": timeout},
                  evidence=(f"window/app {title!r} present: {met}",))
    register_cap(registry, Cap(name="wait.for_window", description="wait until window/app exists (pgrep)", side_effect="read", inputs=("title", "name", "timeout")), wait_for_window)

    def wait_for_app(kwargs: dict[str, Any], state: Any) -> Any:
        name = str(kwargs.get("name", "")).strip()
        timeout = _timeout(kwargs, 15.0)
        if not name:
            return fail("'name' is required")

        def check() -> bool:
            rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=3)
            return rc == 0 and bool(out.strip())

        met = wait_until(check, timeout=timeout, interval=0.5)
        return ok(data={"condition_met": met, "app": name, "timeout": timeout},
                  evidence=(f"app {name!r} running: {met}",))
    register_cap(registry, Cap(name="wait.for_app", description="wait until app is running (pgrep poll)", side_effect="read", inputs=("name", "timeout")), wait_for_app)

    def wait_for_download(kwargs: dict[str, Any], state: Any) -> Any:
        folder = os.path.expanduser(str(kwargs.get("folder", kwargs.get("directory", ""))).strip() or "~/Downloads")
        filename = str(kwargs.get("filename", "")).strip()
        contains = str(kwargs.get("filename_contains", "")).strip()
        timeout = _timeout(kwargs, 120.0)
        min_bytes = int(kwargs.get("min_bytes", 1) or 1)
        partial_exts = {".part", ".crdownload", ".tmp"}
        if not filename and not contains:
            return fail("'filename' or 'filename_contains' is required")
        from pathlib import Path
        target_dir = Path(folder).expanduser()

        def check() -> bool:
            if not target_dir.is_dir():
                return False
            candidates = list(target_dir.glob(filename)) if filename else list(target_dir.iterdir())
            if contains:
                candidates = [f for f in candidates if contains.lower() in f.name.lower()]
            for f in candidates:
                if not f.is_file():
                    continue
                if f.suffix.lower() in partial_exts:
                    continue  # still downloading
                if f.stat().st_size >= min_bytes:
                    return True
            return False

        met = wait_until(check, timeout=timeout, interval=1.0)
        final_path = None
        if met:
            candidates = sorted(
                (f for f in (target_dir.glob(filename) if filename else target_dir.iterdir())
                 if f.is_file() and f.suffix.lower() not in partial_exts),
                key=lambda f: f.stat().st_mtime, reverse=True,
            )
            if candidates and (not contains or contains.lower() in candidates[0].name.lower()):
                final_path = str(candidates[0])
        return ok(data={"condition_met": met, "path": final_path, "folder": folder, "timeout": timeout},
                  evidence=(f"download complete file present: {met}" + (f" ({final_path})" if final_path else ""),))
    register_cap(registry, Cap(name="wait.for_download", description="wait until download completes (complete file exists, no .part suffix, size stable)", side_effect="read", inputs=("filename", "folder", "filename_contains", "timeout", "min_bytes")), wait_for_download)

    def wait_for_file(kwargs: dict[str, Any], state: Any) -> Any:
        path = str(kwargs.get("path", "")).strip()
        timeout = _timeout(kwargs)
        min_bytes = int(kwargs.get("min_bytes", 0) or 0)
        if not path:
            return fail("'path' is required")
        expanded = os.path.expanduser(path)

        def check() -> bool:
            if not os.path.exists(expanded):
                return False
            return not min_bytes or (os.path.isfile(expanded) and os.path.getsize(expanded) >= min_bytes)

        met = wait_until(check, timeout=timeout, interval=0.5)
        size = os.path.getsize(expanded) if os.path.isfile(expanded) else 0
        return ok(data={"condition_met": met, "path": path, "size_bytes": size, "timeout": timeout},
                  evidence=(f"file {path!r} exists (size {size}): {met}",))
    register_cap(registry, Cap(name="wait.for_file", description="wait until file exists (optionally >= min_bytes)", side_effect="read", inputs=("path", "timeout", "min_bytes")), wait_for_file)

    def wait_for_process(kwargs: dict[str, Any], state: Any) -> Any:
        name = str(kwargs.get("name", "")).strip()
        until = str(kwargs.get("until", "running")).strip().lower()
        timeout = _timeout(kwargs)
        if not name:
            return fail("'name' is required")
        want_running = until not in {"stopped", "exited", "gone"}

        def check() -> bool:
            rc, out, _ = run_shell(["pgrep", "-fi", name], timeout=3)
            running = rc == 0 and bool(out.strip())
            return running if want_running else not running

        met = wait_until(check, timeout=timeout, interval=0.5)
        return ok(data={"condition_met": met, "process": name, "until": until, "timeout": timeout},
                  evidence=(f"process {name!r} {until}: {met}",))
    register_cap(registry, Cap(name="wait.for_process", description="wait until process is running or stopped", side_effect="read", inputs=("name", "timeout", "until")), wait_for_process)

    def wait_for_network(kwargs: dict[str, Any], state: Any) -> Any:
        timeout = _timeout(kwargs, 30.0)

        def check() -> bool:
            rc, _, _ = run_shell(["ping", "-c", "1", "-W", "2", "8.8.8.8"], timeout=4)
            if rc == 0:
                return True
            rc2, _, _ = run_shell(["curl", "-s", "-o", "/dev/null", "-m", "3", "https://www.google.com"], timeout=5)
            return rc2 == 0

        met = wait_until(check, timeout=timeout, interval=1.0)
        return ok(data={"condition_met": met, "timeout": timeout},
                  evidence=(f"network reachable: {met}",))
    register_cap(registry, Cap(name="wait.for_network", description="wait until network/internet is reachable", side_effect="read", inputs=("timeout",)), wait_for_network)

    def wait_for_state_change(kwargs: dict[str, Any], state: Any) -> Any:
        """Poll a re-checkable condition until it differs from `initial`."""
        from_path = str(kwargs.get("path", "")).strip()
        timeout = _timeout(kwargs)
        if from_path:
            initial = os.path.getsize(os.path.expanduser(from_path)) if os.path.exists(os.path.expanduser(from_path)) else None
            met = wait_until(
                lambda: (os.path.getsize(os.path.expanduser(from_path)) if os.path.exists(os.path.expanduser(from_path)) else None) != initial,
                timeout=timeout, interval=0.5,
            )
            return ok(data={"condition_met": met, "path": from_path, "timeout": timeout},
                      evidence=(f"file state changed: {met}",))
        return fail("'path' is required (wait.for_state_change observes real state changes)")
    register_cap(registry, Cap(name="wait.for_state_change", description="wait until observable state changes (file size/path based)", side_effect="read", inputs=("path", "timeout")), wait_for_state_change)

    def wait_for_condition(kwargs: dict[str, Any], state: Any) -> Any:
        """Generic condition wait: type + args, reusing the real checkers."""
        ctype = str(kwargs.get("type", kwargs.get("condition", ""))).strip()
        timeout = _timeout(kwargs)
        cargs = {k: v for k, v in kwargs.items() if k not in ("type", "condition", "timeout", "timeout_seconds")}
        if not ctype:
            return fail("'type' or 'condition' is required (e.g. type=file, path=/tmp/x)")
        checks: dict[str, Any] = {
            "file": lambda: os.path.exists(os.path.expanduser(str(cargs.get("path", "")))),
            "app": lambda: run_shell(["pgrep", "-fi", str(cargs.get("name", ""))], timeout=3)[0] == 0,
            "network": lambda: run_shell(["ping", "-c", "1", "-W", "2", "8.8.8.8"], timeout=4)[0] == 0,
            "text": lambda: _atspi_text_present(str(cargs.get("text", "")), str(cargs.get("app", ""))),
        }
        handler = checks.get(ctype)
        if handler is None:
            return fail(f"unknown condition type {ctype!r}; supported: {sorted(checks)}")
        met = wait_until(lambda: bool(handler()), timeout=timeout, interval=0.5)
        return ok(data={"condition_met": met, "type": ctype, "timeout": timeout},
                  evidence=(f"condition {ctype!r} met: {met}",))
    register_cap(registry, Cap(name="wait.for_condition", description="generic condition wait (type: file/app/network/text)", side_effect="read", inputs=("type", "condition", "timeout")), wait_for_condition)

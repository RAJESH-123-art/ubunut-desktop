"""
capabilities/screen.py — Screen observation and UI element finding.

Covers NIKKI capability families: 6 (SCREEN), 7 (ACCESSIBILITY)

Strategy hierarchy:
    AT-SPI  →  Vision/OCR  →  Screenshot fallback
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def install(registry: Any, *, approve_all: bool = False) -> None:

    # ── screen.screenshot ─────────────────────────────────────────────────────
    def _screenshot(args: dict, state: Any) -> Any:
        try:
            path = str(args.get("path", "")).strip()
            from core.logger import take_screenshot
            # take_screenshot() always saves under LOGS_DIR with a unique
            # filename; `name=" labels the file, it is not an output path.
            label = "screenshot"
            if path:
                label = Path(path).stem or "screenshot"
            saved = take_screenshot(name=label)
            result = {"path": str(saved), "message": f"Screenshot saved to {saved}"}
            # When the caller asked for a specific path, copy the capture
            # there so the contract ("saved to <path>") is honoured.
            if path:
                import shutil as _shutil
                _shutil.copyfile(saved, path)
                result["requested_path"] = path
            return ok(result)
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.screenshot",
        description="Take a full-screen screenshot. Inputs: path (optional, auto-generated if omitted).",
        side_effect="local_write",
        inputs=("path",),
    ), _screenshot)

    # ── screen.observe ────────────────────────────────────────────────────────
    def _observe(args: dict, state: Any) -> Any:
        try:
            result: dict = {}
            # AT-SPI tree summary
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                tree = nav.get_desktop_summary()
                result["atspi"] = tree
            except Exception as atspi_err:
                result["atspi_error"] = str(atspi_err)
            # Screenshot hash for change detection
            try:
                import hashlib
                import tempfile

                from core.logger import take_screenshot
                tmp = Path(tempfile.mktemp(suffix=".png"))
                take_screenshot(output_path=str(tmp))
                result["screenshot_hash"] = hashlib.md5(tmp.read_bytes()).hexdigest()
                tmp.unlink(missing_ok=True)
            except Exception as hash_err:
                logger.debug(f"screen.observe: screenshot hash unavailable: {hash_err}")
            return ok(result)
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.observe",
        description="Observe full screen state: AT-SPI accessibility tree + screenshot hash.",
        side_effect="read",
        interfaces=("atspi", "vision"),
    ), _observe)

    # ── screen.find_element ───────────────────────────────────────────────────
    def _find_element(args: dict, state: Any) -> Any:
        try:
            query = str(args.get("query", "")).strip()
            role = str(args.get("role", "")).strip()
            if not query and not role:
                return fail("'query' or 'role' is required")
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                elements = nav.find_elements(name=query or None, role=role or None)
                if elements:
                    el = elements[0]
                    return ok({
                        "found": True,
                        "element": {
                            "name": getattr(el, "name", ""),
                            "role": str(getattr(el, "role", "")),
                            "x": getattr(getattr(el, "extents", None), "x", None),
                            "y": getattr(getattr(el, "extents", None), "y", None),
                        },
                        "count": len(elements),
                    })
            except Exception as atspi_err:
                logger.debug(f"screen.find_element: AT-SPI lookup failed: {atspi_err}")
            return ok({"found": False, "element": None})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.find_element",
        description="Find a UI element by text/label using AT-SPI. Inputs: query, role.",
        side_effect="read",
        inputs=("query", "role"),
        interfaces=("atspi",),
    ), _find_element)

    # ── screen.find_text ──────────────────────────────────────────────────────
    def _find_text(args: dict, state: Any) -> Any:
        try:
            text = str(args.get("text", "")).strip()
            if not text:
                return fail("'text' is required")
            # AT-SPI first
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                elements = nav.find_elements(name=text)
                if elements:
                    return ok({"found": True, "method": "atspi", "count": len(elements)})
            except Exception as atspi_err:
                logger.debug(f"screen.find_text: AT-SPI lookup failed: {atspi_err}")
            # OCR fallback via vision engine
            try:
                from core.vision_engine import VisionEngine
                ve = VisionEngine()
                result_v = ve.find_text(text)
                if result_v:
                    return ok({"found": True, "method": "vision", "location": result_v})
            except Exception as vision_err:
                logger.debug(f"screen.find_text: vision/OCR fallback failed: {vision_err}")
            return ok({"found": False})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.find_text",
        description="Find text visible on screen via AT-SPI or OCR. Inputs: text.",
        side_effect="read",
        inputs=("text",),
        interfaces=("atspi", "vision"),
    ), _find_text)

    # ── screen.find_button ────────────────────────────────────────────────────
    def _find_button(args: dict, state: Any) -> Any:
        try:
            label = str(args.get("label", "")).strip()
            if not label:
                return fail("'label' is required")
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                elements = nav.find_elements(name=label, role="push button")
                if not elements:
                    elements = nav.find_elements(name=label, role="button")
                if elements:
                    el = elements[0]
                    ext = getattr(el, "extents", None)
                    return ok({"found": True, "label": label,
                                "x": getattr(ext, "x", None), "y": getattr(ext, "y", None)})
            except Exception as atspi_err:
                logger.debug(f"screen.find_button: AT-SPI lookup failed: {atspi_err}")
            return ok({"found": False, "label": label})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.find_button",
        description="Find a button by its label using AT-SPI. Inputs: label.",
        side_effect="read",
        inputs=("label",),
        interfaces=("atspi",),
    ), _find_button)

    # ── screen.find_input ─────────────────────────────────────────────────────
    def _find_input(args: dict, state: Any) -> Any:
        try:
            label = str(args.get("label", "")).strip()
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                for role in ("text", "entry", "editable text"):
                    elements = nav.find_elements(name=label or None, role=role)
                    if elements:
                        el = elements[0]
                        ext = getattr(el, "extents", None)
                        return ok({"found": True, "role": role,
                                    "x": getattr(ext, "x", None), "y": getattr(ext, "y", None)})
            except Exception as atspi_err:
                logger.debug(f"screen.find_input: AT-SPI lookup failed: {atspi_err}")
            return ok({"found": False})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.find_input",
        description="Find a text input field. Inputs: label (optional).",
        side_effect="read",
        inputs=("label",),
        interfaces=("atspi",),
    ), _find_input)

    # ── screen.find_menu ──────────────────────────────────────────────────────
    def _find_menu(args: dict, state: Any) -> Any:
        try:
            label = str(args.get("label", "")).strip()
            if not label:
                return fail("'label' is required")
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                for role in ("menu item", "menu", "menu bar"):
                    elements = nav.find_elements(name=label, role=role)
                    if elements:
                        el = elements[0]
                        ext = getattr(el, "extents", None)
                        return ok({"found": True, "role": role,
                                    "x": getattr(ext, "x", None), "y": getattr(ext, "y", None)})
            except Exception as atspi_err:
                logger.debug(f"screen.find_menu: AT-SPI lookup failed: {atspi_err}")
            return ok({"found": False})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.find_menu",
        description="Find a menu or menu item by label. Inputs: label.",
        side_effect="read",
        inputs=("label",),
        interfaces=("atspi",),
    ), _find_menu)

    # ── screen.get_resolution ─────────────────────────────────────────────────
    def _get_resolution(args: dict, state: Any) -> Any:
        try:
            rc, out, _ = run_shell(["xdpyinfo"], timeout=5)
            if rc == 0:
                for line in out.splitlines():
                    if "dimensions" in line:
                        dim = line.strip().split()[1]
                        w, h = dim.split("x")
                        return ok({"width": int(w), "height": int(h), "resolution": dim})
            # Fallback: xrandr
            _rc2, out2, _ = run_shell(["xrandr", "--current"], timeout=5)
            for line in out2.splitlines():
                if " connected" in line and "x" in line:
                    import re
                    m = re.search(r"(\d+)x(\d+)", line)
                    if m:
                        return ok({"width": int(m.group(1)), "height": int(m.group(2))})
            return fail("Could not determine screen resolution")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.get_resolution",
        description="Return current screen resolution as {width, height}.",
        side_effect="read",
    ), _get_resolution)

    # ── screen.get_active_window_title ────────────────────────────────────────
    def _get_active_title(args: dict, state: Any) -> Any:
        try:
            if shutil.which("xdotool"):
                rc, title, _ = run_shell(
                    ["xdotool", "getactivewindow", "getwindowname"], timeout=3
                )
                if rc == 0:
                    return ok({"title": title.strip()})
            rc, out, _ = run_shell(["wmctrl", "-l"], timeout=5)
            for line in out.splitlines():
                parts = line.split(None, 3)
                if len(parts) == 4 and parts[1] != "-1":
                    return ok({"title": parts[3]})
            return fail("Cannot get active window title — install xdotool")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.get_active_window_title",
        description="Return the title of the currently focused window.",
        side_effect="read",
    ), _get_active_title)

    # ── screen.read_ui ────────────────────────────────────────────────────────
    def _read_ui(args: dict, state: Any) -> Any:
        try:
            texts = []
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                texts = nav.get_all_text()
            except Exception as atspi_err:
                logger.debug(f"screen.read_ui: AT-SPI text dump failed: {atspi_err}")
            return ok({"text": "\n".join(texts) if texts else "", "element_count": len(texts)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.read_ui",
        description="Read all visible text in the current UI state via AT-SPI.",
        side_effect="read",
        interfaces=("atspi",),
    ), _read_ui)

    # ── screen.click_element ──────────────────────────────────────────────────
    def _click_element(args: dict, state: Any) -> Any:
        try:
            query = str(args.get("query", "")).strip()
            if not query:
                return fail("'query' is required")
            try:
                from core.atspi_navigator import ATSPINavigator
                nav = ATSPINavigator()
                elements = nav.find_elements(name=query)
                if elements:
                    el = elements[0]
                    ext = getattr(el, "extents", None)
                    if ext:
                        cx = ext.x + ext.width // 2
                        cy = ext.y + ext.height // 2
                        from core.gui_controller import GUIController
                        GUIController().click(cx, cy)
                        return ok({"message": f"Clicked '{query}' at ({cx},{cy})"})
            except Exception as atspi_err:
                logger.debug(f"screen.click_element: AT-SPI click failed: {atspi_err}")
            return fail(f"Element not found: {query}")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap(
        name="screen.click_element",
        description="Find a UI element by name and click it. Inputs: query.",
        side_effect="local_write",
        inputs=("query",),
        interfaces=("atspi",),
    ), _click_element)

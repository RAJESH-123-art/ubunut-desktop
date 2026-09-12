"""
capabilities/browser.py — Browser automation capabilities.
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.browser import BrowserController

_browser: BrowserController | None = None


def _get_b() -> BrowserController:
    if not _browser:
        raise RuntimeError("Browser not open. Call browser.open first.")
    return _browser


def install(registry: Any, *, approve_all: bool = False) -> None:
    def open_browser(args: dict[str, Any], _ctx: Any) -> Any:
        global _browser
        try:
            if not _browser:
                b_name = args.get("browser", "chromium")
                _browser = BrowserController(browser=b_name, headless=False)
                _browser.start()
            return ok({"status": "opened"})
        except Exception as e:
            return fail(str(e))

    def navigate(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            url = args["url"]
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            _get_b().goto(url)
            return ok({"url": _get_b().page.url})
        except Exception as e:
            return fail(str(e))

    def go_back(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _get_b().page.go_back()
            return ok({"url": _get_b().page.url})
        except Exception as e:
            return fail(str(e))

    def go_forward(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _get_b().page.go_forward()
            return ok({"url": _get_b().page.url})
        except Exception as e:
            return fail(str(e))

    def reload(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            _get_b().page.reload()
            return ok({"url": _get_b().page.url})
        except Exception as e:
            return fail(str(e))

    def _browser_pages(b):
        """Public-ish access to the browser's pages: prefer documented
        attributes, fall back to private ones for the current wrapper."""
        pages = getattr(b, "pages", None)
        if pages is not None:
            return pages
        context = getattr(b, "context", None) or getattr(b, "_context", None)
        return context.pages if context is not None else []

    def new_tab(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            context = getattr(b, "context", None) or getattr(b, "_context", None)
            if context is None:
                return fail("Browser has no context")
            page = context.new_page()
            if hasattr(b, "switch_page"):
                b.switch_page(page)
            elif hasattr(b, "_page"):
                b._page = page
            return ok({"status": "new_tab_opened", "url": page.url})
        except Exception as e:
            return fail(str(e))

    def close_tab(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            remaining_after = None
            current = getattr(b, "page", None) or getattr(b, "_page", None)
            if current is not None:
                current.close()
            pages = _browser_pages(b)
            if pages:
                target = pages[-1]
                if hasattr(b, "switch_page"):
                    b.switch_page(target)
                elif hasattr(b, "_page"):
                    b._page = target
                remaining_after = len(pages)
            return ok({"status": "tab_closed", "remaining_tabs": remaining_after or 0})
        except Exception as e:
            return fail(str(e))

    def switch_tab(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            idx = args.get("index")
            title = args.get("title")
            pages = _browser_pages(b)
            if not pages:
                return fail("No browser tabs available")
            target = None
            if idx is not None and 0 <= idx < len(pages):
                target = pages[idx]
            elif title:
                for p in pages:
                    if title.lower() in p.title().lower():
                        target = p
                        break
            if target:
                if hasattr(b, "switch_page"):
                    b.switch_page(target)
                elif hasattr(b, "_page"):
                    b._page = target
                target.bring_to_front()
                return ok({"status": "switched", "url": target.url})
            return fail(f"Tab not found (index={idx}, title={title!r}, tabs={len(pages)})")
        except Exception as e:
            return fail(str(e))

    def get_url(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            return ok({"url": _get_b().page.url})
        except Exception as e:
            return fail(str(e))

    def get_title(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            return ok({"title": _get_b().page.title()})
        except Exception as e:
            return fail(str(e))

    def find_element(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args.get("selector")
            text = args.get("text")
            b = _get_b()
            if sel:
                count = b.page.locator(sel).count()
                return ok({"found": count > 0, "count": count})
            elif text:
                count = b.page.get_by_text(text).count()
                return ok({"found": count > 0, "count": count})
            return fail("Provide selector or text")
        except Exception as e:
            return fail(str(e))

    def click(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args.get("selector")
            text = args.get("text")
            b = _get_b()
            if sel:
                b.click(sel)
            elif text:
                b.page.get_by_text(text).first.click()
            else:
                return fail("Provide selector or text")
            return ok({"status": "clicked"})
        except Exception as e:
            return fail(str(e))

    def type_into(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args["selector"]
            text = args["text"]
            clear = args.get("clear_first", True)
            _get_b().type(sel, text, clear=clear)
            return ok({"status": "typed"})
        except Exception as e:
            return fail(str(e))

    def select_option(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args["selector"]
            val = args["value"]
            _get_b().page.select_option(sel, val)
            return ok({"status": "selected"})
        except Exception as e:
            return fail(str(e))

    def upload_file(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args["selector"]
            path = args["file_path"]
            _get_b().upload_file(sel, path)
            return ok({"status": "uploaded"})
        except Exception as e:
            return fail(str(e))

    def download_file(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            import os
            url = args.get("url")
            dest = str(args["destination"])
            expanded = os.path.expanduser(dest)
            parent = os.path.dirname(expanded)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if url:
                import urllib.request
                urllib.request.urlretrieve(str(url), expanded)
            else:
                sel = args.get("selector")
                if not sel:
                    return fail("Provide url or selector")
                _get_b().download_file(sel, expanded)
            # verify the file actually landed with content
            if not os.path.exists(expanded):
                return fail(f"download reported success but {dest} not found")
            size = os.path.getsize(expanded)
            if size == 0:
                return fail(f"downloaded file {dest} is empty")
            return ok({"status": "downloaded", "path": expanded, "size_bytes": size, "verified": True})
        except Exception as e:
            return fail(str(e))

    def wait_for_page(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            timeout = args.get("timeout", 30000)
            _get_b().page.wait_for_load_state("load", timeout=timeout)
            return ok({"status": "loaded"})
        except Exception as e:
            return fail(str(e))

    def wait_for_element(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args["selector"]
            timeout = args.get("timeout", 30000)
            _get_b().wait_for(sel, timeout_ms=timeout)
            return ok({"status": "element_ready"})
        except Exception as e:
            return fail(str(e))

    def extract_text(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args.get("selector", "body")
            loc = _get_b().page.locator(sel).first
            text = loc.inner_text()
            return ok({"text": text})
        except Exception as e:
            return fail(str(e))

    def extract_links(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args.get("selector", "a")
            locs = _get_b().page.locator(sel).all()
            links = []
            for l in locs:
                href = l.get_attribute("href")
                if href:
                    links.append(href)
            return ok({"links": list(set(links))})
        except Exception as e:
            return fail(str(e))

    def extract_table(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args.get("selector", "table")
            loc = _get_b().page.locator(sel).first
            rows = loc.locator("tr").all()
            data = []
            for r in rows:
                cells = r.locator("td, th").all_inner_texts()
                data.append(cells)
            return ok({"table": data})
        except Exception as e:
            return fail(str(e))

    def extract_data(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            sel = args["selector"]
            attr = args.get("attribute")
            loc = _get_b().page.locator(sel).first
            if attr:
                val = loc.get_attribute(attr)
            else:
                val = loc.inner_text()
            return ok({"data": val})
        except Exception as e:
            return fail(str(e))

    def screenshot(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            path = args["path"]
            _get_b().screenshot(path)
            return ok({"path": path})
        except Exception as e:
            return fail(str(e))

    def scroll(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            amount = args.get("amount", 600)
            x = args.get("x", 0)
            y = args.get("y", amount)
            _get_b().page.mouse.wheel(x, y)
            return ok({"status": "scrolled"})
        except Exception as e:
            return fail(str(e))

    def evaluate(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            script = args["script"]
            res = _get_b().page.evaluate(script)
            return ok({"result": res})
        except Exception as e:
            return fail(str(e))

    def get_cookies(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            return ok({"cookies": _get_b().get_cookies()})
        except Exception as e:
            return fail(str(e))

    def close(args: dict[str, Any], _ctx: Any) -> Any:
        global _browser
        try:
            if _browser:
                _browser.close()
                _browser = None
            return ok({"status": "closed"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("browser.open", "Open browser", "local_write", ("browser",)), open_browser)
    register_cap(registry, Cap("browser.navigate", "Navigate to URL", "local_write", ("url",)), navigate)
    register_cap(registry, Cap("browser.go_back", "Go back", "read"), go_back)
    register_cap(registry, Cap("browser.go_forward", "Go forward", "read"), go_forward)
    register_cap(registry, Cap("browser.reload", "Reload page", "read"), reload)
    register_cap(registry, Cap("browser.new_tab", "New tab", "read"), new_tab)
    register_cap(registry, Cap("browser.close_tab", "Close tab", "local_write"), close_tab)
    register_cap(registry, Cap("browser.switch_tab", "Switch tab", "read", ("index", "title")), switch_tab)
    register_cap(registry, Cap("browser.get_url", "Get URL", "read"), get_url)
    register_cap(registry, Cap("browser.get_title", "Get title", "read"), get_title)
    register_cap(registry, Cap("browser.find_element", "Find element", "read", ("selector", "text")), find_element)
    register_cap(registry, Cap("browser.click", "Click element", "local_write", ("selector", "text")), click)
    register_cap(registry, Cap("browser.type_into", "Type into input", "local_write", ("selector", "text", "clear_first")), type_into)
    register_cap(registry, Cap("browser.select_option", "Select option", "local_write", ("selector", "value")), select_option)
    register_cap(registry, Cap("browser.upload_file", "Upload file", "local_write", ("selector", "file_path")), upload_file)
    register_cap(registry, Cap("browser.download_file", "Download file", "local_write", ("url", "destination")), download_file)
    register_cap(registry, Cap("browser.wait_for_page", "Wait for page", "read", ("timeout",)), wait_for_page)
    register_cap(registry, Cap("browser.wait_for_element", "Wait for element", "read", ("selector", "timeout")), wait_for_element)
    register_cap(registry, Cap("browser.extract_text", "Extract text", "read", ("selector",)), extract_text)
    register_cap(registry, Cap("browser.extract_links", "Extract links", "read", ("selector",)), extract_links)
    register_cap(registry, Cap("browser.extract_table", "Extract table", "read", ("selector",)), extract_table)
    register_cap(registry, Cap("browser.extract_data", "Extract data", "read", ("selector", "attribute")), extract_data)
    register_cap(registry, Cap("browser.screenshot", "Take screenshot", "read", ("path",)), screenshot)
    register_cap(registry, Cap("browser.scroll", "Scroll page", "local_write", ("x", "y", "amount")), scroll)
    register_cap(registry, Cap("browser.evaluate", "Evaluate JS", "read", ("script",)), evaluate)
    register_cap(registry, Cap("browser.get_cookies", "Get cookies", "read"), get_cookies)
    register_cap(registry, Cap("browser.close", "Close browser", "local_write"), close)

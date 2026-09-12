"""
capabilities/research.py — Research capabilities using the shared browser session.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus

from capabilities.base import Cap, fail, ok, register_cap


def _get_b() -> Any:
    from capabilities.browser import _browser
    if not _browser:
        raise RuntimeError("Browser not open. Call browser.open first.")
    return _browser


def install(registry: Any, *, approve_all: bool = False) -> None:
    def _http_search_fallback(query: str, engine: str) -> Any:
        """Standalone search via requests — used when no browser session is
        open. Fetches the HTML results page and extracts result links/title
        snippets without launching a browser."""
        import re

        import requests

        urls = {
            "google": f"https://www.google.com/search?q={quote_plus(query)}",
            "bing": f"https://www.bing.com/search?q={quote_plus(query)}",
            "duckduckgo": f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
        }
        if engine not in urls:
            return fail(f"unknown engine {engine!r} (google/bing/duckduckgo)")
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        try:
            resp = requests.get(urls[engine], headers=headers, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            return fail(f"search request failed: {e}")
        html = resp.text
        # Extract result links + titles — regex tuned for each engine's HTML.
        results = []
        if engine == "duckduckgo":
            for m in re.finditer(r"result__a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", html):
                url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
                if url.startswith("http"):
                    results.append({"title": title.strip(), "url": url})
        else:
            for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html):
                url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
                if title.strip() and not any(d in url for d in ("google.", "bing.com", "microsoft.com", "go.microsoft")):
                    results.append({"title": title.strip()[:120], "url": url})
        # De-duplicate by URL, cap the list
        seen = set()
        unique = [r for r in results if not (r["url"] in seen or seen.add(r["url"]))][:10]
        return ok({"query": query, "engine": engine, "method": "http", "results": unique, "count": len(unique)})

    def search_web(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            from urllib.parse import quote_plus
            query = str(args["query"])
            engine = str(args.get("engine", "google"))
            # Try the open browser session first; fall back to standalone
            # HTTP fetch when no browser is open (search must work headless).
            try:
                b = _get_b()
            except RuntimeError:
                return _http_search_fallback(query, engine)
            if engine == "google":
                b.goto(f"https://www.google.com/search?q={quote_plus(query)}")
            elif engine == "bing":
                b.goto(f"https://www.bing.com/search?q={quote_plus(query)}")
            elif engine == "duckduckgo":
                b.goto(f"https://duckduckgo.com/?q={quote_plus(query)}")
            else:
                return fail(f"unknown engine {engine!r} (google/bing/duckduckgo)")
            return ok({"url": b.page.url})
        except Exception as e:
            return fail(str(e))

    def search_site(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            from urllib.parse import quote_plus
            query = str(args["query"])
            site = str(args["site_url"])
            b = _get_b()
            b.goto(f"https://www.google.com/search?q={quote_plus(f'site:{site} {query}')}")
            return ok({"url": b.page.url})
        except Exception as e:
            return fail(str(e))

    def open_result(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            index = args.get("index", 0)
            b = _get_b()
            # Try to find search results links
            links = b.page.locator("a h3").all()
            if index < len(links):
                links[index].click()
                return ok({"status": "clicked", "url": b.page.url})
            return fail(f"Result {index} not found")
        except Exception as e:
            return fail(str(e))

    def extract_article(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            script = "document.body.innerText"
            text = b.page.evaluate(script)
            return ok({"article": text})
        except Exception as e:
            return fail(str(e))

    def extract_metadata(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            title = b.page.title()
            desc = b.page.locator("meta[name='description']").get_attribute("content")
            author = b.page.locator("meta[name='author']").get_attribute("content")
            return ok({"title": title, "description": desc, "author": author})
        except Exception as e:
            return fail(str(e))

    def extract_table(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            rows = b.page.locator("table tr").all()
            data = []
            for r in rows:
                data.append(r.locator("td, th").all_inner_texts())
            return ok({"table": data})
        except Exception as e:
            return fail(str(e))

    def extract_images(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            b = _get_b()
            imgs = b.page.locator("img").all()
            urls = []
            for img in imgs:
                src = img.get_attribute("src")
                if src:
                    urls.append(src)
            return ok({"images": urls})
        except Exception as e:
            return fail(str(e))

    def collect_results(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            count = args.get("count", 5)
            b = _get_b()
            items = b.page.locator("a:has(h3)").all()[:count]
            res = []
            for item in items:
                title = item.locator("h3").first.inner_text()
                link = item.get_attribute("href")
                res.append({"title": title, "url": link})
            return ok({"results": res})
        except Exception as e:
            return fail(str(e))

    def summarize_page(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            # We don't have an LLM here directly, so we'll just extract top text
            b = _get_b()
            text = b.page.locator("body").inner_text()
            summary = text[:1000] + "..." if len(text) > 1000 else text
            return ok({"summary": summary})
        except Exception as e:
            return fail(str(e))

    def save_results(args: dict[str, Any], _ctx: Any) -> Any:
        try:
            import os
            path = str(args["path"])
            fmt = str(args.get("format", "json"))
            data = args.get("data", {})
            parent = os.path.dirname(os.path.expanduser(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                if fmt == "json":
                    json.dump(data, f, indent=2, default=str)
                else:
                    f.write(str(data))
            # verify the write landed
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return fail(f"save to {path} not verified")
            return ok({"status": "saved", "path": path, "size_bytes": os.path.getsize(path), "verified": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("research.search_web", "Search web", "read", ("query", "engine")), search_web)
    register_cap(registry, Cap("research.search_site", "Search site", "read", ("query", "site_url")), search_site)
    register_cap(registry, Cap("research.open_result", "Open result", "read", ("index",)), open_result)
    register_cap(registry, Cap("research.extract_article", "Extract article", "read"), extract_article)
    register_cap(registry, Cap("research.extract_metadata", "Extract metadata", "read"), extract_metadata)
    register_cap(registry, Cap("research.extract_table", "Extract table", "read"), extract_table)
    register_cap(registry, Cap("research.extract_images", "Extract images", "read"), extract_images)
    register_cap(registry, Cap("research.collect_results", "Collect results", "read", ("count",)), collect_results)
    register_cap(registry, Cap("research.summarize_page", "Summarize page", "read"), summarize_page)
    register_cap(registry, Cap("research.save_results", "Save results", "local_write", ("path", "format")), save_results)

"""Structured live observations for the authoritative task runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.world_model import WorldModel, world


class RuntimeObserver:
    """Collect facts from the strongest available interfaces without an LLM."""

    def __init__(self, *, model: WorldModel = world, page: Any = None, directories: list[str | Path] | None = None) -> None:
        self.model = model
        self.page = page
        self.directories = [Path(item).expanduser() for item in directories or ()]

    def observe(self) -> dict[str, Any]:
        evidence: dict[str, Any] = {"interfaces": []}
        desktop = self._desktop()
        if desktop is not None:
            evidence["desktop"] = desktop
            evidence["interfaces"].append("atspi")
            # A readable desktop scene is a candidate visual surface.  The
            # executor still performs confidence and change verification
            # before treating a visual action as complete.
            evidence["interfaces"].append("vision")
        browser = self._browser()
        if browser is not None:
            evidence["browser"] = browser
            evidence["interfaces"].append("browser_dom")
            # Browser screenshots and mouse input share viewport coordinates,
            # so a live page also supports the visual-control adapter.
            evidence["interfaces"].append("vision")
        files = self._files()
        if files:
            evidence["files"] = files
            evidence["interfaces"].append("filesystem")
        evidence["interfaces"] = sorted(set(evidence["interfaces"]))
        return evidence

    def _desktop(self) -> dict[str, Any] | None:
        try:
            snapshot = self.model.snapshot()
        except Exception:
            return None
        return {
            "open_apps": list(snapshot.get("open_apps", [])),
            "active_window": str(snapshot.get("active_window", "")),
            "windows": list(snapshot.get("windows", [])),
        }

    def _browser(self) -> dict[str, Any] | None:
        if self.page is None:
            return None
        try:
            controls = self.page.locator(
                "button, a, input, textarea, select, [role=button], [role=link]"
            ).evaluate_all(
                """els => els.filter(el => { const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none'; })
                  .slice(0, 80).map(el => ({tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                  name: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 160)}))"""
            )
            body = self.page.locator("body").inner_text(timeout=2_000)[:12_000]
            return {"url": self.page.url, "title": self.page.title(), "text": body, "controls": controls}
        except Exception as exc:
            return {"observation_error": f"{type(exc).__name__}: {exc}"}

    def _files(self) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for directory in self.directories:
            try:
                if not directory.is_dir():
                    continue
                for path in sorted(directory.iterdir(), key=lambda item: item.stat().st_mtime_ns, reverse=True)[:30]:
                    if path.is_file():
                        stat = path.stat()
                        files.append({"path": str(path), "bytes": stat.st_size, "modified_ns": stat.st_mtime_ns})
            except OSError:
                continue
        return files

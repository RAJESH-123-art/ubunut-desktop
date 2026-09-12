from __future__ import annotations

from pathlib import Path

from core.runtime_observer import RuntimeObserver


class _Model:
    def snapshot(self):
        return {"open_apps": ["Calculator"], "active_window": "Calculator", "windows": [{"title": "Calculator"}]}


class _Locator:
    def evaluate_all(self, _script):
        return [{"tag": "button", "role": "", "name": "Save"}]

    def inner_text(self, timeout=0):
        return "Document ready"


class _Page:
    url = "https://example.test"

    def locator(self, selector):
        return _Locator()

    def title(self):
        return "Example"


def test_observer_collects_structured_browser_desktop_and_file_evidence(tmp_path: Path) -> None:
    target = tmp_path / "result.txt"
    target.write_text("done")

    observed = RuntimeObserver(model=_Model(), page=_Page(), directories=[tmp_path]).observe()

    assert set(observed["interfaces"]) == {"atspi", "browser_dom", "filesystem", "vision"}
    assert observed["desktop"]["active_window"] == "Calculator"
    assert observed["browser"]["controls"][0]["name"] == "Save"
    assert observed["files"][0]["path"] == str(target)

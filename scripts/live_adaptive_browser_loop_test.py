"""Live local proof that the adaptive browser loop observes, decides, acts, and verifies."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.action_loop import ActionLoop
from core.browser import brave


def _load_local_secrets() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "secrets.env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    _load_local_secrets()
    browser = brave(headless=True).start()
    checkpoint_path = Path("/tmp/desktop-agent-live-adaptive-loop-checkpoint.json")
    checkpoint_path.unlink(missing_ok=True)
    try:
        browser.page.set_content("""<!doctype html><title>Adaptive Browser Test</title>
          <main><button id="complete" onclick="this.textContent='Local task complete'; this.disabled=true">
          Complete local adaptive test</button></main>""")

        class LocalBrowserLoop(ActionLoop):
            def _get_browser_page(self, *_args):
                return browser.page

            def _close_playwright(self):
                pass

        loop = LocalBrowserLoop(max_steps=4, timeout=15.0)
        result = loop.run(
            "In the browser, click the button labeled Complete local adaptive test. "
            "Finish only after the button says Local task complete.",
            approve_all=True,
            checkpoint_path=checkpoint_path,
        )
        label = browser.page.locator("#complete").inner_text()
        print(f"SUCCESS={result.success}")
        print(f"STEPS={len(result.steps)}")
        print(f"MESSAGE={result.message!r}")
        print(f"BUTTON_LABEL={label!r}")
        print(f"CHECKPOINT={checkpoint_path}")
        if not result.success or label != "Local task complete":
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())

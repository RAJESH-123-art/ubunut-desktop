"""
Generic browser step runner for intents that need a sequence of Playwright actions.
JSON structure:
{
  "actions": [
    {"type": "goto", "url": "..."},
    {"type": "click", "selector": "..."},
    {"type": "type", "selector": "...", "text": "..."},
    {"type": "wait", "sec": 2}
  ]
}
"""

import time
from typing import List, Dict, Any
from loguru import logger

from core.browser import BrowserController, brave
from core.logger import start, finish, notify

def setup():
    """Start Brave via Playwright."""
    b = brave(headless=False)
    b.start()
    time.sleep(2)  # window startup grace
    return {"browser": b}

def execute(args: dict, resources: dict):
    """Run a list of sequential browser actions."""
    task_name = "browser_action"
    start(task_name)
    try:
        browser = resources["browser"]
        actions: List[Dict[str, Any]] = args.get("actions", [])
        for act in actions:
            typ = act.get("type")
            if typ == "goto":
                url = act["url"]
                logger.info(f"Navigating to {url}")
                browser.goto(url)
            elif typ == "click":
                sel = act["selector"]
                logger.info(f"Clicking {sel}")
                browser.click(sel)
            elif typ == "type":
                sel = act["selector"]
                txt = act["text"]
                logger.info(f"Typing {txt!r} into {sel}")
                browser.type(sel, txt)
            elif typ == "wait":
                secs = act.get("seconds", act.get("sec", 2))
                logger.info(f"Waiting {secs} secs")
                time.sleep(secs)
            else:
                logger.warning(f"Unknown browser action type: {typ}")
        notify(f"Ran {len(actions)} browser actions")
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """Close browser."""
    browser = resources.get("browser")
    if browser:
        browser.close()
    notify("Browser action task completed")

if __name__ == "__main__":
    demo = {
        "actions": [
            {"type": "goto", "url": "https://github.com"},
            {"type": "wait", "seconds": 2},
            {"type": "click", "selector": "a[href='/login']"},
            {"type": "wait", "seconds": 2},
            {"type": "type", "selector": "input[name='login']", "text": "test@example.com"},
        ]
    }
    r = setup()
    execute(demo, r)
    cleanup(r)
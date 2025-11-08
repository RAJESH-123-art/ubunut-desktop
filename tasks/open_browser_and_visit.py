"""
Sample automation task: open Firefox, navigate to URL, screenshot, and close.
Demonstrates use of GUIController, logging, and optional wait.
"""

import time
from pathlib import Path
from typing import Optional
from loguru import logger

from core.gui_controller import GUIController
from core.system_utils import open_terminal, command_output
from core.browser_manager import setup_shared_resources, cleanup_shared_resources, finalize_workflow
from core.logger import start, finish, notify, telegram
from core.vision_engine import VisionEngine

def setup():
    """Initialize any resources or dependencies."""
    print("Setting up: open_browser_and_visit")
    # Only open terminal if explicitly needed
    return setup_shared_resources()

def execute(args: dict, resources: dict):
    """Main execution function. Args derived from workflow.yaml or CLI."""
    url = args.get("url", "https://news.ycombinator.com")
    screenshot = args.get("screenshot", False)
    delay = args.get("delay_after_load", 4)
    browser_type = args.get("browser", "firefox") # new param
    
    task_name = "open_browser_and_visit"
    start(task_name, f"Navigate to {url}")
    try:
        if browser_type == "firefox":
            # Legacy GUI automation path (X11/XWayland)
            gui = resources["gui"]
            vision = resources["vision"]
            gui.focus_window("Terminal")
            time.sleep(0.5)
            from core.system_utils import command
            command("firefox &")
            time.sleep(2.5)
            ok = gui.focus_window("Firefox")
            if not ok:
                raise RuntimeError("Firefox window not found/accessible")
            time.sleep(1)
            gui.hotkey("ctrl", "l")
            time.sleep(0.3)
            gui.type_text(url + "\n")
            time.sleep(delay)
            if screenshot:
                p = gui.screenshot(name=f"{task_name}_{url.split('//')[-1].split('/')[0]}")
                notify(f"Screenshot saved: {p}")
        else:
            # Use Playwright for Brave/Chromium
            browser_ctl = resources.get("browser")
            if browser_ctl:
                logger.info(f"Navigating via Playwright {browser_ctl._browser}")
                browser_ctl.goto(url, wait_until="load")
                time.sleep(delay)
                if screenshot:
                    p = browser_ctl.screenshot(f"logs/screenshots/{task_name}_{int(time.time())}.png")
                    notify(f"Screenshot saved: {p}")
            else:
                raise RuntimeError(f"Browser controller not provided for {browser_type}")
        finish("success", task_name)
        # Close if requested
        if args.get("close_after", False):
            if browser_ctl := resources.get("browser"):
                browser_ctl.close()
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """Release resources or handle post-task cleanup."""
    gui = resources.get("gui")
    if gui:
        gui.screenshot("post_browser_task")
    # Extract task args from resources for notifications
    task_args = resources.get("task_args", {})
    notify("Browser visit task completed")
    telegram(f"✅ Browser task done: {task_args.get('url', 'unknown')}")
    cleanup_shared_resources(resources)

# Optional: register this task in the tasks __init__.py
if __name__ == "__main__":
    args = {"url": "https://github.com", "screenshot": True, "close_after": True}
    r = setup()
    execute(args, r)
    cleanup(r)
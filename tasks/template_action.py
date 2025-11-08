"""
Template-based automation: Open Canva (or any web app), create new design,
select a template, fill placeholders, and export final image.
"""

import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from loguru import logger

from core.gui_controller import GUIController
from core.vision_engine import VisionEngine
from core.logger import start, finish, notify
from core.browser import BrowserController, brave

def setup():
    """Initialize resources for template actions."""
    print("Setup: template_action")
    b = brave(headless=False)
    b.start()
    # Wait a moment for window to appear
    time.sleep(2)
    return {"browser": b, "gui": GUIController(safe_mode=True), "vision": VisionEngine()}

def execute(args: dict, resources: dict):
    """Interpret args to launch design studio and pick a template."""
    task_name = "template_action"
    start(task_name)
    try:
        browser = resources["browser"]
        gui = resources["gui"]
        vision = resources["vision"]
        platform = args.get("platform", "canva").lower()

        # Launch design app
        urls = {
            "canva": "https://www.canva.com/design-dashboard/templates/",
            "figma": "https://www.figma.com/files/recent",
            "vistacreate": "https://www.vistacreate.com/templates/",
        }

        url = urls.get(platform, urls["canva"])
        logger.info(f"Opening {platform} at {url}")
        browser.goto(url)
        # wait for some known UI element – very naive
        time.sleep(5)

        # Template selection: simplistic; in real world use vision or web API
        template_query = args.get("template", "social media post")
        search_selector = "[data-testid='SearchInput'], input[placeholder*='search' i], input[placeholder*='Search' i]"
        try:
            browser.wait_for(search_selector, timeout_ms=5000)
            browser.type(search_selector, template_query)
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Search selector not found: {e}")

        # Try to click first template via generic selector – replace with template ID data-attrs if possible
        generic_template = "div[role='link'], a[data-testid*='template']"
        try:
            browser.click(generic_template)
        except Exception as e:
            logger.warning(f"Template click failed: {e}")

        # Wait for editor to load
        time.sleep(3)

        # Example placeholder filling (simplistic)
        placeholders = args.get("placeholders", {})
        for item in placeholders.get("texts", []):
            elem_selector = item.get("selector")
            text = item.get("value")
            if elem_selector:
                try:
                    browser.wait_for(elem_selector, timeout_ms=3000)
                    browser.type(elem_selector, text)
                except Exception as e:
                    logger.warning(f"Failed to fill placeholder: {e}")

        # Export final design (Canva export → download)
        if args.get("export", True):
            export_button = "[aria-label*='share'], button[data-testid='share'], button[aria-label*='Download' i]"
            try:
                browser.wait_for(export_button, timeout_ms=5000)
                browser.click(export_button)
                # Additional download steps can be added here
            except Exception as e:
                logger.warning(f"Export step failed: {e}")

        # Optional screenshot capture
        if args.get("screenshot", True):
            path = browser.screenshot(f"logs/screenshots/{platform}_design_{int(time.time())}.png")
            notify(f"Template design screenshot saved to {path}")

        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """Close browsers."""
    browser = resources.get("browser")
    if browser:
        try:
            browser.close()
        except Exception as e:
            logger.warning(f"Failed to close browser: {e}")
    notify("Template automation completed")

if __name__ == "__main__":
    # Demo CLI style
    demo_args = {
        "platform": "canva",
        "template": "Instagram Story",
        "export": True,
        "placeholders": {
            "texts": [
                {"selector": '[data-testid="editable-element"]:first-child', "value": "Hello World"},
            ]
        },
        "screenshot": True,
    }
    r = setup()
    execute(demo_args, r)
    cleanup(r)
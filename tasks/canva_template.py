"""
Canva template automation for specific aspect ratios.
Supports creating templates with aspect ratios like 4:5, 16:9, etc.
"""

import time
from typing import Dict, Any, Optional
from loguru import logger

from core.browser_manager import setup_shared_resources, cleanup_shared_resources
from core.logger import start, finish, notify

def setup():
    """Initialize shared resources for Canva actions."""
    return setup_shared_resources()

def execute(args: dict, resources: dict):
    """Open Canva, select template with specified aspect ratio"""
    task_name = "canva_template"
    start(task_name)
    try:
        browser = resources["browser"]
        aspect_ratio = args.get("ratio", "4:5")
        template_type = args.get("template", "Instagram Story")
        
        # Navigate to Canva templates
        logger.info(f"Navigating to Canva templates")
        browser.goto("https://www.canva.com/templates/")
        time.sleep(3)
        
        # Click on search box
        try:
            search_selector = '[data-testid="SearchInput"], input[placeholder*="search"], input[placeholder*="Search"], [aria-label*="search"]'
            browser.wait_for(search_selector, timeout_ms=5000)
            browser.type(search_selector, template_type)
            time.sleep(1)
            
            # Try to click first search result
            first_result = 'a[href*="/templates/"], [class*="template"]'
            browser.click(first_result)
            time.sleep(3)
        except Exception as e:
            logger.warning(f"Could not complete search, continuing anyway: {e}")
            # Try navigating directly based on template type
            if "instagram" in template_type.lower():
                browser.goto("https://www.canva.com/templates/Instagram-Story/")
            elif "facebook" in template_type.lower():
                browser.goto("https://www.canva.com/templates/Facebook-Post/")
            time.sleep(3)
        
        # Try to select by aspect ratio - very simplified approach
        try:
            # Look for aspect ratio filters
            # Escape colon in CSS selector
            ratio_class = aspect_ratio.replace(":", "-")
            ratio_filters = f'[aria-label*="{aspect_ratio}"], [data-testid*="{aspect_ratio}"], [class*="{ratio_class}"]'
            browser.wait_for(ratio_filters, timeout_ms=3000)
            browser.click(ratio_filters)
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Could not filter by aspect ratio {aspect_ratio}: {e}")
        
        # Take a screenshot
        screenshot_path = browser.screenshot(f"logs/screenshots/canva_template_{aspect_ratio}_{int(time.time())}.png")
        notify(f"Canva template with aspect ratio {aspect_ratio} opened. Screenshot saved to {screenshot_path}")
        
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """Cleanup resources (keep browser for shared manager)."""
    cleanup_shared_resources(resources)
    notify("Canva template task completed")

if __name__ == "__main__":
    # Demo usage
    demo_args = {"ratio": "4:5", "template": "Instagram Story"}
    r = setup()
    execute(demo_args, r)
    cleanup(r)
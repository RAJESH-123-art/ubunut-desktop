"""
Take a system screenshot using the GUI controller.
"""

import time
from datetime import datetime
from typing import Dict, Any
from loguru import logger

from core.gui_controller import GUIController
from core.logger import start, finish, notify
from core.browser_manager import setup_shared_resources

def setup():
    """Initialize GUI controller for screenshots."""
    # No browser needed for screenshots
    return setup_shared_resources(create_browser=False)

def execute(args: dict, resources: dict):
    """Take a screenshot."""
    task_name = "system_screenshot"
    start(task_name)
    try:
        gui = resources["gui"]
        
        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"desktop_screenshot_{timestamp}"
        
        screenshot_path = gui.screenshot(name=filename)
        logger.info(f"Screenshot saved to: {screenshot_path}")
        notify(f"Screenshot captured: {screenshot_path}")
        
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """No cleanup needed."""
    pass

if __name__ == "__main__":
    r = setup()
    execute({}, r)
    cleanup(r)
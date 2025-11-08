"""
Shared browser manager to prevent threading issues in multi-action workflows.
Single browser instance is reused across tasks in the same workflow.
"""

import os
import time
from typing import Optional, Dict, Any
from loguru import logger

from .browser import BrowserController, brave
from .gui_controller import GUIController
from .vision_engine import VisionEngine
from .logger import start, finish, notify

# Global shared browser instance
_SHARED_BROWSER: Optional[BrowserController] = None
_BROWSER_INITIALIZED = False

def ensure_brave_browser() -> BrowserController:
    """Get or create a shared Brave browser instance."""
    global _SHARED_BROWSER, _BROWSER_INITIALIZED
    
    if _SHARED_BROWSER is None or not _BROWSER_INITIALIZED:
        try:
            logger.info("Initializing shared Brave browser instance")
            _SHARED_BROWSER = brave(headless=False)
            _SHARED_BROWSER.start()
            _BROWSER_INITIALIZED = True
            logger.info("Shared Brave browser ready")
        except Exception as e:
            logger.error(f"Failed to start shared browser: {e}")
            _BROWSER_INITIALIZED = False
            raise
    
    return _SHARED_BROWSER

def close_shared_browser():
    """Close the shared browser if it exists."""
    global _SHARED_BROWSER, _BROWSER_INITIALIZED
    
    if _SHARED_BROWSER is not None:
        try:
            logger.info("Closing shared browser")
            _SHARED_BROWSER.close()
        except Exception as e:
            logger.warning(f"Error closing shared browser: {e}")
        finally:
            _SHARED_BROWSER = None
            _BROWSER_INITIALIZED = False

def setup_shared_resources(create_browser: bool = True) -> Dict[str, Any]:
    """Get or create shared resources for multi-action workflows."""
    resources = {
        "gui": GUIController(safe_mode=True),
        "vision": VisionEngine()
    }
    
    # Only create browser if explicitly requested
    if create_browser:
        resources["browser"] = ensure_brave_browser()
    
    return resources

def cleanup_shared_resources(resources: Dict[str, Any]):
    """Cleanup resources but keep shared browser for next actions."""
    # We don't close the browser here - it will be closed at workflow end
    pass

def finalize_workflow():
    """Call this at the end of a multi-action workflow to clean up everything."""
    close_shared_browser()
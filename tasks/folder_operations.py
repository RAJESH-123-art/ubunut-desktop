"""
Folder operations task: open, navigate to, create folders.
"""

import os
import time
from pathlib import Path
from typing import Dict, Any
from loguru import logger
import subprocess

from core.gui_controller import GUIController
from core.logger import start, finish, notify
from core.system_utils import open_directory, ensure_dir
from core.browser_manager import setup_shared_resources

def setup():
    """Initialize for folder operations."""
    # No browser needed for folder operations
    return setup_shared_resources(create_browser=False)

def execute(args: dict, resources: dict):
    """Perform folder operations."""
    task_name = "folder_operations"
    start(task_name)
    try:
        gui = resources["gui"]
        operation = args.get("operation", "")
        folder_name = args.get("name", "")
        
        if operation == "open" or operation == "go to":
            # Handle special folder names
            if folder_name.lower() in ("downloads", "documents", "pictures", "music", "videos", "desktop"):
                home = Path.home()
                folder_path = home / folder_name.capitalize()
            elif folder_name.startswith("/") or folder_name.startswith("~"):
                folder_path = Path(folder_name).expanduser()
            else:
                # Try to resolve as relative path
                folder_path = Path(folder_name)
                if not folder_path.is_absolute():
                    folder_path = Path.home() / folder_path
            
            # Open with the default file manager
            open_directory(folder_path)
            notify(f"Opened folder: {folder_path}")
                
        elif operation == "create":
            # Create new folder
            if folder_name.startswith("/") or folder_name.startswith("~"):
                folder_path = Path(folder_name).expanduser()
            else:
                folder_path = Path.cwd() / folder_name
            
            ensure_dir(folder_path)
            logger.info(f"Created folder: {folder_path}")
            notify(f"Folder created: {folder_path.name}")
                
        else:
            logger.warning(f"Unsupported folder operation: {operation}")
        
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """No cleanup needed."""
    pass

if __name__ == "__main__":
    args = {"operation": "open", "name": "Downloads"}
    r = setup()
    execute(args, r)
    cleanup(r)
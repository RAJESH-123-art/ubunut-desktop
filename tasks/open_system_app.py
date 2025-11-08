"""
Generic system app launcher.
Opens system applications by name with appropriate commands.
"""

import time
import subprocess
from typing import Dict, Any
from pathlib import Path
from loguru import logger

from core.gui_controller import GUIController
from core.logger import start, finish, notify
from core.system_utils import command
from core.browser_manager import setup_shared_resources

def setup():
    """Initialize for system app operations."""
    # No browser needed for opening system apps
    return setup_shared_resources(create_browser=False)

def execute(args: dict, resources: dict):
    """Open a system app by name."""
    task_name = "open_system_app"
    start(task_name)
    try:
        gui = resources["gui"]
        app_name = args.get("app_name", "")
        
        if not app_name:
            raise ValueError("No app name provided")
        
        # Map common app names to their command
        app_commands = {
            "file manager": "nautilus",
            "files": "nautilus",
            "nautilus": "nautilus",
            "terminal": "gnome-terminal",
            "calculator": "gnome-calculator",
            "text editor": "gedit",
            "gedit": "gedit",
            "firefox": "firefox",
            "brave": "brave-browser",
            "chrome": "google-chrome",
            "chromium": "chromium-browser",
            "vlc": "vlc",
            "music player": "rhythmbox",
            "photo viewer": "eog",
            "image viewer": "eog",
            "system settings": "gnome-control-center",
            "settings": "gnome-control-center",
        }
        
        cmd = app_commands.get(app_name.lower())
        if cmd:
            logger.info(f"Opening {app_name} with command: {cmd}")
            command(f"{cmd} &")
        else:
            # Try generic approach - treat app_name as a command
            logger.info(f"Trying to open {app_name} directly")
            try:
                command(f"{app_name} &")
            except Exception as e:
                logger.error(f"Failed to launch {app_name}: {e}")
                # As a fallback, try xdg-open with common desktop files
                try:
                    from glob import glob
                    for desktop_file in glob(f"/usr/share/applications/*{app_name.lower()}*.desktop"):
                        command(f"gtk-launch {Path(desktop_file).stem}")
                        break
                except Exception as fallback_e:
                    logger.error(f"Fallback methods also failed: {fallback_e}")
        
        time.sleep(2)  # Give time for app to start
        notify(f"Opened app: {app_name}")
        
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """No cleanup needed."""
    pass

if __name__ == "__main__":
    args = {"app_name": "calculator"}
    r = setup()
    execute(args, r)
    cleanup(r)
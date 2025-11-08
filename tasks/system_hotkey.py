"""
System hotkey task: press key combinations.
"""

import time
from typing import Dict, Any
from loguru import logger

from core.gui_controller import GUIController
from core.logger import start, finish, notify
from core.browser_manager import setup_shared_resources

def setup():
    """Initialize GUI controller for hotkeys."""
    # No browser needed for hotkey operations
    return setup_shared_resources(create_browser=False)

def execute(args: dict, resources: dict):
    """Press hotkeys."""
    task_name = "system_hotkey"
    start(task_name)
    try:
        gui = resources["gui"]
        keys = args.get("keys", [])
        
        if not keys:
            raise ValueError("No keys provided")
        
        # Map common keys to pyautogui format
        key_map = {
            "control": "ctrl",
            "command": "cmd",
            "option": "alt",
            "escape": "esc",
            "return": "enter",
            "delete": "del",
            "space": "space"
        }
        
        # Convert keys
        py_keys = [key_map.get(k.lower(), k) for k in keys]
        
        # Press hotkey combination
        if len(py_keys) == 1:
            gui.press(py_keys[0])
        elif len(py_keys) == 2:
            gui.hotkey(py_keys[0], py_keys[1])
        elif len(py_keys) == 3:
            gui.hotkey(py_keys[0], py_keys[1], py_keys[2])
        else:
            logger.warning(f"Too many keys for hotkey: {keys}")
        
        logger.info(f"Pressed hotkey: {' + '.join(py_keys)}")
        notify(f"Hotkey pressed: {' + '.join(keys)}")
        
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """No cleanup needed."""
    pass

if __name__ == "__main__":
    args = {"keys": ["control", "c"]}
    r = setup()
    execute(args, r)
    cleanup(r)
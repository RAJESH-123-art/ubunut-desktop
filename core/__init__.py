"""
Desktop Automation Core Modules.

Imports are lazy (done inside functions/tasks) so that importing any core
submodule — e.g. core.smart_parser, core.memory, core.app_registry — does NOT
trigger pynput / X-server connection at import time.

If you need a specific class, import it directly:
    from core.gui_controller import GUIController
    from core.vision_engine   import VisionEngine
"""

__all__ = [
    "BrowserController",
    "GUIController",
    "VisionEngine",
    "brave",
    "clipboard_get",
    "clipboard_set",
    "command",
    "command_output",
    "copy_file",
    "env_check",
    "file_modified",
    "file_size",
    "finish",
    "get_system_info",
    "log_action",
    "move_file",
    "notify",
    "notify_send",
    "open_directory",
    "open_terminal",
    "start",
    "take_screenshot",
    "telegram",
    "temp_dir",
    "user_downloads",
    "user_home",
    "user_pictures",
]

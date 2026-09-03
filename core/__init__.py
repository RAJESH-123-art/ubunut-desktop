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
    "GUIController", "VisionEngine", "BrowserController", "brave",
    "command", "command_output", "notify_send", "open_directory", "open_terminal",
    "clipboard_set", "clipboard_get", "move_file", "copy_file",
    "file_size", "file_modified", "temp_dir", "user_home", "user_downloads",
    "user_pictures", "env_check",
    "log_action", "take_screenshot", "notify", "telegram", "start", "finish",
    "get_system_info",
]

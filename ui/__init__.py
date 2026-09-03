"""
User interface modules for Desktop Automation.

Imports are lazy: importing this package does NOT trigger pynput / X-server
connection.  Access modules directly when needed:

    from ui.assistant import main as assistant_main
    from ui.cli import main as cli_main
"""

__all__ = ["assistant", "cli"]

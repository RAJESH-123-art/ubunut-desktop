"""
Lock the desktop screen.

Tries multiple lock methods in order, returning True on the first success:
  1. loginctl lock-session       — systemd-logind (most desktops)
  2. gnome-screensaver-command   — legacy GNOME screensaver
  3. xdg-screensaver lock        — XDG standard (X11/XWayland)
  4. dbus-send to GNOME ScreenSaver D-Bus interface

Args:
    (none)
"""
from loguru import logger

from core.logger import finish, notify, start
from core.shell_fallback import try_commands

_LOCK_COMMANDS: list[list[str]] = [
    ["loginctl", "lock-session"],
    ["gnome-screensaver-command", "--lock"],
    ["xdg-screensaver", "lock"],
    [
        "dbus-send", "--type=method_call",
        "--dest=org.gnome.ScreenSaver",
        "/org/gnome/ScreenSaver",
        "org.gnome.ScreenSaver.Lock",
    ],
]


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "lock_screen"
    start(task_name)
    try:
        if try_commands(_LOCK_COMMANDS):
            logger.info("✅ Screen locked")
            notify("Screen locked")
            finish("success", task_name)
            return True

        raise RuntimeError(
            "Screen lock failed — no working lock tool found.\n"
            "Install loginctl (systemd), gnome-screensaver, or xdg-screensaver."
        )

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({}, r)
    cleanup(r)

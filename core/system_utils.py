import os
import shlex
import shutil
import subprocess
import time
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict

from loguru import logger

from .logger import IS_WAYLAND, log_action

_GTK_CLIPBOARD: object | None = None


def _gtk_clipboard() -> object | None:
    """Return the live desktop clipboard without requiring an external tool."""
    global _GTK_CLIPBOARD
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk

        if Gdk.Display.get_default() is None:
            return None
        _GTK_CLIPBOARD = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        return _GTK_CLIPBOARD
    except Exception as exc:
        logger.debug(f"GTK clipboard is unavailable: {exc}")
        return None


def _flush_gtk_events() -> None:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        deadline = time.monotonic() + 0.25
        while Gtk.events_pending() and time.monotonic() < deadline:
            Gtk.main_iteration_do(False)
    except Exception as gtk_err:
        # GTK clipboard drain is best-effort — a missing Gtk or no display
        # just means no clipboard synchronisation, never a hard failure.
        logger.debug(f"_drain_gtk_events: GTK event drain failed: {gtk_err}")


def command_output(command: str, shell: bool = True, capture: bool = True, **kwargs) -> str:
    """Run a system command and return its output (stripped)."""
    cmd = command if shell else shlex.split(command)
    raise_on_error = kwargs.pop('raise_on_error', True)
    try:
        out = subprocess.run(cmd, shell=shell, check=raise_on_error, capture_output=capture, text=True, **kwargs)
        return out.stdout.strip() if capture else ""
    except subprocess.CalledProcessError as e:
        if raise_on_error:
            logger.error(f"Command failed: {command} - error: {e.stderr}")
            raise
        return ""


def command(command: str, shell: bool = True, raise_on_error: bool = True) -> bool:
    """Run a system command, returning status."""
    try:
        subprocess.run(command, shell=shell, check=raise_on_error)
        log_action("system_command", take_shoot=False, extras={"cmd": command})
        return True
    except subprocess.CalledProcessError:
        return False


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_file(src: Path, dst: Path, overwrite: bool = False) -> None:
    ensure_dir(dst.parent)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"File exists: {dst}")
    command(f"cp {src} {dst}")


def move_file(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    command(f"mv {src} {dst}")


def env_check() -> Dict[str, Any]:
    """Collect environment and capability checks for logging."""
    caps = {
        "env": {
            "session_type": os.getenv("XDG_SESSION_TYPE", "unknown"),
            "desktop": os.getenv("XDG_CURRENT_DESKTOP", "unknown"),
            "display": os.getenv("DISPLAY", "none"), # Still relevant for XWayland
        },
        "tools": {
            "ydotool": bool(shutil.which("ydotool")),
            "gdbus": bool(shutil.which("gdbus")),
            "wmctrl": bool(shutil.which("wmctrl")),
            "xdotool": bool(shutil.which("xdotool")),
            "wl-copy": bool(shutil.which("wl-copy")),
            "wl-paste": bool(shutil.which("wl-paste")),
            "xclip": bool(shutil.which("xclip")),
            "gnome-screenshot": bool(shutil.which("gnome-screenshot")),
            "grim": bool(shutil.which("grim")),
            "scrot": bool(shutil.which("scrot")),
            "notify-send": bool(shutil.which("notify-send")),
        },
        "python_packages": {
            "loguru": find_spec("loguru") is not None,
            "yaml": find_spec("yaml") is not None,
            "playwright": find_spec("playwright") is not None,
            "openai": find_spec("openai") is not None,
            "evdev": find_spec("evdev") is not None,
            "pyatspi": find_spec("pyatspi") is not None,
            "cv2": find_spec("cv2") is not None,
        },
        "is_wayland": IS_WAYLAND,
    }
    return caps


def notify_send(message: str, title: str = "Automation", urgency: str = "normal", timeout: str = "5000") -> bool:
    if not shutil.which("notify-send"):
        logger.warning("notify-send not available")
        return False
    return command(f"notify-send -t {timeout} -u {urgency} {title} {message}")


def open_directory(path: Path) -> None:
    command(f"xdg-open {path}")


def open_terminal(path: Path) -> None:
    # Preference order: gnome-terminal, konsole, xterm
    candidates = ["gnome-terminal", "konsole", "xterm"]
    for term in candidates:
        if shutil.which(term):
            command(f'{term} --working-directory="{path}" &')
            return
    raise RuntimeError("No terminal emulator found")


def clipboard_set(text: str) -> bool:
    """Set clipboard content through wl-copy, xclip, or the live GTK session."""
    if shutil.which("wl-copy"):
        # wl-copy forks a daemon that serves the clipboard offer and KEEPS
        # the inherited stdout/stderr open forever — with capture_output=True
        # subprocess.run() blocks waiting for pipe EOF long after wl-copy
        # itself exits (verified live: rc=0 instantly when piped to /dev/null,
        # hang with captured pipes). Detach the daemon's stdio and do not
        # capture so this returns as soon as wl-copy exits.
        with open(os.devnull, "wb") as devnull:
            result = subprocess.run(
                ["wl-copy", "--", text],
                check=False,
                stdout=devnull,
                stderr=devnull,
                stdin=subprocess.DEVNULL,
            )
        return result.returncode == 0
    elif shutil.which("xclip"):
        result = subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    clipboard = _gtk_clipboard()
    if clipboard is None:
        logger.warning("No Wayland/X11/GTK clipboard backend is available")
        return False
    try:
        clipboard.set_text(text, -1)  # type: ignore[union-attr]
        clipboard.store()  # type: ignore[union-attr]
        _flush_gtk_events()
        return True
    except Exception as exc:
        logger.warning(f"GTK clipboard set failed: {exc}")
        return False


def clipboard_get() -> str:
    """Get clipboard content through wl-paste, xclip, or the live GTK session."""
    if shutil.which("wl-paste"):
        return command_output("wl-paste --no-newline")
    elif shutil.which("xclip"):
        return command_output("xclip -selection clipboard -o")
    clipboard = _gtk_clipboard()
    if clipboard is None:
        logger.warning("No Wayland/X11/GTK clipboard backend is available")
        return ""
    try:
        value = clipboard.wait_for_text()  # type: ignore[union-attr]
        _flush_gtk_events()
        return str(value or "")
    except Exception as exc:
        logger.warning(f"GTK clipboard read failed: {exc}")
        return ""


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def file_modified(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def temp_dir() -> Path:
    p = Path(os.getenv("TMPDIR", "/tmp"))
    ensure_dir(p)
    return p


def user_home() -> Path:
    return Path.home()


def user_downloads() -> Path:
    home = user_home()
    return home / "Downloads"


def user_pictures() -> Path:
    home = user_home()
    return home / "Pictures"


def is_running_as_root() -> bool:
    return os.geteuid() == 0


def is_flatpak() -> bool:
    return os.getenv('FLATPAK_ID') is not None

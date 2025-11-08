import os
import subprocess
import json
import shlex
from typing import Optional, Dict, Mapping, Any
from pathlib import Path
from loguru import logger

from .logger import log_action

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
            "wayland_display": os.getenv("WAYLAND_DISPLAY", "none"),
            "display": os.getenv("DISPLAY", "none"),
        },
        "tools": {
            "xdotool": bool(command_output("which xdotool", shell=False, raise_on_error=False)),
            "wmctrl": bool(command_output("which wmctrl", shell=False, raise_on_error=False)),
            "notify-send": bool(command_output("which notify-send", shell=False, raise_on_error=False)),
        }
    }
    return caps

def notify_send(message: str, title: str = "Automation", urgency: str = "normal", timeout: str = "5000") -> bool:
    if not command_output("which notify-send", shell=False):
        logger.warning("notify-send not available")
        return False
    return command(f"notify-send -t {timeout} -u {urgency} {title} {message}")

def open_directory(path: Path) -> None:
    command(f"xdg-open {path}")

def open_terminal(path: Path) -> None:
    # Preference order: gnome-terminal, konsole, xterm
    candidates = ["gnome-terminal", "konsole", "xterm"]
    for term in candidates:
        if command_output(f"which {term}", shell=False):
            command(f'{term} --working-directory="{path}" &')
            return
    raise RuntimeError("No terminal emulator found")

def clipboard_set(text: str) -> None:
    command(f'echo -n {shlex.quote(text)} | wl-copy --type text/plain')

def clipboard_get() -> str:
    return command_output("wl-paste --type text/plain", shell=False)

def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0

def file_modified(path: Path) -> Optional[float]:
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

def is_wayland() -> bool:
    return os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland"

def is_x11() -> bool:
    return os.getenv("XDG_SESSION_TYPE", "").lower() == "x11" or "DISPLAY" in os.environ and not is_wayland()
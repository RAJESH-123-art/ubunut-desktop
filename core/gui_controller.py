import os, time, subprocess, platform
from typing import Optional, Tuple, Dict, Any, List
from pathlib import Path

import pyautogui
import pynput.mouse
import pynput.keyboard
from loguru import logger

from .logger import log_action, take_screenshot

# Detect display server (Wayland vs X11)
def is_wayland() -> bool:
    return os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland"

# Fallback to xdotool for X11 and XWayland windows
class GUIController:
    def __init__(self, safe_mode: bool = True):
        self.safe_mode = safe_mode
        self.session_type = "wayland" if is_wayland() else "x11"
        self.mouse = pynput.mouse.Controller()
        self.keyboard = pynput.keyboard.Controller()
        if self.safe_mode:
            pyautogui.PAUSE = 0.2
            pyautogui.FAILSAFE = True
        else:
            pyautogui.PAUSE = 0.1
            pyautogui.FAILSAFE = False
        logger.info(f"GUIController initialized for {self.session_type}")

    # Screen utilities
    def screenshot(self, name: Optional[str] = None, region: Optional[Dict[str, int]] = None) -> str:
        return take_screenshot(name=name, region=region)

    def get_screen_size(self) -> Tuple[int, int]:
        try:
            return pyautogui.size()
        except Exception as exc:
            logger.error(f"Failed to get screen size: {exc}")
            return (1920, 1080)

    # Mouse actions
    def move_to(self, x: int, y: int, duration: float = 0.2):
        """Smooth mouse move; safe_mode adds extra pause."""
        pyautogui.moveTo(x, y, duration=duration)
        log_action(f"move_to", window="screen", take_shoot=False, extras={"x": x, "y": y})

    def click(self, x: Optional[int] = None, y: Optional[int] = None, 
              button: str = "left", clicks: int = 1, interval: float = None):
        if x is not None and y is not None:
            self.move_to(x, y)
        # pyautogui.click requires interval to be a number, default to 0.0 if None
        click_interval = interval if interval is not None else 0.0
        pyautogui.click(button=button, clicks=clicks, interval=click_interval)
        log_action(f"click_{button}", take_shoot=False, extras={"clicks": clicks, "pos": (x, y)})

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None):
        self.click(x, y, clicks=2, interval=0.1)

    def drag_to(self, to_x: int, to_y: int, duration: float = 0.5):
        pyautogui.dragTo(to_x, to_y, duration=duration)
        log_action("drag_to", take_shoot=False, extras={"to": (to_x, to_y)})

    def scroll(self, clicks: int, direction: str = "down", x: Optional[int] = None, y: Optional[int] = None):
        if x is not None and y is not None:
            pyautogui.moveTo(x, y)
        if direction == "down":
            pyautogui.scroll(clicks)
        else:
            pyautogui.scroll(-clicks)
        log_action("scroll", take_shoot=False, extras={"clicks": clicks, "button": direction})

    # Keyboard actions
    def type_text(self, text: str, interval: float = 0.05):
        pyautogui.typewrite(text, interval=interval)
        log_action("type_text", take_shoot=False, extras={"len": len(text)})

    def hotkey(self, *keys, interval: float = 0.2):
        """Accepts pyautogui hotkey strings like 'ctrl' 'c' or 'command' 'v'."""
        pyautogui.hotkey(*keys, interval=interval)
        log_action("hotkey", take_shoot=False, extras={"keys": list(keys)})

    def press(self, key: str):
        pyautogui.press(key)
        log_action("press", take_shoot=False, extras={"key": key})

    # Window management (use subprocess xdotool/wmctrl)
    def get_window_list(self) -> List[Dict[str, str]]:
        """Return list of windows with id and title."""
        cmd = ["wmctrl", "-l"]
        try:
            out = subprocess.check_output(cmd, text=True).strip()
            windows = []
            for line in out.splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    wid, desktop, host, title = parts[:4]
                    windows.append({"id": wid, "title": title, "desktop": desktop, "host": host})
            return windows
        except Exception as exc:
            logger.error(f"Failed to list windows: {exc}")
            return []

    def focus_window(self, title_or_id: str) -> bool:
        """Focus a window by title or ID using wmctrl."""
        cmd = ["wmctrl", "-a", title_or_id]
        try:
            subprocess.check_call(cmd)
            log_action("focus_window", take_shoot=False, extras={"target": title_or_id})
            return True
        except Exception as exc:
            logger.error(f"Failed to focus window {title_or_id}: {exc}")
            return False

    def close_window(self, title_or_id: str) -> bool:
        cmd = ["wmctrl", "-c", title_or_id]
        try:
            subprocess.check_call(cmd)
            log_action("close_window", take_shoot=False, extras={"target": title_or_id})
            return True
        except Exception as exc:
            logger.error(f"Failed to close window {title_or_id}: {exc}")
            return False

    def minimize_window(self, title_or_id: str) -> bool:
        cmd = ["xdotool", "windowminimize"]
        try:
            # Find window by name/class
            out = subprocess.check_output(["xdotool", "search", "--name", title_or_id], text=True).strip()
            wid = out.splitlines()[0]
            subprocess.check_call(cmd + [wid])
            return True
        except Exception as exc:
            logger.error(f"Failed to minimize window {title_or_id}: {exc}")
            return False

    def get_active_window_info(self) -> Optional[Dict[str, Any]]:
        """Get info about the active window on X11/XWayland."""
        cmd = ["xdotool", "getactivewindow", "getwindowname", "getwindowpid"]
        try:
            out = subprocess.check_output(cmd, text=True, timeout=2).strip()
            # out includes name and pid on separate lines
            lines = out.splitlines()
            if len(lines) >= 2:
                title = lines[0]
                pid = lines[1]
                return {"title": title, "pid": pid}
            return None
        except Exception as exc:
            logger.debug(f"Active window detection not available: {exc}")
            return None

    def click_image(self, image_path: str, confidence: float = 0.8) -> bool:
        """
        Locate and click an image on screen using pyautogui.
        'image_path' should be an absolute path or relative to project root.
        """
        try:
            center = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            if center:
                self.click(center.x, center.y)
                return True
            logger.warning(f"Image not found on screen: {image_path}")
            return False
        except Exception as exc:
            logger.error(f"Error clicking image {image_path}: {exc}")
            return False

    def wait_for_image(self, image_path: str, timeout: float = 10.0) -> bool:
        """Wait for an image to appear on screen (non-blocking)."""
        start = time.time()
        while time.time() - start < timeout:
            if pyautogui.locateOnScreen(image_path, confidence=0.6):
                return True
            time.sleep(0.5)
        logger.warning(f"Image {image_path} not shown within timeout")
        return False

    def get_mouse_position(self) -> Tuple[int, int]:
        """Current cursor position."""
        pos = pyautogui.position()
        return (pos.x, pos.y)
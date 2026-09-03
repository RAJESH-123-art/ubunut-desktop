"""GUI automation controller for Wayland (GNOME) and X11.

Mouse/keyboard actions use pynput (works on Wayland via uinput).
Window management uses GNOME Shell D-Bus (gdbus) as primary on Wayland,
with wmctrl fallback for XWayland apps.
"""

import json
import os
import re
import shutil
import subprocess
import time
from typing import cast

from loguru import logger
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

from .logger import log_action, take_screenshot


def _to_int(value: object, default: int = 0) -> int:
    """Safely coerce a JSON value to int."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default

IS_WAYLAND = os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland"

# Pynput key name mapping
_KEY_MAP: dict[str, Key | KeyCode] = {
    "ctrl": Key.ctrl, "control": Key.ctrl,
    "alt": Key.alt, "shift": Key.shift,
    "super": Key.cmd, "cmd": Key.cmd, "command": Key.cmd, "win": Key.cmd,
    "tab": Key.tab, "enter": Key.enter, "return": Key.enter,
    "esc": Key.esc, "escape": Key.esc,
    "space": Key.space, "backspace": Key.backspace,
    "delete": Key.delete, "del": Key.delete,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
    "home": Key.home, "end": Key.end,
    "page_up": Key.page_up, "page_down": Key.page_down,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
}

MouseButton = Button


def _parse_key(k: str) -> Key | KeyCode:
    mapped: Key | KeyCode | None = _KEY_MAP.get(k.lower())
    if mapped is not None:
        return mapped
    return KeyCode.from_char(k)


class GUIController:
    """Drives mouse, keyboard, and window management on Wayland/X11."""

    def __init__(self, safe_mode: bool = True) -> None:
        self.safe_mode: bool = safe_mode
        self.mouse: MouseController = MouseController()
        self.keyboard: KeyboardController = KeyboardController()
        self._pause: float = 0.2 if safe_mode else 0.1
        self._has_ydotool: bool = bool(shutil.which("ydotool"))
        self._has_wmctrl: bool = bool(shutil.which("wmctrl"))
        logger.info(
            f"GUIController initialized — Wayland={IS_WAYLAND}, ydotool={self._has_ydotool}, wmctrl={self._has_wmctrl}"
        )

    # ── Screen ──────────────────────────────────────────────────────────────

    def screenshot(self, name: str | None = None, region: dict[str, int] | None = None) -> str:
        return take_screenshot(name=name, region=region)

    def get_screen_size(self) -> tuple[int, int]:
        """
        Detect screen resolution.

        Priority order:
          1. GNOME Shell D-Bus  — works on any GNOME Wayland session (no XWayland needed)
          2. xrandr             — X11 / XWayland
          3. wlr-randr          — wlroots-based compositors (Sway, Hyprland)
          4. Hardcoded fallback — 1920×1080
        """
        # ── 1. GNOME Shell D-Bus (Wayland-native, no XWayland required) ──────
        try:
            out = self._gnome_eval(
                "let m=global.display.get_monitor_geometry("
                "global.display.get_primary_monitor());"
                "JSON.stringify({w:m.width,h:m.height})"
            )
            if out:
                m = re.search(r'"(\{[^}]+\})"', out)
                if m:
                    import json
                    data = json.loads(m.group(1).replace('\\"', '"'))
                    if "w" in data and "h" in data:
                        w, h = int(data["w"]), int(data["h"])
                        logger.debug(f"Screen size via GNOME D-Bus: {w}×{h}")
                        return (w, h)
        except Exception as exc:
            logger.debug(f"GNOME D-Bus screen size probe failed: {exc}")

        # ── 2. xrandr (X11 / XWayland) ───────────────────────────────────────
        # ── 3. wlr-randr (wlroots Wayland) ───────────────────────────────────
        for cmd in (["xrandr", "--current"], ["wlr-randr"]):
            if not shutil.which(cmd[0]):
                continue
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
                out = result.stdout
                m = re.search(r"current (\d+) x (\d+)", out)
                if m:
                    return (int(m.group(1)), int(m.group(2)))
                m = re.search(r"(\d+)x(\d+) px", out)
                if m:
                    return (int(m.group(1)), int(m.group(2)))
            except (OSError, subprocess.SubprocessError):
                logger.debug("Screen size probe failed for %s", cmd[0])

        # ── 4. Hardcoded fallback ─────────────────────────────────────────────
        logger.warning("Could not detect screen size, defaulting to 1920×1080")
        return (1920, 1080)

    def get_mouse_position(self) -> tuple[int, int]:
        pos = self.mouse.position
        return (int(pos[0]), int(pos[1]))

    # ── Mouse ────────────────────────────────────────────────────────────────

    def move_to(self, x: int, y: int, duration: float = 0.2) -> None:
        """Smooth mouse move using pynput."""
        if duration > 0:
            cur_x, cur_y = self.mouse.position
            steps = max(int(duration / 0.01), 5)
            for i in range(steps + 1):
                t = i / steps
                self.mouse.position = (
                    int(cur_x + (x - cur_x) * t),
                    int(cur_y + (y - cur_y) * t),
                )
                time.sleep(duration / steps)
        else:
            self.mouse.position = (x, y)
        log_action("move_to", window="screen", take_shoot=False, extras={"x": x, "y": y})

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
        interval: float | None = None,
    ) -> None:
        if x is not None and y is not None:
            self.move_to(x, y, duration=0.1)
        btn_map: dict[str, Button] = {
            "left": Button.left,
            "right": Button.right,
            "middle": Button.middle,
        }
        btn = btn_map.get(button, Button.left)
        click_interval = 0.05 if interval is None else interval
        for i in range(clicks):
            self.mouse.press(btn)
            time.sleep(0.05)
            self.mouse.release(btn)
            if i < clicks - 1:
                time.sleep(click_interval)
        log_action(f"click_{button}", take_shoot=False, extras={"clicks": clicks, "pos": (x, y)})

    def double_click(self, x: int | None = None, y: int | None = None) -> None:
        self.click(x, y, clicks=2, interval=0.1)

    def drag_to(self, to_x: int, to_y: int, duration: float = 0.5) -> None:
        self.mouse.press(Button.left)
        self.move_to(to_x, to_y, duration=duration)
        self.mouse.release(Button.left)
        log_action("drag_to", take_shoot=False, extras={"to": (to_x, to_y)})

    def scroll(self, clicks: int, direction: str = "down", x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self.mouse.position = (x, y)
        dy = -clicks if direction == "down" else clicks
        self.mouse.scroll(0, dy)
        log_action("scroll", take_shoot=False, extras={"clicks": clicks, "direction": direction})

    # ── Keyboard ─────────────────────────────────────────────────────────────

    def type_text(self, text: str, interval: float = 0.05) -> None:
        for char in text:
            self.keyboard.type(char)
            time.sleep(interval)
        log_action("type_text", take_shoot=False, extras={"len": len(text)})

    def hotkey(self, *keys: str, interval: float = 0.1) -> None:
        """Press a key combination, e.g. hotkey('ctrl', 'c')."""
        parsed: list[Key | KeyCode] = [_parse_key(k) for k in keys]
        for k in parsed:
            self.keyboard.press(k)
            time.sleep(0.04)
        time.sleep(interval)
        for k in reversed(parsed):
            self.keyboard.release(k)
        log_action("hotkey", take_shoot=False, extras={"keys": list(keys)})

    def press(self, key: str) -> None:
        k = _parse_key(key)
        self.keyboard.press(k)
        time.sleep(0.05)
        self.keyboard.release(k)
        log_action("press", take_shoot=False, extras={"key": key})

    # ── Window management (GNOME Wayland via gdbus, wmctrl fallback) ─────────

    def _gnome_eval(self, js: str, timeout: int = 3) -> str | None:
        """Run JavaScript in GNOME Shell via D-Bus (Wayland-native)."""
        try:
            result = subprocess.run(
                [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.Shell",
                    "--object-path", "/org/gnome/Shell",
                    "--method", "org.gnome.Shell.Eval",
                    js,
                ],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug(f"GNOME Shell eval failed: {exc}")
            return None

    def get_window_list(self) -> list[dict[str, str]]:
        """Return list of open windows."""
        # Try GNOME Shell first
        out = self._gnome_eval(
            'JSON.stringify(global.get_window_actors().map(a=>{let w=a.meta_window;return {title:w.get_title(),pid:String(w.get_pid())};}))'
        )
        if out:
            m = re.search(r'"(\[.*\])"', out, re.DOTALL)
            if m:
                try:
                    raw = m.group(1).replace('\"', '"').replace("\\n", "")
                    parsed = cast(object, json.loads(raw))
                    if isinstance(parsed, list):
                        result: list[dict[str, str]] = []
                        for item in cast(list[object], parsed):
                            if not isinstance(item, dict):
                                continue
                            w = cast(dict[str, object], item)
                            result.append({"title": str(w.get("title", "")), "pid": str(w.get("pid", ""))})
                        return result
                except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                    logger.debug(f"GNOME window list parse failed: {exc}")

        # Fallback: wmctrl (works for XWayland apps)
        if self._has_wmctrl:
            try:
                wm_result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, check=False)
                windows: list[dict[str, str]] = []
                for line in wm_result.stdout.strip().splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        wid, desktop, _host, title = parts
                        windows.append({"id": wid, "title": title, "desktop": desktop})
                return windows
            except OSError as exc:
                logger.debug(f"wmctrl list failed: {exc}")
        return []

    def focus_window(self, title_or_id: str) -> bool:
        """Focus a window by title (or partial title)."""
        needle = json.dumps(title_or_id)
        out = self._gnome_eval(
            f"let w=global.get_window_actors().find(a=>a.meta_window.get_title()?.includes({needle}))?.meta_window;if(w){{w.activate(0);true;}}else{{false;}}"
        )
        if out and "true" in out.lower():
            log_action("focus_window", take_shoot=False, extras={"target": title_or_id})
            return True
        # wmctrl fallback
        if self._has_wmctrl:
            try:
                _ = subprocess.run(["wmctrl", "-a", title_or_id], check=True, timeout=5)
                return True
            except (OSError, subprocess.SubprocessError):
                logger.debug("wmctrl focus fallback failed")
        logger.error(f"Failed to focus window: {title_or_id}")
        return False

    def close_window(self, title_or_id: str) -> bool:
        needle = json.dumps(title_or_id)
        out = self._gnome_eval(
            f"let w=global.get_window_actors().find(a=>a.meta_window.get_title()?.includes({needle}))?.meta_window;if(w){{w.delete(0);true;}}else{{false;}}"
        )
        if out and "true" in out.lower():
            log_action("close_window", take_shoot=False, extras={"target": title_or_id})
            return True
        if self._has_wmctrl:
            try:
                _ = subprocess.run(["wmctrl", "-c", title_or_id], check=True, timeout=5)
                return True
            except (OSError, subprocess.SubprocessError):
                logger.debug("wmctrl close fallback failed")
        logger.error(f"Failed to close window: {title_or_id}")
        return False

    def minimize_window(self, title_or_id: str) -> bool:
        needle = json.dumps(title_or_id)
        out = self._gnome_eval(
            f"let w=global.get_window_actors().find(a=>a.meta_window.get_title()?.includes({needle}))?.meta_window;if(w){{w.minimize();true;}}else{{false;}}"
        )
        if out and "true" in out.lower():
            return True
        logger.error(f"Failed to minimize window: {title_or_id}")
        return False

    def maximize_window(self, title_or_id: str) -> bool:
        # Meta.MaximizeFlags.BOTH = 3
        needle = json.dumps(title_or_id)
        out = self._gnome_eval(
            f"let w=global.get_window_actors().find(a=>a.meta_window.get_title()?.includes({needle}))?.meta_window;if(w){{w.maximize(3);true;}}else{{false;}}"
        )
        if out and "true" in out.lower():
            log_action("maximize_window", take_shoot=False, extras={"target": title_or_id})
            return True
        if self._has_wmctrl:
            try:
                _ = subprocess.run(
                    ["wmctrl", "-r", title_or_id, "-b", "add,maximized_vert,maximized_horz"],
                    check=True, timeout=5,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                logger.debug("wmctrl maximize fallback failed")
        logger.error(f"Failed to maximize window: {title_or_id}")
        return False

    def unmaximize_window(self, title_or_id: str) -> bool:
        needle = json.dumps(title_or_id)
        out = self._gnome_eval(
            f"let w=global.get_window_actors().find(a=>a.meta_window.get_title()?.includes({needle}))?.meta_window;if(w){{w.unmaximize(3);true;}}else{{false;}}"
        )
        if out and "true" in out.lower():
            return True
        if self._has_wmctrl:
            try:
                _ = subprocess.run(
                    ["wmctrl", "-r", title_or_id, "-b", "remove,maximized_vert,maximized_horz"],
                    check=True, timeout=5,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                logger.debug("wmctrl unmaximize fallback failed")
        return False

    def move_window(self, title_or_id: str, x: int, y: int) -> bool:
        if self._has_wmctrl:
            try:
                _ = subprocess.run(["wmctrl", "-r", title_or_id, "-e", f"0,{x},{y},-1,-1"], check=True, timeout=5)
                log_action("move_window", take_shoot=False, extras={"target": title_or_id, "x": x, "y": y})
                return True
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error(f"Failed to move window {title_or_id}: {exc}")
        logger.warning("move_window requires wmctrl; install with: sudo apt install wmctrl")
        return False

    def resize_window(self, title_or_id: str, width: int, height: int) -> bool:
        if self._has_wmctrl:
            try:
                _ = subprocess.run(["wmctrl", "-r", title_or_id, "-e", f"0,-1,-1,{width},{height}"], check=True, timeout=5)
                log_action("resize_window", take_shoot=False, extras={"target": title_or_id, "width": width, "height": height})
                return True
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error(f"Failed to resize window {title_or_id}: {exc}")
        logger.warning("resize_window requires wmctrl; install with: sudo apt install wmctrl")
        return False

    def get_window_geometry(self, title_or_id: str) -> dict[str, int] | None:
        needle = json.dumps(title_or_id)
        out = self._gnome_eval(
            f"let a=global.get_window_actors().find(a=>a.meta_window.get_title()?.includes({needle}));if(a){{let r=a.meta_window.get_frame_rect();JSON.stringify({{x:r.x,y:r.y,width:r.width,height:r.height}});}}else{{null;}}"
        )
        if out:
            m = re.search(r'"(\{[^}]+\})"', out)
            if m:
                try:
                    parsed = cast(object, json.loads(m.group(1).replace('\"', '"')))
                    if isinstance(parsed, dict):
                        pdict = cast(dict[str, object], parsed)
                        return {
                            "x": _to_int(pdict.get("x")),
                            "y": _to_int(pdict.get("y")),
                            "width": _to_int(pdict.get("width")),
                            "height": _to_int(pdict.get("height")),
                        }
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    logger.debug(f"Geometry parse failed: {exc}")
        return None

    def get_active_window_info(self) -> dict[str, str] | None:
        out = self._gnome_eval(
            "let w=global.display.get_focus_window();w?JSON.stringify({title:w.get_title(),pid:w.get_pid()}):null"
        )
        if out:
            m = re.search(r'"(\{.*?\})"', out)
            if m:
                try:
                    parsed = cast(object, json.loads(m.group(1).replace('\"', '"')))
                    if isinstance(parsed, dict):
                        pdict = cast(dict[str, object], parsed)
                        return {"title": str(pdict.get("title", "")), "pid": str(pdict.get("pid", ""))}
                except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                    logger.debug(f"Active window parse failed: {exc}")
        return None

    def activate_window(self, title_or_id: str) -> bool:
        return self.focus_window(title_or_id)

    def switch_to_desktop(self, desktop_num: int) -> bool:
        out = self._gnome_eval(
            f"let ws=global.workspace_manager.get_workspace_by_index({desktop_num});if(ws){{ws.activate(0);true;}}else{{false;}}"
        )
        if out and "true" in out.lower():
            log_action("switch_desktop", take_shoot=False, extras={"desktop": desktop_num})
            return True
        if self._has_wmctrl:
            try:
                _ = subprocess.run(["wmctrl", "-s", str(desktop_num)], check=True, timeout=5)
                return True
            except (OSError, subprocess.SubprocessError):
                logger.debug("wmctrl desktop switch fallback failed")
        logger.error(f"Failed to switch to desktop {desktop_num}")
        return False

    # ── Image-based actions (OpenCV) ─────────────────────────────────────────

    def click_image(self, image_path: str, confidence: float = 0.8) -> bool:
        """Locate an image on screen using OpenCV and click its centre."""
        try:
            import cv2

            ss_path = self.screenshot("click_image_ss")
            if not ss_path:
                return False
            screen = cv2.imread(ss_path)
            template = cv2.imread(image_path)
            if screen is None or template is None:
                logger.error(f"Failed to load images: {ss_path}, {image_path}")
                return False
            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val >= confidence:
                tshape = cast(tuple[int, ...], template.shape)
                th: int = int(tshape[0])
                tw: int = int(tshape[1])
                self.click(int(max_loc[0]) + tw // 2, int(max_loc[1]) + th // 2)
                return True
            logger.warning(f"Image not found (conf={max_val:.2f}): {image_path}")
            return False
        except (OSError, ValueError) as exc:
            logger.error(f"click_image error: {exc}")
            return False

    def _image_visible(self, image_path: str, confidence: float = 0.6) -> bool:
        try:
            import cv2

            ss_path = self.screenshot("wait_image_ss")
            if not ss_path:
                return False
            screen = cv2.imread(ss_path)
            template = cv2.imread(image_path)
            if screen is None or template is None:
                return False
            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            return max_val >= confidence
        except (OSError, ValueError):
            return False

    def wait_for_image(self, image_path: str, timeout: float = 10.0) -> bool:
        """Poll until an image appears on screen."""
        start = time.time()
        while time.time() - start < timeout:
            if self._image_visible(image_path, confidence=0.6):
                return True
            time.sleep(0.5)
        logger.warning(f"Image not found within {timeout}s: {image_path}")
        return False

"""
Wayland-compatible GUI automation using ydotool.
PyAutoGUI doesn't work on Wayland, so we use ydotool for keyboard/mouse control.
"""

import subprocess
import time
from typing import Optional
from loguru import logger


class WaylandGUI:
    """GUI controller specifically for Wayland using ydotool."""
    
    # Key code mappings for ydotool
    KEY_CODES = {
        'a': 30, 'b': 48, 'c': 46, 'd': 32, 'e': 18, 'f': 33, 'g': 34, 'h': 35,
        'i': 23, 'j': 36, 'k': 37, 'l': 38, 'm': 50, 'n': 49, 'o': 24, 'p': 25,
        'q': 16, 'r': 19, 's': 31, 't': 20, 'u': 22, 'v': 47, 'w': 17, 'x': 45,
        'y': 21, 'z': 44,
        '0': 11, '1': 2, '2': 3, '3': 4, '4': 5, '5': 6, '6': 7, '7': 8, '8': 9, '9': 10,
        'space': 57, 'enter': 28, 'tab': 15, 'esc': 1, 'backspace': 14,
        'ctrl': 29, 'shift': 42, 'alt': 56,
        'up': 103, 'down': 108, 'left': 105, 'right': 106,
    }
    
    def __init__(self):
        logger.info("WaylandGUI initialized using ydotool")
        
    def type_text(self, text: str, interval: float = 0.1):
        """Type text using ydotool."""
        try:
            logger.info(f"Typing text: {text}")
            for char in text:
                char_lower = char.lower()
                if char_lower in self.KEY_CODES:
                    keycode = self.KEY_CODES[char_lower]
                    # Key down
                    subprocess.run(['ydotool', 'key', f'{keycode}:1'], check=True)
                    time.sleep(0.05)
                    # Key up
                    subprocess.run(['ydotool', 'key', f'{keycode}:0'], check=True)
                    time.sleep(interval)
                elif char == ' ':
                    subprocess.run(['ydotool', 'key', '57:1'], check=True)
                    time.sleep(0.05)
                    subprocess.run(['ydotool', 'key', '57:0'], check=True)
                    time.sleep(interval)
            logger.info(f"Successfully typed: {text}")
            return True
        except Exception as e:
            logger.error(f"Failed to type text: {e}")
            return False
    
    def press_key(self, key: str):
        """Press a single key."""
        try:
            key_lower = key.lower()
            if key_lower in self.KEY_CODES:
                keycode = self.KEY_CODES[key_lower]
                subprocess.run(['ydotool', 'key', f'{keycode}:1'], check=True)
                time.sleep(0.05)
                subprocess.run(['ydotool', 'key', f'{keycode}:0'], check=True)
                logger.info(f"Pressed key: {key}")
                return True
        except Exception as e:
            logger.error(f"Failed to press key {key}: {e}")
            return False
    
    def hotkey(self, *keys):
        """Press a keyboard shortcut (e.g., ctrl+f)."""
        try:
            # Press all keys down
            keycodes = []
            for key in keys:
                key_lower = key.lower()
                if key_lower in self.KEY_CODES:
                    keycode = self.KEY_CODES[key_lower]
                    keycodes.append(keycode)
                    subprocess.run(['ydotool', 'key', f'{keycode}:1'], check=True)
                    time.sleep(0.05)
            
            time.sleep(0.1)
            
            # Release all keys in reverse order
            for keycode in reversed(keycodes):
                subprocess.run(['ydotool', 'key', f'{keycode}:0'], check=True)
                time.sleep(0.05)
            
            logger.info(f"Pressed hotkey: {'+'.join(keys)}")
            return True
        except Exception as e:
            logger.error(f"Failed to press hotkey {keys}: {e}")
            return False
    
    def click(self, x: Optional[int] = None, y: Optional[int] = None):
        """Click at coordinates (ydotool doesn't support absolute positioning easily)."""
        try:
            # ydotool mouse click
            # Note: ydotool click requires manual positioning with mousemove first
            if x is not None and y is not None:
                # Move mouse to position (this is tricky in ydotool)
                subprocess.run(['ydotool', 'mousemove', '-a', str(x), str(y)], check=True)
                time.sleep(0.2)
            
            # Left click
            subprocess.run(['ydotool', 'click', '0xC0'], check=True)  # Left button
            logger.info(f"Clicked at ({x}, {y})")
            return True
        except Exception as e:
            logger.error(f"Failed to click: {e}")
            return False


def test_wayland_gui():
    """Test function to verify ydotool is working."""
    gui = WaylandGUI()
    
    print("Testing ydotool...")
    print("1. Testing hotkey Ctrl+F")
    gui.hotkey('ctrl', 'f')
    time.sleep(1)
    
    print("2. Testing text typing")
    gui.type_text("VLC")
    time.sleep(1)
    
    print("3. Testing enter key")
    gui.press_key('enter')
    
    print("Test complete!")


if __name__ == "__main__":
    test_wayland_gui()

#!/usr/bin/env python3
"""
Hardware-level virtual keyboard input driver using Linux uinput (evdev).
Works on Wayland, X11, native GTK/Qt/Flutter apps.
"""

import time
from evdev import UInput, ecodes as e

CHAR_TO_KEY = {
    'a': e.KEY_A, 'b': e.KEY_B, 'c': e.KEY_C, 'd': e.KEY_D, 'e': e.KEY_E,
    'f': e.KEY_F, 'g': e.KEY_G, 'h': e.KEY_H, 'i': e.KEY_I, 'j': e.KEY_J,
    'k': e.KEY_K, 'l': e.KEY_L, 'm': e.KEY_M, 'n': e.KEY_N, 'o': e.KEY_O,
    'p': e.KEY_P, 'q': e.KEY_Q, 'r': e.KEY_R, 's': e.KEY_S, 't': e.KEY_T,
    'u': e.KEY_U, 'v': e.KEY_V, 'w': e.KEY_W, 'x': e.KEY_X, 'y': e.KEY_Y,
    'z': e.KEY_Z,
    '0': e.KEY_0, '1': e.KEY_1, '2': e.KEY_2, '3': e.KEY_3, '4': e.KEY_4,
    '5': e.KEY_5, '6': e.KEY_6, '7': e.KEY_7, '8': e.KEY_8, '9': e.KEY_9,
    ' ': e.KEY_SPACE, '\n': e.KEY_ENTER, '\t': e.KEY_TAB,
    '-': e.KEY_MINUS, '=': e.KEY_EQUAL, '[': e.KEY_LEFTBRACE, ']': e.KEY_RIGHTBRACE,
    ';': e.KEY_SEMICOLON, "'": e.KEY_APOSTROPHE, ',': e.KEY_COMMA, '.': e.KEY_DOT,
    '/': e.KEY_SLASH, '\\': e.KEY_BACKSLASH, '`': e.KEY_GRAVE,
}

SHIFT_CHAR_TO_KEY = {
    'A': e.KEY_A, 'B': e.KEY_B, 'C': e.KEY_C, 'D': e.KEY_D, 'E': e.KEY_E,
    'F': e.KEY_F, 'G': e.KEY_G, 'H': e.KEY_H, 'I': e.KEY_I, 'J': e.KEY_J,
    'K': e.KEY_K, 'L': e.KEY_L, 'M': e.KEY_M, 'N': e.KEY_N, 'O': e.KEY_O,
    'P': e.KEY_P, 'Q': e.KEY_Q, 'R': e.KEY_R, 'S': e.KEY_S, 'T': e.KEY_T,
    'U': e.KEY_U, 'V': e.KEY_V, 'W': e.KEY_W, 'X': e.KEY_X, 'Y': e.KEY_Y,
    'Z': e.KEY_Z,
    '!': e.KEY_1, '@': e.KEY_2, '#': e.KEY_3, '$': e.KEY_4, '%': e.KEY_5,
    '^': e.KEY_6, '&': e.KEY_7, '*': e.KEY_8, '(': e.KEY_9, ')': e.KEY_0,
    '_': e.KEY_MINUS, '+': e.KEY_EQUAL, '{': e.KEY_LEFTBRACE, '}': e.KEY_RIGHTBRACE,
    ':': e.KEY_SEMICOLON, '"': e.KEY_APOSTROPHE, '<': e.KEY_COMMA, '>': e.KEY_DOT,
    '?': e.KEY_SLASH, '|': e.KEY_BACKSLASH, '~': e.KEY_GRAVE,
}

class VirtualKeyboard:
    def __init__(self) -> None:
        # UInput expects Sequence[int]; cast list to satisfy pyright invariance check
        from collections.abc import Sequence as _Seq
        cap: dict[int, _Seq[int]] = {e.EV_KEY: list(range(1, 240))}
        self.ui = UInput(cap, name="MasterAgent Virtual Keyboard")
        time.sleep(0.5)

    def press_key(self, keycode: int, delay: float = 0.005):
        self.ui.write(e.EV_KEY, keycode, 1)
        self.ui.syn()
        time.sleep(delay)
        self.ui.write(e.EV_KEY, keycode, 0)
        self.ui.syn()
        time.sleep(delay)

    def type_char(self, char: str, delay: float = 0.005):
        if char in CHAR_TO_KEY:
            self.press_key(CHAR_TO_KEY[char], delay)
        elif char in SHIFT_CHAR_TO_KEY:
            self.ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            self.ui.syn()
            time.sleep(delay)
            self.press_key(SHIFT_CHAR_TO_KEY[char], delay)
            self.ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            self.ui.syn()
            time.sleep(delay)

    def type_text(self, text: str, cpm: int = 1200):
        """
        Type text at a specified Characters Per Minute (CPM) rate.
        cpm = 1200 → ~20 chars/sec → ~240 WPM.
        """
        char_delay = 60.0 / cpm if cpm > 0 else 0.001
        for ch in text:
            self.type_char(ch, delay=max(0.001, char_delay / 2))

    def close(self):
        self.ui.close()

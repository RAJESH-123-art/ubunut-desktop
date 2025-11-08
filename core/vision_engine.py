import os
import subprocess
import time
from typing import Optional, Tuple, Dict, Any

import cv2
import pytesseract
import numpy as np
from loguru import logger

from .logger import log_action, take_screenshot

class VisionEngine:
    """Image-based automation helpers: template matching and OCR."""

    def __init__(self):
        # allow override of tesseract cmd via env
        tess_cmd = os.getenv("TESSERACT_CMD")
        if tess_cmd:
            pytesseract.pytesseract.tesseract_cmd = tess_cmd
        logger.info("VisionEngine initialized")

    def _read(self, path: str):
        img = cv2.imread(path)
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {path}")
        return img

    def find_template(self, screen_path: Optional[str], template_path: str, threshold: float = 0.8) -> Optional[Tuple[int, int]]:
        """
        Returns center (x,y) of matched template in screen.
        If screen_path is None, take a fresh screenshot first.
        """
        if screen_path is None:
            screen_path = take_screenshot(name="vision_search", region=None)
        screen = self._read(screen_path)
        template = self._read(template_path)
        res = cv2.matchTemplate(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY),
                                cv2.cvtColor(template, cv2.COLOR_BGR2GRAY),
                                cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= threshold:
            th, tw = template.shape[:2]
            center = (int(max_loc[0] + tw/2), int(max_loc[1] + th/2))
            log_action("template_found", take_shoot=False, extras={"score": float(max_val), "center": center})
            return center
        logger.info(f"Template not found threshold={threshold} max={max_val:.3f}")
        return None

    def ocr_text(self, image_path: Optional[str] = None, lang: str = "eng") -> str:
        if image_path is None:
            image_path = take_screenshot(name="ocr")
        img = self._read(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        text = pytesseract.image_to_string(gray, lang=lang)
        log_action("ocr_text", take_shoot=False, extras={"chars": len(text)})
        return text

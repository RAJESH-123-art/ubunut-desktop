import os
import shutil
from typing import cast

import cv2
from cv2.typing import MatLike
from loguru import logger

from .logger import log_action, take_screenshot

try:
    import pytesseract  # type: ignore[import-untyped]
except ImportError:
    pytesseract = None  # type: ignore[assignment]

_TESSERACT_AVAILABLE: bool = (pytesseract is not None) and (
    shutil.which("tesseract") is not None or os.getenv("TESSERACT_CMD") is not None
)


class VisionEngine:
    """Image-based automation helpers: template matching and OCR.

    OCR requires the tesseract binary (sudo apt install tesseract-ocr).
    Template matching works without it (pure OpenCV).
    """

    def __init__(self) -> None:
        tesseract_cmd = os.getenv("TESSERACT_CMD")
        if pytesseract is not None and tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        if not _TESSERACT_AVAILABLE:
            logger.warning(
                "VisionEngine: tesseract not installed — OCR disabled. Template matching still works. Install with: sudo apt install tesseract-ocr"
            )
        else:
            logger.info("VisionEngine initialized")

    def _read(self, path: str) -> MatLike:
        img = cv2.imread(path)
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {path}")
        return img

    def find_template(
        self, screen_path: str | None, template_path: str, threshold: float = 0.8
    ) -> tuple[int, int] | None:
        """
        Returns center (x,y) of matched template in screen.
        If screen_path is None, take a fresh screenshot first.
        """
        if screen_path is None:
            screen_path = take_screenshot(name="vision_search", region=None)
        screen = self._read(screen_path)
        template = self._read(template_path)
        res = cv2.matchTemplate(
            cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(template, cv2.COLOR_BGR2GRAY),
            cv2.TM_CCOEFF_NORMED,
        )
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= threshold:
            tshape = cast(tuple[int, ...], template.shape)
            th: int = int(tshape[0])
            tw: int = int(tshape[1])
            center = (int(max_loc[0]) + tw // 2, int(max_loc[1]) + th // 2)
            log_action("template_found", take_shoot=False, extras={"score": float(max_val), "center": center})
            return center
        logger.info(f"Template not found threshold={threshold} max={max_val:.3f}")
        return None

    def ocr_text(self, image_path: str | None = None, lang: str = "eng") -> str:
        if pytesseract is None or not _TESSERACT_AVAILABLE:
            raise RuntimeError(
                "OCR unavailable: tesseract binary not installed. Install with: sudo apt install tesseract-ocr"
            )
        if image_path is None:
            image_path = take_screenshot(name="ocr")
        img = self._read(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        raw = pytesseract.image_to_string(gray, lang=lang)  # type: ignore[union-attr]
        text = raw if isinstance(raw, str) else str(raw)
        log_action("ocr_text", take_shoot=False, extras={"chars": len(text)})
        return text

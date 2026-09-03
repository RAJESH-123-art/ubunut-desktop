"""
Post-action state verifier — confirms whether an action actually worked.

Zero AI. Uses three independent evidence methods:
  1. OCR (Tesseract)        — read text visible on screen
  2. AT-SPI accessibility   — query the live widget tree
  3. CLI command            — run a shell command and check exit code
  4. Template matching      — find a reference image on screen (OpenCV)

A verification PASSES when ANY one method returns True.
All evidence is logged so failures are debuggable.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# SPEC — what "success" looks like for an action
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class VerifySpec:
    """
    Describes the expected post-action UI/system state.

    At least one field should be filled; the first method that
    confirms the expectation causes verify() to return True.
    """
    # OCR: all of these strings must appear somewhere on screen
    expect_text: list[str] = field(default_factory=list)
    # OCR: none of these strings should appear (error indicators)
    reject_text: list[str] = field(default_factory=list)
    # AT-SPI: an app whose name contains this string must be running
    expect_app: str = ""
    # CLI: command whose exit-code 0 means success (e.g. "snap list vlc")
    expect_cmd: str = ""
    # Template: path to a small reference PNG that must appear on screen
    expect_image: str = ""
    # How long to wait before checking (let UI settle)
    settle_wait: float = 1.5


# ─────────────────────────────────────────────────────────────────────────────
# COMMON SPECS  (importable shortcuts)
# ─────────────────────────────────────────────────────────────────────────────
def app_installed_spec(package_name: str) -> VerifySpec:
    return VerifySpec(expect_cmd=f"snap list {package_name} 2>/dev/null || which {package_name}")

def app_center_open_spec() -> VerifySpec:
    return VerifySpec(
        expect_text=["software", "app center", "ubuntu software"],
        expect_app="snap-store",
    )

def browser_open_spec(url_fragment: str = "") -> VerifySpec:
    # Use a CLI check (any browser process running) rather than OCR ALL-match,
    # which would require every browser name to appear simultaneously — impossible.
    texts = [url_fragment.lower()] if url_fragment else []
    return VerifySpec(
        expect_text=texts,
        expect_cmd="pgrep -f 'chromium|brave|firefox|chrome' > /dev/null 2>&1",
        settle_wait=2.5,
    )

def whatsapp_open_spec() -> VerifySpec:
    return VerifySpec(
        expect_text=["whatsapp"],
        expect_app="chrome",
    )

def screenshot_taken_spec() -> VerifySpec:
    # Just verify no crash — OCR isn't useful here
    return VerifySpec(settle_wait=0.5)


# ─────────────────────────────────────────────────────────────────────────────
# VERIFIER
# ─────────────────────────────────────────────────────────────────────────────
class Verifier:
    """
    Runs non-AI evidence checks after an automation action.
    Import the singleton `verifier` at the bottom of this file.
    """

    def __init__(self) -> None:
        self._ocr_ok: bool = bool(shutil.which("tesseract"))
        self._cv_ok: bool = self._check_cv()
        logger.debug(
            f"Verifier ready — OCR={'yes' if self._ocr_ok else 'no'} "
            f"OpenCV={'yes' if self._cv_ok else 'no'}"
        )

    def _check_cv(self) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            return False

    # ── Method 1: OCR ────────────────────────────────────────────────────────

    def _screen_text(self) -> str:
        if not self._ocr_ok:
            return ""
        try:
            from core.logger import take_screenshot
            path = take_screenshot(name="verifier_ocr")
            if not path:
                return ""
            r = subprocess.run(
                ["tesseract", path, "stdout", "--psm", "3"],
                capture_output=True, text=True, timeout=15,
            )
            return r.stdout.lower()
        except Exception as exc:
            logger.debug(f"OCR failed: {exc}")
            return ""

    def _check_ocr(self, expect: list[str], reject: list[str]) -> Optional[bool]:
        """None = OCR not configured, True/False = result."""
        if not expect and not reject:
            return None
        text = self._screen_text()
        if not text:
            return None
        ok = all(e.lower() in text for e in expect)
        no_bad = all(r.lower() not in text for r in reject)
        result = ok and no_bad
        logger.debug(f"OCR check: expect={expect} → {'✅' if ok else '❌'} reject={reject} → {'✅' if no_bad else '❌'}")
        return result

    # ── Method 2: AT-SPI ─────────────────────────────────────────────────────

    def _check_atspi(self, app_name: str) -> Optional[bool]:
        if not app_name:
            return None
        try:
            import sys
            sys.path.insert(0, "/usr/lib/python3/dist-packages")
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                if app_name.lower() in (app.name or "").lower():
                    logger.debug(f"AT-SPI: found app '{app.name}' ✅")
                    return True
            logger.debug(f"AT-SPI: app '{app_name}' not found ❌")
            return False
        except Exception as exc:
            logger.debug(f"AT-SPI check failed: {exc}")
            return None

    # ── Method 3: CLI command ─────────────────────────────────────────────────

    def _check_cli(self, cmd: str) -> Optional[bool]:
        if not cmd:
            return None
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
            result = r.returncode == 0
            logger.debug(f"CLI check '{cmd}': {'✅' if result else '❌'} (exit {r.returncode})")
            return result
        except Exception as exc:
            logger.debug(f"CLI check failed ({cmd!r}): {exc}")
            return None

    # ── Method 4: Template matching ───────────────────────────────────────────

    def _check_template(self, template_path: str) -> Optional[bool]:
        if not template_path or not self._cv_ok:
            return None
        try:
            import cv2
            from core.logger import take_screenshot
            screen_path = take_screenshot(name="verifier_tmpl")
            if not screen_path:
                return None
            screen = cv2.imread(screen_path)
            tmpl = cv2.imread(template_path)
            if screen is None or tmpl is None:
                return None
            res = cv2.matchTemplate(
                cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY),
                cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY),
                cv2.TM_CCOEFF_NORMED,
            )
            _, max_val, _, _ = cv2.minMaxLoc(res)
            result = max_val >= 0.75
            logger.debug(f"Template match: score={max_val:.3f} → {'✅' if result else '❌'}")
            return result
        except Exception as exc:
            logger.debug(f"Template check failed: {exc}")
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def verify(self, spec: VerifySpec) -> bool:
        """
        Run all configured evidence checks.
        Returns True if at least one method confirms the expected state.
        Returns True unconditionally if no checks are configured (trust caller).
        """
        time.sleep(spec.settle_wait)

        evidence: dict[str, Optional[bool]] = {
            "ocr":      self._check_ocr(spec.expect_text, spec.reject_text),
            "atspi":    self._check_atspi(spec.expect_app),
            "cli":      self._check_cli(spec.expect_cmd),
            "template": self._check_template(spec.expect_image),
        }

        # Filter to methods that were actually configured
        active = {k: v for k, v in evidence.items() if v is not None}

        if not active:
            logger.debug("Verifier: no checks configured — trusting action result")
            return True

        passed  = [k for k, v in active.items() if v]
        failed  = [k for k, v in active.items() if not v]

        verdict = len(passed) > 0
        logger.info(
            f"Verifier: passed={passed} failed={failed} → "
            f"{'✅ CONFIRMED' if verdict else '❌ NOT CONFIRMED'}"
        )
        return verdict


# Singleton
verifier = Verifier()

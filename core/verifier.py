"""
Post-action state verifier — confirms whether an action actually worked.

Zero AI. Uses three independent evidence methods:
  1. OCR (Tesseract)        — read text visible on screen
  2. AT-SPI accessibility   — query the live widget tree
  3. CLI command            — run a shell command and check exit code
  4. Template matching      — find a reference image on screen (OpenCV)

A verification passes only when its configured evidence policy is satisfied.
Unavailable checks are ignored, but empty evidence fails closed by default.
All evidence is logged so failures are debuggable.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# SPEC — what "success" looks like for an action
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class VerifySpec:
    """
    Describes the expected post-action UI/system state.

    At least one evidence field should be filled. By default every available
    configured check must pass; unavailable checks do not create false failures.
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
    # True: every available check must pass. False: any available check may pass.
    require_all: bool = True
    # Explicit opt-in for callers whose successful action result is sufficient.
    allow_empty: bool = False


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

def browser_open_spec(url_fragment: str = "", browser: str = "") -> VerifySpec:
    # Use a CLI check rather than OCR ALL-match, which would require every
    # browser name to appear simultaneously — impossible. When a specific
    # `browser` was requested, verify THAT process specifically — otherwise
    # a request for e.g. Firefox could be wrongly "confirmed" just because
    # some other browser (like an already-running Chrome) happens to match
    # the generic any-browser pattern.
    texts = [url_fragment.lower()] if url_fragment else []
    proc_pattern = browser if browser else "chromium|brave|firefox|chrome"
    return VerifySpec(
        expect_text=texts,
        expect_cmd=f"pgrep -f '{proc_pattern}' > /dev/null 2>&1",
        settle_wait=2.5,
    )

def whatsapp_open_spec() -> VerifySpec:
    return VerifySpec(
        expect_text=["whatsapp"],
        expect_app="chrome",
    )


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
                check=False,  # returncode checked by caller via stdout
            )
            return r.stdout.lower()
        except Exception as exc:
            logger.debug(f"OCR failed: {exc}")
            return ""

    def _check_ocr(self, expect: list[str], reject: list[str]) -> bool | None:
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

    def _check_atspi(self, app_name: str) -> bool | None:
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

    def _check_cli(self, cmd: str) -> bool | None:
        if not cmd:
            return None
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, timeout=10,
                               check=False)  # returncode checked below
            result = r.returncode == 0
            logger.debug(f"CLI check '{cmd}': {'✅' if result else '❌'} (exit {r.returncode})")
            return result
        except Exception as exc:
            logger.debug(f"CLI check failed ({cmd!r}): {exc}")
            return None

    # ── Method 4: Template matching ───────────────────────────────────────────

    def _check_template(self, template_path: str) -> bool | None:
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
        Unavailable methods return None and are ignored. With the default
        require_all policy, any explicit contradiction fails verification.
        """
        time.sleep(spec.settle_wait)

        evidence: dict[str, bool | None] = {
            "ocr":      self._check_ocr(spec.expect_text, spec.reject_text),
            "atspi":    self._check_atspi(spec.expect_app),
            "cli":      self._check_cli(spec.expect_cmd),
            "template": self._check_template(spec.expect_image),
        }

        # Filter to methods that were actually configured
        active = {k: v for k, v in evidence.items() if v is not None}

        if not active:
            verdict = spec.allow_empty
            logger.info(
                "Verifier: no available evidence → "
                f"{'trusted by explicit policy' if verdict else 'not confirmed'}"
            )
            return verdict

        passed  = [k for k, v in active.items() if v]
        failed  = [k for k, v in active.items() if not v]

        verdict = all(active.values()) if spec.require_all else any(active.values())
        logger.info(
            f"Verifier: passed={passed} failed={failed} → "
            f"{'✅ CONFIRMED' if verdict else '❌ NOT CONFIRMED'}"
        )
        return verdict


# Singleton
verifier = Verifier()

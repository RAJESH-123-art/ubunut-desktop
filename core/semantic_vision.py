"""
SemanticVision — Tier 3 Vision Model (NVIDIA Cloud API) for Universal UI Automation.

Three tiers (tried in order):
  Tier 1: AT-SPI (fast, exact, works for GTK apps)
  Tier 2: OCR text matching (find text on screen)
  Tier 3: NVIDIA Vision Model (any app, any UI, cloud)

Requires: NVIDIA API key with vision model access.
Set env vars: NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_VISION_MODEL
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Tuple

from loguru import logger

from core.atspi_utils import do_action
from core.logger import take_screenshot


@dataclass
class VisionResult:
    success: bool
    x: int = -1
    y: int = -1
    method: str = ""
    error: str = ""
    raw_response: str = ""


class SemanticVision:
    """
    Universal UI element finder using three-tier fallback.
    
    Usage:
        sv = SemanticVision()
        result = sv.find_and_click("click the Submit button")
        if result.success:
            print(f"Clicked at ({result.x}, {result.y}) via {result.method}")
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        vision_model: str | None = None,
        timeout: float = 30.0,
    ):
        """
        Args:
            api_key: NVIDIA API key (or env NVIDIA_API_KEY)
            base_url: API base URL (or env NVIDIA_BASE_URL, default: https://integrate.api.nvidia.com/v1)
            vision_model: Vision model name (or env NVIDIA_VISION_MODEL, default: nvidia/llama-3.2-90b-vision)
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.base_url = base_url or os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        self.vision_model = vision_model or os.getenv("NVIDIA_VISION_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        self.timeout = timeout
        
        if not self.api_key:
            logger.warning("NVIDIA_API_KEY not set — Tier 3 (Vision) will be unavailable")
        
        # Lazy import for OpenAI client
        self._client = None
    
    def _get_client(self):
        """Lazy-initialize OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                )
            except ImportError:
                logger.error("openai package not installed: pip install openai")
                raise
        return self._client

    # ═══════════════════════════════════════════════════════════════
    # TIER 1: AT-SPI (Local, Fast, Exact)
    # ═══════════════════════════════════════════════════════════════
    
    def find_by_atspi(self, description: str, app_name: str = "") -> object | None:
        """
        Find UI element via AT-SPI accessibility tree.
        Works for GTK/Qt apps with proper accessibility support.
        """
        try:
            import sys

            import pyatspi
            sys.path.insert(0, "/usr/lib/python3/dist-packages")
            
            # Strip quote characters from each token -- an LLM describing a
            # target as "click the '3' button" produces the literal token
            # "'3'" (WITH quotes), which can never substring-match a real
            # button whose AT-SPI name is just "3" (no quotes: "'3'" is
            # longer than "3", so it can never be "in" it). Confirmed live:
            # this silently made AT-SPI matching fail for calculator digit/
            # operator buttons ('3', '+', '=') whenever the model quoted the
            # symbol in its description, forcing a fall-through to much less
            # reliable OCR/vision-guessed coordinates for exactly the
            # targets AT-SPI direct actions handle best.
            words = [w.strip("'\"\u2018\u2019\u201c\u201d") for w in description.lower().split()]
            app_aliases = {
                "libreoffice": "soffice",
                "libreoffice calc": "soffice",
                "libreoffice writer": "soffice",
                "libreoffice impress": "soffice",
            }
            matched_app_name = app_aliases.get(app_name.lower(), app_name).lower()
            # Destructive/window-chrome controls that a vague, fuzzy-matched
            # description should never land on by accident -- e.g. an LLM
            # decision like "click the equals button" must never silently hit
            # a window's Close/Quit control instead just because of incidental
            # substring overlap. Only allow a match here if the description
            # explicitly asks for it as a whole word (a genuine "close the
            # window" request still works fine).
            _guarded_names = {"close", "quit", "exit"}
            desktop = pyatspi.Registry.getDesktop(0)
            
            best_score, best_node = 0, None
            
            def walk(node):
                nonlocal best_score, best_node
                if node is None or best_score >= 100:
                    return
                try:
                    node_name = (node.name or "").lower()
                    if node_name and not (node_name in _guarded_names and node_name not in words):
                        # Only select nodes that can actually perform an action.
                        # GTK may expose duplicate visual/accessibility nodes where
                        # one has the right name but zero actions; selecting that
                        # decoy previously made clicks report success while doing
                        # nothing (confirmed live with Calculator's `c` button).
                        try:
                            action_iface = node.queryAction()
                            action_names = {
                                action_iface.getName(i).lower()
                                for i in range(action_iface.nActions)
                            }
                            actionable = bool(
                                action_names.intersection({"click", "press", "activate"})
                            )
                        except Exception:
                            actionable = False
                        if actionable:
                            score = sum(1 for w in words if w in node_name)
                            # Exact accessible-name matches must beat incidental
                            # substring ties. For "click the 'c' button", both
                            # `c` and `basic` scored 1, so traversal selected
                            # Basic and reported a successful Clear action while
                            # the expression remained unchanged.
                            if node_name in words:
                                score += 100
                            if score > best_score:
                                best_score, best_node = score, node
                except Exception as exc:
                    logger.debug(f"semantic vision: node walk failed: {exc}")
                for i in range(node.childCount):
                    if best_score >= 100:
                        break
                    try:
                        walk(node.getChildAtIndex(i))
                    except Exception as exc:
                        logger.debug(f"semantic vision: child walk failed: {exc}")
            
            for app in desktop:
                if best_score >= 100:
                    break
                if app is None:
                    continue
                if matched_app_name and matched_app_name not in (app.name or "").lower():
                    continue
                walk(app)
            
            if best_node and best_score > 0:
                logger.debug(f"AT-SPI: found '{description}' (score={best_score})")
                return best_node
        except Exception as exc:
            logger.debug(f"AT-SPI find failed: {exc}")
        return None

    # ═══════════════════════════════════════════════════════════════
    # TIER 2: OCR (Local, Text-Based)
    # ═══════════════════════════════════════════════════════════════
    
    def find_by_ocr(self, description: str) -> Tuple[int, int] | None:
        """
        Find element via OCR text matching.
        Returns (x, y) center coordinates of matching text.
        """
        try:
            import cv2
            import pytesseract
            
            ss = take_screenshot(name="semantic_vision_ocr")
            img = cv2.imread(ss)
            if img is None:
                return None
            
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            words = description.lower().split()
            
            for i, text in enumerate(data['text']):
                if not text.strip():
                    continue
                conf = int(data['conf'][i]) if data['conf'][i] != '-1' else 0
                if conf < 50:
                    continue
                if any(w in text.lower() for w in words):
                    x = data['left'][i] + data['width'][i] // 2
                    y = data['top'][i] + data['height'][i] // 2
                    logger.debug(f"OCR: found '{description}' at ({x}, {y}) conf={conf}")
                    return (x, y)
        except Exception as exc:
            logger.debug(f"OCR find failed: {exc}")
        return None

    # ═══════════════════════════════════════════════════════════════
    # TIER 3: NVIDIA VISION MODEL (Cloud, Universal)
    # ═══════════════════════════════════════════════════════════════
    
    def find_by_vision_model(self, description: str) -> Tuple[int, int] | None:
        """
        Use NVIDIA Vision Model to locate UI element.
        Returns (x, y) pixel coordinates.
        """
        if not self.api_key:
            logger.debug("Vision model: no API key configured")
            return None
        
        try:
            # Take screenshot
            ss = take_screenshot(name="semantic_vision_nvidia")
            with open(ss, "rb") as f:
                img_bytes = f.read()
            img_b64 = base64.b64encode(img_bytes).decode()

            # The model must be told the EXACT pixel dimensions of the image
            # it's looking at and asked to answer in that same coordinate
            # space. Without this, a model that internally resizes the image
            # before "looking" at it has no way to know what space to answer
            # in, and coordinates can land systematically offset from the
            # real click target -- this is the same scale-mismatch problem
            # documented for computer-use tools generally (see
            # VERCEPT_LEVEL_ROADMAP.md). We never resize the screenshot
            # ourselves, so no scale-back math is needed on our end -- just
            # removing the model's ambiguity about which space to answer in.
            width, height = 0, 0
            try:
                import cv2
                dims_img = cv2.imread(ss)
                if dims_img is not None:
                    height, width = dims_img.shape[:2]
            except Exception as exc:
                logger.debug(f"semantic vision: cv2 image read failed: {exc}")
            dims_note = f"exactly {width}x{height} pixels (width x height)" if width and height else "of unknown but fixed pixel dimensions"

            # Build prompt for vision model
            prompt = (
                f"Look at this desktop screenshot, which is {dims_note}. "
                f"Find the UI element described as: '{description}'. "
                f"Return ONLY a JSON object with pixel coordinates IN THIS EXACT "
                f"IMAGE's coordinate space (0,0 = top-left corner of THIS image, "
                f"not a resized or cropped version of it): "
                f'{{"x": <int 0-{width or 99999}>, "y": <int 0-{height or 99999}>}}. '
                f"If not found, return {{\"x\": -1, \"y\": -1}}"
            )
            
            client = self._get_client()
            
            # Call NVIDIA vision model
            response = client.chat.completions.create(
                model=self.vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                    ]
                }],
                # Reasoning models (e.g. nemotron-*-reasoning) spend tokens on internal
                # reasoning before emitting the final JSON, so this needs headroom beyond
                # a plain vision model's ~100 tokens.
                max_tokens=2048,
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=self.timeout,
            )
            
            result_text = response.choices[0].message.content or ""
            result = json.loads(result_text) if result_text else {}
            x, y = int(result.get("x", -1)), int(result.get("y", -1))
            
            if x > 0 and y > 0:
                logger.info(f"NVIDIA Vision: found '{description}' at ({x}, {y})")
                return (x, y)
            else:
                logger.debug(f"NVIDIA Vision: element '{description}' not found")
                return None
                
        except Exception as exc:
            logger.debug(f"NVIDIA Vision failed: {exc}")
            return None

    # ═══════════════════════════════════════════════════════════════
    # UNIFIED INTERFACE
    # ═══════════════════════════════════════════════════════════════
    
    def find_and_click(self, description: str, app_name: str = "") -> VisionResult:
        """
        Try all three tiers to find and click the described element.
        Returns VisionResult with success status and details.
        """
        from core.gui_controller import GUIController
        gui = GUIController()
        
        # ── Tier 1: AT-SPI ─────────────────────────────────────────────
        node = self.find_by_atspi(description, app_name)
        if node:
            try:
                if do_action(node):
                    logger.info(f"✅ Clicked '{description}' via AT-SPI (Tier 1)")
                    return VisionResult(True, method="atspi", x=0, y=0)
                logger.debug(f"AT-SPI action returned False for '{description}'")
            except Exception as exc:
                logger.debug(f"AT-SPI click failed: {exc}")
        
        # ── Tier 2: OCR ────────────────────────────────────────────────
        coords = self.find_by_ocr(description)
        if coords:
            try:
                gui.click(*coords)
                logger.info(f"✅ Clicked '{description}' via OCR at {coords} (Tier 2)")
                return VisionResult(True, method="ocr", x=coords[0], y=coords[1])
            except Exception as exc:
                logger.debug(f"OCR click failed: {exc}")
        
        # ── Tier 3: NVIDIA Vision Model ───────────────────────────────
        coords = self.find_by_vision_model(description)
        if coords:
            try:
                gui.click(*coords)
                logger.info(f"✅ Clicked '{description}' via NVIDIA Vision at {coords} (Tier 3)")
                return VisionResult(True, method="nvidia_vision", x=coords[0], y=coords[1])
            except Exception as exc:
                logger.debug(f"Vision click failed: {exc}")
        
        logger.error(f"❌ Could not find '{description}' via any method")
        return VisionResult(False, error=f"Element '{description}' not found")


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def create_vision_from_env() -> SemanticVision:
    """Create SemanticVision from environment variables."""
    return SemanticVision()


# Global singleton (initialized from env)
semantic_vision = SemanticVision()


# ═══════════════════════════════════════════════════════════════
# CLI TESTING
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m core.semantic_vision 'description' [app_name]")
        print("Env vars: NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_VISION_MODEL")
        sys.exit(1)
    
    desc = sys.argv[1]
    app = sys.argv[2] if len(sys.argv) > 2 else ""
    
    sv = create_vision_from_env()
    result = sv.find_and_click(desc, app)
    
    print(f"Success: {result.success}")
    print(f"Method: {result.method}")
    print(f"Coordinates: ({result.x}, {result.y})")
    if result.error:
        print(f"Error: {result.error}")
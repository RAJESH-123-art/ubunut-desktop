"""Confidence-gated, single-request visual target localization."""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualTarget:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    label: str

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def validate_visual_target(
    payload: object,
    *,
    image_width: int,
    image_height: int,
    minimum_confidence: float,
) -> VisualTarget:
    if not isinstance(payload, dict):
        raise TypeError("Visual localization response must be an object")
    try:
        target = VisualTarget(
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            confidence=float(payload["confidence"]),
            label=str(payload.get("label", "")).strip(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Visual localization response is incomplete: {exc}") from exc
    if not 0.0 <= target.confidence <= 1.0:
        raise ValueError("Visual target confidence must be between 0 and 1")
    if target.confidence < minimum_confidence:
        raise RuntimeError(
            f"Visual target confidence {target.confidence:.2f} is below "
            f"required {minimum_confidence:.2f}"
        )
    if target.width < 2 or target.height < 2:
        raise ValueError("Visual target bounding box is too small")
    if (
        target.x < 0
        or target.y < 0
        or target.x + target.width > image_width
        or target.y + target.height > image_height
    ):
        raise ValueError("Visual target bounding box is outside the screenshot")
    return target


class VisualLocator:
    """Locate one target from one screenshot without making action decisions."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.base_url = base_url or os.getenv(
            "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )
        self.model = model or os.getenv(
            "NVIDIA_VISION_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
        )
        self.timeout = max(1.0, min(timeout, 20.0))
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def locate(
        self,
        screenshot: str | Path,
        target_description: str,
        *,
        minimum_confidence: float = 0.85,
    ) -> VisualTarget:
        path = Path(screenshot)
        if not path.is_file():
            raise FileNotFoundError(f"Screenshot does not exist: {path}")

        import cv2

        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Could not decode screenshot: {path}")
        image_height, image_width = image.shape[:2]
        local = self._locate_named_color_region(
            image,
            target_description,
            minimum_confidence=minimum_confidence,
        )
        if local is not None:
            return validate_visual_target(
                {
                    "x": local.x,
                    "y": local.y,
                    "width": local.width,
                    "height": local.height,
                    "confidence": local.confidence,
                    "label": local.label,
                },
                image_width=image_width,
                image_height=image_height,
                minimum_confidence=minimum_confidence,
            )
        if not self.available():
            raise RuntimeError("NVIDIA_API_KEY is not configured for visual localization")
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = (
            f"Locate exactly one visible UI target described as {target_description!r} in this "
            f"{image_width}x{image_height} screenshot. Return only JSON with a single exact "
            "pixel point on the target's safe clickable interior in the original screenshot "
            "coordinate system and a calibrated confidence: {\"x\":int,\"y\":int,"
            "\"confidence\":number,\"label\":string}. Do not return a bounding box. "
            "If ambiguous or absent, use confidence 0."
        )
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            }],
            temperature=0.0,
            max_tokens=500,
            timeout=self.timeout,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        text = (response.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            # Vision models occasionally wrap an otherwise valid object in a
            # short explanation.  Accept exactly one JSON object from that
            # response, but never invent coordinates or parse arbitrary text.
            match = re.search(r"\{\s*\"x\".*?\}", text, flags=re.DOTALL)
            if match is None:
                raise ValueError(f"Visual locator returned invalid JSON: {exc}") from exc
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError as nested_exc:
                raise ValueError(
                    f"Visual locator returned invalid embedded JSON: {nested_exc}"
                ) from nested_exc
        if isinstance(payload, list):
            if len(payload) != 1:
                raise ValueError(
                    "Visual localization must return exactly one target, "
                    f"received {len(payload)}"
                )
            payload = payload[0]
        if isinstance(payload, dict) and "width" not in payload and "height" not in payload:
            # The action needs one reliable point, not a model-estimated box.
            # Validate it through the same bounds/confidence contract by
            # representing that point as a minimal clickable region.
            payload = {**payload, "width": 2, "height": 2}
        return validate_visual_target(
            payload,
            image_width=image_width,
            image_height=image_height,
            minimum_confidence=minimum_confidence,
        )

    @staticmethod
    def _locate_named_color_region(
        image: Any,
        target_description: str,
        *,
        minimum_confidence: float,
    ) -> VisualTarget | None:
        """Locate one sizeable region only when its colour is explicit.

        This is a deterministic visual primitive, not a replacement for a
        semantic vision model.  It deliberately declines descriptions without
        exactly one recognised colour or scenes with competing regions, so it
        cannot invent a target from a vague instruction.
        """
        normalized = re.findall(r"[a-z]+", target_description.lower())
        families = {
            "red": ((0, 8), (172, 179)),
            "orange": ((9, 26),),
            "yellow": ((27, 38),),
            "green": ((39, 89),),
            "blue": ((90, 132),),
            "purple": ((133, 171),),
        }
        aliases = {"amber": "orange", "violet": "purple"}
        requested = {
            aliases.get(word, word)
            for word in normalized
            if aliases.get(word, word) in families
        }
        if len(requested) != 1:
            return None
        family = requested.pop()
        try:
            import cv2
            import numpy as np

            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
            for lower_hue, upper_hue in families[family]:
                mask |= cv2.inRange(
                    hsv,
                    np.array([lower_hue, 110, 70]),
                    np.array([upper_hue, 255, 255]),
                )
            count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
            height, width = image.shape[:2]
            minimum_area = max(200, int(width * height * 0.0005))
            candidates = [
                tuple(map(int, stats[index]))
                for index in range(1, count)
                if int(stats[index, cv2.CC_STAT_AREA]) >= minimum_area
            ]
        except Exception:
            return None
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[4], reverse=True)
        if len(candidates) > 1 and candidates[1][4] >= candidates[0][4] * 0.55:
            return None
        x, y, component_width, component_height, _area = candidates[0]
        confidence = 0.98
        if confidence < minimum_confidence:
            return None
        return VisualTarget(
            x=x,
            y=y,
            width=component_width,
            height=component_height,
            confidence=confidence,
            label=f"local_{family}_region",
        )

    @staticmethod
    def refine_high_contrast_target(
        screenshot: str | Path,
        target: VisualTarget,
    ) -> VisualTarget:
        """Snap a nearby vision point to one clear coloured visual region.

        This is intentionally conservative: it only refines a high-confidence
        model result toward a sizeable saturated component near that result.
        It does not search the screen or create a target on its own.
        """
        if target.confidence < 0.9:
            return target
        try:
            import cv2
            import numpy as np

            image = cv2.imread(str(screenshot))
            if image is None:
                return target
            height, width = image.shape[:2]
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            # Saturation excludes white/grey canvas backgrounds and ordinary
            # black text while retaining common coloured canvas objects.
            mask = cv2.inRange(hsv, np.array([0, 70, 45]), np.array([179, 255, 255]))
            count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
            proposed_x, proposed_y = target.center
            max_distance = max(80.0, (width * width + height * height) ** 0.5 * 0.2)
            candidates: list[tuple[float, int, int, int, int]] = []
            minimum_area = max(100, int(width * height * 0.0001))
            for index in range(1, count):
                x, y, component_width, component_height, area = map(int, stats[index])
                if area < minimum_area:
                    continue
                center_x = x + component_width / 2
                center_y = y + component_height / 2
                distance = ((center_x - proposed_x) ** 2 + (center_y - proposed_y) ** 2) ** 0.5
                if distance <= max_distance:
                    candidates.append((distance, x, y, component_width, component_height))
            if not candidates:
                return target
            _distance, x, y, component_width, component_height = min(candidates)
            return VisualTarget(
                x=x,
                y=y,
                width=component_width,
                height=component_height,
                confidence=target.confidence,
                label=target.label,
            )
        except Exception:
            return target

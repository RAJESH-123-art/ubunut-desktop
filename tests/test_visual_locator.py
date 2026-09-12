from __future__ import annotations

import cv2
import numpy as np

from core.visual_locator import VisualLocator


def test_local_locator_uses_an_explicit_single_colour_region_without_cloud(tmp_path) -> None:
    image = np.full((300, 500, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (40, 70), (180, 190), (183, 101, 14), thickness=-1)  # blue BGR
    path = tmp_path / "canvas.png"
    assert cv2.imwrite(str(path), image)

    target = VisualLocator(api_key=None).locate(
        path,
        "solid blue draggable card",
        minimum_confidence=0.9,
    )

    assert target.label == "local_blue_region"
    assert target.center == (110, 130)


def test_local_locator_declines_competing_colour_regions(tmp_path) -> None:
    image = np.full((300, 500, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (30, 70), (160, 190), (183, 101, 14), thickness=-1)
    cv2.rectangle(image, (280, 70), (410, 190), (183, 101, 14), thickness=-1)
    path = tmp_path / "ambiguous.png"
    assert cv2.imwrite(str(path), image)

    assert VisualLocator(api_key=None)._locate_named_color_region(
        image,
        "blue card",
        minimum_confidence=0.9,
    ) is None

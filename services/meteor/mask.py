"""
Meteor Exclusion Mask
Persistent regions where detections are always suppressed.

Workflow:
  - User marks a detection "Not a Meteor" in the UI.
  - MeteorController creates an ExclusionZone from that detection's bounding
    box (with padding) and saves it to config['meteor']['exclusion_zones'].
  - On every subsequent frame, apply_exclusion_zones() blacks out those
    rectangles in the Hough detection mask before line finding.
"""
from dataclasses import dataclass, asdict
from typing import List

import cv2
import numpy as np


@dataclass
class ExclusionZone:
    x: int       # left edge (image pixels)
    y: int       # top edge  (image pixels)
    w: int       # width
    h: int       # height
    note: str = ""  # human-readable reason, e.g. "telescope mount"


def apply_exclusion_zones(
    detection_mask: np.ndarray,
    zones: List[ExclusionZone],
) -> np.ndarray:
    """
    Zero out every ExclusionZone rectangle in *detection_mask*.

    Works on any single-channel uint8 ndarray (the dilated edge mask fed into
    HoughLinesP).  Returns the original array if *zones* is empty.
    """
    if not zones:
        return detection_mask

    h, w = detection_mask.shape[:2]
    result = detection_mask.copy()
    for zone in zones:
        x1 = max(0, zone.x)
        y1 = max(0, zone.y)
        x2 = min(w, zone.x + zone.w)
        y2 = min(h, zone.y + zone.h)
        if x2 > x1 and y2 > y1:
            result[y1:y2, x1:x2] = 0
    return result


def zone_from_detection(
    x1: int, y1: int, x2: int, y2: int,
    image_width: int,
    image_height: int,
    padding: int = 80,
) -> ExclusionZone:
    """
    Build an ExclusionZone from detection endpoint coords with *padding*.

    The resulting rectangle covers the detected line plus *padding* pixels on
    every side, clamped to the image boundaries.
    """
    lx = max(0, min(x1, x2) - padding)
    ly = max(0, min(y1, y2) - padding)
    rx = min(image_width,  max(x1, x2) + padding)
    ry = min(image_height, max(y1, y2) + padding)
    return ExclusionZone(x=lx, y=ly, w=rx - lx, h=ry - ly)


def zones_from_config(cfg: dict) -> List[ExclusionZone]:
    """Deserialise exclusion zones from the 'meteor' config sub-dict."""
    return [
        ExclusionZone(**z)
        for z in cfg.get("exclusion_zones", [])
        if isinstance(z, dict)
    ]


def zones_to_config(zones: List[ExclusionZone]) -> List[dict]:
    """Serialise exclusion zones for storage in the config."""
    return [asdict(z) for z in zones]

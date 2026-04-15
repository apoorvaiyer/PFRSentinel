"""
Meteor Detector
CV-based meteor trail detection, adapted from the AllSky meteor module.
https://github.com/AllskyTeam/allsky/blob/master/scripts/modules/allsky_meteor.py

Algorithm:
  1. Grayscale + Canny edge detection
  2. Dilate / erode to connect nearby edges
  3. Contour-based cloud mask (large blobs are clouds, not meteors)
  4. Exclusion zone mask (user-rejected regions suppressed permanently)
  5. Probabilistic Hough line detection
  6. Length filter to discard short noise lines
"""
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image


@dataclass
class MeteorDetection:
    x1: int
    y1: int
    x2: int
    y2: int
    length: float       # Euclidean length in pixels
    angle_deg: float    # Angle from horizontal (degrees)


def detect_meteors(
    image: Image.Image,
    min_length: int = 100,
    exclusion_zones: Optional[List] = None,
) -> List[MeteorDetection]:
    """
    Detect meteor trails in a PIL image.

    Args:
        image:           PIL Image (any mode — converted to RGB internally).
        min_length:      Minimum trail length in pixels.
        exclusion_zones: Optional list of ExclusionZone instances.  Regions
                         inside these rectangles are zeroed out before Hough
                         detection, so persistent false-positive sources
                         (e.g. equipment edges) are permanently suppressed.

    Returns:
        List of MeteorDetection instances (may be empty).
    """
    img_array = np.array(image.convert("RGB"))
    img_gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

    # --- Edge detection ---
    edges = cv2.Canny(img_gray.astype(np.uint8), 100, 200, apertureSize=3)

    # --- Morphological cleanup: connect nearby edge fragments ---
    k3 = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(edges, k3, iterations=2)
    dilated = cv2.erode(dilated, k3, iterations=1)

    # --- Cloud mask: large contours are clouds/nebulae, not meteors ---
    cloud_mask = np.zeros(dilated.shape, np.uint8)
    contours, _ = cv2.findContours(dilated, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    has_large = False
    for cnt in contours:
        if cv2.contourArea(cnt) > 1550:
            has_large = True
            cv2.drawContours(cloud_mask, [cnt], 0, 255, -1)

    if has_large:
        k7 = np.ones((7, 7), np.uint8)
        expanded_cloud = cv2.dilate(cloud_mask, k7, iterations=1)
        detection_mask = cv2.bitwise_and(dilated, cv2.bitwise_not(expanded_cloud))
    else:
        detection_mask = dilated

    # --- User-defined exclusion zones ---
    if exclusion_zones:
        from .mask import apply_exclusion_zones
        detection_mask = apply_exclusion_zones(detection_mask, exclusion_zones)

    # --- Probabilistic Hough lines ---
    lines = cv2.HoughLinesP(
        detection_mask,
        rho=3,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=min_length,
        maxLineGap=20,
    )

    detections: List[MeteorDetection] = []
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            dx, dy = x2 - x1, y2 - y1
            length = float(np.hypot(dx, dy))
            if length >= min_length:
                angle = float(np.degrees(np.arctan2(dy, dx)))
                detections.append(
                    MeteorDetection(
                        x1=int(x1), y1=int(y1),
                        x2=int(x2), y2=int(y2),
                        length=length,
                        angle_deg=angle,
                    )
                )

    return detections


def annotate_image(image: Image.Image, detections: List[MeteorDetection]) -> Image.Image:
    """
    Return a copy of *image* with detected meteor trails drawn in green.

    If *detections* is empty the image is returned unchanged (copy).
    """
    if not detections:
        return image.copy()

    arr = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    for det in detections:
        cv2.line(bgr, (det.x1, det.y1), (det.x2, det.y2), (0, 255, 0), 3)
        mid_x = (det.x1 + det.x2) // 2
        mid_y = (det.y1 + det.y2) // 2 - 10
        cv2.putText(
            bgr, f"{det.length:.0f}px",
            (mid_x, mid_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
        )

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)

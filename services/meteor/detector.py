"""
Meteor Detector
CV-based meteor trail detection using frame differencing.

Algorithm:
  1. Frame differencing: subtract previous frame to isolate transient events
  2. Sky circle mask: restrict to fisheye's circular sky region
  3. Grayscale + Canny edge detection
  4. Dilate / erode to connect nearby edges
  5. Contour-based cloud mask (large blobs are clouds, not meteors)
  6. Exclusion zone mask (user-rejected regions suppressed permanently)
  7. Probabilistic Hough line detection
  8. Length filter + strict validation (angle, brightness)
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


def estimate_adaptive_threshold(image: Image.Image, sample_ratio: float = 0.1) -> int:
    """
    Estimate a noise-adaptive diff threshold from the image.

    Samples a central sub-region (default 10% of area), computes the
    standard deviation of pixel values, then maps SNR to a threshold via
    a quadratic function adapted from MetDetPy:  ``1.2 * std^2 + 3.6``.

    Returns an integer threshold clamped to [5, 100].
    """
    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    side = max(1, int((h * w * sample_ratio) ** 0.5))
    cy, cx = h // 2, w // 2
    half = side // 2
    crop = gray[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
    std = float(np.std(crop))
    threshold = int(1.2 * std * std + 3.6)
    return max(5, min(100, threshold))


def compute_frame_difference(
    current: Image.Image,
    previous: Image.Image,
    threshold: int = 25,
) -> Image.Image:
    """
    Compute the absolute difference between two frames.

    Pixels below *threshold* are zeroed to suppress read-noise jitter.
    Pass *threshold* = 0 to skip noise gating (e.g. when using adaptive
    thresholding externally).
    Returns an RGB PIL Image suitable for passing to ``detect_meteors``.
    """
    cur_gray = cv2.cvtColor(np.array(current.convert("RGB")), cv2.COLOR_RGB2GRAY)
    prev_gray = cv2.cvtColor(np.array(previous.convert("RGB")), cv2.COLOR_RGB2GRAY)

    # Resize if frames differ (e.g. first-frame edge case)
    if cur_gray.shape != prev_gray.shape:
        prev_gray = cv2.resize(prev_gray, (cur_gray.shape[1], cur_gray.shape[0]))

    diff = cv2.absdiff(cur_gray, prev_gray)
    if threshold > 0:
        diff[diff < threshold] = 0
    rgb = cv2.cvtColor(diff, cv2.COLOR_GRAY2RGB)
    return Image.fromarray(rgb)


def apply_sky_circle_mask(
    image: Image.Image,
    cx: float,
    cy: float,
    radius: float,
) -> Image.Image:
    """
    Zero out all pixels outside the sky circle.

    Used to restrict meteor detection to the fisheye's circular sky region,
    eliminating equipment edges and frame corners.
    """
    arr = np.array(image.convert("RGB"))
    h, w = arr.shape[:2]
    Y, X = np.ogrid[:h, :w]
    dist_sq = (X - cx) ** 2 + (Y - cy) ** 2
    outside = dist_sq > radius ** 2
    arr[outside] = 0
    return Image.fromarray(arr)


def _validate_trail_brightness(
    gray: np.ndarray,
    det: MeteorDetection,
    min_brightness: int = 30,
) -> bool:
    """Check that mean pixel intensity along the trail exceeds *min_brightness*."""
    # Sample points along the line using Bresenham-style steps
    n_samples = max(int(det.length), 2)
    xs = np.linspace(det.x1, det.x2, n_samples, dtype=int)
    ys = np.linspace(det.y1, det.y2, n_samples, dtype=int)
    h, w = gray.shape[:2]
    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)
    mean_val = float(np.mean(gray[ys, xs]))
    return mean_val >= min_brightness


def _is_axis_aligned(angle_deg: float, tolerance: float = 1.5) -> bool:
    """True if the line is within *tolerance* degrees of exactly 0 or +/-90."""
    a = abs(angle_deg)
    return a < tolerance or abs(a - 90.0) < tolerance


def check_speed_plausibility(
    det: MeteorDetection,
    exposure_sec: float,
    image_width: int,
    min_speed_pct: float = 2.0,
    max_speed_pct: float = 50.0,
) -> bool:
    """
    Check whether a trail's angular speed is plausible for a meteor.

    Speed is expressed as percentage of image width traversed per second.
    Meteors typically move 2-50% of image width/sec; planes and satellites
    are much slower (< 1%).  Very high values may indicate noise.

    Returns True if the speed falls within the plausible range.
    """
    if exposure_sec <= 0:
        return True  # Can't validate without exposure info
    speed_pct = (det.length / image_width) / exposure_sec * 100.0
    return min_speed_pct <= speed_pct <= max_speed_pct


def detect_meteors(
    image: Image.Image,
    min_length: int = 100,
    exclusion_zones: Optional[List] = None,
    strict_validation: bool = False,
) -> List[MeteorDetection]:
    """
    Detect meteor trails in a PIL image.

    Args:
        image:             PIL Image (any mode — converted to RGB internally).
                           For best results, pass a frame-difference image from
                           ``compute_frame_difference()``.
        min_length:        Minimum trail length in pixels.
        exclusion_zones:   Optional list of ExclusionZone instances.
        strict_validation: When True, reject axis-aligned lines (0/90 deg)
                           and lines with low mean brightness — reduces false
                           positives on diff images.

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
            if length < min_length:
                continue
            angle = float(np.degrees(np.arctan2(dy, dx)))
            if strict_validation and _is_axis_aligned(angle):
                continue
            det = MeteorDetection(
                x1=int(x1), y1=int(y1),
                x2=int(x2), y2=int(y2),
                length=length,
                angle_deg=angle,
            )
            if strict_validation and not _validate_trail_brightness(img_gray, det):
                continue
            detections.append(det)

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

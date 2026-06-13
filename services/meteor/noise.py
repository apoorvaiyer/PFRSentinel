"""
Diff-noise estimation for meteor detection thresholding.

Port of MetDetPy's adaptive binary threshold approach:
  threshold = coeff * sigma^2 + intercept
  sigma = MAD-based noise estimate of the transient map, EMA-smoothed across frames.

The key difference from the old estimate_adaptive_threshold() in detector.py:
  OLD: sigma was measured from a stretched sky frame (scene structure, not noise).
  NEW: sigma is measured from the transient map (max-mean diff), which is near-zero
       for static regions and only bright at genuine transient pixels. This gives a
       much lower, more appropriate threshold.
"""
from typing import Optional

import numpy as np


_SENSITIVITY = {
    "high":   (0.9, 3.0, 3),
    "normal": (1.2, 3.6, 5),
    "low":    (2.0, 4.4, 7),
}


def estimate_diff_noise(diff: np.ndarray, sample_area: float = 0.1) -> float:
    """
    MAD-based noise estimate from a central sub-region of *diff*.

    Uses MAD (median absolute deviation) rather than std so a bright meteor
    streak through the sample region doesn't inflate the estimate.
    MAD is converted to equivalent Gaussian sigma via the 1.4826 factor.

    Returns sigma in [0, 255].
    """
    h, w = diff.shape[:2]
    side = max(4, int((h * w * sample_area) ** 0.5))
    cy, cx = h // 2, w // 2
    half = side // 2
    crop = diff[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
    if crop.size == 0:
        return 5.0
    flat = crop.astype(np.float32).ravel()
    median = float(np.median(flat))
    mad = float(np.median(np.abs(flat - median)))
    return float(np.clip(mad * 1.4826, 0.0, 255.0))


def noise_to_threshold(sigma: float, sensitivity: str = "normal") -> int:
    """
    Map noise sigma to a binary detection threshold via MetDetPy's formula.

    sensitivity choices: "high" (more sensitive), "normal", "low" (fewer FP).
    Output is clamped to [floor, 100].
    """
    coeff, intercept, floor = _SENSITIVITY.get(sensitivity, _SENSITIVITY["normal"])
    raw = coeff * sigma * sigma + intercept
    return max(floor, min(100, int(raw)))


class DiffNoiseEMA:
    """
    Exponential moving average of the noise estimate across frames.

    Smooths out frame-to-frame variation in sky brightness without
    discarding recent changes (e.g. clouds rolling in).
    """

    def __init__(self, alpha: float = 0.1):
        self._alpha = alpha
        self._sigma: Optional[float] = None

    def update(self, diff: np.ndarray, sample_area: float = 0.1) -> float:
        """Ingest a new diff frame and return the updated EMA sigma."""
        s = estimate_diff_noise(diff, sample_area)
        if self._sigma is None:
            self._sigma = s
        else:
            self._sigma = (1.0 - self._alpha) * self._sigma + self._alpha * s
        return self._sigma

    def reset(self):
        self._sigma = None

    @property
    def value(self) -> Optional[float]:
        return self._sigma

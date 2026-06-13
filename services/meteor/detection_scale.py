"""
DetectionScale — coordinate conversion between detection and full-resolution spaces.

The meteor detector runs at a reduced resolution (detection_long_side, default 1280 px)
for speed. Exclusion zones, thumbnails, and rejection coordinates live in full-resolution
pixel space. This module is the single conversion boundary — all other code imports from
here rather than recomputing the scale factor.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionScale:
    """
    Immutable scale descriptor.

    factor = detection_width / full_width  (≤ 1.0)
    """
    factor: float

    def to_detection(self, px: float) -> float:
        return px * self.factor

    def to_full(self, px: float) -> float:
        return px / self.factor if self.factor > 0 else px

    def scale_length(self, px: int) -> int:
        """Scale a pixel length to detection coords (always ≥ 1)."""
        return max(1, int(px * self.factor))

    def detection_size(self, full_w: int, full_h: int):
        """Return (det_w, det_h) for a given full-resolution frame."""
        return (max(1, int(full_w * self.factor)), max(1, int(full_h * self.factor)))


def make_scale(detection_frame_width: int, full_frame_width: int) -> DetectionScale:
    """
    Build a DetectionScale from the actual frame widths after both the
    resize_percent step and the detection downscale step have been applied.

    Pass the widths in pixels — aspect ratio is assumed identical.
    """
    if full_frame_width <= 0:
        return DetectionScale(factor=1.0)
    return DetectionScale(factor=detection_frame_width / full_frame_width)

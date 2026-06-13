"""Tests for services/meteor/detector.py — detection algorithm contract."""
import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.meteor.detector import (
    MeteorDetection, annotate_image, detect_meteors,
    compute_frame_difference, apply_sky_circle_mask,
    estimate_adaptive_threshold,
)


def _blank(width=256, height=256, fill=10) -> Image.Image:
    return Image.fromarray(np.full((height, width, 3), fill, dtype=np.uint8))


def _with_line(x1, y1, x2, y2, width=512, height=512, bg=10, line_val=200) -> Image.Image:
    arr = np.full((height, width, 3), bg, dtype=np.uint8)
    img = Image.fromarray(arr)
    ImageDraw.Draw(img).line([(x1, y1), (x2, y2)], fill=(line_val, line_val, line_val), width=3)
    return img


# ---------------------------------------------------------------------------
# Contract — output type / shape
# ---------------------------------------------------------------------------

class TestDetectorContract:
    def test_returns_list(self):
        assert isinstance(detect_meteors(_blank()), list)

    def test_empty_image_no_detections(self):
        assert detect_meteors(_blank(fill=10)) == []

    def test_uniform_bright_no_detections(self):
        assert detect_meteors(_blank(fill=255)) == []

    def test_returns_meteor_detection_instances(self):
        img = _with_line(50, 50, 300, 50)
        for det in detect_meteors(img, min_length=100, threshold=50):
            assert isinstance(det, MeteorDetection)

    def test_detection_fields_populated(self):
        img = _with_line(50, 50, 300, 50)
        result = detect_meteors(img, min_length=100, threshold=50)
        assert result, "Expected at least one detection on a 250px horizontal line"
        det = result[0]
        assert isinstance(det.x1, int)
        assert det.length > 0
        assert isinstance(det.angle_deg, float)
        assert 0.0 <= det.nonline_prob <= 1.0

    def test_input_image_not_mutated(self):
        img = _with_line(50, 50, 300, 50)
        original = np.array(img.copy())
        detect_meteors(img, min_length=100)
        np.testing.assert_array_equal(np.array(img), original)


# ---------------------------------------------------------------------------
# Line detection sensitivity
# ---------------------------------------------------------------------------

class TestDetectorLineDetection:
    def test_horizontal_line_detected(self):
        img = _with_line(50, 128, 350, 128, line_val=220)
        assert detect_meteors(img, min_length=100, threshold=50), "300px line not found"

    def test_diagonal_line_detected(self):
        img = _with_line(100, 100, 300, 300, line_val=220)
        assert detect_meteors(img, min_length=100, threshold=50), "Diagonal not found"

    def test_short_line_below_min_length_ignored(self):
        img = _with_line(100, 100, 130, 100, line_val=220)
        assert detect_meteors(img, min_length=100, threshold=50) == []

    def test_detected_length_is_plausible(self):
        img = _with_line(50, 128, 250, 128, line_val=220)
        result = detect_meteors(img, min_length=100, threshold=50)
        assert result
        best = max(result, key=lambda d: d.length)
        assert 160 <= best.length <= 240, f"Expected ~200px, got {best.length:.0f}"

    def test_vertical_line_detected(self):
        img = _with_line(200, 50, 200, 330, line_val=220)
        assert detect_meteors(img, min_length=100, threshold=50), "Vertical not found"


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

class TestAnnotation:
    def test_annotate_returns_image(self):
        assert isinstance(annotate_image(_blank(), []), Image.Image)

    def test_annotate_empty_returns_copy(self):
        img = _blank()
        result = annotate_image(img, [])
        assert result is not img
        np.testing.assert_array_equal(np.array(result), np.array(img))

    def test_annotate_with_detection_changes_image(self):
        img = _blank(256, 256, fill=10)
        det = MeteorDetection(10, 10, 200, 200, 268.0, 45.0)
        result = annotate_image(img, [det])
        assert not np.array_equal(np.array(result), np.array(img))

    def test_annotate_size_preserved(self):
        img = _blank(400, 300)
        det = MeteorDetection(10, 10, 300, 250, 380.0, 30.0)
        assert annotate_image(img, [det]).size == img.size

    def test_annotate_does_not_mutate_input(self):
        img = _blank()
        original = np.array(img.copy())
        annotate_image(img, [MeteorDetection(10, 10, 200, 200, 268.0, 45.0)])
        np.testing.assert_array_equal(np.array(img), original)


# ---------------------------------------------------------------------------
# Frame differencing (deprecated helper — still tested for compat)
# ---------------------------------------------------------------------------

class TestFrameDifferencing:
    def test_identical_frames_blank_diff(self):
        img = _with_line(50, 128, 300, 128)
        diff = compute_frame_difference(img, img.copy(), threshold=25)
        assert detect_meteors(diff, min_length=100) == []

    def test_static_line_disappears_in_diff(self):
        frame_a = _with_line(50, 128, 300, 128)
        frame_b = _with_line(50, 128, 300, 128)
        diff = compute_frame_difference(frame_a, frame_b, threshold=25)
        assert detect_meteors(diff, min_length=100) == []

    def test_new_line_appears_in_diff(self):
        blank = _blank(512, 512)
        lined = _with_line(50, 128, 300, 128, line_val=220)
        diff = compute_frame_difference(lined, blank, threshold=5)
        assert detect_meteors(diff, min_length=100, threshold=5), "New line not in diff"

    def test_diff_threshold_suppresses_noise(self):
        np.random.seed(42)
        base = np.full((256, 256, 3), 20, dtype=np.uint8)
        noisy = np.clip(base.astype(int) + np.random.randint(0, 10, base.shape), 0, 255).astype(np.uint8)
        diff = compute_frame_difference(Image.fromarray(noisy), Image.fromarray(base), threshold=25)
        assert np.array(diff).max() == 0

    def test_diff_threshold_preserves_signal(self):
        blank = _blank(512, 512, fill=10)
        lined = _with_line(50, 128, 300, 128, bg=10, line_val=200)
        diff = compute_frame_difference(lined, blank, threshold=25)
        assert np.array(diff).max() > 100


# ---------------------------------------------------------------------------
# Sky circle mask (deprecated helper)
# ---------------------------------------------------------------------------

class TestSkyCircleMask:
    def test_line_outside_circle_masked(self):
        img = _with_line(50, 450, 400, 450, width=512, height=512)
        masked = apply_sky_circle_mask(img, cx=256, cy=256, radius=100)
        assert detect_meteors(masked, min_length=100) == []

    def test_line_inside_circle_preserved(self):
        img = _with_line(150, 256, 350, 256, width=512, height=512, line_val=220)
        masked = apply_sky_circle_mask(img, cx=256, cy=256, radius=200)
        assert detect_meteors(masked, min_length=100, threshold=50)

    def test_mask_does_not_mutate_input(self):
        img = _with_line(50, 256, 400, 256, width=512, height=512)
        original = np.array(img.copy())
        apply_sky_circle_mask(img, cx=256, cy=256, radius=200)
        np.testing.assert_array_equal(np.array(img), original)


# ---------------------------------------------------------------------------
# Adaptive threshold (deprecated helper)
# ---------------------------------------------------------------------------

class TestAdaptiveThreshold:
    def test_dark_image_low_threshold(self):
        t = estimate_adaptive_threshold(_blank(512, 512, fill=10))
        assert 5 <= t <= 15

    def test_threshold_clamped_range(self):
        t = estimate_adaptive_threshold(_blank(256, 256, fill=128))
        assert 5 <= t <= 100

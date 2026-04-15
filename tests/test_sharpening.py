"""
Tests for services/sharpening.py — cosmetic star unsharp mask.
"""
import numpy as np
import pytest
from PIL import Image

import os
import sys
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.sharpening import apply_unsharp_mask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _star_frame(width=128, height=128):
    """Synthetic night-sky frame: dark background with a few bright star dots."""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    # Scatter a handful of dim stars
    for cx, cy in [(32, 32), (64, 64), (96, 48), (20, 100)]:
        arr[cy - 1:cy + 2, cx - 1:cx + 2] = 200
    return arr


def _saturated_frame(width=64, height=64):
    """Frame with a fully saturated white moon-like blob on a mid-gray background.

    The gray background (value 100) lets unsharp mask produce a visible dark
    halo at the blob edge — if the background were pure black, uint8 clipping
    at 0 would mask any change.
    """
    arr = np.full((height, width, 3), 100, dtype=np.uint8)
    arr[16:48, 16:48] = 255
    return arr


# ---------------------------------------------------------------------------
# Shape / dtype contract
# ---------------------------------------------------------------------------

class TestOutputContract:
    def test_numpy_shape_preserved(self):
        arr = _star_frame()
        result = apply_unsharp_mask(arr)
        assert result.shape == arr.shape, "Output shape must match input"

    def test_numpy_dtype_preserved(self):
        arr = _star_frame()
        result = apply_unsharp_mask(arr)
        assert result.dtype == np.uint8, "Output dtype must be uint8"

    def test_pil_size_preserved(self):
        img = Image.fromarray(_star_frame())
        result = apply_unsharp_mask(img)
        assert isinstance(result, Image.Image)
        assert result.size == img.size, "PIL output size must match input"

    def test_pil_mode_preserved(self):
        img = Image.fromarray(_star_frame())
        result = apply_unsharp_mask(img)
        assert result.mode == img.mode, "PIL output mode must match input"

    def test_input_not_mutated_numpy(self):
        arr = _star_frame()
        original = arr.copy()
        apply_unsharp_mask(arr)
        np.testing.assert_array_equal(arr, original, err_msg="Input ndarray must not be modified")

    def test_input_not_mutated_pil(self):
        img = Image.fromarray(_star_frame())
        original = np.asarray(img.copy())
        apply_unsharp_mask(img)
        np.testing.assert_array_equal(np.asarray(img), original, err_msg="Input PIL Image must not be modified")

    def test_rgba_input_accepted(self):
        """RGBA images should pass through without error."""
        img = Image.fromarray(_star_frame()).convert('RGBA')
        result = apply_unsharp_mask(img)
        assert result.size == img.size
        assert result.mode == 'RGBA'


# ---------------------------------------------------------------------------
# Sharpening actually changes the image
# ---------------------------------------------------------------------------

class TestSharpeningEffect:
    def test_sharpened_differs_from_input(self):
        """High-amount sharpening on a star frame must produce different pixels."""
        arr = _star_frame(128, 128)
        result = apply_unsharp_mask(arr, radius=2.0, amount=300, threshold=0)
        assert not np.array_equal(arr, result), (
            "apply_unsharp_mask with amount=300 should change at least some pixels"
        )

    def test_subtle_defaults_change_image(self):
        """Even default settings should alter a frame with star-like edges."""
        arr = _star_frame(128, 128)
        result = apply_unsharp_mask(arr)
        assert not np.array_equal(arr, result), (
            "Default sharpening settings should produce some pixel change on a star frame"
        )

    def test_zero_amount_is_noop(self):
        """amount=0 means no sharpening — output must equal input."""
        arr = _star_frame()
        result = apply_unsharp_mask(arr, radius=1.5, amount=0, threshold=0)
        np.testing.assert_array_equal(arr, result)


# ---------------------------------------------------------------------------
# Saturated-pixel protection
# ---------------------------------------------------------------------------

class TestSaturatedRegions:
    def test_saturated_pixels_unchanged(self):
        """Pixels where any channel > 250 in the original must keep original values."""
        arr = _saturated_frame()
        result = apply_unsharp_mask(arr, radius=2.0, amount=400, threshold=0)
        # The saturated blob (all 255) should be identical in the result
        np.testing.assert_array_equal(
            arr[20:44, 20:44],
            result[20:44, 20:44],
            err_msg="Interior of saturated region must not be altered by sharpening"
        )

    def test_dark_pixels_can_change(self):
        """Non-saturated dark pixels adjacent to a bright blob may change."""
        arr = _saturated_frame()
        result = apply_unsharp_mask(arr, radius=2.0, amount=400, threshold=0)
        # At least some dark pixels should differ (edge enhancement)
        assert not np.array_equal(arr, result)


# ---------------------------------------------------------------------------
# Config-driven disabled path (simulates pipeline behaviour)
# ---------------------------------------------------------------------------

class TestDisabledConfig:
    def _process(self, img, config):
        """Minimal replica of the pipeline's sharpening block."""
        sharpening_cfg = config.get('sharpening', {})
        if sharpening_cfg.get('enabled', False):
            return apply_unsharp_mask(
                img,
                radius=float(sharpening_cfg.get('radius', 1.5)),
                amount=int(sharpening_cfg.get('amount', 80)),
                threshold=int(sharpening_cfg.get('threshold', 3)),
            )
        return img  # no-op path

    def test_disabled_is_noop(self):
        """Disabled config must return the exact same object (identity)."""
        img = Image.fromarray(_star_frame())
        config = {'sharpening': {'enabled': False, 'radius': 2.0, 'amount': 400, 'threshold': 0}}
        result = self._process(img, config)
        assert result is img, "Disabled sharpening must return the original object unchanged"

    def test_missing_sharpening_key_is_noop(self):
        """Config without a 'sharpening' key must also be a no-op."""
        img = Image.fromarray(_star_frame())
        result = self._process(img, {})
        assert result is img

    def test_enabled_config_changes_image(self):
        """Enabled config with aggressive settings must alter the frame."""
        arr = _star_frame()
        img = Image.fromarray(arr)
        config = {'sharpening': {'enabled': True, 'radius': 2.0, 'amount': 300, 'threshold': 0}}
        result = self._process(img, config)
        result_arr = np.asarray(result)
        assert not np.array_equal(arr, result_arr)

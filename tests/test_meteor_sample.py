"""
Tests against real FITS sample images — algorithm smoke tests.

NOTE (Phase 2 rework): these tests run detect_meteors() directly on single frames,
not on transient maps. Results will differ from the production pipeline (which
uses FrameStack.transient_map()). The primary value here is confirming no crashes
and no gross regressions on known data.

Some previously documented false-positive counts (e.g. moon frame) may change with
the Phase 2 algorithm rework. Update the count bounds based on observed results.
"""
import glob
import os
import sys

import numpy as np
import pytest
from PIL import Image

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.meteor.detector import detect_meteors, compute_frame_difference

SAMPLE_DIR = os.path.join(project_root, "sample_images")
FITS_FILES = sorted(glob.glob(os.path.join(SAMPLE_DIR, "*.fits")))
LUM_FITS = [f for f in FITS_FILES if os.path.basename(f).startswith("lum_")]


def _fits_to_pil(path: str) -> Image.Image:
    from astropy.io import fits
    with fits.open(path) as hdul:
        data = np.squeeze(hdul[0].data.astype(np.float32))
    if data.ndim == 3:
        data = data[0]
    lo, hi = data.min(), data.max()
    if hi > lo:
        data = (data - lo) / (hi - lo)
    return Image.fromarray((data * 255).astype(np.uint8)).convert("RGB")


@pytest.mark.skipif(not FITS_FILES, reason="No FITS files in sample_images/")
class TestSampleImages:
    def test_all_fits_load_without_error(self):
        for path in FITS_FILES:
            img = _fits_to_pil(path)
            assert img.mode == "RGB" and img.width > 0

    def test_detector_runs_on_all_fits(self):
        for path in FITS_FILES:
            result = detect_meteors(_fits_to_pil(path), min_length=100)
            assert isinstance(result, list), f"Expected list for {os.path.basename(path)}"

    def test_moon_frame_self_diff_zero_detections(self):
        moon_frame = os.path.join(SAMPLE_DIR, "raw_20260107_040940.fits")
        if not os.path.exists(moon_frame):
            pytest.skip("raw_20260107_040940.fits not present")
        img = _fits_to_pil(moon_frame)
        diff = compute_frame_difference(img, img.copy(), threshold=25)
        assert detect_meteors(diff, min_length=100) == []

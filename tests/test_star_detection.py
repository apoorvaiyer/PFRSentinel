"""
Test star detection and seeing estimation
"""
import pytest
import numpy as np
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.star_detection import detect_stars, estimate_fwhm, seeing_label, analyze_stars


def _make_star_image(width=256, height=256, stars=None, noise_level=0):
    """Create a synthetic image with Gaussian point sources."""
    img = np.zeros((height, width), dtype=np.uint8)

    if noise_level > 0:
        img = np.random.randint(0, noise_level, (height, width), dtype=np.uint8)

    if stars:
        yy, xx = np.mgrid[0:height, 0:width]
        for (cx, cy, brightness, sigma) in stars:
            gaussian = brightness * np.exp(
                -((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2)
            )
            img = np.clip(img.astype(np.float64) + gaussian, 0, 255).astype(np.uint8)

    return img


class TestStarDetection:
    """Test star detection on synthetic images"""

    def test_detect_known_stars(self):
        """Test detection on image with known point sources (white dots on black)"""
        stars = [
            (60, 60, 200, 3),
            (120, 120, 180, 3),
            (200, 80, 220, 3),
        ]
        img = _make_star_image(256, 256, stars=stars)
        detected = detect_stars(img)
        assert len(detected) >= 2, f"Expected at least 2 stars, got {len(detected)}"

    def test_blank_image_returns_zero(self):
        """Test star count returns 0 on blank/uniform image"""
        img = np.zeros((256, 256), dtype=np.uint8)
        detected = detect_stars(img)
        assert len(detected) == 0

    def test_uniform_bright_image_returns_zero(self):
        """Test star count returns 0 on uniformly bright image"""
        img = np.full((256, 256), 128, dtype=np.uint8)
        detected = detect_stars(img)
        assert len(detected) == 0

    def test_noisy_image_limited_false_positives(self):
        """Test noisy image doesn't false-positive excessively"""
        img = np.random.randint(0, 30, (256, 256), dtype=np.uint8)
        detected = detect_stars(img)
        assert len(detected) < 20, f"Too many false positives: {len(detected)}"

    def test_small_image_no_crash(self):
        """Test graceful handling of very small images"""
        img = np.zeros((16, 16), dtype=np.uint8)
        detected = detect_stars(img)
        assert isinstance(detected, list)

    def test_rgb_input_accepted(self):
        """Test detection works with RGB input"""
        stars = [(60, 60, 200, 3), (120, 120, 180, 3)]
        gray = _make_star_image(256, 256, stars=stars)
        rgb = np.stack([gray, gray, gray], axis=2)
        detected = detect_stars(rgb)
        assert len(detected) >= 1

    def test_star_dict_structure(self):
        """Test detected stars have correct dict keys"""
        stars = [(100, 100, 220, 3)]
        img = _make_star_image(256, 256, stars=stars)
        detected = detect_stars(img)
        if detected:
            star = detected[0]
            assert 'x' in star
            assert 'y' in star
            assert 'size' in star


class TestFWHMEstimation:
    """Test FWHM measurement"""

    def test_fwhm_reasonable_for_gaussian(self):
        """Test FWHM returns reasonable values for known Gaussian spots"""
        stars = [(128, 128, 220, 4)]
        img = _make_star_image(256, 256, stars=stars)
        detected = detect_stars(img)
        if detected:
            fwhm = estimate_fwhm(img, detected)
            assert 1.0 < fwhm < 20.0, f"FWHM {fwhm} out of expected range"

    def test_fwhm_zero_for_no_stars(self):
        """Test FWHM returns 0 when no stars provided"""
        img = np.zeros((256, 256), dtype=np.uint8)
        fwhm = estimate_fwhm(img, [])
        assert fwhm == 0.0

    def test_fwhm_empty_image(self):
        """Test FWHM on empty image with star coords returns 0"""
        img = np.zeros((256, 256), dtype=np.uint8)
        fwhm = estimate_fwhm(img, [{'x': 128, 'y': 128, 'size': 5}])
        assert fwhm == 0.0


class TestSeeingLabel:
    """Test seeing quality labels"""

    def test_excellent_seeing(self):
        assert seeing_label(2.0) == "Excellent"

    def test_good_seeing(self):
        assert seeing_label(3.0) == "Good"

    def test_fair_seeing(self):
        assert seeing_label(5.0) == "Fair"

    def test_poor_seeing(self):
        assert seeing_label(7.0) == "Poor"

    def test_bad_seeing(self):
        assert seeing_label(10.0) == "Bad"

    def test_zero_fwhm_returns_na(self):
        assert seeing_label(0) == "N/A"


class TestAnalyzeStars:
    """Test full analysis pipeline"""

    def test_analyze_returns_all_tokens(self):
        """Test analyze_stars returns STAR_COUNT, FWHM, SEEING keys"""
        stars = [(100, 100, 220, 3), (180, 60, 200, 3)]
        img = _make_star_image(256, 256, stars=stars)
        result = analyze_stars(img)
        assert 'STAR_COUNT' in result
        assert 'FWHM' in result
        assert 'SEEING' in result

    def test_analyze_blank_image(self):
        """Test analyze_stars on blank image returns zero count"""
        img = np.zeros((256, 256), dtype=np.uint8)
        result = analyze_stars(img)
        assert result['STAR_COUNT'] == '0'
        assert result['SEEING'] == 'N/A'

    def test_analyze_returns_string_values(self):
        """Test all token values are strings (for overlay replacement)"""
        img = _make_star_image(256, 256, stars=[(100, 100, 220, 3)])
        result = analyze_stars(img)
        for key, val in result.items():
            assert isinstance(val, str), f"{key} should be str, got {type(val)}"

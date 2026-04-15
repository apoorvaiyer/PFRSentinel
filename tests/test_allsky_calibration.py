"""
Tests for services/allsky/fisheye.py and services/allsky/calibration.py.

Uses synthetic star fields so no hardware or network access is required.
"""
import math
import json
import tempfile
import os
import numpy as np
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel


# ===================================================================
# FisheyeModel — projection
# ===================================================================

class TestFisheyeProjection:
    """Test FisheyeModel coordinate projection."""

    def _default_model(self) -> FisheyeModel:
        """Create a simple centred equidistant model (axis pointing at zenith)."""
        return FisheyeModel(
            cx=960.0, cy=540.0,
            a1=600.0, a3=0.0, a5=0.0,
            roll=0.0,
            axis_alt=90.0, axis_az=0.0,
        )

    def test_zenith_maps_to_centre(self):
        """Alt=90° (zenith) should project to the optical centre."""
        model = self._default_model()
        xy = model.altaz_to_pixel(90.0, 0.0)
        assert xy is not None
        assert abs(xy[0] - 960.0) < 2.0, f"Zenith x={xy[0]} ≠ 960"
        assert abs(xy[1] - 540.0) < 2.0, f"Zenith y={xy[1]} ≠ 540"

    def test_below_horizon_returns_none(self):
        """Alt < 0 should return None."""
        model = self._default_model()
        assert model.altaz_to_pixel(-5.0, 0.0) is None

    def test_north_is_up(self):
        """Az=0° (North) at low altitude should project above the centre (lower y)."""
        model = self._default_model()
        xy = model.altaz_to_pixel(30.0, 0.0)   # North, 30° up
        assert xy is not None
        assert xy[1] < 540.0, f"North should be above centre; y={xy[1]}"

    def test_south_is_below(self):
        """Az=180° (South) should project below the centre (higher y)."""
        model = self._default_model()
        xy_s = model.altaz_to_pixel(30.0, 180.0)
        xy_n = model.altaz_to_pixel(30.0, 0.0)
        assert xy_s is not None and xy_n is not None
        assert xy_s[1] > xy_n[1], "South should have higher y than North"

    def test_east_west_symmetric(self):
        """East (Az=90°) and West (Az=270°) should be symmetric about centre."""
        model = self._default_model()
        xy_e = model.altaz_to_pixel(30.0, 90.0)
        xy_w = model.altaz_to_pixel(30.0, 270.0)
        assert xy_e is not None and xy_w is not None
        # x should be symmetric about cx=960
        assert abs((xy_e[0] - 960.0) + (xy_w[0] - 960.0)) < 2.0

    def test_radial_distance_increases_with_altitude_decrease(self):
        """Objects at lower altitude (further from zenith) should be further from centre."""
        model = self._default_model()
        xy_70 = model.altaz_to_pixel(70.0, 0.0)
        xy_30 = model.altaz_to_pixel(30.0, 0.0)
        assert xy_70 is not None and xy_30 is not None
        r_70 = math.hypot(xy_70[0] - 960.0, xy_70[1] - 540.0)
        r_30 = math.hypot(xy_30[0] - 960.0, xy_30[1] - 540.0)
        assert r_30 > r_70, f"r(30°)={r_30:.1f} should > r(70°)={r_70:.1f}"

    def test_vectorised_matches_scalar(self):
        """altaz_array_to_pixels should give same result as scalar altaz_to_pixel."""
        model = self._default_model()
        alts = np.array([10.0, 30.0, 60.0, 80.0])
        azs  = np.array([0.0, 90.0, 180.0, 270.0])
        px_v, py_v, vis = model.altaz_array_to_pixels(alts, azs)
        for i, (alt, az) in enumerate(zip(alts, azs)):
            xy = model.altaz_to_pixel(float(alt), float(az))
            assert xy is not None
            assert abs(px_v[i] - xy[0]) < 1.0
            assert abs(py_v[i] - xy[1]) < 1.0


# ===================================================================
# FisheyeModel — JSON persistence
# ===================================================================

class TestFisheyePersistence:
    def test_save_load_roundtrip(self):
        """Save model to temp file and reload; all fields must match."""
        model = FisheyeModel(
            cx=800.0, cy=600.0, a1=550.0, a3=10.0, a5=-0.5,
            roll=0.05, axis_alt=88.0, axis_az=5.0,
            rms_residual=1.23, n_matches=45,
            calibrated_at="2024-01-15T22:30:00+00:00",
        )
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name

        try:
            model.save(path)
            loaded = FisheyeModel.load(path)

            assert abs(loaded.cx - model.cx) < 1e-6
            assert abs(loaded.cy - model.cy) < 1e-6
            assert abs(loaded.a1 - model.a1) < 1e-6
            assert abs(loaded.a3 - model.a3) < 1e-6
            assert abs(loaded.a5 - model.a5) < 1e-8
            assert abs(loaded.roll - model.roll) < 1e-6
            assert loaded.n_matches == model.n_matches
            assert abs(loaded.rms_residual - model.rms_residual) < 1e-6
        finally:
            os.unlink(path)

    def test_try_load_missing_file_returns_none(self):
        """try_load() should return None for a non-existent path."""
        result = FisheyeModel.try_load('/nonexistent/path/model.json')
        assert result is None

    def test_is_valid_requires_matches_and_positive_a1(self):
        """is_valid() should be False if n_matches < 5 or a1 <= 0."""
        m_bad = FisheyeModel(n_matches=3, a1=500.0)
        m_ok  = FisheyeModel(n_matches=25, a1=500.0)
        assert not m_bad.is_valid()
        assert m_ok.is_valid()


# ===================================================================
# CalibrationError (graceful degradation)
# ===================================================================

class TestCalibrationError:
    def test_insufficient_stars_raises(self):
        """Calibration should raise CalibrationError if too few stars detected."""
        pytest.importorskip('scipy')
        from services.allsky.calibration import calibrate, CalibrationError
        from PIL import Image as PILImage

        # All-black image → no stars
        blank = PILImage.new('RGB', (1920, 1080), color=(0, 0, 0))
        with pytest.raises(CalibrationError, match="star"):
            calibrate(blank, lat_deg=51.5, lon_deg=-0.1, min_matches=20)

    def test_synthetic_bright_stars(self):
        """
        Plant synthetic Gaussian star blobs at known pixel positions and verify
        that detection finds most of them. Does NOT run the full fit.
        """
        from services.allsky.star_centroid import detect_stars
        import numpy as np
        from PIL import Image as PILImage

        rng = np.random.default_rng(42)
        img_arr = np.zeros((1080, 1920), dtype=np.uint8)

        # Plant 30 Gaussian blobs
        planted_positions = []
        for _ in range(30):
            x = int(rng.integers(50, 1870))
            y = int(rng.integers(50, 1030))
            # 2D Gaussian
            ys, xs = np.mgrid[max(0, y-8):min(1080, y+9),
                               max(0, x-8):min(1920, x+9)]
            blob = 200 * np.exp(-((xs - x)**2 + (ys - y)**2) / (2 * 2.5**2))
            img_arr[ys, xs] = np.clip(img_arr[ys, xs] + blob, 0, 255).astype(np.uint8)
            planted_positions.append((x, y))

        img = PILImage.fromarray(img_arr, mode='L')
        detected = detect_stars(img, max_stars=50, border_px=15)

        # Should find at least 20 of the 30 planted stars
        assert len(detected) >= 20, f"Found {len(detected)} of 30 planted stars"

        # Each detected position should be within 5px of a planted position
        matched = 0
        for dx, dy, dflux in detected:
            for px, py in planted_positions:
                if math.hypot(dx - px, dy - py) < 5.0:
                    matched += 1
                    break
        assert matched >= 15, f"Only {matched}/30 detected stars match planted positions"

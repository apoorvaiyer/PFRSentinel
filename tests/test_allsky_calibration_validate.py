"""
Tests for services/allsky/calibration_validate.py.

Covers:
  - score_matches_with_spread():   azimuth spread lowers the score of
    clustered matches, leaves well-spread matches near their raw count.
  - validate_bright_anchors():     catches spurious fits by checking that
    projected bright catalog stars land on detected stars.
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.calibration_validate import (
    A3_MAX,
    A3_MIN,
    score_matches_with_spread,
    validate_bright_anchors,
    validate_lens_polynomial,
    warn_sky_coverage,
)
from services.allsky.fisheye import FisheyeModel


def _make_match(alt: float, az: float, xy=(0.0, 0.0)):
    """Build one match entry in the shape _brightness_match emits."""
    return (xy, {'name': 'x', 'vmag': 3.0}, (alt, az))


# ===================================================================
# score_matches_with_spread
# ===================================================================

class TestScoreMatchesWithSpread:
    def test_empty_returns_zero(self):
        assert score_matches_with_spread([]) == 0.0

    def test_small_list_returns_raw_count(self):
        matches = [_make_match(45.0, 0.0), _make_match(45.0, 0.0)]
        assert score_matches_with_spread(matches) == 2.0

    def test_well_spread_gets_full_score(self):
        """Eight matches at 45° azimuth intervals => spread ≈ 1.0 => score ≈ n."""
        matches = [_make_match(45.0, az) for az in range(0, 360, 45)]
        s = score_matches_with_spread(matches)
        assert math.isclose(s, len(matches), rel_tol=0.05), (
            f"Spread-8 score {s} not close to raw count {len(matches)}"
        )

    def test_clustered_matches_are_halved(self):
        """10 matches all near az=120° => factor ≈ 0.5 => score ≈ 5."""
        matches = [_make_match(30.0, 118.0 + i * 0.5) for i in range(10)]
        s = score_matches_with_spread(matches)
        assert 4.5 <= s <= 6.0, (
            f"Clustered score {s} should be about half of raw count 10"
        )

    def test_spread_outscores_cluster_at_equal_count(self):
        """Spread fit beats clustered fit when both have the same count."""
        spread = [_make_match(45.0, az) for az in (0, 45, 90, 135, 180, 225, 270, 315)]
        clustered = [_make_match(45.0, 120.0 + i * 0.3) for i in range(8)]
        assert score_matches_with_spread(spread) > score_matches_with_spread(clustered)


# ===================================================================
# validate_bright_anchors
# ===================================================================

class TestValidateBrightAnchors:
    def _zenith_model(self) -> FisheyeModel:
        return FisheyeModel(
            cx=960.0, cy=540.0, a1=600.0,
            a3=0.0, a5=0.0, roll=0.0,
            axis_alt=90.0, axis_az=0.0,
            east_left=True,
        )

    def _above_horizon(self, model: FisheyeModel, positions):
        """Build above_horizon list sorted brightest first for given (alt, az)s."""
        stars = []
        for i, (alt, az) in enumerate(positions):
            stars.append(({'name': f'S{i}', 'vmag': float(i)}, alt, az))
        return stars

    def _detect_from_projection(self, model, positions, jitter=0.0, skip_first=0):
        """Synthesise detections at projected positions of `positions[skip_first:]`."""
        det = []
        for alt, az in positions[skip_first:]:
            xy = model.altaz_to_pixel(alt, az)
            if xy is None:
                continue
            dx = xy[0] + jitter
            dy = xy[1] + jitter
            det.append((dx, dy, 1000.0))
        return det

    def test_all_anchors_match(self):
        model = self._zenith_model()
        positions = [(70.0, 0.0), (60.0, 60.0), (55.0, 120.0),
                     (50.0, 200.0), (45.0, 260.0), (40.0, 320.0)]
        ah = self._above_horizon(model, positions)
        det = self._detect_from_projection(model, positions, jitter=2.0)
        ok, msg = validate_bright_anchors(model, ah, det)
        assert ok, msg
        assert '6/6' in msg

    def test_all_anchors_miss(self):
        """Bright anchors have no detections anywhere near them."""
        model = self._zenith_model()
        positions = [(70.0, 0.0), (60.0, 60.0), (55.0, 120.0),
                     (50.0, 200.0), (45.0, 260.0), (40.0, 320.0)]
        ah = self._above_horizon(model, positions)
        # Detections only at totally unrelated pixels
        det = [(10.0, 10.0, 1.0), (20.0, 20.0, 1.0),
               (30.0, 30.0, 1.0), (40.0, 40.0, 1.0)]
        ok, msg = validate_bright_anchors(model, ah, det)
        assert not ok
        assert 'missed' in msg

    def test_partial_miss_below_min_hits_rejected(self):
        """2/6 anchors matched is below min_hits=5 → rejected."""
        model = self._zenith_model()
        positions = [(70.0, 0.0), (60.0, 60.0), (55.0, 120.0),
                     (50.0, 200.0), (45.0, 260.0), (40.0, 320.0)]
        ah = self._above_horizon(model, positions)
        # Only project last 2 stars into detections (skip first 4)
        det = self._detect_from_projection(model, positions, skip_first=4)
        ok, msg = validate_bright_anchors(model, ah, det)
        assert not ok

    def test_four_of_six_rejected(self):
        """4/6 is a former-pass case — now rejected under stricter 5/6 default."""
        model = self._zenith_model()
        positions = [(70.0, 0.0), (60.0, 60.0), (55.0, 120.0),
                     (50.0, 200.0), (45.0, 260.0), (40.0, 320.0)]
        ah = self._above_horizon(model, positions)
        # Project first 4 of 6 only
        det = []
        for alt, az in positions[:4]:
            xy = model.altaz_to_pixel(alt, az)
            det.append((xy[0], xy[1], 1.0))
        ok, msg = validate_bright_anchors(model, ah, det)
        assert not ok, f"Expected 4/6 to be rejected under min_hits=5; got ok={ok} ({msg})"

    def test_five_of_six_accepted(self):
        """5/6 anchors matched is at the threshold → accepted."""
        model = self._zenith_model()
        positions = [(70.0, 0.0), (60.0, 60.0), (55.0, 120.0),
                     (50.0, 200.0), (45.0, 260.0), (40.0, 320.0)]
        ah = self._above_horizon(model, positions)
        det = []
        for alt, az in positions[:5]:
            xy = model.altaz_to_pixel(alt, az)
            det.append((xy[0], xy[1], 1.0))
        ok, msg = validate_bright_anchors(model, ah, det)
        assert ok, f"Expected 5/6 to be accepted; got ({msg})"

    def test_skips_when_too_few_anchors_visible(self):
        """Only 2 anchors above min_alt=10° → check is skipped (ok=True)."""
        model = self._zenith_model()
        ah = [
            ({'name': 'A', 'vmag': 1.0}, 30.0, 90.0),
            ({'name': 'B', 'vmag': 2.0}, 20.0, 270.0),
            # Rest below 10°, ignored
            ({'name': 'C', 'vmag': 3.0},  5.0, 180.0),
        ]
        det = [(0.0, 0.0, 1.0)]
        ok, msg = validate_bright_anchors(model, ah, det)
        assert ok
        assert 'skipping' in msg

    def test_empty_detections_rejects(self):
        model = self._zenith_model()
        positions = [(70.0, 0.0), (60.0, 60.0), (55.0, 120.0),
                     (50.0, 200.0), (45.0, 260.0), (40.0, 320.0)]
        ah = self._above_horizon(model, positions)
        ok, msg = validate_bright_anchors(model, ah, [])
        assert not ok
        assert 'no detected' in msg.lower()


# ===================================================================
# validate_lens_polynomial
# ===================================================================

class TestValidateLensPolynomial:
    def test_default_model_accepts(self):
        """Fresh FisheyeModel has a3=0, comfortably within range."""
        ok, _msg = validate_lens_polynomial(FisheyeModel())
        assert ok

    def test_typical_real_lens_accepts(self):
        """Real fisheye lenses have modest negative a3 (e.g. -47 to -50)."""
        m = FisheyeModel(a3=-48.0)
        ok, _msg = validate_lens_polynomial(m)
        assert ok

    def test_strongly_positive_a3_rejected(self):
        """a3=+23.86 (observed spurious-fit case) must be rejected."""
        m = FisheyeModel(a3=23.86)
        ok, msg = validate_lens_polynomial(m)
        assert not ok
        assert 'a3' in msg

    def test_very_negative_a3_rejected(self):
        m = FisheyeModel(a3=A3_MIN - 1.0)
        ok, _msg = validate_lens_polynomial(m)
        assert not ok

    def test_range_boundaries(self):
        """A3_MIN and A3_MAX themselves are accepted."""
        ok_min, _ = validate_lens_polynomial(FisheyeModel(a3=A3_MIN))
        ok_max, _ = validate_lens_polynomial(FisheyeModel(a3=A3_MAX))
        assert ok_min and ok_max


# ===================================================================
# warn_sky_coverage (smoke test — just make sure it doesn't raise)
# ===================================================================

class TestWarnSkyCoverage:
    def test_no_matched_stars_attr_is_safe(self):
        warn_sky_coverage(FisheyeModel())  # should not raise

    def test_clustered_stars_does_not_raise(self):
        model = FisheyeModel()
        model.matched_stars = [
            {'az': 120.0}, {'az': 121.0}, {'az': 119.0}, {'az': 122.0},
        ]
        warn_sky_coverage(model)   # logs a warning; no assert needed

    def test_spread_stars_does_not_raise(self):
        model = FisheyeModel()
        model.matched_stars = [
            {'az': az} for az in (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)
        ]
        warn_sky_coverage(model)

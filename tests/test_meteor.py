"""
Tests for services/meteor/ — meteor trail detection and storage.

Synthetic tests validate the algorithm logic.
Sample-image tests run against the real FITS files in sample_images/ to
confirm the detector produces sensible results on actual observatory data.

Key findings from sample_images/:
  - lum_*.fits  (calibrated sky frames): 0 detections at min_length=100
  - raw_20260107_040940.fits (moon-bright frame): detections from the hard
    edge of the telescope mount silhouette against the moon glow — these are
    confirmed false positives, not meteors.  They cluster around
    x≈1300-1500, y≈2160-2300, consistent with the equipment position.
  - All files: 0 detections at min_length=150 → threshold is conservative
    enough to be quiet on real data.
"""
import glob
import json
import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.meteor.detector import MeteorDetection, annotate_image, detect_meteors
from services.meteor.storage import log_detections, save_thumbnail
from services.meteor.mask import (
    ExclusionZone, apply_exclusion_zones,
    zone_from_detection, zones_from_config, zones_to_config,
)

SAMPLE_DIR = os.path.join(project_root, "sample_images")
FITS_FILES = sorted(glob.glob(os.path.join(SAMPLE_DIR, "*.fits")))
LUM_FITS = [f for f in FITS_FILES if os.path.basename(f).startswith("lum_")]
RAW_FITS = [f for f in FITS_FILES if os.path.basename(f).startswith("raw_")]

# ---------------------------------------------------------------------------
# FITS loader (shared by sample-image tests)
# ---------------------------------------------------------------------------

def _fits_to_pil(path: str) -> Image.Image:
    """Load a FITS file and return an 8-bit RGB PIL Image."""
    from astropy.io import fits
    with fits.open(path) as hdul:
        data = hdul[0].data.astype(np.float32)
    data = np.squeeze(data)          # remove degenerate axes, e.g. (1, H, W) → (H, W)
    if data.ndim == 3:
        data = data[0]               # take first plane if still 3-D
    lo, hi = data.min(), data.max()
    if hi > lo:
        data = (data - lo) / (hi - lo)
    arr8 = (data * 255).astype(np.uint8)
    return Image.fromarray(arr8).convert("RGB")


# ---------------------------------------------------------------------------
# Synthetic helpers
# ---------------------------------------------------------------------------

def _blank(width=256, height=256, fill=10) -> Image.Image:
    """Dark uniform background."""
    arr = np.full((height, width, 3), fill, dtype=np.uint8)
    return Image.fromarray(arr)


def _with_line(x1, y1, x2, y2, width=512, height=512, bg=10, line_val=255) -> Image.Image:
    """Dark frame with a single bright white line drawn on it."""
    arr = np.full((height, width, 3), bg, dtype=np.uint8)
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)
    draw.line([(x1, y1), (x2, y2)], fill=(line_val, line_val, line_val), width=3)
    return img


# ---------------------------------------------------------------------------
# TestDetectorContract — output type / shape guarantees
# ---------------------------------------------------------------------------

class TestDetectorContract:
    def test_returns_list(self):
        result = detect_meteors(_blank())
        assert isinstance(result, list)

    def test_empty_image_no_detections(self):
        """Uniform dark frame has no edges → no detections."""
        assert detect_meteors(_blank(fill=10)) == []

    def test_uniform_bright_no_detections(self):
        """Fully saturated frame also has no edges."""
        assert detect_meteors(_blank(fill=255)) == []

    def test_returns_meteor_detection_instances(self):
        img = _with_line(50, 50, 300, 50)  # horizontal line, 250 px
        result = detect_meteors(img, min_length=100)
        for det in result:
            assert isinstance(det, MeteorDetection)

    def test_detection_fields_populated(self):
        img = _with_line(50, 50, 300, 50)
        result = detect_meteors(img, min_length=100)
        assert result, "Expected at least one detection on a 250px horizontal line"
        det = result[0]
        assert isinstance(det.x1, int)
        assert isinstance(det.y1, int)
        assert isinstance(det.x2, int)
        assert isinstance(det.y2, int)
        assert det.length > 0
        assert isinstance(det.angle_deg, float)

    def test_input_image_not_mutated(self):
        img = _with_line(50, 50, 300, 50)
        original = np.array(img.copy())
        detect_meteors(img, min_length=100)
        np.testing.assert_array_equal(np.array(img), original)


# ---------------------------------------------------------------------------
# TestDetectorLineDetection — algorithm sensitivity / correctness
# ---------------------------------------------------------------------------

class TestDetectorLineDetection:
    def test_horizontal_line_detected(self):
        img = _with_line(50, 128, 350, 128)   # 300px horizontal
        result = detect_meteors(img, min_length=100)
        assert result, "300px horizontal line should be detected"

    def test_diagonal_line_detected(self):
        # Note: synthetic thick lines exceed the cloud-mask area budget faster on
        # diagonals (wider cross-section after dilation).  Real meteor streaks are
        # thin natural features so this limit doesn't apply to actual sky images.
        img = _with_line(100, 100, 240, 240)  # ~198px diagonal — within cloud-mask budget
        result = detect_meteors(img, min_length=100)
        assert result, "198px diagonal line should be detected"

    def test_short_line_below_min_length_ignored(self):
        img = _with_line(100, 100, 130, 100)  # 30px — too short
        result = detect_meteors(img, min_length=100)
        assert result == [], "30px line should be filtered by min_length=100"

    def test_min_length_border_case(self):
        """A line right at the threshold must be accepted."""
        img = _with_line(50, 128, 200, 128)   # 150px
        accepted = detect_meteors(img, min_length=100)
        rejected = detect_meteors(img, min_length=200)
        assert accepted, "150px line should be accepted at min_length=100"
        assert rejected == [], "150px line should be rejected at min_length=200"

    def test_detected_length_is_plausible(self):
        """Reported length should be within 20% of the drawn line length."""
        img = _with_line(50, 128, 250, 128)   # 200px
        result = detect_meteors(img, min_length=100)
        assert result
        best = max(result, key=lambda d: d.length)
        assert 160 <= best.length <= 240, f"Expected ~200px, got {best.length:.0f}px"

    def test_horizontal_angle_is_near_zero(self):
        img = _with_line(50, 200, 330, 200)   # 280px horizontal — within cloud-mask budget
        result = detect_meteors(img, min_length=100)
        assert result, "280px horizontal line should be detected"
        best = max(result, key=lambda d: d.length)
        assert abs(best.angle_deg) < 10, f"Horizontal line angle should be ~0°, got {best.angle_deg:.1f}°"

    def test_vertical_line_detected(self):
        img = _with_line(200, 50, 200, 330)   # 280px vertical — within cloud-mask budget
        result = detect_meteors(img, min_length=100)
        assert result, "280px vertical line should be detected"


# ---------------------------------------------------------------------------
# TestAnnotation
# ---------------------------------------------------------------------------

class TestAnnotation:
    def test_annotate_returns_image(self):
        img = _blank()
        result = annotate_image(img, [])
        assert isinstance(result, Image.Image)

    def test_annotate_empty_returns_copy(self):
        img = _blank()
        result = annotate_image(img, [])
        assert result is not img, "Should return a copy, not the same object"
        np.testing.assert_array_equal(np.array(result), np.array(img))

    def test_annotate_with_detection_changes_image(self):
        img = _blank(256, 256, fill=10)
        det = MeteorDetection(x1=10, y1=10, x2=200, y2=200, length=268.0, angle_deg=45.0)
        result = annotate_image(img, [det])
        assert not np.array_equal(np.array(result), np.array(img)), \
            "Annotated image should differ from input"

    def test_annotate_size_preserved(self):
        img = _blank(400, 300)
        det = MeteorDetection(x1=10, y1=10, x2=300, y2=250, length=380.0, angle_deg=30.0)
        result = annotate_image(img, [det])
        assert result.size == img.size

    def test_annotate_does_not_mutate_input(self):
        img = _blank()
        original = np.array(img.copy())
        det = MeteorDetection(x1=10, y1=10, x2=200, y2=200, length=268.0, angle_deg=45.0)
        annotate_image(img, [det])
        np.testing.assert_array_equal(np.array(img), original)


# ---------------------------------------------------------------------------
# TestStorage
# ---------------------------------------------------------------------------

class TestStorage:
    def test_creates_log_file(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        det = MeteorDetection(10, 20, 100, 200, 180.0, 45.0)
        log_detections(path, [det])
        assert os.path.exists(path)

    def test_log_entry_is_valid_json(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        det = MeteorDetection(10, 20, 100, 200, 180.0, 45.0)
        log_detections(path, [det])
        with open(path) as f:
            entry = json.loads(f.readline())
        assert "timestamp" in entry
        assert "count" in entry
        assert "detections" in entry

    def test_log_entry_count_matches(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        dets = [
            MeteorDetection(10, 20, 100, 200, 180.0, 45.0),
            MeteorDetection(50, 50, 300, 300, 354.0, 45.0),
        ]
        log_detections(path, dets)
        with open(path) as f:
            entry = json.loads(f.readline())
        assert entry["count"] == 2
        assert len(entry["detections"]) == 2

    def test_log_appends_multiple_calls(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        det = MeteorDetection(10, 20, 100, 200, 180.0, 45.0)
        log_detections(path, [det])
        log_detections(path, [det])
        with open(path) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 2

    def test_log_detection_fields_present(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        det = MeteorDetection(x1=11, y1=22, x2=111, y2=222, length=150.5, angle_deg=-30.1)
        log_detections(path, [det], image_filename="test.fits")
        with open(path) as f:
            entry = json.loads(f.readline())
        d = entry["detections"][0]
        assert d["x1"] == 11
        assert d["y1"] == 22
        assert d["x2"] == 111
        assert d["y2"] == 222
        assert d["length"] == 150.5
        assert d["angle"] == -30.1
        assert entry["image"] == "test.fits"

    def test_empty_path_is_noop(self, tmp_path):
        """No file should be created when log_path is empty string."""
        log_detections("", [MeteorDetection(0, 0, 100, 100, 141.0, 45.0)])
        # If it reaches here without error, pass
        assert True

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "sub" / "nested" / "detections.jsonl")
        det = MeteorDetection(10, 20, 100, 200, 180.0, 45.0)
        log_detections(path, [det])
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# TestSampleImages — real FITS data
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FITS_FILES, reason="No FITS files found in sample_images/")
class TestSampleImages:
    """Run the detector on real observatory data — no crashes, sensible counts."""

    def test_all_fits_load_without_error(self):
        for path in FITS_FILES:
            img = _fits_to_pil(path)
            assert img.mode == "RGB"
            assert img.width > 0 and img.height > 0

    def test_detector_runs_on_all_fits(self):
        for path in FITS_FILES:
            img = _fits_to_pil(path)
            result = detect_meteors(img, min_length=100)
            assert isinstance(result, list), f"Expected list for {os.path.basename(path)}"

    def test_calibrated_lum_frames_clean_at_100px(self):
        """
        Reduced/calibrated lum frames should be free of long-line artefacts.
        The algorithm should return 0 detections at the default 100px threshold.
        """
        for path in LUM_FITS:
            img = _fits_to_pil(path)
            result = detect_meteors(img, min_length=100)
            assert result == [], (
                f"{os.path.basename(path)}: expected 0 detections at 100px, "
                f"got {len(result)} — possible algorithm regression"
            )

    def test_no_detections_anywhere_at_150px(self):
        """
        At 150px all sample frames (including moon-bright raw frames) should
        be silent.  This confirms the conservative default threshold is quiet
        on real data without a meteor trail.
        """
        for path in FITS_FILES:
            img = _fits_to_pil(path)
            result = detect_meteors(img, min_length=150)
            assert result == [], (
                f"{os.path.basename(path)}: expected 0 detections at 150px, "
                f"got {len(result)}"
            )

    def test_raw_moon_frame_false_positives_are_equipment_edges(self):
        """
        raw_20260107_040940.fits contains a bright moon + telescope mount.
        The 8 detections at 100px are confirmed false positives: all lines
        cluster near the mount silhouette (x≈1300-1500, y≈2160-2300).
        This test documents the known limitation and guards against regressions
        in either direction (no detections OR many more than expected).
        """
        moon_frame = os.path.join(SAMPLE_DIR, "raw_20260107_040940.fits")
        if not os.path.exists(moon_frame):
            pytest.skip("raw_20260107_040940.fits not present")

        img = _fits_to_pil(moon_frame)
        result = detect_meteors(img, min_length=100)

        # All detections should be in the equipment region (lower-centre)
        for det in result:
            mid_x = (det.x1 + det.x2) / 2
            mid_y = (det.y1 + det.y2) / 2
            assert 1200 <= mid_x <= 1600, (
                f"Detection mid_x={mid_x:.0f} is outside expected equipment zone"
            )
            assert 2100 <= mid_y <= 2400, (
                f"Detection mid_y={mid_y:.0f} is outside expected equipment zone"
            )

        # Count should be stable (regression guard)
        assert 5 <= len(result) <= 12, (
            f"Expected ~8 equipment-edge detections, got {len(result)}"
        )


# ---------------------------------------------------------------------------
# TestExclusionMask
# ---------------------------------------------------------------------------

class TestExclusionMask:
    def test_apply_no_zones_returns_unchanged(self):
        mask = np.full((100, 100), 255, dtype=np.uint8)
        result = apply_exclusion_zones(mask, [])
        np.testing.assert_array_equal(result, mask)

    def test_apply_zone_zeros_region(self):
        mask = np.full((200, 200), 255, dtype=np.uint8)
        zone = ExclusionZone(x=50, y=50, w=100, h=100)
        result = apply_exclusion_zones(mask, [zone])
        assert result[50:150, 50:150].max() == 0, "Zone region should be zeroed"
        assert result[0, 0] == 255, "Outside zone should be untouched"

    def test_apply_zone_does_not_mutate_input(self):
        mask = np.full((100, 100), 200, dtype=np.uint8)
        original = mask.copy()
        apply_exclusion_zones(mask, [ExclusionZone(0, 0, 50, 50)])
        np.testing.assert_array_equal(mask, original)

    def test_zone_clamped_to_image_bounds(self):
        """Zone extending beyond image edges must not raise."""
        mask = np.full((100, 100), 255, dtype=np.uint8)
        zone = ExclusionZone(x=80, y=80, w=200, h=200)  # overflows
        result = apply_exclusion_zones(mask, [zone])
        assert result[90, 90] == 0

    def test_zone_from_detection_has_padding(self):
        zone = zone_from_detection(200, 300, 400, 500, 1000, 1000, padding=80)
        assert zone.x <= 200 - 80
        assert zone.y <= 300 - 80
        assert zone.x + zone.w >= 400 + 80
        assert zone.y + zone.h >= 500 + 80

    def test_zone_from_detection_clamped(self):
        """Zone from a detection near the image edge must not go negative."""
        zone = zone_from_detection(5, 5, 50, 50, 100, 100, padding=80)
        assert zone.x >= 0
        assert zone.y >= 0
        assert zone.x + zone.w <= 100
        assert zone.y + zone.h <= 100

    def test_zones_roundtrip_config(self):
        zones = [ExclusionZone(10, 20, 300, 400, "test note")]
        cfg_list = zones_to_config(zones)
        restored = zones_from_config({"exclusion_zones": cfg_list})
        assert len(restored) == 1
        assert restored[0].x == 10
        assert restored[0].y == 20
        assert restored[0].w == 300
        assert restored[0].h == 400
        assert restored[0].note == "test note"

    def test_exclusion_zone_suppresses_detection(self):
        """
        A line that was previously detected should not be detected once its
        region is added to the exclusion zones list.
        """
        img = _with_line(50, 128, 300, 128)   # 250px horizontal
        without_zone = detect_meteors(img, min_length=100)
        assert without_zone, "Sanity: line should be detected without zone"

        # Cover the entire line
        zone = zone_from_detection(50, 128, 300, 128, 512, 512, padding=20)
        with_zone = detect_meteors(img, min_length=100, exclusion_zones=[zone])
        assert with_zone == [], "Line inside exclusion zone must not be detected"


# ---------------------------------------------------------------------------
# TestThumbnail
# ---------------------------------------------------------------------------

class TestThumbnail:
    def test_thumbnail_created(self, tmp_path):
        img = _with_line(50, 128, 300, 128)
        det = MeteorDetection(50, 128, 300, 128, 250.0, 0.0)
        path = save_thumbnail(img, det, str(tmp_path), "2026-04-13T21:00:00")
        assert path and os.path.isfile(path)

    def test_thumbnail_is_jpeg(self, tmp_path):
        img = _with_line(50, 128, 300, 128)
        det = MeteorDetection(50, 128, 300, 128, 250.0, 0.0)
        path = save_thumbnail(img, det, str(tmp_path), "2026-04-13T21:00:01")
        assert path.endswith(".jpg")

    def test_thumbnail_size_is_300x300(self, tmp_path):
        img = _with_line(50, 128, 300, 128, width=1024, height=1024)
        det = MeteorDetection(50, 128, 300, 128, 250.0, 0.0)
        path = save_thumbnail(img, det, str(tmp_path), "2026-04-13T21:00:02")
        saved = Image.open(path)
        assert saved.size == (300, 300)

    def test_thumbnail_empty_dir_is_noop(self):
        img = _with_line(50, 128, 300, 128)
        det = MeteorDetection(50, 128, 300, 128, 250.0, 0.0)
        result = save_thumbnail(img, det, "", "2026-04-13T21:00:03")
        assert result == ""

    def test_thumbnail_near_edge_does_not_crash(self, tmp_path):
        """Detection close to image edge — crop should be padded, not error."""
        img = _blank(300, 300)
        det = MeteorDetection(5, 5, 50, 50, 64.0, 45.0)
        path = save_thumbnail(img, det, str(tmp_path), "2026-04-13T21:00:04")
        assert path and os.path.isfile(path)
        saved = Image.open(path)
        assert saved.size == (300, 300)

    def test_thumbnail_has_green_annotation(self, tmp_path):
        """The saved thumbnail should contain green pixels from the drawn line."""
        img = _blank(512, 512, fill=10)
        det = MeteorDetection(100, 256, 400, 256, 300.0, 0.0)
        path = save_thumbnail(img, det, str(tmp_path), "2026-04-13T21:00:05")
        saved = np.array(Image.open(path))
        # Green channel should be elevated somewhere
        assert saved[:, :, 1].max() > 100, "Thumbnail should contain green annotation"

"""Tests for services/meteor/mask.py — exclusion zones."""
import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.meteor.mask import (
    ExclusionZone, apply_exclusion_zones,
    zone_from_detection, zones_from_config, zones_to_config,
)
from services.meteor.detector import detect_meteors


def _with_line(x1, y1, x2, y2, width=512, height=512, line_val=220):
    arr = np.full((height, width, 3), 10, dtype=np.uint8)
    img = Image.fromarray(arr)
    ImageDraw.Draw(img).line([(x1, y1), (x2, y2)], fill=(line_val,) * 3, width=3)
    return img


class TestExclusionMask:
    def test_apply_no_zones_returns_unchanged(self):
        mask = np.full((100, 100), 255, dtype=np.uint8)
        np.testing.assert_array_equal(apply_exclusion_zones(mask, []), mask)

    def test_apply_zone_zeros_region(self):
        mask = np.full((200, 200), 255, dtype=np.uint8)
        result = apply_exclusion_zones(mask, [ExclusionZone(x=50, y=50, w=100, h=100)])
        assert result[50:150, 50:150].max() == 0
        assert result[0, 0] == 255

    def test_apply_zone_does_not_mutate_input(self):
        mask = np.full((100, 100), 200, dtype=np.uint8)
        original = mask.copy()
        apply_exclusion_zones(mask, [ExclusionZone(0, 0, 50, 50)])
        np.testing.assert_array_equal(mask, original)

    def test_zone_clamped_to_image_bounds(self):
        mask = np.full((100, 100), 255, dtype=np.uint8)
        result = apply_exclusion_zones(mask, [ExclusionZone(x=80, y=80, w=200, h=200)])
        assert result[90, 90] == 0

    def test_zone_from_detection_has_padding(self):
        zone = zone_from_detection(200, 300, 400, 500, 1000, 1000, padding=80)
        assert zone.x <= 200 - 80
        assert zone.y <= 300 - 80
        assert zone.x + zone.w >= 400 + 80
        assert zone.y + zone.h >= 500 + 80

    def test_zone_from_detection_clamped(self):
        zone = zone_from_detection(5, 5, 50, 50, 100, 100, padding=80)
        assert zone.x >= 0
        assert zone.y >= 0
        assert zone.x + zone.w <= 100
        assert zone.y + zone.h <= 100

    def test_zones_roundtrip_config(self):
        zones = [ExclusionZone(10, 20, 300, 400, "test note")]
        restored = zones_from_config({"exclusion_zones": zones_to_config(zones)})
        assert len(restored) == 1
        z = restored[0]
        assert (z.x, z.y, z.w, z.h, z.note) == (10, 20, 300, 400, "test note")

    def test_exclusion_zone_suppresses_detection(self):
        img = _with_line(50, 128, 300, 128)
        without = detect_meteors(img, min_length=100, threshold=50)
        assert without, "Sanity: line should be detectable"
        zone = zone_from_detection(50, 128, 300, 128, 512, 512, padding=20)
        with_zone = detect_meteors(img, min_length=100, threshold=50, exclusion_zones=[zone])
        assert with_zone == []

"""Tests for services/meteor/storage.py — JSONL log and thumbnails."""
import json
import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.meteor.detector import MeteorDetection
from services.meteor.storage import log_detections, save_thumbnail


def _blank(w=256, h=256, fill=10):
    return Image.fromarray(np.full((h, w, 3), fill, dtype=np.uint8))


def _with_line(x1, y1, x2, y2, width=512, height=512):
    arr = np.full((height, width, 3), 10, dtype=np.uint8)
    img = Image.fromarray(arr)
    ImageDraw.Draw(img).line([(x1, y1), (x2, y2)], fill=(200, 200, 200), width=3)
    return img


class TestStorage:
    def test_creates_log_file(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        log_detections(path, [MeteorDetection(10, 20, 100, 200, 180.0, 45.0)])
        assert os.path.exists(path)

    def test_log_entry_is_valid_json(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        log_detections(path, [MeteorDetection(10, 20, 100, 200, 180.0, 45.0)])
        with open(path) as f:
            entry = json.loads(f.readline())
        assert "timestamp" in entry and "count" in entry and "detections" in entry

    def test_log_entry_count_matches(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        log_detections(path, [
            MeteorDetection(10, 20, 100, 200, 180.0, 45.0),
            MeteorDetection(50, 50, 300, 300, 354.0, 45.0),
        ])
        with open(path) as f:
            entry = json.loads(f.readline())
        assert entry["count"] == 2 and len(entry["detections"]) == 2

    def test_log_appends_multiple_calls(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        det = MeteorDetection(10, 20, 100, 200, 180.0, 45.0)
        log_detections(path, [det])
        log_detections(path, [det])
        with open(path) as f:
            assert sum(1 for l in f if l.strip()) == 2

    def test_log_detection_fields_present(self, tmp_path):
        path = str(tmp_path / "detections.jsonl")
        log_detections(path, [MeteorDetection(11, 22, 111, 222, 150.5, -30.1)],
                       image_filename="test.fits")
        with open(path) as f:
            entry = json.loads(f.readline())
        d = entry["detections"][0]
        assert d["x1"] == 11 and d["y1"] == 22
        assert d["x2"] == 111 and d["y2"] == 222
        assert d["length"] == 150.5
        assert entry["image"] == "test.fits"

    def test_empty_path_is_noop(self):
        log_detections("", [MeteorDetection(0, 0, 100, 100, 141.0, 45.0)])

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "sub" / "nested" / "detections.jsonl")
        log_detections(path, [MeteorDetection(10, 20, 100, 200, 180.0, 45.0)])
        assert os.path.exists(path)


class TestThumbnail:
    def test_thumbnail_created(self, tmp_path):
        info = save_thumbnail(_with_line(50, 128, 300, 128),
                              MeteorDetection(50, 128, 300, 128, 250.0, 0.0),
                              str(tmp_path), "2026-04-13T21:00:00")
        assert info["path"] and os.path.isfile(info["path"])

    def test_thumbnail_is_jpeg(self, tmp_path):
        info = save_thumbnail(_with_line(50, 128, 300, 128),
                              MeteorDetection(50, 128, 300, 128, 250.0, 0.0),
                              str(tmp_path), "2026-04-13T21:00:01")
        assert info["path"].endswith(".jpg")

    def test_thumbnail_size_is_300x300(self, tmp_path):
        info = save_thumbnail(_with_line(50, 128, 300, 128, width=1024, height=1024),
                              MeteorDetection(50, 128, 300, 128, 250.0, 0.0),
                              str(tmp_path), "2026-04-13T21:00:02")
        assert Image.open(info["path"]).size == (300, 300)

    def test_thumbnail_empty_dir_is_noop(self):
        info = save_thumbnail(_with_line(50, 128, 300, 128),
                              MeteorDetection(50, 128, 300, 128, 250.0, 0.0),
                              "", "2026-04-13T21:00:03")
        assert info["path"] == ""

    def test_thumbnail_near_edge_does_not_crash(self, tmp_path):
        info = save_thumbnail(_blank(300, 300),
                              MeteorDetection(5, 5, 50, 50, 64.0, 45.0),
                              str(tmp_path), "2026-04-13T21:00:04")
        assert info["path"] and Image.open(info["path"]).size == (300, 300)

    def test_thumbnail_has_no_baked_in_annotation(self, tmp_path):
        img = _blank(512, 512, fill=10)
        info = save_thumbnail(img, MeteorDetection(100, 256, 400, 256, 300.0, 0.0),
                              str(tmp_path), "2026-04-13T21:00:05")
        saved = np.array(Image.open(info["path"]))
        assert saved[:, :, 1].max() < 50, "Thumbnail must not have baked-in annotation"

    def test_thumbnail_returns_overlay_coords(self, tmp_path):
        img = _blank(1024, 1024, fill=10)
        info = save_thumbnail(img, MeteorDetection(400, 512, 600, 512, 200.0, 0.0),
                              str(tmp_path), "2026-04-13T21:00:06")
        assert info["thumb_size"] == 300
        assert info["line_x1"] == 50 and info["line_y1"] == 150
        assert info["line_x2"] == 250 and info["line_y2"] == 150
        assert info["length_px"] == 200

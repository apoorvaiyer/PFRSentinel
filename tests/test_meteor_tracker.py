"""Tests for services/meteor/tracker.py — kept for legacy compat; Phase 6 removes tracker."""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.meteor.detector import MeteorDetection
from services.meteor.tracker import MeteorTracker


class TestMeteorTracker:
    def test_single_frame_not_confirmed(self):
        tracker = MeteorTracker(min_frames=2, max_gap_sec=1.0)
        det = MeteorDetection(100, 100, 200, 200, 141.0, 45.0)
        assert tracker.update([det], 0.0) == []
        assert tracker.update([], 2.0) == []

    def test_two_frames_confirmed(self):
        tracker = MeteorTracker(min_frames=2, max_gap_sec=1.0)
        det1 = MeteorDetection(100, 100, 200, 200, 141.0, 45.0)
        det2 = MeteorDetection(120, 120, 220, 220, 141.0, 45.0)
        tracker.update([det1], 0.0)
        tracker.update([det2], 0.5)
        confirmed = tracker.update([], 2.0)
        assert len(confirmed) == 1 and confirmed[0].frame_count == 2

    def test_inconsistent_direction_rejected(self):
        tracker = MeteorTracker(min_frames=2, max_gap_sec=1.0, max_direction_std=0.3)
        tracker.update([MeteorDetection(100, 100, 200, 100, 100.0, 0.0)], 0.0)
        tracker.update([MeteorDetection(110, 100, 110, 200, 100.0, 90.0)], 0.5)
        assert tracker.update([], 2.0) == []

    def test_flush_returns_pending(self):
        tracker = MeteorTracker(min_frames=2, max_gap_sec=10.0)
        tracker.update([MeteorDetection(100, 100, 200, 200, 141.0, 45.0)], 0.0)
        tracker.update([MeteorDetection(120, 120, 220, 220, 141.0, 45.0)], 0.5)
        assert len(tracker.flush()) == 1

    def test_reset_clears_state(self):
        tracker = MeteorTracker(min_frames=2, max_gap_sec=10.0)
        tracker.update([MeteorDetection(100, 100, 200, 200, 141.0, 45.0)], 0.0)
        tracker.reset()
        assert tracker.flush() == []

    def test_meteor_event_properties(self):
        tracker = MeteorTracker(min_frames=2, max_gap_sec=1.0)
        tracker.update([MeteorDetection(100, 100, 200, 200, 141.0, 45.0)], 0.0)
        tracker.update([MeteorDetection(120, 120, 250, 250, 184.0, 45.0)], 0.5)
        confirmed = tracker.update([], 2.0)
        assert len(confirmed) == 1
        ev = confirmed[0]
        assert ev.best.length == 184.0
        assert ev.frame_count == 2
        assert 0.4 <= ev.duration_sec <= 0.6

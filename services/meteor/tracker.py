"""
Multi-Frame Meteor Tracker
Accumulates detections across consecutive frames and only reports
events that show consistent linear motion over multiple frames.

Inspired by MetDetPy's MeteorSeries / Collector pattern.

Usage:
    tracker = MeteorTracker(min_frames=2, max_gap_sec=4.0)
    # Each frame:
    confirmed = tracker.update(detections, timestamp)
    # confirmed is a list of MeteorEvent for newly confirmed series.

WARNING: Multi-frame confirmation requires short exposure times
(< 2 seconds) to be effective.  With long exposures (e.g. 10 s)
a meteor appears and vanishes within a single frame, so requiring
multiple frames would cause real meteors to be discarded.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .detector import MeteorDetection


@dataclass
class MeteorEvent:
    """A confirmed meteor event spanning multiple frames."""
    detections: List[MeteorDetection]
    timestamps: List[float]
    direction_std: float    # Std dev of direction angles (radians) — lower = more linear

    @property
    def best(self) -> MeteorDetection:
        return max(self.detections, key=lambda d: d.length)

    @property
    def frame_count(self) -> int:
        return len(self.detections)

    @property
    def duration_sec(self) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        return self.timestamps[-1] - self.timestamps[0]


@dataclass
class _ActiveSeries:
    """Internal: a detection series that may or may not be confirmed yet."""
    detections: List[MeteorDetection] = field(default_factory=list)
    timestamps: List[float] = field(default_factory=list)
    last_update: float = 0.0

    def midpoint(self) -> Tuple[float, float]:
        det = self.detections[-1]
        return ((det.x1 + det.x2) / 2.0, (det.y1 + det.y2) / 2.0)

    def direction_std(self) -> float:
        if len(self.detections) < 2:
            return 0.0
        angles = [math.atan2(d.y2 - d.y1, d.x2 - d.x1)
                  for d in self.detections]
        mean_a = math.atan2(
            sum(math.sin(a) for a in angles),
            sum(math.cos(a) for a in angles),
        )
        diffs = [abs(math.atan2(math.sin(a - mean_a), math.cos(a - mean_a)))
                 for a in angles]
        return float((sum(d * d for d in diffs) / len(diffs)) ** 0.5)


class MeteorTracker:
    """
    Accumulates per-frame detections into multi-frame series.

    A series is *confirmed* when it expires (no new match for *max_gap_sec*)
    and contains >= *min_frames* detections with consistent direction
    (std dev < *max_direction_std* radians).

    Args:
        min_frames:       Minimum frames with detections to confirm.
        max_gap_sec:      Seconds with no match before a series expires.
        match_distance:   Max pixel distance to associate a new detection
                          with an existing series (midpoint-to-midpoint).
        max_direction_std: Max direction angle std dev (radians).
                          0.6 rad ≈ 34 deg — rejects curved paths.
    """

    def __init__(
        self,
        min_frames: int = 2,
        max_gap_sec: float = 4.0,
        match_distance: float = 300.0,
        max_direction_std: float = 0.6,
    ):
        self.min_frames = max(2, min_frames)
        self.max_gap_sec = max_gap_sec
        self.match_distance = match_distance
        self.max_direction_std = max_direction_std
        self._active: List[_ActiveSeries] = []

    def update(
        self,
        detections: List[MeteorDetection],
        timestamp: float,
    ) -> List[MeteorEvent]:
        """
        Feed one frame's detections and return any newly confirmed events.

        *timestamp* should be a monotonic float (e.g. ``time.monotonic()``).
        """
        # Expire stale series
        confirmed = self._expire(timestamp)

        # Match new detections to active series
        matched_indices: set = set()
        for det in detections:
            mid = ((det.x1 + det.x2) / 2.0, (det.y1 + det.y2) / 2.0)
            best_i, best_dist = -1, self.match_distance
            for i, series in enumerate(self._active):
                smid = series.midpoint()
                dist = math.hypot(mid[0] - smid[0], mid[1] - smid[1])
                if dist < best_dist:
                    best_dist = dist
                    best_i = i

            if best_i >= 0:
                self._active[best_i].detections.append(det)
                self._active[best_i].timestamps.append(timestamp)
                self._active[best_i].last_update = timestamp
                matched_indices.add(best_i)
            else:
                # Start new series
                self._active.append(_ActiveSeries(
                    detections=[det],
                    timestamps=[timestamp],
                    last_update=timestamp,
                ))

        return confirmed

    def flush(self) -> List[MeteorEvent]:
        """Force-expire all active series (e.g. on capture stop)."""
        confirmed = []
        for series in self._active:
            event = self._try_confirm(series)
            if event:
                confirmed.append(event)
        self._active.clear()
        return confirmed

    def reset(self):
        """Discard all state."""
        self._active.clear()

    def _expire(self, now: float) -> List[MeteorEvent]:
        confirmed = []
        surviving = []
        for series in self._active:
            if (now - series.last_update) > self.max_gap_sec:
                event = self._try_confirm(series)
                if event:
                    confirmed.append(event)
            else:
                surviving.append(series)
        self._active = surviving
        return confirmed

    def _try_confirm(self, series: _ActiveSeries) -> Optional[MeteorEvent]:
        if len(series.detections) < self.min_frames:
            return None
        dstd = series.direction_std()
        if dstd > self.max_direction_std:
            return None
        return MeteorEvent(
            detections=series.detections,
            timestamps=series.timestamps,
            direction_std=dstd,
        )

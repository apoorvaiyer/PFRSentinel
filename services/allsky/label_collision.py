"""
Grid-based label collision avoidance for all-sky overlay rendering.

Labels are placed in 4 candidate positions around each marker.
Higher-priority labels (bright stars, Messier) are placed first; lower-priority
labels are dropped if they overlap an already-placed label.
"""
import numpy as np
from typing import List, Tuple, Optional, Set


# Candidate offsets (dx, dy) in pixels relative to marker centre
# Order: right, below, left, above
_OFFSETS = [(6, 0), (0, 6), (-6, 0), (0, -6)]


class LabelGrid:
    """
    Occupancy grid for label placement.

    Divide the image into cells; mark cells occupied when a label is placed.
    Uses a generous cell size to create soft exclusion zones.
    """

    def __init__(self, img_width: int, img_height: int, cell_size: int = 12):
        self._w = img_width
        self._h = img_height
        self._cell = cell_size
        cols = (img_width  + cell_size - 1) // cell_size
        rows = (img_height + cell_size - 1) // cell_size
        self._grid: Set[Tuple[int, int]] = set()
        self._cols = cols
        self._rows = rows

    def _bbox_cells(self, x: float, y: float, w: float, h: float):
        """Yield (col, row) grid cells covered by a bounding box."""
        x0 = int(x) // self._cell
        y0 = int(y) // self._cell
        x1 = int(x + w) // self._cell
        y1 = int(y + h) // self._cell
        for c in range(max(0, x0), min(self._cols, x1 + 1)):
            for r in range(max(0, y0), min(self._rows, y1 + 1)):
                yield (c, r)

    def is_free(self, x: float, y: float, w: float, h: float) -> bool:
        """True if bounding box does not overlap any occupied cell."""
        for cell in self._bbox_cells(x, y, w, h):
            if cell in self._grid:
                return False
        return True

    def occupy(self, x: float, y: float, w: float, h: float) -> None:
        """Mark cells covered by bounding box as occupied."""
        for cell in self._bbox_cells(x, y, w, h):
            self._grid.add(cell)

    def try_place(
        self,
        marker_x: float,
        marker_y: float,
        label_w: float,
        label_h: float,
        offsets: List[Tuple[int, int]] = None,
    ) -> Optional[Tuple[float, float]]:
        """
        Try to place a label near (marker_x, marker_y).

        Tests candidate positions in order; returns (label_x, label_y) for
        the first collision-free position, or None if all are occupied.
        Also marks the chosen cell as occupied.
        """
        if offsets is None:
            offsets = _OFFSETS

        for dx, dy in offsets:
            lx = marker_x + dx
            ly = marker_y + dy - label_h / 2.0
            # Keep within image
            if lx < 0 or ly < 0 or lx + label_w > self._w or ly + label_h > self._h:
                continue
            if self.is_free(lx, ly, label_w, label_h):
                self.occupy(lx, ly, label_w, label_h)
                return lx, ly

        return None  # No free slot found


def estimate_text_size(text: str, font_size: int) -> Tuple[float, float]:
    """
    Rough estimate of rendered text bounding box in pixels.
    Assumes ~0.6× monospace ratio.
    """
    w = len(text) * font_size * 0.6
    h = float(font_size) * 1.2
    return w, h

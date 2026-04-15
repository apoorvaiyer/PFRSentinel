"""
Meteor Detection Storage
Appends detection events to a JSONL log file (one JSON object per line).
Also saves annotated 300×300 thumbnail crops for display in the UI.
"""
import json
import os
from datetime import datetime
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image

from .detector import MeteorDetection


def log_detections(
    log_path: str,
    detections: List[MeteorDetection],
    image_filename: str = "",
) -> None:
    """
    Append a detection event to *log_path*.

    Creates parent directories and the file if they do not exist.
    Silently ignores I/O errors — detection logging is best-effort.
    """
    if not log_path:
        return

    parent = os.path.dirname(log_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "image": image_filename,
        "count": len(detections),
        "detections": [
            {
                "x1": d.x1, "y1": d.y1,
                "x2": d.x2, "y2": d.y2,
                "length": round(d.length, 1),
                "angle": round(d.angle_deg, 1),
            }
            for d in detections
        ],
    }

    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def save_thumbnail(
    image: Image.Image,
    detection: MeteorDetection,
    thumb_dir: str,
    timestamp: str,
    size: int = 300,
) -> str:
    """
    Crop a *size*×*size* region centred on *detection* from *image*, draw the
    detection line in green, and save it as a JPEG.

    Returns the saved file path, or an empty string on failure.
    """
    if not thumb_dir:
        return ""
    try:
        os.makedirs(thumb_dir, exist_ok=True)

        mid_x = (detection.x1 + detection.x2) // 2
        mid_y = (detection.y1 + detection.y2) // 2
        half = size // 2

        # Crop bounds clamped to image
        left   = max(0, mid_x - half)
        top    = max(0, mid_y - half)
        right  = min(image.width,  mid_x + half)
        bottom = min(image.height, mid_y + half)

        crop = image.crop((left, top, right, bottom))

        # Pad to size×size with black if the crop is near an edge
        if crop.size != (size, size):
            padded = Image.new("RGB", (size, size), (0, 0, 0))
            padded.paste(crop, (0, 0))
            crop = padded

        # Draw detection line offset to crop coordinates
        arr = np.array(crop)
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        lx1 = max(0, min(size - 1, detection.x1 - left))
        ly1 = max(0, min(size - 1, detection.y1 - top))
        lx2 = max(0, min(size - 1, detection.x2 - left))
        ly2 = max(0, min(size - 1, detection.y2 - top))
        cv2.line(bgr, (lx1, ly1), (lx2, ly2), (0, 255, 0), 2)
        mid_crop_x = (lx1 + lx2) // 2
        mid_crop_y = max(0, (ly1 + ly2) // 2 - 8)
        cv2.putText(
            bgr, f"{detection.length:.0f}px",
            (mid_crop_x, mid_crop_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1,
        )
        annotated = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

        safe_ts = timestamp.replace(":", "-").replace("T", "_")
        path = os.path.join(thumb_dir, f"meteor_{safe_ts}.jpg")
        annotated.save(path, "JPEG", quality=90)
        return path

    except Exception:
        return ""

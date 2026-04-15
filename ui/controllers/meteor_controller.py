"""
Meteor Controller
Receives processed frames, runs meteor detection in a daemon thread,
saves thumbnails, persists detections, and handles user rejections.
"""
import os
import threading
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QObject, Signal, QTimer
from PIL import Image

from services.logger import app_logger
from services.meteor.detector import detect_meteors, annotate_image, MeteorDetection
from services.meteor.storage import log_detections, save_thumbnail
from services.meteor.mask import (
    zone_from_detection, zones_from_config, zones_to_config, ExclusionZone,
)


_MAX_RECENT_EVENTS = 20


class MeteorController(QObject):
    """
    Responsibilities:
    - Receive timelapse_ready signal from ImageProcessor (clean + overlaid frame)
    - Run detect_meteors (with active exclusion zones) in a daemon thread
    - Save a 300×300 annotated thumbnail crop per event
    - Persist events to a JSONL log file
    - Handle "Not a Meteor" rejections: add exclusion zone, purge thumbnail
    - Emit status_updated for MeteorPanel
    """

    status_updated = Signal(dict)

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window

        self._session_frames: int = 0
        self._session_detections: int = 0
        self._last_detection_time: Optional[str] = None
        self._recent_events: List[dict] = []

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._emit_status)
        self._status_timer.start()

    # ------------------------------------------------------------------ #
    #  Frame handling                                                      #
    # ------------------------------------------------------------------ #

    def on_frame_ready(self, clean_image: Image.Image, _overlaid: Image.Image):
        """Called for every processed frame via image_processor.timelapse_ready."""
        cfg = self._get_config()
        if not cfg.get("enabled", False):
            return

        self._session_frames += 1
        threading.Thread(
            target=self._run_detection,
            args=(clean_image.copy(), cfg),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------ #
    #  Detection  (background thread — no Qt calls allowed)               #
    # ------------------------------------------------------------------ #

    def _run_detection(self, image: Image.Image, cfg: dict):
        try:
            min_length = int(cfg.get("min_length", 100))
            zones = zones_from_config(cfg)
            detections = detect_meteors(image, min_length=min_length,
                                        exclusion_zones=zones or None)

            if not detections:
                return

            timestamp = datetime.now().isoformat(timespec="seconds")
            best = max(detections, key=lambda d: d.length)

            # Save thumbnail centred on the longest detection
            thumb_path = save_thumbnail(
                image, best,
                thumb_dir=self._resolve_thumb_dir(),
                timestamp=timestamp,
            )

            event = {
                "timestamp":      timestamp,
                "count":          len(detections),
                "max_length":     round(best.length, 1),
                "thumbnail_path": thumb_path,
                # Stored for exclusion zone creation on rejection
                "best_x1": best.x1, "best_y1": best.y1,
                "best_x2": best.x2, "best_y2": best.y2,
                "img_w":   image.width,
                "img_h":   image.height,
            }

            if cfg.get("save_detections", True):
                log_detections(self._resolve_log_path(cfg), detections)

            if cfg.get("save_annotated", False):
                self._save_annotated(image, detections, cfg, timestamp)

            self._session_detections += len(detections)
            self._last_detection_time = timestamp

            # Evict oldest events, deleting their thumbnails to free disk
            new_list = ([event] + self._recent_events)[:_MAX_RECENT_EVENTS]
            evicted = self._recent_events[_MAX_RECENT_EVENTS - 1:]
            for old in evicted:
                self._delete_thumbnail(old.get("thumbnail_path", ""))
            self._recent_events = new_list

            app_logger.info(
                f"Meteor: {len(detections)} detection(s), "
                f"longest={event['max_length']}px"
            )

        except Exception as exc:
            app_logger.error(f"Meteor detection error: {exc}")

    def _save_annotated(
        self,
        image: Image.Image,
        detections: List[MeteorDetection],
        cfg: dict,
        timestamp: str,
    ):
        annotated_dir = cfg.get("annotated_dir", "").strip()
        if not annotated_dir:
            return
        try:
            os.makedirs(annotated_dir, exist_ok=True)
            annotated = annotate_image(image, detections)
            fname = f"meteor_{timestamp.replace(':', '-')}.jpg"
            annotated.save(os.path.join(annotated_dir, fname), "JPEG", quality=90)
        except Exception as exc:
            app_logger.error(f"Meteor: could not save annotated image: {exc}")

    # ------------------------------------------------------------------ #
    #  Rejection                                                           #
    # ------------------------------------------------------------------ #

    def on_detection_rejected(self, timestamp: str):
        """
        User marked an event as 'Not a Meteor'.

        1. Find the event by timestamp.
        2. Build an ExclusionZone from its detection coords.
        3. Append zone to config and save.
        4. Remove event from history and delete its thumbnail.
        5. Emit updated status so the panel rebuilds immediately.
        """
        event = next(
            (e for e in self._recent_events if e.get("timestamp") == timestamp),
            None,
        )
        if not event:
            return

        # Build exclusion zone with 80px padding around the rejected line
        zone = zone_from_detection(
            x1=event["best_x1"], y1=event["best_y1"],
            x2=event["best_x2"], y2=event["best_y2"],
            image_width=event.get("img_w", 9999),
            image_height=event.get("img_h", 9999),
            padding=80,
        )
        zone.note = f"Rejected {timestamp}"

        cfg = self._get_config()
        existing_zones = zones_from_config(cfg)
        existing_zones.append(zone)

        # Preserve all other meteor config keys, update only exclusion_zones
        updated_cfg = dict(cfg)
        updated_cfg["exclusion_zones"] = zones_to_config(existing_zones)
        self._main_window.config.set("meteor", updated_cfg)
        self._main_window.config.save()

        # Remove from history
        self._delete_thumbnail(event.get("thumbnail_path", ""))
        self._recent_events = [
            e for e in self._recent_events if e.get("timestamp") != timestamp
        ]

        app_logger.info(
            f"Meteor: rejection saved — zone added at "
            f"({zone.x},{zone.y}) {zone.w}×{zone.h}px"
        )
        self._emit_status()

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def on_capture_stopped(self):
        self._session_frames = 0
        self._session_detections = 0
        self._last_detection_time = None
        self._emit_status()

    def shutdown(self):
        app_logger.debug("Meteor controller shutdown")

    # ------------------------------------------------------------------ #
    #  Status                                                              #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        return {
            "session_frames":     self._session_frames,
            "session_detections": self._session_detections,
            "last_detection_time": self._last_detection_time,
            "recent_events":      list(self._recent_events),
        }

    def _emit_status(self):
        self.status_updated.emit(self.get_status())

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _get_config(self) -> dict:
        return self._main_window.config.get("meteor", {})

    def _resolve_log_path(self, cfg: dict) -> str:
        log_file = cfg.get("log_file", "").strip()
        if log_file:
            return log_file
        return os.path.join(self._appdata_dir(), "meteor_detections.jsonl")

    def _resolve_thumb_dir(self) -> str:
        return os.path.join(self._appdata_dir(), "meteor_thumbnails")

    def _appdata_dir(self) -> str:
        from app_config import APP_DATA_FOLDER
        return os.path.join(os.getenv("LOCALAPPDATA", ""), APP_DATA_FOLDER)

    @staticmethod
    def _delete_thumbnail(path: str):
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

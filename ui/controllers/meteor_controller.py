"""
Meteor Controller
Receives processed frames, runs meteor detection in a daemon thread,
saves thumbnails, persists detections, and handles user rejections.

Detection uses frame differencing: consecutive frames are subtracted so only
transient events (meteors) produce edges.  A sky circle mask restricts
detection to the fisheye's circular sky region.
"""
import math
import os
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QTimer
from PIL import Image

from services.logger import app_logger
from services.meteor.detector import (
    detect_meteors, annotate_image, MeteorDetection,
    compute_frame_difference, apply_sky_circle_mask,
    estimate_adaptive_threshold, check_speed_plausibility,
)
from services.meteor.storage import log_detections, log_event, save_thumbnail
from services.meteor.mask import (
    zone_from_detection, zones_from_config, zones_to_config, ExclusionZone,
)
from services.meteor.tracker import MeteorTracker


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
        self._lock = threading.Lock()  # Detection threads and Qt timer both touch counters/events
        self._detection_semaphore = threading.Semaphore(1)  # Drop frames if detection is slower than capture

        self._session_frames: int = 0
        self._session_detections: int = 0
        self._last_detection_time: Optional[str] = None
        self._recent_events: List[dict] = []

        # Frame differencing state
        self._previous_frame: Optional[Image.Image] = None
        self._last_detection_ts: float = 0.0   # monotonic, for cooldown
        self._sky_circle: Optional[Tuple[float, float, float]] = None  # (cx, cy, r)
        self._tracker: Optional[MeteorTracker] = None

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

        # --- Roof gate (soft — only blocks when confirmed closed) ---
        ml_results = getattr(self._main_window, 'last_ml_results', None) or {}
        if ml_results.get('roof_status') == 'Closed':
            self._previous_frame = None  # invalidate diff chain
            return

        with self._lock:
            self._session_frames += 1
        current = clean_image.copy()

        # --- Cooldown gate ---
        cooldown = float(cfg.get("detection_cooldown", 30))
        if cooldown > 0 and (time.monotonic() - self._last_detection_ts) < cooldown:
            self._previous_frame = current
            return

        # --- First-frame guard + sky circle init ---
        if self._previous_frame is None:
            self._previous_frame = current
            self._resolve_sky_circle(current)
            return

        # --- Frame differencing (adaptive or fixed threshold) ---
        if cfg.get("adaptive_threshold", True):
            threshold = estimate_adaptive_threshold(current)
        else:
            threshold = int(cfg.get("diff_threshold", 25))
        diff_image = compute_frame_difference(
            current, self._previous_frame, threshold)
        self._previous_frame = current

        # --- Sky circle mask ---
        if self._sky_circle:
            diff_image = apply_sky_circle_mask(diff_image, *self._sky_circle)

        # --- Ensure tracker is initialised if multi-frame mode enabled ---
        if cfg.get("multi_frame_confirm", False) and self._tracker is None:
            self._tracker = MeteorTracker(
                min_frames=int(cfg.get("min_confirm_frames", 2)))
        elif not cfg.get("multi_frame_confirm", False):
            self._tracker = None

        # Non-blocking: skip this frame if a detection is already running
        if not self._detection_semaphore.acquire(blocking=False):
            return

        threading.Thread(
            target=self._run_detection,
            args=(diff_image, current, cfg),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------ #
    #  Detection  (background thread — no Qt calls allowed)               #
    # ------------------------------------------------------------------ #

    def _run_detection(self, diff_image: Image.Image,
                       original_image: Image.Image, cfg: dict):
        try:
            min_length = int(cfg.get("min_length", 100))
            zones = zones_from_config(cfg)
            detections = detect_meteors(
                diff_image, min_length=min_length,
                exclusion_zones=zones or None,
                strict_validation=True,
            )

            if not detections:
                # Still feed empty frame to tracker so series can expire
                if self._tracker:
                    self._process_confirmed(
                        self._tracker.update([], time.monotonic()),
                        original_image, cfg)
                return

            # --- Speed plausibility filter ---
            exposure_sec = self._get_exposure_sec()
            if exposure_sec > 0:
                detections = [
                    d for d in detections
                    if check_speed_plausibility(
                        d, exposure_sec, original_image.width)
                ]
                if not detections:
                    if self._tracker:
                        self._process_confirmed(
                            self._tracker.update([], time.monotonic()),
                            original_image, cfg)
                    return

            # --- Multi-frame confirmation or direct report ---
            if self._tracker:
                confirmed = self._tracker.update(detections, time.monotonic())
                self._process_confirmed(confirmed, original_image, cfg)
            else:
                self._report_detections(detections, original_image, cfg)

        except Exception as exc:
            app_logger.error(f"Meteor detection error: {exc}")
        finally:
            self._detection_semaphore.release()

    def _report_detections(self, detections: List[MeteorDetection],
                           original_image: Image.Image, cfg: dict):
        """Report detections directly (single-frame mode)."""
        self._last_detection_ts = time.monotonic()
        timestamp = datetime.now().isoformat(timespec="seconds")
        best = max(detections, key=lambda d: d.length)

        thumb_info = save_thumbnail(
            original_image, best,
            thumb_dir=self._resolve_thumb_dir(),
            timestamp=timestamp,
        )

        annotated_path = ""
        if cfg.get("save_annotated", False):
            annotated_path = self._save_annotated(
                original_image, detections, cfg, timestamp)

        event = {
            "timestamp":      timestamp,
            "count":          len(detections),
            "max_length":     round(best.length, 1),
            "thumbnail_path": thumb_info.get("path", ""),
            "annotated_path": annotated_path,
            "confirmed":      False,
            "thumb_left":     thumb_info.get("thumb_left", 0),
            "thumb_top":      thumb_info.get("thumb_top", 0),
            "thumb_size":     thumb_info.get("thumb_size", 300),
            "line_x1":        thumb_info.get("line_x1", 0),
            "line_y1":        thumb_info.get("line_y1", 0),
            "line_x2":        thumb_info.get("line_x2", 0),
            "line_y2":        thumb_info.get("line_y2", 0),
            "length_px":      thumb_info.get("length_px", int(round(best.length))),
            "best_x1": best.x1, "best_y1": best.y1,
            "best_x2": best.x2, "best_y2": best.y2,
            "img_w":   original_image.width,
            "img_h":   original_image.height,
        }

        if cfg.get("save_detections", True):
            log_detections(self._resolve_log_path(cfg), detections)

        with self._lock:
            self._session_detections += len(detections)
            self._last_detection_time = timestamp

            new_list = ([event] + self._recent_events)[:_MAX_RECENT_EVENTS]
            evicted = self._recent_events[_MAX_RECENT_EVENTS - 1:]
            self._recent_events = new_list

        for old in evicted:
            self._evict_event_files(old)

        app_logger.info(
            f"Meteor: {len(detections)} detection(s), "
            f"longest={event['max_length']}px"
        )

    def _process_confirmed(self, events, original_image: Image.Image,
                           cfg: dict):
        """Handle confirmed events from the multi-frame tracker."""
        for meteor_event in events:
            self._report_detections(
                [meteor_event.best], original_image, cfg)
            app_logger.info(
                f"Meteor: confirmed across {meteor_event.frame_count} frames, "
                f"direction_std={meteor_event.direction_std:.2f} rad"
            )

    def _save_annotated(
        self,
        image: Image.Image,
        detections: List[MeteorDetection],
        cfg: dict,
        timestamp: str,
    ) -> str:
        """Save the full-frame annotated image and return its path ("" on failure)."""
        annotated_dir = cfg.get("annotated_dir", "").strip()
        if not annotated_dir:
            return ""
        try:
            os.makedirs(annotated_dir, exist_ok=True)
            annotated = annotate_image(image, detections)
            fname = f"meteor_{timestamp.replace(':', '-')}.jpg"
            path = os.path.join(annotated_dir, fname)
            annotated.save(path, "JPEG", quality=90)
            return path
        except Exception as exc:
            app_logger.error(f"Meteor: could not save annotated image: {exc}")
            return ""

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
        with self._lock:
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

        # Remove from history (delete both thumbnail and full annotated frame)
        self._evict_event_files(event)
        with self._lock:
            self._recent_events = [
                e for e in self._recent_events if e.get("timestamp") != timestamp
            ]

        app_logger.info(
            f"Meteor: rejection saved — zone added at "
            f"({zone.x},{zone.y}) {zone.w}×{zone.h}px"
        )
        self._emit_status()

    # ------------------------------------------------------------------ #
    #  Confirmation                                                         #
    # ------------------------------------------------------------------ #

    def on_detection_confirmed(self, timestamp: str):
        """
        User marked an event as a real meteor.

        Moves the thumbnail (and full annotated frame if present) into a
        ``confirmed/`` subdirectory so future evictions don't delete them,
        marks the event ``confirmed=True`` so the card shows a Confirmed
        badge, and appends a record to the JSONL log for persistence.
        """
        with self._lock:
            event = next(
                (e for e in self._recent_events if e.get("timestamp") == timestamp),
                None,
            )
        if not event or event.get("confirmed"):
            return

        new_thumb = self._move_to_confirmed(event.get("thumbnail_path", ""))
        new_annotated = self._move_to_confirmed(event.get("annotated_path", ""))

        with self._lock:
            for e in self._recent_events:
                if e.get("timestamp") == timestamp:
                    e["confirmed"] = True
                    if new_thumb:
                        e["thumbnail_path"] = new_thumb
                    if new_annotated:
                        e["annotated_path"] = new_annotated
                    break

        cfg = self._get_config()
        if cfg.get("save_detections", True):
            log_event(self._resolve_log_path(cfg), {
                "event": "confirmed",
                "timestamp": timestamp,
                "thumbnail": new_thumb or event.get("thumbnail_path", ""),
                "annotated": new_annotated or event.get("annotated_path", ""),
            })

        app_logger.info(f"Meteor: confirmed detection at {timestamp}")
        self._emit_status()

    @staticmethod
    def _move_to_confirmed(path: str) -> str:
        """Move *path* into a sibling ``confirmed/`` folder; return the new path."""
        if not path or not os.path.isfile(path):
            return ""
        try:
            parent = os.path.dirname(path)
            confirmed_dir = os.path.join(parent, "confirmed")
            os.makedirs(confirmed_dir, exist_ok=True)
            new_path = os.path.join(confirmed_dir, os.path.basename(path))
            os.replace(path, new_path)
            return new_path
        except OSError:
            return ""

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def on_capture_stopped(self):
        if self._tracker:
            self._tracker.flush()  # expire any pending series
            self._tracker = None
        with self._lock:
            old_events = list(self._recent_events)
            self._session_frames = 0
            self._session_detections = 0
            self._last_detection_time = None
            self._recent_events = []
        # Clean up any unconfirmed files lingering from this session
        for ev in old_events:
            self._evict_event_files(ev)
        self._previous_frame = None
        self._last_detection_ts = 0.0
        self._sky_circle = None
        self._emit_status()

    def shutdown(self):
        if self._tracker:
            self._tracker.reset()
            self._tracker = None
        self._previous_frame = None
        self._sky_circle = None
        app_logger.debug("Meteor controller shutdown")

    # ------------------------------------------------------------------ #
    #  Status                                                              #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        with self._lock:
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

    def _resolve_sky_circle(self, image: Image.Image):
        """Compute and cache the sky circle (cx, cy, radius) for masking."""
        if self._sky_circle is not None:
            return
        try:
            # Prefer calibration model (precise optical centre)
            ctrl = getattr(self._main_window, 'allsky_controller', None)
            model = getattr(ctrl, '_model', None) if ctrl else None
            if model and hasattr(model, 'cx') and hasattr(model, 'a1'):
                r = model.a1 * (math.pi / 2)  # 90-deg horizon radius
                self._sky_circle = (model.cx, model.cy, r)
                app_logger.info(
                    f"Meteor: sky circle from calibration — "
                    f"cx={model.cx:.0f}, cy={model.cy:.0f}, r={r:.0f}")
                return
        except Exception:
            pass
        try:
            from services.allsky.star_centroid import estimate_sky_circle
            cx, cy, r = estimate_sky_circle(image)
            self._sky_circle = (cx, cy, r)
            app_logger.info(
                f"Meteor: sky circle auto-detected — "
                f"cx={cx:.0f}, cy={cy:.0f}, r={r:.0f}")
        except Exception as exc:
            app_logger.debug(f"Meteor: sky circle detection failed ({exc}), "
                             "running without mask")

    def _get_exposure_sec(self) -> float:
        """Return current exposure time in seconds, or 0 if unavailable."""
        try:
            cfg = self._main_window.config
            cam_name = cfg.get("zwo_selected_camera_name", "") or cfg.get("zwo_camera_name", "")
            profile = cfg.get_camera_profile(cam_name) if cam_name else {}
            ms = float(profile.get("exposure_ms", 0))
            return ms / 1000.0
        except (TypeError, ValueError, AttributeError):
            return 0.0

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
    def _delete_file(path: str):
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _evict_event_files(self, event: dict):
        """Delete thumbnail + full annotated frame for an unconfirmed event.
        Confirmed events keep their files (they live in confirmed/ subdirs)."""
        if event.get("confirmed"):
            return
        self._delete_file(event.get("thumbnail_path", ""))
        self._delete_file(event.get("annotated_path", ""))

"""
Background calibration accumulation service.

Receives frames from the image processing pipeline, detects stars,
accumulates detection data, and progressively refines the fisheye lens
model through multi-image joint calibration.

Lifecycle:
  1. Created by AllSkyController on startup.
  2. Loads existing model from %LOCALAPPDATA%/PFRSentinel/allsky_calibration.json.
  3. Fed by ImageProcessorWorker via feed_frame() on each captured frame.
  4. Detects stars inline (~50 ms), stores detection data (not raw images).
  5. When enough frames span enough time, launches a background _RefineWorker.
  6. On success, saves improved model and emits quality_upgraded signal.

Thread safety:
  - feed_frame() is called from the image-processor worker thread.
  - _maybe_refine() runs on the main thread (via queued signal).
  - _RefineWorker runs in its own QThread.
"""
import copy
import os
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from PySide6.QtCore import QObject, Signal, QThread

from services.logger import app_logger as log

from .star_centroid import detect_stars, estimate_sky_circle
from .fisheye import FisheyeModel
from .catalogs import get_bright_stars
from .coords import radec_to_altaz
from .calibration import calibrate, CalibrationError
from .multi_calibrate import refine_from_detections

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BUFFER = 60             # rolling buffer capacity (frame dicts)
MIN_FRAMES = 3              # minimum frames before attempting refinement
MIN_SPAN_MINUTES = 5.0      # minimum time span across buffered frames
REFINE_COOLDOWN_S = 120     # seconds between refinement attempts
INITIAL_COOLDOWN_S = 180    # seconds between initial single-image cal attempts
MAX_RESIDUAL_PX = 20.0      # max accepted median residual (pixels)


# ---------------------------------------------------------------------------
# Quality assessment
# ---------------------------------------------------------------------------

class CalibrationQuality:
    """
    Calibration quality levels with display metadata.

    Each level has a value string, numeric rank (for comparison), a
    user-facing description, and background/text colour pair for the UI
    badge (dark-theme palette).
    """

    # (value, rank, description, badge_bg, badge_text)
    _LEVELS = {
        'none':        (0, 'Not calibrated',
                        '#1E1E1E', '#706F6A'),
        'preliminary': (1, 'Single image — rough overlay',
                        '#2D2305', '#FFD166'),
        'acceptable':  (2, 'Multi-image — improving',
                        '#2D1A05', '#FF9F43'),
        'good':        (3, 'Multi-image — accurate',
                        '#132D21', '#3DD68C'),
        'excellent':   (4, 'Long baseline — best accuracy',
                        '#0D2D1A', '#4ADE80'),
    }

    NONE        = 'none'
    PRELIMINARY = 'preliminary'
    ACCEPTABLE  = 'acceptable'
    GOOD        = 'good'
    EXCELLENT   = 'excellent'

    ALL = (NONE, PRELIMINARY, ACCEPTABLE, GOOD, EXCELLENT)

    @classmethod
    def rank(cls, value: str) -> int:
        return cls._LEVELS.get(value, cls._LEVELS['none'])[0]

    @classmethod
    def description(cls, value: str) -> str:
        return cls._LEVELS.get(value, cls._LEVELS['none'])[1]

    @classmethod
    def badge_colors(cls, value: str) -> tuple:
        """Return (background_hex, text_hex) for the UI badge."""
        entry = cls._LEVELS.get(value, cls._LEVELS['none'])
        return entry[2], entry[3]


def model_quality(
    model: Optional[FisheyeModel],
    n_images: int = 1,
    span_minutes: float = 0.0,
) -> str:
    """
    Assess calibration quality from model metrics.

    Returns one of the CalibrationQuality level strings:
    'none', 'preliminary', 'acceptable', 'good', 'excellent'.
    """
    if model is None or not model.is_valid():
        return CalibrationQuality.NONE
    rms = model.rms_residual
    n = model.n_matches
    if n_images >= 20 and span_minutes >= 60 and rms <= 8.0:
        return CalibrationQuality.EXCELLENT
    if n_images >= 10 and n >= 100 and rms <= 12.0:
        return CalibrationQuality.GOOD
    if n_images >= 3 and n >= 30 and rms <= 15.0:
        return CalibrationQuality.ACCEPTABLE
    return CalibrationQuality.PRELIMINARY


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _RefineWorker(QThread):
    """Run multi-image joint calibration in a background thread."""

    finished = Signal(object)   # FisheyeModel on success
    failed = Signal(str)        # error message

    def __init__(self, frames, seed_model, parent=None):
        super().__init__(parent)
        self._frames = frames
        self._seed = seed_model

    def run(self):
        try:
            model = refine_from_detections(
                self._frames,
                self._seed,
                max_residual_px=MAX_RESIDUAL_PX,
            )
            self.finished.emit(model)
        except Exception as e:
            self.failed.emit(str(e))


class _InitialCalWorker(QThread):
    """Run single-image calibration when no model exists yet."""

    finished = Signal(object)   # FisheyeModel
    failed = Signal(str)

    def __init__(self, image, lat, lon, dt, parent=None):
        super().__init__(parent)
        self._image = image
        self._lat = lat
        self._lon = lon
        self._dt = dt

    def run(self):
        try:
            model = calibrate(
                self._image, self._lat, self._lon, dt=self._dt,
                min_matches=6,
            )
            self.finished.emit(model)
        except Exception as e:
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class CalibrationService(QObject):
    """
    Background service that accumulates star detections from incoming
    frames and progressively refines the fisheye lens model.

    Signals:
        quality_upgraded(str, object): quality level name + FisheyeModel.
        status_changed(str): human-readable status for the UI.
    """

    quality_upgraded = Signal(str, object)
    status_changed = Signal(str)

    # Internal signal: queued to main thread for safe QThread creation.
    _check_refine = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[dict] = []
        self._lock = threading.Lock()
        self._model: Optional[FisheyeModel] = None
        self._quality = 'none'
        self._last_refine_time = 0.0
        self._last_initial_attempt_time = 0.0
        self._refine_worker: Optional[_RefineWorker] = None
        self._initial_worker: Optional[_InitialCalWorker] = None
        self._pending_initial = None   # (image, dt, lat, lon) awaiting cal
        self._check_refine.connect(self._maybe_refine)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_model(self, path: str) -> None:
        """Load an existing calibration model from disk."""
        model = FisheyeModel.try_load(path)
        if model and model.is_valid():
            self._model = model
            self._quality = model_quality(
                model, model.n_images, model.span_minutes,
            )
            log.info(f"CalibrationService loaded model: {model}, "
                     f"quality={self._quality}")

    def set_model(self, model: FisheyeModel) -> None:
        """
        Inject a model directly (e.g. after the user clicks Calibrate Now).
        Resets the frame buffer so accumulation starts fresh.
        """
        self._model = model
        new_q = model_quality(model, model.n_images, model.span_minutes)
        with self._lock:
            self._frames.clear()
        if new_q != self._quality:
            self._quality = new_q
            self.quality_upgraded.emit(self._quality, model)

    def feed_frame(
        self,
        image,
        dt: datetime,
        lat: float,
        lon: float,
    ) -> None:
        """
        Accept a new frame for calibration accumulation.

        Thread-safe.  Called from the image-processor worker thread.
        Detects stars inline (~50 ms), stores only detection data.
        """
        if lat == 0.0 and lon == 0.0:
            return

        # ------ No model yet: queue for initial single-image cal ------
        if self._model is None:
            # Cooldown guard: initial cal is expensive (~30s on slow hardware)
            # and failures on a dense star field with no good fit will spin
            # the CPU if retried every frame. Back off on repeated failures.
            now = time.monotonic()
            if now - self._last_initial_attempt_time < INITIAL_COOLDOWN_S:
                return
            if self._pending_initial is None and (
                self._initial_worker is None
                or not self._initial_worker.isRunning()
            ):
                self._pending_initial = (image.copy(), dt, lat, lon)
                self._check_refine.emit()
            return

        # ------ Normal path: detect stars, store frame data -----------
        frame = self._detect_frame(image, dt, lat, lon)
        if frame is None:
            return

        with self._lock:
            self._frames.append(frame)
            if len(self._frames) > MAX_BUFFER:
                self._frames.pop(0)

        self._check_refine.emit()

    def shutdown(self) -> None:
        """Stop any running workers cleanly."""
        for w in (self._refine_worker, self._initial_worker):
            if w and w.isRunning():
                w.quit()
                w.wait(3000)

    @property
    def current_quality(self) -> str:
        return self._quality

    @property
    def current_model(self) -> Optional[FisheyeModel]:
        return self._model

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    # ------------------------------------------------------------------
    # Frame detection (runs on caller's thread — image-processor worker)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_frame(image, dt, lat, lon) -> Optional[dict]:
        """Detect stars and compute catalog AltAz for one frame."""
        try:
            sky_cx, sky_cy, sky_r = estimate_sky_circle(image)
            detected = detect_stars(
                image, max_stars=200,
                sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r,
            )
            if len(detected) < 5:
                log.debug(f"CalibrationService: {len(detected)} stars — "
                          "too few, skipping frame")
                return None

            catalog = get_bright_stars(max_mag=6.5)
            above_horizon = []
            for s in catalog:
                alt, az = radec_to_altaz(
                    s['ra_deg'], s['dec_deg'], lat, lon, dt,
                )
                if float(alt) > 3.0:
                    above_horizon.append((s, float(alt), float(az)))
            above_horizon.sort(key=lambda x: x[0]['vmag'])

            return {
                'dt': dt,
                'detected': detected,
                'above_horizon': above_horizon,
                'sky_cx': sky_cx,
                'sky_cy': sky_cy,
                'sky_r': sky_r,
            }
        except Exception as e:
            log.debug(f"CalibrationService frame detection failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Refinement triggering (runs on main thread via _check_refine)
    # ------------------------------------------------------------------

    def _maybe_refine(self) -> None:
        """Check thresholds and start the appropriate worker."""
        # --- Handle pending initial calibration ---
        if self._model is None and self._pending_initial is not None:
            self._start_initial_cal()
            return

        if self._model is None:
            return

        # --- Guard: worker already running ---
        if self._refine_worker and self._refine_worker.isRunning():
            return
        if self._initial_worker and self._initial_worker.isRunning():
            return

        # --- Cooldown ---
        now = time.monotonic()
        if now - self._last_refine_time < REFINE_COOLDOWN_S:
            return

        # --- Threshold checks ---
        with self._lock:
            n = len(self._frames)
            if n < MIN_FRAMES:
                return

            dts = [f['dt'] for f in self._frames]
            span_s = (max(dts) - min(dts)).total_seconds()
            span_min = span_s / 60.0
            if span_min < MIN_SPAN_MINUTES:
                return

            frames_copy = copy.deepcopy(self._frames)

        log.info(f"CalibrationService: triggering refinement "
                 f"({n} frames, {span_min:.1f} min span)")
        self.status_changed.emit(f"Refining calibration ({n} frames)\u2026")
        self._last_refine_time = now

        self._refine_worker = _RefineWorker(
            frames_copy, self._model, parent=self,
        )
        self._refine_worker.finished.connect(self._on_refine_done)
        self._refine_worker.failed.connect(self._on_refine_failed)
        self._refine_worker.start()

    # ------------------------------------------------------------------
    # Initial single-image calibration
    # ------------------------------------------------------------------

    def _start_initial_cal(self) -> None:
        if self._initial_worker and self._initial_worker.isRunning():
            return

        image, dt, lat, lon = self._pending_initial
        self._last_initial_attempt_time = time.monotonic()
        log.info("CalibrationService: starting initial single-image calibration")
        self.status_changed.emit("Auto-calibrating\u2026")

        self._initial_worker = _InitialCalWorker(
            image, lat, lon, dt, parent=self,
        )
        self._initial_worker.finished.connect(self._on_initial_done)
        self._initial_worker.failed.connect(self._on_initial_failed)
        self._initial_worker.start()

    def _on_initial_done(self, model: FisheyeModel) -> None:
        self._pending_initial = None
        self._model = model
        model.n_images = 1
        model.span_minutes = 0.0

        self._quality = model_quality(model, 1, 0.0)
        self._save_model(model)

        log.info(f"Initial calibration succeeded: {model}, "
                 f"quality={self._quality}")
        self.status_changed.emit(
            f"Calibrated: {model.n_matches} stars, "
            f"RMS={model.rms_residual:.1f}px ({self._quality})"
        )
        self.quality_upgraded.emit(self._quality, model)

    def _on_initial_failed(self, error: str) -> None:
        self._pending_initial = None
        log.warning(
            f"Initial auto-calibration failed: {error}. "
            f"Next attempt allowed in {INITIAL_COOLDOWN_S}s."
        )
        # Short message for the panel — full detail stays in the log.
        self.status_changed.emit(
            f"Auto-calibration failed — will retry in {INITIAL_COOLDOWN_S // 60} min. (See logs)"
        )

    # ------------------------------------------------------------------
    # Multi-image refinement results
    # ------------------------------------------------------------------

    def _on_refine_done(self, model: FisheyeModel) -> None:
        with self._lock:
            n_images = len(self._frames)
            dts = [f['dt'] for f in self._frames]
            span_min = (
                (max(dts) - min(dts)).total_seconds() / 60.0
                if dts else 0.0
            )

        model.n_images = n_images
        model.span_minutes = round(span_min, 1)

        new_q = model_quality(model, n_images, span_min)
        improved = (
            CalibrationQuality.rank(new_q) > CalibrationQuality.rank(self._quality)
            or (
                model.rms_residual < self._model.rms_residual
                and model.n_matches >= self._model.n_matches
            )
        )

        if improved:
            self._model = model
            self._quality = new_q
            self._save_model(model)

            log.info(f"Calibration refined: {model}, quality={new_q}")
            self.status_changed.emit(
                f"Refined: {model.n_matches} stars, "
                f"RMS={model.rms_residual:.1f}px ({new_q})"
            )
            self.quality_upgraded.emit(new_q, model)
        else:
            log.info(
                f"Refinement did not improve model "
                f"(RMS={model.rms_residual:.2f}px vs "
                f"{self._model.rms_residual:.2f}px)"
            )
            self.status_changed.emit(
                f"Calibrated: {self._model.n_matches} stars, "
                f"RMS={self._model.rms_residual:.1f}px ({self._quality})"
            )

    def _on_refine_failed(self, error: str) -> None:
        log.warning(f"Calibration refinement failed: {error}")
        # Restore previous status text
        if self._model:
            self.status_changed.emit(
                f"Calibrated: {self._model.n_matches} stars, "
                f"RMS={self._model.rms_residual:.1f}px ({self._quality})"
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _save_model(model: FisheyeModel) -> None:
        """Save model to the production calibration file."""
        try:
            from app_config import APP_DATA_FOLDER
            cal_dir = os.path.join(
                os.getenv('LOCALAPPDATA', ''), APP_DATA_FOLDER,
            )
            os.makedirs(cal_dir, exist_ok=True)
            cal_path = os.path.join(cal_dir, 'allsky_calibration.json')
            model.calibrated_at = datetime.now(timezone.utc).isoformat()
            model.save(cal_path)
            log.info(f"Calibration saved to {cal_path}")
        except Exception as e:
            log.error(f"Failed to save calibration: {e}")

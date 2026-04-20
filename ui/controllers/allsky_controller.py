"""
All-Sky Overlay Controller.

Manages:
  - Calibration trigger (runs in background QThread)
  - Background calibration accumulation service
  - Config save/load for allsky_overlay section
  - Signals to panel for status updates
"""
import os
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, QThread, QTimer

from services.logger import app_logger as log

if TYPE_CHECKING:
    from ui.main_window import MainWindow


def _short_cal_error(error_msg: str) -> str:
    """Summarise a calibration error into a one-line UI status.

    The raw CalibrationError messages embed diagnostic detail (per-star
    pixel misses, fallback chaining) that's useful in the log but too
    noisy for the small status label on the AllSky panel.
    """
    lower = error_msg.lower()
    if 'bright-anchor' in lower or 'bright anchors' in lower:
        return ("Calibration rejected — star alignment was off. "
                "Try again on a clearer night or check lat/lon. (See logs)")
    if 'triangle match' in lower and 'failed' in lower:
        return "Calibration failed — couldn't match star patterns. (See logs)"
    if 'need' in lower and 'star' in lower:
        return "Calibration failed — not enough stars detected. (See logs)"
    if 'scipy' in lower:
        return "Calibration failed — internal dependency error. (See logs)"
    # Generic fallback: first ~100 chars, trimmed at a sensible break
    short = error_msg.split('.')[0].split(';')[0].split('(')[0].strip()
    if len(short) > 120:
        short = short[:117] + '…'
    return f"Calibration failed — {short}. (See logs)"


class CalibrationWorker(QThread):
    """Background thread: detect stars, match, fit fisheye model."""

    progress = Signal(str)       # Status message
    finished = Signal(object)    # FisheyeModel on success
    failed   = Signal(str)       # Error message on failure

    def __init__(self, image, lat: float, lon: float, dt, parent=None):
        super().__init__(parent)
        self._image = image
        self._lat = lat
        self._lon = lon
        self._dt = dt

    def run(self):
        try:
            from services.allsky.calibration import calibrate, CalibrationError
            self.progress.emit("Detecting stars…")
            model = calibrate(
                self._image,
                lat_deg=self._lat,
                lon_deg=self._lon,
                dt=self._dt,
            )
            self.finished.emit(model)
        except Exception as e:
            self.failed.emit(str(e))


class AllSkyController(QObject):
    """
    Business logic for the All-Sky Settings panel.

    Signals:
        status_changed(str): Human-readable calibration status message.
        calibration_done(dict): Emitted with model_info after successful calibration.
        settings_changed(): Emitted when any setting is changed (triggers config save).
    """

    status_changed   = Signal(str)
    quality_changed  = Signal(str)   # CalibrationQuality level string
    calibration_done = Signal(dict)
    settings_changed = Signal()

    def __init__(self, main_window: 'MainWindow', parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._worker: Optional[CalibrationWorker] = None
        self._model = None

        # Background calibration accumulation service
        from services.allsky.calibration_service import CalibrationService
        self._cal_service = CalibrationService(parent=self)
        self._cal_service.quality_upgraded.connect(self._on_quality_upgraded)
        self._cal_service.status_changed.connect(self.status_changed)

        # Load existing model into both controller and service
        self._update_status()

    # ------------------------------------------------------------------
    # Public API (called by panel)
    # ------------------------------------------------------------------

    def load_from_config(self) -> None:
        """Refresh panel state from current config."""
        self._update_status()

    def start_calibration(self, image=None) -> None:
        """
        Begin background calibration.

        If image is None, uses the most recent raw frame cached by
        MainWindow (set for both Watch mode and Camera mode).
        """
        log.info("Calibrate Now clicked")
        if self._worker and self._worker.isRunning():
            log.warning("Calibrate Now ignored — calibration already in progress")
            self.status_changed.emit("Calibration already in progress…")
            return

        if image is None:
            image, source = self._get_latest_frame()
            if image is not None:
                log.info(f"Calibrate Now using image from {source}")

        if image is None:
            log.warning(
                "Calibrate Now: no image available. Start capture (Camera mode) "
                "or wait for the watcher to process a frame (Watch mode)."
            )
            self.status_changed.emit("No image available — start capture first.")
            return

        lat = float(self._mw.config.get('weather', {}).get('latitude', 0) or 0)
        lon = float(self._mw.config.get('weather', {}).get('longitude', 0) or 0)
        dt = datetime.now(timezone.utc)

        if lat == 0.0 and lon == 0.0:
            log.warning("Calibrate Now: lat/lon not configured (both zero)")
            self.status_changed.emit(
                "Warning: lat/lon not configured. Set in Output > Weather Settings."
            )

        log.info(f"Calibrate Now starting worker (lat={lat}, lon={lon}, dt={dt.isoformat()})")
        self.status_changed.emit("Calibrating… detecting stars")
        self._worker = CalibrationWorker(image, lat, lon, dt, parent=self)
        self._worker.progress.connect(self.status_changed)
        self._worker.finished.connect(self._on_calibration_done)
        self._worker.failed.connect(self._on_calibration_failed)
        self._worker.start()

    @property
    def calibration_service(self):
        """The background calibration accumulation service."""
        return self._cal_service

    def get_calibration_info(self) -> Optional[dict]:
        """Return a summary dict of the current calibration model."""
        if self._model is None:
            return None
        return {
            'rms_residual': self._model.rms_residual,
            'n_matches': self._model.n_matches,
            'calibrated_at': self._model.calibrated_at,
            'a1': self._model.a1,
            'cx': self._model.cx,
            'cy': self._model.cy,
        }

    def shutdown(self) -> None:
        """Stop any running calibration threads and background service."""
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(3000)
        self._cal_service.shutdown()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_calibration_done(self, model) -> None:
        from services.app_config import APP_DATA_FOLDER
        import os

        self._model = model
        info = self.get_calibration_info()

        # Persist model JSON
        cal_dir = os.path.join(
            os.getenv('LOCALAPPDATA', ''), APP_DATA_FOLDER
        )
        os.makedirs(cal_dir, exist_ok=True)
        cal_path = os.path.join(cal_dir, 'allsky_calibration.json')
        try:
            model.save(cal_path)
            # Update config with new calibration path
            allsky_cfg = dict(self._mw.config.get('allsky_overlay', {}))
            allsky_cfg['calibration_file'] = cal_path
            self._mw.config.set('allsky_overlay', allsky_cfg)
            self._mw.config.save()
        except Exception as e:
            log.error(f"Failed to save calibration: {e}")

        # Notify the background service so it uses this model as its seed
        # and resets its frame buffer for fresh accumulation.
        self._cal_service.set_model(model)

        from services.allsky.calibration_service import model_quality
        quality = model_quality(model, model.n_images, model.span_minutes)

        msg = (f"Calibrated: {model.n_matches} stars, "
               f"RMS={model.rms_residual:.2f}px ({quality})")
        self.status_changed.emit(msg)
        self.quality_changed.emit(quality)
        self.calibration_done.emit(info)
        self.settings_changed.emit()

        # Discord notification
        self._notify_discord(info)

    def _on_calibration_failed(self, error_msg: str) -> None:
        log.warning(f"All-sky calibration failed: {error_msg}")
        self.status_changed.emit(_short_cal_error(error_msg))

    def _update_status(self) -> None:
        """Load existing model and emit current status."""
        cal_path = self._mw.config.get('allsky_overlay', {}).get('calibration_file', '')
        if cal_path:
            from services.allsky.fisheye import FisheyeModel
            model = FisheyeModel.try_load(cal_path)
            if model and model.is_valid():
                self._model = model
                # Seed the background service with the existing model
                self._cal_service.load_model(cal_path)
                from services.allsky.calibration_service import model_quality
                quality = model_quality(
                    model, model.n_images, model.span_minutes,
                )
                ts = model.calibrated_at[:10] if model.calibrated_at else 'unknown date'
                self.status_changed.emit(
                    f"Calibrated ({ts}): {model.n_matches} stars, "
                    f"RMS={model.rms_residual:.2f}px ({quality})"
                )
                self.quality_changed.emit(quality)
                return
        self.status_changed.emit("Not calibrated — click 'Calibrate Now'")
        self.quality_changed.emit('none')

    def _on_quality_upgraded(self, quality: str, model) -> None:
        """Handle quality upgrade from the background service."""
        self._model = model
        info = self.get_calibration_info()
        # Update config path (service already saved the file)
        from services.app_config import APP_DATA_FOLDER
        cal_path = os.path.join(
            os.getenv('LOCALAPPDATA', ''), APP_DATA_FOLDER,
            'allsky_calibration.json',
        )
        allsky_cfg = dict(self._mw.config.get('allsky_overlay', {}))
        allsky_cfg['calibration_file'] = cal_path
        self._mw.config.set('allsky_overlay', allsky_cfg)
        self._mw.config.save()

        self.quality_changed.emit(quality)
        self.calibration_done.emit(info)
        self.settings_changed.emit()

    def _get_latest_frame(self):
        """Return (image, source_description) for the most recent frame.

        Tries, in order:
          1. MainWindow._cached_raw_image — set for BOTH Watch and Camera
             modes whenever a raw frame arrives. Primary source.
          2. CameraController._last_frame — legacy fallback (may not exist).
          3. MainWindow.last_processed_image — path on disk to the last
             saved output image. Loaded as a PIL image.

        Returns (None, '') if nothing is available.
        """
        cached = getattr(self._mw, '_cached_raw_image', None)
        if cached is not None:
            return cached, "cached raw frame"

        try:
            from ui.controllers.camera_controller import CameraController
            for child in self._mw.children():
                if isinstance(child, CameraController):
                    frame = getattr(child, '_last_frame', None)
                    if frame is not None:
                        return frame, "camera controller"
        except Exception as e:
            log.debug(f"Camera controller probe failed: {e}")

        output_path = getattr(self._mw, 'last_processed_image', None)
        if output_path and os.path.isfile(output_path):
            try:
                from PIL import Image as PILImage
                return PILImage.open(output_path).copy(), f"disk ({output_path})"
            except Exception as e:
                log.debug(f"Could not load last processed image: {e}")

        return None, ""

    def _notify_discord(self, info: dict) -> None:
        try:
            discord_cfg = self._mw.config.get('discord', {})
            if discord_cfg.get('post_calibration', False):
                from services.discord_alerts import DiscordAlertsService
                # Find existing service instance
                for child in self._mw.children():
                    if hasattr(child, 'send_calibration_complete'):
                        child.send_calibration_complete(info)
                        break
        except Exception as e:
            log.debug(f"Discord calibration notify failed: {e}")

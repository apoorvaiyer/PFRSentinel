"""
Timelapse Controller
Owns the TimelapseWriter, wires it to the image processing pipeline,
and exposes status to the UI panel.
"""
from PySide6.QtCore import QObject, Signal, QTimer
from services.timelapse_writer import TimelapseWriter
from services.logger import app_logger


class TimelapseController(QObject):
    """
    Thin controller between the image processor and TimelapseWriter.

    Responsibilities:
    - Own the TimelapseWriter instance
    - Receive timelapse_ready signal from ImageProcessor
    - Pick clean or overlaid frame based on include_overlays config
    - Emit status updates for the panel to display
    """

    status_updated = Signal(dict)  # Emits get_status() dict periodically

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._writer = TimelapseWriter()

        # Status timer: update panel every 5 seconds while recording
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._emit_status)
        self._status_timer.start()

    # ------------------------------------------------------------------ #
    #  Frame handling (connected to image_processor.timelapse_ready)      #
    # ------------------------------------------------------------------ #

    def on_timelapse_ready(self, clean_image, overlaid_image):
        """
        Called by ImageProcessor.timelapse_ready signal on every processed frame.
        Picks the right image version and delegates to TimelapseWriter.
        """
        cfg = self._get_timelapse_config()
        if not cfg.get('enabled', False):
            return

        self._writer.configure(cfg)
        frame = overlaid_image if cfg.get('include_overlays', False) else clean_image
        self._writer.add_frame(frame)

    # ------------------------------------------------------------------ #
    #  Lifecycle (called by main_window on capture start/stop)            #
    # ------------------------------------------------------------------ #

    def on_capture_stopped(self):
        """Stop the active session when capture is stopped."""
        self._writer.stop()
        self._emit_status()

    def shutdown(self):
        """Graceful shutdown — close any active session."""
        self._writer.stop()

    # ------------------------------------------------------------------ #
    #  Status                                                              #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        return self._writer.get_status()

    def _emit_status(self):
        self.status_updated.emit(self._writer.get_status())

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _get_timelapse_config(self) -> dict:
        return self._main_window.config.get('timelapse', {})

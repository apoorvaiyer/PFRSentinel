"""
Timelapse Controller
Owns the TimelapseWriter, wires it to the image processing pipeline,
and exposes status to the UI panel.
"""
import threading
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
        self._writer.on_session_finished = self._on_session_finished

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

        # Inject current roof state for roof-gated window mode
        if cfg.get('window_mode') == 'roof':
            ml_results = getattr(self._main_window, 'last_ml_results', None) or {}
            cfg['roof_open'] = ml_results.get('roof_status') == 'Open'

        self._writer.configure(cfg)
        frame = overlaid_image if cfg.get('include_overlays', False) else clean_image
        self._writer.add_frame(frame)

    # ------------------------------------------------------------------ #
    #  Lifecycle (called by main_window on capture start/stop)            #
    # ------------------------------------------------------------------ #

    def on_capture_stopped(self):
        """Stop the active session when capture is stopped."""
        app_logger.info("Timelapse: capture stopped, closing active session")
        self._writer.stop()
        self._emit_status()

    def shutdown(self):
        """Graceful shutdown — close any active session."""
        app_logger.debug("Timelapse: controller shutdown")
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

    def _on_session_finished(self, path: str, frame_count: int, elapsed_seconds: int):
        """
        Called by TimelapseWriter after each session finalizes.
        Posts to Discord in a daemon thread so it doesn't block the caller.
        """
        mins, secs = divmod(elapsed_seconds, 60)
        app_logger.info(
            f"Timelapse: session finished — {frame_count} frames  {mins}m{secs:02d}s"
        )

        discord_cfg = self._main_window.config.get('discord', {})
        if not discord_cfg.get('enabled', False) or not discord_cfg.get('post_timelapse', False):
            return

        app_logger.info("Timelapse: posting completed video to Discord")

        def _post():
            try:
                from services.discord_alerts import DiscordAlerts
                alerts = DiscordAlerts(self._main_window.config)
                alerts.send_timelapse_completed(path, frame_count, elapsed_seconds)
                app_logger.info("Timelapse: Discord post sent")
            except Exception as e:
                app_logger.error(f"Timelapse: Discord post failed: {e}")

        threading.Thread(target=_post, daemon=True).start()

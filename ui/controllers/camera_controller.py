"""
Camera Controller for Qt UI
Adapter between PySide6 UI and existing ZWO camera service.

Uses ZWOCamera.start_capture() with callbacks - NO reimplementation of capture logic.
All auto-exposure, calibration, scheduled windows, etc. are handled by ZWOCamera.
"""
from PySide6.QtCore import QObject, QTimer, Signal
from datetime import datetime
import os
import sys
import threading
import time

from services.logger import app_logger
from services.camera import ZWOCamera
from .camera_settings import apply_camera_settings


# ZWO SDK errors that corrupt the DLL for the process lifetime — only a
# USB reset or app restart recovers.
_UNRECOVERABLE_ERROR_PATTERNS = (
    "access violation",
    "0xe06d7363",
    "winerror -529697949",
    "exception: exception",
)

_DISCORD_ERROR_SUPPRESS_AFTER_ATTEMPTS = 3
_WEDGED_THREAD_JOIN_TIMEOUT_SEC = 3.0
_SUSTAINED_CAPTURE_RESET_SEC = 300
# Max consecutive recovery firings that skip because the old capture thread
# is still blocked in the ZWO SDK. After this, the SDK is deemed unrecoverable
# and the user is asked to restart. Windows USB IO usually times out in
# 30–60s, so 6 × 30s delays covers the pathological case.
_MAX_WEDGED_SKIPS = 6


class CameraControllerQt(QObject):
    """
    Qt-compatible camera controller.
    
    Uses existing ZWOCamera.start_capture() with callbacks.
    All capture logic (auto-exposure, calibration, etc.) is handled by ZWOCamera.
    """
    
    cameras_detected = Signal(list)  # List of camera names
    capture_started = Signal()
    capture_stopped = Signal()
    frame_ready = Signal(object, dict)  # PIL Image, metadata
    error = Signal(str)
    calibration_status = Signal(bool)  # True=calibrating, False=complete
    # Queued cross-thread completion signals — QTimer.singleShot from a
    # non-Qt worker thread silently never fires (log 2026-04-20 08:03).
    _usb_reset_done = Signal(bool)             # recovery path: ok?
    camera_revive_done = Signal(bool, str)     # user Revive: (ok, name)
    _capture_start_done = Signal(bool, str)    # worker result: (ok, error_msg)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = main_window.config
        
        self.zwo_camera = None
        self.is_connected = False
        self.is_capturing = False

        # 24/7 rigs: without auto-recovery a single SDK wedge ends captures
        # for the night. See _schedule_auto_recovery.
        self._user_requested_stop = False
        self._auto_recovery_attempts = 0
        self._auto_recovery_schedule = [30, 120, 300, 900, 1800]
        self._auto_recovery_timer: QTimer | None = None
        self._last_successful_frame_ts = 0.0

        # Held across a fatal error so the capture thread can be joined
        # before the next recovery attempt; concurrent SDK calls from a
        # still-alive thread and a new reinit corrupt the ZWO DLL.
        self._dying_camera = None
        self._unrecoverable_mode = False
        self._usb_reset_attempted = False
        self._usb_reset_in_progress = False
        self._suppress_discord_errors = False
        # Count of consecutive recovery attempts skipped because the dying
        # capture thread is still wedged inside the SDK.
        self._wedged_skip_count = 0

        self._capture_starting = False
        self._usb_reset_done.connect(self._on_usb_reset_done)
        self._capture_start_done.connect(self._on_capture_start_done)

    def detect_cameras(self):
        """Detect connected ZWO cameras"""
        app_logger.info("Detecting cameras...")
        
        sdk_path = self.config.get('zwo_sdk_path', '')
        
        if not sdk_path or not os.path.exists(sdk_path):
            self.error.emit("SDK path not found")
            return
        
        try:
            import zwoasi as asi
            
            try:
                asi.init(sdk_path)
            except Exception as e:
                if "already" not in str(e).lower():
                    self.error.emit(f"SDK init failed: {e}")
                    return
            
            num_cameras = asi.get_num_cameras()
            
            if num_cameras == 0:
                self.cameras_detected.emit([])
                return
            
            camera_list = []
            for i in range(num_cameras):
                try:
                    name = asi.list_cameras()[i]
                    camera_list.append(f"{name} (Index: {i})")
                except Exception as e:
                    # Skip cameras that fail to enumerate - they may be phantom devices
                    # or cameras in a bad state that can't be used anyway
                    app_logger.warning(f"Camera {i} failed to enumerate: {e} - skipping")
            
            self.cameras_detected.emit(camera_list)
            app_logger.info(f"Detected {len(camera_list)} camera(s)")
            
        except Exception as e:
            self.error.emit(f"Detection failed: {e}")
            app_logger.error(f"Camera detection failed: {e}")
    
    def _resolve_camera_index(self, sdk_path: str, camera_name: str, saved_index: int) -> int:
        """Re-detect cameras and resolve the correct index by name.

        Camera indices shift when other USB cameras (NINA, guide cam, etc.)
        come online or go offline.  This method always does a fresh SDK
        enumeration so we never send the wrong camera's config to a device.

        Returns the resolved camera index.
        Raises Exception if the target camera is not found.
        """
        import zwoasi as asi

        if not sdk_path or not os.path.exists(sdk_path):
            raise Exception("SDK path not configured or not found.")

        try:
            asi.init(sdk_path)
        except Exception as e:
            if "already" not in str(e).lower():
                raise Exception(f"SDK init failed: {e}")

        num_cameras = asi.get_num_cameras()
        if num_cameras == 0:
            raise Exception("No ZWO cameras detected. Check USB connections.")

        # Build fresh camera list — wrap list_cameras() in a timeout so a
        # wedged camera in a bad USB state can't hang the capture-start worker.
        _names = [None]
        _exc = [None]

        def _list():
            try:
                _names[0] = list(asi.list_cameras())
            except Exception as _e:
                _exc[0] = _e

        _t = threading.Thread(target=_list, daemon=True)
        _t.start()
        _t.join(8.0)
        if _t.is_alive():
            raise Exception(
                "SDK list_cameras() timed out — camera may be in a bad USB state. "
                "Try the Revive button."
            )
        if _exc[0]:
            raise _exc[0]
        fresh_cameras = list(enumerate(_names[0] or []))

        if not fresh_cameras:
            raise Exception("No ZWO cameras could be enumerated.")

        # Clean the saved name (strip " (Index: N)" suffix if present)
        clean_name = camera_name
        if '(Index:' in camera_name:
            clean_name = camera_name.split('(Index:')[0].strip()

        # If name is missing/default ('Unknown', empty), fall back to first camera
        if not clean_name or clean_name == 'Unknown':
            idx, name = fresh_cameras[0]
            app_logger.warning(
                f"No saved camera name — auto-selecting first camera: '{name}' at index {idx}"
            )
            self.config.set('zwo_selected_camera', idx)
            self.config.set('zwo_selected_camera_name', name)
            self.config.save()
            camera_list = [f"{n} (Index: {i})" for i, n in fresh_cameras]
            self.config.set('available_cameras', camera_list)
            return idx

        # Try exact match by name
        for idx, name in fresh_cameras:
            if clean_name in name:
                if idx != saved_index:
                    app_logger.warning(
                        f"Camera index changed: '{clean_name}' moved from "
                        f"index {saved_index} → {idx}  (other cameras may have come online)"
                    )
                    # Persist the corrected index so config stays current
                    self.config.set('zwo_selected_camera', idx)
                    self.config.save()
                else:
                    app_logger.info(f"Camera '{clean_name}' confirmed at index {idx}")

                # Update the available_cameras list in config to reflect reality
                camera_list = [f"{n} (Index: {i})" for i, n in fresh_cameras]
                self.config.set('available_cameras', camera_list)
                return idx

        # Camera not found at all — list what IS available for diagnostics
        available = ", ".join(f"[{i}] {n}" for i, n in fresh_cameras)
        raise Exception(
            f"Camera '{clean_name}' not found. "
            f"Available cameras: {available}. "
            f"Please click 'Detect Cameras' and select the correct camera."
        )

    def start_capture(self):
        """Start camera capture using ZWOCamera's built-in capture loop"""
        if self.is_capturing or self._capture_starting:
            return

        self._cancel_auto_recovery_timer()
        self._user_requested_stop = False

        if self.zwo_camera is not None:
            app_logger.info("Cleaning up existing camera instance...")
            try:
                if self.is_connected:
                    self.zwo_camera.disconnect()
                self.zwo_camera = None
            except Exception as e:
                app_logger.warning(f"Error cleaning up old camera instance: {e}")
                self.zwo_camera = None

        # Read all config on the main thread — keeps cross-thread Config access
        # out of the blocking SDK calls that run in the worker thread.
        sdk_path = self.config.get('zwo_sdk_path', '')
        saved_camera_index = self.config.get('zwo_selected_camera', 0)
        camera_name = self.config.get('zwo_selected_camera_name', 'Unknown')

        app_logger.info(f"Starting capture — saved camera: '{camera_name}' at index {saved_camera_index}")

        clean_camera_name = camera_name
        if '(Index:' in camera_name:
            clean_camera_name = camera_name.split('(Index:')[0].strip()

        from services.config import DEFAULT_CAMERA_PROFILE
        profile = self.config.get_camera_profile(clean_camera_name)
        app_logger.info(f"Loading settings from camera profile: {clean_camera_name}")
        app_logger.debug(f"Profile contents: {profile}")

        wb_config = dict(self.config.get('white_balance', {}))
        wb_config.setdefault('mode', 'asi_auto')
        dev_mode = self.config.get('dev_mode', {})

        params = {
            'sdk_path': sdk_path,
            'saved_camera_index': saved_camera_index,
            'camera_name': camera_name,
            'clean_camera_name': clean_camera_name,
            'exposure_ms': profile.get('exposure_ms', DEFAULT_CAMERA_PROFILE['exposure_ms']),
            'gain': profile.get('gain', DEFAULT_CAMERA_PROFILE['gain']),
            'max_exposure_ms': profile.get('max_exposure_ms', DEFAULT_CAMERA_PROFILE['max_exposure_ms']),
            'target_brightness': profile.get('target_brightness', DEFAULT_CAMERA_PROFILE['target_brightness']),
            'wb_r': profile.get('wb_r', DEFAULT_CAMERA_PROFILE['wb_r']),
            'wb_b': profile.get('wb_b', DEFAULT_CAMERA_PROFILE['wb_b']),
            'offset': profile.get('offset', DEFAULT_CAMERA_PROFILE['offset']),
            'flip': profile.get('flip', DEFAULT_CAMERA_PROFILE['flip']),
            'bayer_pattern': profile.get('bayer_pattern', DEFAULT_CAMERA_PROFILE['bayer_pattern']),
            'auto_exposure': self.config.get('zwo_auto_exposure', False),
            'wb_config': wb_config,
            'capture_interval': self.config.get('zwo_interval', 5.0),
            'scheduled_capture_mode': self.config.get('scheduled_capture_mode', 'always'),
            'scheduled_start_time': self.config.get('scheduled_start_time', '17:00'),
            'scheduled_end_time': self.config.get('scheduled_end_time', '09:00'),
            'scheduled_window_interval': self.config.get('scheduled_window_interval', 5.0),
            'use_raw16': dev_mode.get('use_raw16', False),
        }

        app_logger.info(
            f"Camera config: exposure_ms={params['exposure_ms']}, gain={params['gain']}, "
            f"auto_exposure={params['auto_exposure']}, max_exposure_ms={params['max_exposure_ms']}"
        )

        self._capture_starting = True
        threading.Thread(target=self._start_capture_worker, args=(params,), daemon=True).start()

    def _start_capture_worker(self, params: dict):
        """Blocking SDK work for start_capture — runs off the Qt main thread.

        _resolve_camera_index, ZWOCamera construction, and connect_camera all
        make synchronous ZWO SDK calls that can hang for 10-30s under USB
        instability. Running them here keeps the Qt event loop responsive.
        """
        try:
            camera_index = self._resolve_camera_index(
                params['sdk_path'], params['camera_name'], params['saved_camera_index']
            )

            cam = ZWOCamera(
                sdk_path=params['sdk_path'],
                camera_index=camera_index,
                exposure_sec=params['exposure_ms'] / 1000.0,
                gain=params['gain'],
                white_balance_r=params['wb_r'],
                white_balance_b=params['wb_b'],
                offset=params['offset'],
                flip=params['flip'],
                auto_exposure=params['auto_exposure'],
                max_exposure_sec=params['max_exposure_ms'] / 1000.0,
                bayer_pattern=params['bayer_pattern'],
                wb_config=params['wb_config'],
                scheduled_capture_mode=params['scheduled_capture_mode'],
                scheduled_start_time=params['scheduled_start_time'],
                scheduled_end_time=params['scheduled_end_time'],
                scheduled_window_interval=params['scheduled_window_interval'],
                camera_name=params['clean_camera_name'],
            )
            cam.target_brightness = params['target_brightness']
            cam.set_capture_interval(params['capture_interval'])
            cam.use_raw16 = params['use_raw16']
            cam.on_error_callback = self._on_camera_error
            cam.on_calibration_callback = self._on_calibration_status

            if not cam.connect_camera(camera_index):
                raise Exception("Failed to connect to camera")

            app_logger.info("Starting capture loop...")
            cam.start_capture(
                on_frame_callback=self._on_frame_captured,
                on_log_callback=lambda msg: app_logger.info(msg),
            )

            # Store before emitting — queued signal ensures main thread sees it.
            self.zwo_camera = cam
            self.is_connected = True
            self._capture_start_done.emit(True, "")

        except Exception as e:
            import traceback
            app_logger.error(f"Failed to start capture: {e}")
            app_logger.debug(f"Stack trace: {traceback.format_exc()}")
            self._capture_start_done.emit(False, str(e))

    def _on_capture_start_done(self, ok: bool, err: str):
        """Handle _start_capture_worker result on the main Qt thread."""
        self._capture_starting = False

        if ok:
            if self._user_requested_stop:
                app_logger.info("Capture connected but stop was requested — tearing down")
                if self.zwo_camera:
                    try:
                        self.zwo_camera.disconnect()
                    except Exception:
                        pass
                    self.zwo_camera = None
                self.is_connected = False
                return
            self.is_capturing = True
            self.capture_started.emit()
            app_logger.info("Camera capture started")
        else:
            self.is_capturing = False
            self.is_connected = False
            self.error.emit(err)
            from services.posthog_service import capture_error
            capture_error(Exception(err), context='camera_start')
            if self._user_requested_stop:
                return
            if self._is_unrecoverable_error(err):
                if not self._usb_reset_attempted:
                    self._usb_reset_attempted = True
                    self._start_usb_reset_worker()
                    return
                self._enter_unrecoverable_mode(err)
                return
            self._schedule_auto_recovery()
    def stop_capture(self):
        """Stop camera capture"""
        if not self.is_capturing:
            return

        self._user_requested_stop = True
        self._cancel_auto_recovery_timer()
        self._unrecoverable_mode = False
        self._usb_reset_attempted = False
        self._suppress_discord_errors = False
        self._wedged_skip_count = 0
        self._dying_camera = None

        try:
            # Update state immediately for responsive UI
            self.is_capturing = False
            self.is_connected = False

            # Capture reference before clearing — the background thread
            # needs the actual object, not self.zwo_camera which we null below
            camera = self.zwo_camera
            self.zwo_camera = None

            if camera:
                # Run stop + disconnect in background to avoid blocking UI.
                # stop_capture() sets is_capturing=False and aborts the exposure,
                # then join()s the capture thread. disconnect_camera() resets the
                # hardware. Both can involve SDK calls that may block.
                import threading
                def shutdown():
                    try:
                        camera.stop_capture()
                    except Exception as e:
                        app_logger.debug(f"Error stopping capture: {e}")
                    try:
                        camera.disconnect_camera()
                    except Exception as e:
                        app_logger.debug(f"Error disconnecting camera: {e}")
                threading.Thread(target=shutdown, daemon=True).start()

            self.capture_stopped.emit()
            app_logger.info("Camera capture stopped")

        except Exception as e:
            app_logger.error(f"Error stopping capture: {e}")
    
    def _on_frame_captured(self, pil_image, metadata):
        """Callback from ZWOCamera when a frame is captured.
        
        This is called from the ZWOCamera's capture thread.
        We emit a Qt signal to safely update the UI.
        """
        # Add UI-specific metadata fields
        if metadata is None:
            metadata = {}
        metadata['filename'] = f"capture_{datetime.now().strftime('%H%M%S')}.jpg"
        metadata['timestamp'] = datetime.now().strftime('%H:%M:%S')
        
        # Emit signal (thread-safe way to update Qt UI — AutoConnection
        # marshals to the main thread because this runs on the capture thread).
        self.frame_ready.emit(pil_image, metadata)

        # Reset the retry budget after a sustained stream — otherwise a rig
        # that wedges once a day eventually exhausts attempts despite every
        # recovery succeeding.
        now = time.time()
        if self._auto_recovery_attempts and self._last_successful_frame_ts:
            if now - self._last_successful_frame_ts > _SUSTAINED_CAPTURE_RESET_SEC:
                app_logger.info("Sustained capture stream — resetting auto-recovery counter")
                self._auto_recovery_attempts = 0
                self._suppress_discord_errors = False
                self._usb_reset_attempted = False
        self._last_successful_frame_ts = now
    
    def _on_camera_error(self, error_msg, is_fatal: bool = False):
        """Callback from ZWOCamera on errors.

        Args:
            error_msg: Human-readable error description.
            is_fatal: True when the capture loop has terminated and cannot
                recover on its own. In that case we must drop our own
                is_capturing flag and emit capture_stopped so the UI (AppBar,
                tray menu) doesn't keep pretending capture is running.
        """
        app_logger.error(f"Camera error: {error_msg}")
        self.error.emit(error_msg)

        if is_fatal:
            app_logger.error("Camera error is fatal — tearing down capture state for UI sync")
            # Mirror stop_capture()'s state reset, but without touching the
            # camera (the loop already exited and cleanup ran).
            self.is_capturing = False
            self.is_connected = False
            if self.zwo_camera is not None:
                self._dying_camera = self.zwo_camera
            self.zwo_camera = None
            self.capture_stopped.emit()
            if not self._user_requested_stop:
                self._schedule_auto_recovery()

    def _schedule_auto_recovery(self):
        if self._unrecoverable_mode:
            app_logger.info(
                "Auto-recovery suppressed — in unrecoverable mode, awaiting "
                "manual restart."
            )
            return
        # Schedule clamps at the final interval rather than stopping — on a
        # 24/7 rig, keep trying forever is better than giving up.
        idx = min(self._auto_recovery_attempts, len(self._auto_recovery_schedule) - 1)
        delay_s = self._auto_recovery_schedule[idx]
        self._auto_recovery_attempts += 1

        if (
            not self._suppress_discord_errors
            and self._auto_recovery_attempts > _DISCORD_ERROR_SUPPRESS_AFTER_ATTEMPTS
        ):
            self._suppress_discord_errors = True
            app_logger.warning(
                f"Auto-recovery: reached attempt #{self._auto_recovery_attempts}; "
                "suppressing further Discord error pings until capture resumes."
            )

        app_logger.warning(
            f"Auto-recovery: scheduling capture restart in {delay_s}s "
            f"(attempt #{self._auto_recovery_attempts})"
        )

        self._cancel_auto_recovery_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_auto_recovery_fire)
        timer.start(delay_s * 1000)
        self._auto_recovery_timer = timer

    def _cancel_auto_recovery_timer(self):
        if self._auto_recovery_timer is not None:
            try:
                self._auto_recovery_timer.stop()
            except Exception:
                pass
            self._auto_recovery_timer.deleteLater()
            self._auto_recovery_timer = None

    def _on_auto_recovery_fire(self):
        self._auto_recovery_timer = None

        if self._user_requested_stop:
            app_logger.info("Auto-recovery: user stop requested — cancelled")
            return
        if self.is_capturing:
            app_logger.info("Auto-recovery: capture already running — cancelled")
            return

        if not self._join_or_skip_dying_camera():
            return

        app_logger.info(
            f"Auto-recovery: attempting capture restart "
            f"(attempt #{self._auto_recovery_attempts})"
        )
        try:
            self.start_capture()
        except Exception as e:
            # Safety net: start_capture's except block already re-schedules,
            # but if the exception escaped before that block (e.g. import
            # failure), keep the recovery chain alive.
            app_logger.error(f"Auto-recovery restart raised: {e}")
            self._schedule_auto_recovery()

    def _join_or_skip_dying_camera(self) -> bool:
        """Wait for the previous capture thread to exit; skip recovery if it
        is still wedged.

        Returns True if it's safe to proceed with SDK calls, False if the
        caller must abort this recovery cycle.  Calling the ZWO SDK while
        another thread is blocked inside it crashes the DLL (SEH 0xe06d7363);
        Windows USB IO usually times out within 30–60s, so we prefer to
        re-schedule rather than race.
        """
        dying = self._dying_camera
        if dying is None:
            self._wedged_skip_count = 0
            return True
        try:
            joined = dying.wait_for_capture_thread_exit(
                timeout=_WEDGED_THREAD_JOIN_TIMEOUT_SEC
            )
        except Exception as e:
            app_logger.warning(f"Error while joining dying capture thread: {e}")
            joined = False
        if joined:
            self._dying_camera = None
            self._wedged_skip_count = 0
            return True
        self._wedged_skip_count += 1
        if self._wedged_skip_count >= _MAX_WEDGED_SKIPS:
            app_logger.error(
                f"Previous capture thread still wedged after "
                f"{self._wedged_skip_count} recovery attempts — giving up."
            )
            self._enter_unrecoverable_mode(
                "capture thread stuck inside ZWO SDK; process restart required"
            )
            return False
        app_logger.warning(
            f"Previous capture thread still wedged "
            f"(skip {self._wedged_skip_count}/{_MAX_WEDGED_SKIPS}) — "
            "rescheduling retry to avoid concurrent SDK crash."
        )
        self._schedule_auto_recovery()
        return False

    @staticmethod
    def _is_unrecoverable_error(message: str) -> bool:
        if not message:
            return False
        lowered = message.lower()
        return any(pat in lowered for pat in _UNRECOVERABLE_ERROR_PATTERNS)

    def _run_usb_reset_async(self, camera_name: str, on_done):
        """Spawn a daemon worker that toggles the camera's USB device and
        invokes on_done(success: bool, name: str) when finished.

        Serializes via _usb_reset_in_progress so a user-initiated Revive
        click and a recovery-triggered reset can't toggle the same device
        in parallel — concurrent pnputil operations race.
        """
        from services.camera import clean_camera_name
        name = clean_camera_name(camera_name or '')
        if sys.platform != 'win32' or not name:
            if sys.platform != 'win32':
                app_logger.info("USB reset unavailable: not on Windows.")
            else:
                app_logger.warning("USB reset skipped: no camera name.")
            on_done(False, name)
            return
        if self._usb_reset_in_progress:
            app_logger.warning(
                f"USB reset already in progress — ignoring request for '{name}'"
            )
            on_done(False, name)
            return
        self._usb_reset_in_progress = True

        def worker():
            ok = False
            try:
                from services.usb_reset_win import (
                    disable_enable_zwo_camera_usb, is_usb_reset_available,
                )
                if not is_usb_reset_available():
                    app_logger.warning("USB reset API unavailable.")
                else:
                    ok = bool(disable_enable_zwo_camera_usb(
                        camera_name=name,
                        logger=app_logger.info,
                    ))
                    app_logger.info(
                        f"USB reset {'succeeded' if ok else 'did not complete'} "
                        f"for '{name}'"
                    )
            except Exception as e:
                app_logger.error(f"USB reset raised: {e}")
            finally:
                self._usb_reset_in_progress = False
                on_done(ok, name)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _start_usb_reset_worker(self):
        app_logger.warning(
            "Unrecoverable SDK state — starting USB reset in background worker."
        )
        self._run_usb_reset_async(
            self.config.get('zwo_selected_camera_name', '') or '',
            lambda ok, _name: self._usb_reset_done.emit(ok),
        )

    def _on_usb_reset_done(self, success: bool):
        if success:
            self._schedule_auto_recovery()
            return
        # Admin denial (CM_Disable_DevNode 0x17) is the usual cause. The ZWO
        # DLL stays corrupt for the process lifetime without a successful
        # reset; further retries just re-crash.
        self._enter_unrecoverable_mode(
            "USB reset failed — run the application as Administrator or "
            "restart it to recover"
        )

    def revive_missing_camera(self, camera_name: str):
        """Best-effort user-triggered USB reset. Emits camera_revive_done."""
        app_logger.info(f"User-initiated revive for '{camera_name}'")
        self._run_usb_reset_async(
            camera_name,
            lambda ok, name: self.camera_revive_done.emit(ok, name),
        )

    def _enter_unrecoverable_mode(self, last_error: str):
        self._unrecoverable_mode = True
        self._cancel_auto_recovery_timer()
        self.capture_stopped.emit()
        self.error.emit(
            "Camera unrecoverable — ZWO SDK state is corrupted. "
            "Please restart the application. "
            f"(last error: {last_error})"
        )

    def should_notify_discord(self) -> bool:
        return not self._suppress_discord_errors

    def mark_discord_notified(self):
        """Call after sending a Discord error so the unrecoverable-mode
        one-shot notification silences subsequent per-attempt pings."""
        if self._unrecoverable_mode:
            self._suppress_discord_errors = True
    
    def _on_calibration_status(self, is_calibrating: bool):
        """Callback from ZWOCamera when calibration status changes
        
        Args:
            is_calibrating: True when calibration starts, False when complete
        """
        self.calibration_status.emit(is_calibrating)
    
    def update_settings(self):
        """Update camera settings from config (live update)"""
        if not self.zwo_camera:
            return
        try:
            apply_camera_settings(self.zwo_camera, self.config)
        except Exception as e:
            app_logger.error(f"Failed to update camera settings: {e}")

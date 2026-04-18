"""
Camera Controller for Qt UI
Adapter between PySide6 UI and existing ZWO camera service.

Uses ZWOCamera.start_capture() with callbacks - NO reimplementation of capture logic.
All auto-exposure, calibration, scheduled windows, etc. are handled by ZWOCamera.
"""
from PySide6.QtCore import QObject, Signal
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.logger import app_logger
from services.zwo_camera import ZWOCamera


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
    
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = main_window.config
        
        self.zwo_camera = None
        self.is_connected = False
        self.is_capturing = False
    
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

        # Build fresh camera list
        fresh_cameras = []
        for i in range(num_cameras):
            try:
                fresh_cameras.append((i, asi.list_cameras()[i]))
            except Exception:
                pass

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
        if self.is_capturing:
            return
        
        try:
            sdk_path = self.config.get('zwo_sdk_path', '')
            saved_camera_index = self.config.get('zwo_selected_camera', 0)
            camera_name = self.config.get('zwo_selected_camera_name', 'Unknown')

            app_logger.info(f"Starting capture — saved camera: '{camera_name}' at index {saved_camera_index}")

            # --- Fresh camera detection to resolve correct index by name ---
            # Camera indices can change when other cameras (NINA, guide cam, etc.)
            # come online or go offline. Always re-detect and match by name.
            camera_index = self._resolve_camera_index(sdk_path, camera_name, saved_camera_index)
            
            # ==================== NEW: Load camera-specific profile ====================
            # Extract clean camera name (remove index suffix like "(Index: 1)")
            clean_camera_name = camera_name
            if '(Index:' in camera_name:
                clean_camera_name = camera_name.split('(Index:')[0].strip()
            
            # Get camera profile (creates default if doesn't exist)
            profile = self.config.get_camera_profile(clean_camera_name)
            
            if profile:
                app_logger.info(f"Loading settings from camera profile: {clean_camera_name}")
                app_logger.debug(f"Profile contents: {profile}")

                # Merge profile with global config: profile values take priority,
                # but if a profile value is still at its default and the global
                # config has a different (user-customized) value, use the global.
                # This prevents auto-created profiles from overwriting user settings.
                _defaults = {
                    'exposure_ms': 100.0, 'gain': 100, 'max_exposure_ms': 30000.0,
                    'target_brightness': 100, 'wb_r': 75, 'wb_b': 99,
                    'offset': 20, 'flip': 0, 'bayer_pattern': 'BGGR'
                }
                _global_map = {k: f'zwo_{k}' for k in _defaults}

                def _pick(key):
                    """Pick profile value unless it's at default and global differs."""
                    pval = profile.get(key, _defaults[key])
                    gval = self.config.get(_global_map[key], _defaults[key])
                    if pval == _defaults[key] and gval != _defaults[key]:
                        app_logger.info(
                            f"Profile '{key}' is default ({pval}), "
                            f"using global config value ({gval}) instead"
                        )
                        # Also update the profile so it stays in sync
                        profile[key] = gval
                        return gval
                    return pval

                exposure_ms = _pick('exposure_ms')
                gain = _pick('gain')
                max_exposure_ms = _pick('max_exposure_ms')
                target_brightness = _pick('target_brightness')
                wb_r = _pick('wb_r')
                wb_b = _pick('wb_b')
                offset = _pick('offset')
                flip = _pick('flip')
                bayer_pattern = _pick('bayer_pattern')

                # Save merged profile back so future loads are correct
                self.config.save_camera_profile(clean_camera_name, profile)

                # Sync merged values to global config for UI display
                self.config.set('zwo_exposure_ms', exposure_ms)
                self.config.set('zwo_gain', gain)
                self.config.set('zwo_max_exposure_ms', max_exposure_ms)
                self.config.set('zwo_target_brightness', target_brightness)
                self.config.set('zwo_wb_r', wb_r)
                self.config.set('zwo_wb_b', wb_b)
                self.config.set('zwo_offset', offset)
                self.config.set('zwo_flip', flip)
                self.config.set('zwo_bayer_pattern', bayer_pattern)
            else:
                # Fallback to global settings if profile doesn't exist
                app_logger.warning(f"No profile found for {clean_camera_name}, using global settings")
                exposure_ms = self.config.get('zwo_exposure_ms', 100.0)
                gain = self.config.get('zwo_gain', 100)
                max_exposure_ms = self.config.get('zwo_max_exposure_ms', 30000.0)
                target_brightness = self.config.get('zwo_target_brightness', 100)
                wb_r = self.config.get('zwo_wb_r', 75)
                wb_b = self.config.get('zwo_wb_b', 99)
                offset = self.config.get('zwo_offset', 20)
                flip = self.config.get('zwo_flip', 0)
                bayer_pattern = self.config.get('zwo_bayer_pattern', 'BGGR')
            
            # Auto exposure is GLOBAL (algorithm setting, not camera-specific)
            auto_exposure = self.config.get('zwo_auto_exposure', False)
            # ============================================================================
            
            exposure_sec = exposure_ms / 1000.0
            
            app_logger.info(f"Camera config: exposure_ms={exposure_ms}, gain={gain}, "
                           f"auto_exposure={auto_exposure}, max_exposure_ms={max_exposure_ms}")
            
            # Clean up any existing camera instance first
            if self.zwo_camera is not None:
                app_logger.info("Cleaning up existing camera instance...")
                try:
                    if self.is_connected:
                        self.zwo_camera.disconnect()
                    self.zwo_camera = None
                except Exception as e:
                    app_logger.warning(f"Error cleaning up old camera instance: {e}")
                    self.zwo_camera = None
            
            # Build full wb_config dict for software white balance
            wb_settings = self.config.get('white_balance', {})
            wb_config = dict(wb_settings)  # copy so we don't mutate config
            wb_config.setdefault('mode', 'asi_auto')

            # Initialize camera with all settings (from profile)
            self.zwo_camera = ZWOCamera(
                sdk_path=sdk_path,
                camera_index=camera_index,
                exposure_sec=exposure_sec,
                gain=gain,
                white_balance_r=wb_r,
                white_balance_b=wb_b,
                offset=offset,
                flip=flip,
                auto_exposure=auto_exposure,
                max_exposure_sec=max_exposure_ms / 1000.0,
                bayer_pattern=bayer_pattern,
                wb_config=wb_config,
                scheduled_capture_enabled=self.config.get('scheduled_capture_enabled', False),
                scheduled_start_time=self.config.get('scheduled_start_time', '17:00'),
                scheduled_end_time=self.config.get('scheduled_end_time', '09:00'),
                camera_name=clean_camera_name  # Pass clean name for profile tracking
            )
            
            # Set target brightness and interval
            self.zwo_camera.target_brightness = target_brightness
            self.zwo_camera.set_capture_interval(self.config.get('zwo_interval', 5.0))
            
            # Set RAW16 mode from dev_mode config (for full bit depth capture)
            dev_mode = self.config.get('dev_mode', {})
            self.zwo_camera.use_raw16 = dev_mode.get('use_raw16', False)
            
            # Set error callback for disconnect recovery
            self.zwo_camera.on_error_callback = self._on_camera_error
            
            # Set calibration callback for status updates
            self.zwo_camera.on_calibration_callback = self._on_calibration_status
            
            # Connect to camera
            if not self.zwo_camera.connect_camera(camera_index):
                raise Exception("Failed to connect to camera")
            
            self.is_connected = True
            
            # Start capture using ZWOCamera's built-in capture loop with callbacks
            # This runs in its own thread inside ZWOCamera and handles:
            # - Auto-exposure calibration
            # - Scheduled capture windows
            # - Error recovery and reconnection
            app_logger.info("Starting capture loop...")
            self.zwo_camera.start_capture(
                on_frame_callback=self._on_frame_captured,
                on_log_callback=lambda msg: app_logger.info(msg)
            )
            
            self.is_capturing = True
            self.capture_started.emit()
            app_logger.info("Camera capture started")
            
        except Exception as e:
            self.is_capturing = False
            self.is_connected = False
            self.error.emit(str(e))
            app_logger.error(f"Failed to start capture: {e}")
            import traceback
            app_logger.debug(f"Stack trace: {traceback.format_exc()}")
            from services.posthog_service import capture_error
            capture_error(e, context='camera_start')
            return  # Prevent any further execution
    
    def stop_capture(self):
        """Stop camera capture"""
        if not self.is_capturing:
            return

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
        
        # Emit signal (thread-safe way to update Qt UI)
        self.frame_ready.emit(pil_image, metadata)
        
        # Also notify main window directly
        if self.main_window:
            self.main_window.on_image_captured(pil_image, metadata)
    
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
            self.zwo_camera = None
            self.capture_stopped.emit()
    
    def _on_calibration_status(self, is_calibrating: bool):
        """Callback from ZWOCamera when calibration status changes
        
        Args:
            is_calibrating: True when calibration starts, False when complete
        """
        self.calibration_status.emit(is_calibrating)
    
    def update_settings(self):
        """Update camera settings from config (live update)

        Pushes ALL mutable settings to the running ZWOCamera so they take
        effect on the very next captured frame — no stop/restart required.
        """
        if not self.zwo_camera:
            return

        try:
            # Get current camera name for profile lookup
            camera_name = self.config.get('zwo_selected_camera_name', '')
            if '(Index:' in camera_name:
                camera_name = camera_name.split('(Index:')[0].strip()

            # Get settings from camera profile (with fallback to global config)
            profile = self.config.get_camera_profile(camera_name) if camera_name else {}

            # --- Exposure & Gain ---
            exposure_ms = profile.get('exposure_ms', self.config.get('zwo_exposure_ms', 100.0))
            gain = profile.get('gain', self.config.get('zwo_gain', 100))

            # --- Auto-exposure ---
            auto_exposure = profile.get('auto_exposure', self.config.get('zwo_auto_exposure', False))
            target_brightness = profile.get('target_brightness', self.config.get('zwo_target_brightness', 100))
            max_exposure_ms = profile.get('max_exposure_ms', self.config.get('zwo_max_exposure_ms', 30000.0))

            self.zwo_camera.auto_exposure = auto_exposure
            self.zwo_camera.target_brightness = target_brightness
            self.zwo_camera.max_exposure = max_exposure_ms / 1000.0

            # Only override exposure when auto-exposure is OFF.
            # When auto-exposure is driving, it computes its own exposure and
            # resetting it to the UI spinner value would restart the ramp-up.
            if not auto_exposure:
                self.zwo_camera.set_exposure(exposure_ms / 1000.0)
            else:
                # If auto-exposure is active but current exposure exceeds the
                # (possibly lowered) max, clamp it immediately.
                max_sec = max_exposure_ms / 1000.0
                if self.zwo_camera.exposure_seconds > max_sec:
                    app_logger.info(
                        f"Clamping auto-exposure from "
                        f"{self.zwo_camera.exposure_seconds*1000:.0f}ms to "
                        f"new max {max_sec*1000:.0f}ms"
                    )
                    self.zwo_camera.set_exposure(max_sec)

            self.zwo_camera.set_gain(gain)

            if self.zwo_camera.calibration_manager:
                # When auto-exposure is active, do NOT override the current
                # exposure — let the algorithm keep its computed value.
                # Only push manual exposure when auto-exposure is off.
                cal_exposure = (
                    None if auto_exposure
                    else exposure_ms / 1000.0
                )
                self.zwo_camera.calibration_manager.update_settings(
                    exposure_seconds=cal_exposure,
                    gain=gain,
                    target_brightness=target_brightness,
                    max_exposure_sec=max_exposure_ms / 1000.0
                )

            # --- Capture interval ---
            self.zwo_camera.set_capture_interval(
                self.config.get('zwo_interval', 5.0)
            )

            # --- Offset (black level) — push to SDK ---
            offset = profile.get('offset', self.config.get('zwo_offset', 20))
            self.zwo_camera.offset = offset
            if self.zwo_camera.camera and self.zwo_camera.asi:
                try:
                    self.zwo_camera.camera.set_control_value(
                        self.zwo_camera.asi.ASI_BRIGHTNESS, offset
                    )
                except Exception as e:
                    app_logger.debug(f"Could not set offset live: {e}")

            # --- Flip — push to SDK ---
            flip = profile.get('flip', self.config.get('zwo_flip', 0))
            if flip != self.zwo_camera.flip:
                self.zwo_camera.flip = flip
                if self.zwo_camera.camera and self.zwo_camera.asi:
                    try:
                        self.zwo_camera.camera.set_control_value(
                            self.zwo_camera.asi.ASI_FLIP, flip
                        )
                    except Exception as e:
                        app_logger.debug(f"Could not set flip live: {e}")

            # --- Bayer pattern (software-side, no SDK call) ---
            bayer = profile.get('bayer_pattern', self.config.get('zwo_bayer_pattern', 'BGGR'))
            self.zwo_camera.bayer_pattern = bayer

            # --- Scheduled capture ---
            self.zwo_camera.scheduled_capture_enabled = self.config.get(
                'scheduled_capture_enabled', False
            )
            self.zwo_camera.scheduled_start_time = self.config.get(
                'scheduled_start_time', '17:00'
            )
            self.zwo_camera.scheduled_end_time = self.config.get(
                'scheduled_end_time', '09:00'
            )

            # --- White balance config ---
            wb_settings = self.config.get('white_balance', {})
            self.zwo_camera.wb_config = dict(wb_settings)
            self.zwo_camera.wb_config.setdefault('mode', 'asi_auto')

            # Also update wb_mode for reconnection consistency
            self.zwo_camera.wb_mode = wb_settings.get('mode', 'asi_auto')

            app_logger.debug(
                f"Settings updated live: exposure={exposure_ms}ms, gain={gain}, "
                f"max_exposure={max_exposure_ms}ms, "
                f"interval={self.zwo_camera.capture_interval}s, offset={offset}, "
                f"flip={flip}, bayer={bayer}"
            )

        except Exception as e:
            app_logger.error(f"Failed to update camera settings: {e}")

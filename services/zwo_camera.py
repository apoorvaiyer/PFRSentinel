"""
ZWO ASI Camera capture module
Provides interface to capture images from ZWO cameras using the ASI SDK

This is the main public interface. Connection management is delegated to
camera_connection.py, calibration to camera_calibration.py.
"""
import threading
import time

from .camera_calibration import CameraCalibration
from .camera_connection import CameraConnection
from .camera_utils import is_within_scheduled_window as check_scheduled_window
from .logger import app_logger
from . import zwo_capture_worker


class ZWOCamera:
    """Interface to ZWO ASI camera using zwoasi library"""
    
    def __init__(self, sdk_path=None, camera_index=0, exposure_sec=1.0, gain=100,
                 white_balance_r=75, white_balance_b=99, offset=20, flip=0,
                 auto_exposure=False, max_exposure_sec=30.0, auto_wb=False,
                 wb_mode='asi_auto', wb_config=None, bayer_pattern='BGGR',
                 scheduled_capture_enabled=False, scheduled_start_time="17:00",
                 scheduled_end_time="09:00", status_callback=None, camera_name=None,
                 config_callback=None):
        # Initialize log callback FIRST (before CameraConnection uses self.log)
        self.on_log_callback = None
        self.on_frame_callback = None
        self.on_calibration_callback = None
        self.on_error_callback = None
        
        # Initialize connection manager (delegates SDK/connection logic)
        self._connection = CameraConnection(sdk_path=sdk_path, logger=self.log)
        self._connection.config_callback = config_callback
        self._connection.camera_name = camera_name
        self._connection.camera_index = camera_index
        
        # Legacy attribute aliases (for backward compatibility)
        self.sdk_path = sdk_path
        self.camera_index = camera_index
        self.camera_name = camera_name
        self.config_callback = config_callback
        
        # Capture state
        self.is_capturing = False
        self.capture_thread = None
        self.status_callback = status_callback  # Callback for schedule status updates
        
        # Capture settings
        self.exposure_seconds = exposure_sec
        self.gain = gain
        self.capture_interval = 5.0  # Seconds between captures
        self.auto_exposure = auto_exposure
        self.max_exposure = max_exposure_sec  # Max exposure for auto mode
        self.target_brightness = 100  # Target brightness for auto exposure
        self.exposure_algorithm = 'percentile'  # 'mean', 'median', or 'percentile'
        self.exposure_percentile = 75  # Use 75th percentile (focuses on brighter areas)
        self.clipping_threshold = 245  # Consider pixels > this value as clipped
        self.clipping_prevention = True  # Prevent further exposure increase if clipping detected
        self.white_balance_r = white_balance_r
        self.white_balance_b = white_balance_b
        self.auto_wb = auto_wb
        self.wb_mode = wb_mode  # 'asi_auto', 'manual', or 'gray_world'
        self.wb_config = wb_config if wb_config else {'mode': wb_mode}  # Full WB config
        self.flip = flip  # 0=none, 1=horizontal, 2=vertical, 3=both
        self.offset = offset
        self.bayer_pattern = bayer_pattern  # RGGB, BGGR, GRBG, GBRG
        self.use_raw16 = False  # Use RAW16 mode for full bit depth (set by dev mode)
        
        # Scheduled capture settings
        self.scheduled_capture_enabled = scheduled_capture_enabled
        self.scheduled_start_time = scheduled_start_time  # Format: "HH:MM"
        self.scheduled_end_time = scheduled_end_time      # Format: "HH:MM"
        
        # Exposure tracking for UI
        self.exposure_start_time = None
        self.exposure_remaining = 0.0
        
        # Rapid calibration mode
        self.calibration_mode = False  # Fast convergence before normal capture
        self.calibration_complete = False
        
        # Initialize calibration manager
        self.calibration_manager = None  # Will be initialized after camera connection
    
    # =========================================================================
    # Property aliases for backward compatibility (delegate to connection manager)
    # =========================================================================
    
    @property
    def camera(self):
        """Camera instance (delegated to connection manager)"""
        return self._connection.camera
    
    @camera.setter
    def camera(self, value):
        """Set camera instance"""
        self._connection.camera = value
    
    @property
    def asi(self):
        """ASI SDK instance (delegated to connection manager)"""
        return self._connection.asi
    
    @asi.setter
    def asi(self, value):
        """Set ASI SDK instance"""
        self._connection.asi = value
    
    @property
    def cameras(self):
        """List of detected cameras"""
        return self._connection.cameras
    
    @cameras.setter
    def cameras(self, value):
        """Set cameras list"""
        self._connection.cameras = value
    
    @property
    def supports_raw16(self) -> bool:
        """Whether camera supports RAW16 mode (delegated to connection manager)"""
        return self._connection.supports_raw16
    
    @property
    def sensor_bit_depth(self) -> int:
        """Camera's native ADC bit depth (delegated to connection manager)"""
        return self._connection.bit_depth
    
    @property
    def camera_info(self) -> dict:
        """Camera properties dict (delegated to connection manager)"""
        return self._connection.camera_info
    
    @property
    def current_bit_depth(self) -> int:
        """Current capture bit depth (8 for RAW8, 16 for RAW16)"""
        return self._connection.current_bit_depth
    
    def __del__(self):
        """Destructor to ensure camera is disconnected when object is destroyed"""
        try:
            self.disconnect_camera()
        except Exception:
            pass  # Ignore errors during cleanup in destructor
    
    def __enter__(self):
        """Context manager entry - allows use with 'with' statement"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup even if exception occurs"""
        self.disconnect_camera()
        return False  # Don't suppress exceptions
        
    def log(self, message):
        """Send log message via callback"""
        if self.on_log_callback:
            self.on_log_callback(message)
        app_logger.debug(message)
    
    def is_within_scheduled_window(self):
        """
        Check if current time is within the scheduled capture window.
        Handles overnight captures (e.g., 17:00 - 09:00).
        Returns True if scheduled capture is disabled or if within window.
        """
        return check_scheduled_window(
            self.scheduled_capture_enabled,
            self.scheduled_start_time,
            self.scheduled_end_time
        )
    
    def initialize_sdk(self):
        """Initialize the ZWO ASI SDK (delegates to connection manager)"""
        # Update connection manager's logger to use our log method
        self._connection._logger = self.log
        return self._connection.initialize_sdk()
    
    def reset_sdk_completely(self):
        """
        Completely reset the SDK state (nuclear option).
        Delegates to connection manager.
        """
        return self._connection.reset_sdk_completely()
    
    def detect_cameras(self):
        """Detect connected ZWO cameras (delegates to connection manager)"""
        return self._connection.detect_cameras()
    
    def reconnect_camera_safe(self):
        """
        Safely reconnect to camera by re-detecting available cameras first.
        Delegates to connection manager, but handles calibration manager init.
        
        IMPORTANT: This passes current camera settings to ensure ROI and other
        settings are properly restored after reconnection. Without this, the
        camera may capture at wrong resolution causing reshape errors.
        """
        # Build settings dict from current properties to restore after reconnection
        settings = {
            'gain': self.gain,
            'exposure_sec': self.exposure_seconds,
            'wb_r': self.white_balance_r,
            'wb_b': self.white_balance_b,
            'wb_mode': self.wb_mode,
            'offset': self.offset,
            'flip': self.flip,
            'use_raw16': self.use_raw16,  # Preserve RAW16 mode after reconnection
        }
        
        success = self._connection.reconnect_safe(
            target_camera_name=self.camera_name,
            settings=settings
        )
        
        if success:
            # Sync camera_name and camera_index from connection manager
            self.camera_name = self._connection.camera_name
            self.camera_index = self._connection.camera_index
            
            # Initialize calibration manager for the reconnected camera
            self._init_calibration_manager()
        
        return success
    
    def connect_camera(self, camera_index=0):
        """
        Connect to a specific camera.
        Delegates connection to connection manager, then initializes calibration.
        """
        # Build settings dict from our properties
        settings = {
            'gain': self.gain,
            'exposure_sec': self.exposure_seconds,
            'wb_r': self.white_balance_r,
            'wb_b': self.white_balance_b,
            'wb_mode': self.wb_mode,
            'offset': self.offset,
            'flip': self.flip,
            'use_raw16': self.use_raw16,  # RAW16 mode for full bit depth
        }
        
        # Delegate connection to connection manager
        # Pass expected name so connect() can verify the SDK returned the right camera
        success = self._connection.connect(
            camera_index, settings, expected_camera_name=self.camera_name
        )
        
        if success:
            # Sync camera_name and camera_index from connection manager
            self.camera_name = self._connection.camera_name
            self.camera_index = self._connection.camera_index
            
            # Initialize calibration manager
            self._init_calibration_manager()
            
            self.log(f"✓ Camera connection successful")
            if self.scheduled_capture_enabled:
                self.log(f"Scheduled capture enabled: {self.scheduled_start_time} - {self.scheduled_end_time}")
        
        return success
    
    def _init_calibration_manager(self):
        """Initialize the calibration manager for connected camera"""
        if not self.camera:
            return
        
        self.log(f"Initializing calibration manager (max_exposure={self.max_exposure}s)...")
        self.calibration_manager = CameraCalibration(
            self.camera, self.asi, self.log, 
            bit_depth=self.current_bit_depth  # Pass current RAW mode bit depth
        )
        self.calibration_manager.update_settings(
            exposure_seconds=self.exposure_seconds,
            gain=self.gain,
            target_brightness=self.target_brightness,
            max_exposure_sec=self.max_exposure,
            algorithm=self.exposure_algorithm,
            percentile=self.exposure_percentile,
            clipping_threshold=self.clipping_threshold,
            clipping_prevention=self.clipping_prevention
        )
    
    def _configure_camera(self):
        """Configure camera settings (delegates to connection manager)"""
        if not self.camera:
            return
        
        settings = {
            'gain': self.gain,
            'exposure_sec': self.exposure_seconds,
            'wb_r': self.white_balance_r,
            'wb_b': self.white_balance_b,
            'wb_mode': self.wb_mode,
            'offset': self.offset,
            'flip': self.flip,
            'use_raw16': self.use_raw16,  # RAW16 mode for full bit depth
        }
        self._connection.configure(settings)
    
    def set_raw16_mode(self, enabled: bool) -> bool:
        """
        Change RAW mode (RAW8/RAW16) during live capture.
        
        Args:
            enabled: True for RAW16, False for RAW8
            
        Returns:
            True if mode changed successfully
        """
        if not self.camera:
            self.log("Cannot change RAW mode: camera not connected")
            return False
        
        if enabled and not self.supports_raw16:
            self.log("Camera does not support RAW16 mode")
            return False
        
        try:
            with self._connection.sdk_lock:
                if not self.camera:
                    raise Exception("Camera disconnected before RAW mode change")

                # Verify we're talking to the right camera before writing settings
                if not self._connection.verify_identity():
                    raise Exception("Camera identity mismatch — aborting RAW mode change")

                # Update our setting
                self.use_raw16 = enabled

                # Get camera info for ROI
                camera_info = self.camera.get_camera_property()
                width = camera_info['MaxWidth']
                height = camera_info['MaxHeight']

                # Set new image type
                image_type = self.asi.ASI_IMG_RAW16 if enabled else self.asi.ASI_IMG_RAW8
                self.camera.set_roi(start_x=0, start_y=0, width=width, height=height,
                                    bins=1, image_type=image_type)
                self.camera.set_image_type(image_type)

                # Update connection manager state
                self._connection.current_image_type = image_type
                self._connection.current_bit_depth = 16 if enabled else 8

            # Update calibration manager bit depth (outside lock — no SDK calls)
            if self.calibration_manager:
                self.calibration_manager.bit_depth = self.current_bit_depth

            mode_str = "RAW16" if enabled else "RAW8"
            self.log(f"Switched to {mode_str} mode ({self.current_bit_depth}-bit capture)")
            return True
            
        except Exception as e:
            self.log(f"Error changing RAW mode: {e}")
            return False

    def disconnect_camera(self):
        """Disconnect from camera gracefully (idempotent - safe to call multiple times)"""
        # Stop capture first if active
        if self.is_capturing:
            self.log("Stopping active capture before disconnect...")
            self.stop_capture()
        
        # Create callback to stop exposure before disconnect
        def stop_exposure_callback():
            if self.exposure_start_time is not None:
                self.log("Aborting in-progress exposure...")
                try:
                    self._connection.camera.stop_exposure()
                except Exception:
                    pass
                self.exposure_start_time = None
                self.exposure_remaining = 0.0
                self.log("Exposure aborted")
        
        # Delegate to connection manager
        self._connection.disconnect(stop_exposure_callback=stop_exposure_callback)
        
        # Clear exposure tracking
        self.exposure_start_time = None
        self.exposure_remaining = 0.0
    
    def capture_single_frame(self):
        return zwo_capture_worker.capture_single_frame(self)

    def capture_loop(self):
        return zwo_capture_worker.capture_loop(self)

    def start_capture(self, on_frame_callback, on_log_callback=None):
        """Start continuous capture"""
        if self.is_capturing:
            self.log("Capture already running")
            return False
        
        if not self.camera:
            self.log("ERROR: Camera not connected")
            return False
        
        self.on_frame_callback = on_frame_callback
        self.on_log_callback = on_log_callback
        self.is_capturing = True
        
        # Start capture thread
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()
        
        return True
    
    def stop_capture(self):
        """Stop continuous capture and wait for thread to finish"""
        if not self.is_capturing:
            return

        self.log("Stopping capture...")
        self.is_capturing = False

        # Abort any running calibration so its loop exits quickly
        if self.calibration_manager:
            self.calibration_manager.abort()

        # Abort any in-progress exposure so the poll loop exits immediately
        if self.camera and self.exposure_start_time is not None:
            try:
                self.camera.stop_exposure()
            except Exception:
                pass

        # Wait for capture thread — should exit quickly now that
        # is_capturing is False and exposure is aborted
        if self.capture_thread and self.capture_thread.is_alive():
            self.log("Waiting for capture thread to finish...")
            self.capture_thread.join(timeout=3.0)

            if self.capture_thread.is_alive():
                self.log("Warning: Capture thread still running (will finish in background)")
            else:
                self.log("Capture thread finished successfully")

            self.capture_thread = None

        self.log("Capture stopped")
    
    def set_exposure(self, seconds):
        """Set exposure time in seconds"""
        self.exposure_seconds = max(0.000001, min(3600, seconds))
    
    def set_gain(self, gain):
        """Set gain value"""
        self.gain = max(0, min(600, int(gain)))
    
    def set_capture_interval(self, seconds):
        """Set interval between captures"""
        self.capture_interval = max(1.0, seconds)
    
    def update_exposure(self, exposure_seconds):
        """Update exposure setting and apply immediately to camera if connected"""
        self.exposure_seconds = exposure_seconds
        
        # If camera is connected and not in auto exposure mode, apply immediately
        if self.camera and not self.auto_exposure:
            try:
                self.camera.set_control_value(self.asi.ASI_EXPOSURE, int(exposure_seconds * 1000000))
                self.log(f"Exposure updated to {exposure_seconds*1000:.2f}ms")
            except Exception as e:
                self.log(f"Failed to update camera exposure: {e}")
    
    def run_calibration(self):
        """Rapid calibration to find optimal exposure before starting interval captures"""
        if not self.auto_exposure or not self.camera or not self.calibration_manager:
            return

        self.log(f"Starting rapid auto-exposure calibration... "
                 f"(max_exposure={self.max_exposure}s, "
                 f"cal_max={self.calibration_manager.max_exposure_sec}s)")
        self.calibration_mode = True

        # Notify UI that calibration is starting
        if self.on_calibration_callback:
            self.on_calibration_callback(True)

        # Run calibration using the calibration manager
        import time as _time
        _cal_start = _time.time()
        success = self.calibration_manager.run_calibration(max_attempts=15)
        _cal_duration = _time.time() - _cal_start

        # Update our exposure from calibration manager
        self.exposure_seconds = self.calibration_manager.exposure_seconds

        self.calibration_complete = True
        self.calibration_mode = False

        # PostHog: calibration results
        from .posthog_service import capture_event
        cal_history = getattr(self.calibration_manager, 'calibration_history', [])
        capture_event('calibration_completed', {
            'success': success,
            'duration_seconds': round(_cal_duration, 1),
            'attempts': len(cal_history) if cal_history else None,
            'final_exposure_ms': round(self.exposure_seconds * 1000, 2),
            'final_brightness': round(cal_history[-1][1], 1) if cal_history else None,
            'target_brightness': self.calibration_manager.target_brightness,
            'max_exposure_ms': round(self.max_exposure * 1000, 0),
        })

        # Notify UI that calibration is complete
        if self.on_calibration_callback:
            self.on_calibration_callback(False)
    
    def adjust_exposure_auto(self, img_array):
        """
        Adjust exposure based on image brightness with intelligent step sizing.
        
        Returns:
            dict with 'needs_recalibration' flag and brightness info, or None if auto-exposure disabled
        """
        if not self.auto_exposure or not self.calibration_manager:
            return None
        
        # Use calibration manager to adjust exposure
        result = self.calibration_manager.adjust_exposure_auto(img_array)
        
        # Update our exposure from calibration manager
        self.exposure_seconds = self.calibration_manager.exposure_seconds
        
        return result

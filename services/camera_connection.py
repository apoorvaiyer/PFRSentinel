"""
Camera connection management for ZWO ASI cameras

Handles SDK initialization, camera detection, connection/reconnection, and configuration.
This module is used internally by ZWOCamera - do not import directly.
"""
import os
import sys
import time
import threading
from typing import Optional, List, Dict, Callable, Any
from .camera_config import verify_camera_identity, configure_camera


class CameraConnection:
    """
    Manages ZWO ASI SDK and camera connection lifecycle.

    This class handles:
    - SDK initialization and reset
    - Camera detection and enumeration
    - Camera connection with retry logic
    - Camera configuration (gain, exposure, WB, etc.)
    - Safe reconnection after disconnects
    """

    def __init__(self, sdk_path: Optional[str] = None, logger: Optional[Callable[[str], None]] = None):
        """
        Initialize connection manager.

        Args:
            sdk_path: Path to ASICamera2.dll (optional, will search defaults)
            logger: Callback function for logging messages
        """
        self.sdk_path = sdk_path
        self._logger = logger

        # SDK state
        self.asi = None
        self.camera = None
        self.cameras: List[Dict[str, Any]] = []

        # Camera identification for reconnection
        self.camera_name: Optional[str] = None
        self.camera_index: int = 0

        # Camera capabilities (populated on connect)
        self.camera_info: dict = {}
        self.supports_raw16: bool = False
        self.bit_depth: int = 8  # Sensor ADC bit depth

        # Current capture mode
        self.current_image_type = None  # ASI_IMG_RAW8 or ASI_IMG_RAW16
        self.current_bit_depth: int = 8  # 8 for RAW8, 16 for RAW16

        # Thread safety
        self._cleanup_lock = threading.Lock()
        self.sdk_lock = threading.Lock()  # Guards all SDK calls (capture vs disconnect race)

        # Callback for persisting camera name to config
        self.config_callback: Optional[Callable[[str, Any], None]] = None

        # USB reset capability (Windows only)
        self._usb_reset_available = False
        self._usb_reset_func = None
        self._usb_disable_enable_func = None
        self._init_usb_reset()

    def log(self, message: str) -> None:
        """Log message via callback or app_logger"""
        if self._logger:
            self._logger(message)
        else:
            from .logger import app_logger
            app_logger.debug(message)

    def _init_usb_reset(self) -> None:
        """Initialize USB reset capability (Windows only)"""
        if sys.platform != 'win32':
            return  # USB reset only supported on Windows

        try:
            from .usb_reset_win import (
                reset_zwo_camera_usb, disable_enable_zwo_camera_usb,
                is_usb_reset_available
            )
            if is_usb_reset_available():
                self._usb_reset_available = True
                self._usb_reset_func = reset_zwo_camera_usb
                self._usb_disable_enable_func = disable_enable_zwo_camera_usb
                self.log("\u2713 USB reset capability available (includes disable/enable)")
            else:
                self.log("⚠ USB reset not available (Windows API load failed)")
        except ImportError as e:
            self.log(f"⚠ USB reset module not available: {e}")
        except Exception as e:
            self.log(f"⚠ Error initializing USB reset: {e}")

    @staticmethod
    def _is_running_as_admin() -> bool:
        """Check if the current process has Administrator privileges."""
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    # =========================================================================
    # SDK Initialization
    # =========================================================================

    def initialize_sdk(self) -> bool:
        """
        Initialize the ZWO ASI SDK.

        Returns:
            True if successful, False otherwise
        """
        self.log("=== Initializing ZWO ASI SDK ===")
        try:
            import zwoasi as asi
            self.asi = asi
            self.log("zwoasi module imported successfully")

            if self.sdk_path and os.path.exists(self.sdk_path):
                self.log(f"Attempting SDK init with configured path: {self.sdk_path}")
                asi.init(self.sdk_path)
                self.log(f"✓ ZWO SDK initialized successfully from: {self.sdk_path}")
            else:
                # Try default locations
                self.log("SDK path not configured or not found, trying default location")
                if os.path.exists('ASICamera2.dll'):
                    self.log("Found ASICamera2.dll in application directory")
                    asi.init('ASICamera2.dll')
                    self.log("✓ ZWO SDK initialized from: ASICamera2.dll")
                else:
                    self.log("ERROR: ASICamera2.dll not found in application directory")
                    self.log("Please configure SDK path in Capture tab settings")
                    return False

            return True

        except ImportError as e:
            self.log(f"ERROR: zwoasi library not installed: {e}")
            self.log("Run: pip install zwoasi")
            return False
        except Exception as e:
            self.log(f"ERROR initializing ZWO SDK: {e}")
            import traceback
            self.log(f"Stack trace: {traceback.format_exc()}")
            return False

    def reset_sdk_completely(self) -> bool:
        """
        Completely reset the SDK state (nuclear option).
        Use this when SDK gets into an inconsistent state.

        Returns:
            True if successful, False otherwise
        """
        self.log("=== Complete SDK Reset ===")

        try:
            # Close camera if connected
            if self.camera:
                self.log("Disconnecting camera before SDK reset...")
                self.disconnect()

            # Clear SDK reference
            self.log("Clearing SDK reference...")
            self.asi = None

            # Wait for cleanup
            time.sleep(1.0)

            # Reinitialize SDK
            self.log("Reinitializing SDK...")
            if not self.initialize_sdk():
                self.log("✗ SDK reinitialization failed")
                return False

            self.log("✓ SDK reset complete")
            return True

        except Exception as e:
            self.log(f"✗ ERROR during SDK reset: {e}")
            import traceback
            self.log(f"Stack trace: {traceback.format_exc()}")
            return False

    # =========================================================================
    # Camera Detection
    # =========================================================================

    def detect_cameras(self) -> List[Dict[str, Any]]:
        """
        Detect connected ZWO cameras.

        Returns:
            List of camera info dicts with 'index' and 'name' keys
        """
        self.log("=== Starting Camera Detection ===")

        if not self.asi:
            self.log("SDK not initialized, initializing now...")
            if not self.initialize_sdk():
                self.log("Camera detection failed: SDK initialization failed")
                return []

        try:
            self.log("Querying SDK for number of connected cameras...")
            num_cameras = self.asi.get_num_cameras()
            self.cameras = []

            if num_cameras == 0:
                self.log("⚠ No ZWO cameras detected by SDK")
                self.log("Check: 1) USB cable connected, 2) Camera powered, 3) USB drivers installed")
                return []

            self.log(f"✓ Found {num_cameras} ZWO camera(s) connected")
            self.log("Enumerating camera details...")

            # Snapshot list_cameras() once — calling it per-iteration races against
            # the driver still binding after a hot-plug / disable-enable cycle,
            # producing "list index out of range" on the just-appeared camera.
            # If the list is shorter than num_cameras, retry a few times to let
            # the SDK converge.
            camera_list = []
            for poll_attempt in range(3):
                camera_list = list(self.asi.list_cameras())
                if len(camera_list) >= num_cameras:
                    break
                self.log(
                    f"  ⚠ Enumeration race: get_num_cameras={num_cameras} but "
                    f"list_cameras returned {len(camera_list)} — retrying in 1s "
                    f"({poll_attempt + 1}/3)"
                )
                time.sleep(1.0)

            # If we still disagree after retries, trust list_cameras() — it's the
            # one that returns actual names we can use.
            for i, name in enumerate(camera_list):
                self.cameras.append({'index': i, 'name': name})
                self.log(f"  ✓ Camera {i}: {name}")

            if len(camera_list) != num_cameras:
                self.log(
                    f"  ⚠ Enumeration still inconsistent after retries: "
                    f"num_cameras={num_cameras}, enumerated={len(camera_list)}"
                )

            self.log(f"Camera detection complete: {len(self.cameras)} camera(s) enumerated")
            return self.cameras

        except Exception as e:
            self.log(f"ERROR during camera detection: {e}")
            import traceback
            self.log(f"Stack trace: {traceback.format_exc()}")
            return []

    # =========================================================================
    # Camera Connection
    # =========================================================================

    def connect(self, camera_index: int = 0, settings: Optional[Dict[str, Any]] = None,
                expected_camera_name: Optional[str] = None,
                post_recovery: bool = False) -> bool:
        """
        Connect to a specific camera.

        Args:
            camera_index: Index of camera to connect to
            settings: Optional dict with camera settings (gain, exposure_sec, wb_r, wb_b, etc.)
            expected_camera_name: If provided, verify the opened camera matches this name.
                Prevents connecting to the wrong physical camera when SDK indices shift.
            post_recovery: If True, we just came out of a USB disable/enable cycle.
                Use a longer retry schedule (6 attempts with exponential backoff)
                and re-run detect_cameras() between attempts, because the camera
                may appear in the SDK list before it's actually openable, and
                its index may shift on re-enumeration.

        Returns:
            True if successful, False otherwise
        """
        self.log(f"=== Connecting to Camera (Index: {camera_index}) ===")

        if not self.asi:
            self.log("SDK not initialized, initializing now...")
            if not self.initialize_sdk():
                self.log("Connection failed: SDK initialization failed")
                return False

        try:
            if self.camera:
                self.log("Existing camera connection detected, disconnecting first...")
                self.disconnect()

            # Add delay to allow SDK cleanup (especially important after other apps like ASICap)
            time.sleep(0.5)  # Increased from 0.2s

            self.log(f"Opening camera at index {camera_index}...")

            # Retry schedule: tighter for normal connects, longer & with
            # re-detection for post-recovery opens (driver may still be binding
            # and SDK index may shift).
            if post_recovery:
                backoff_schedule = [1.0, 2.0, 4.0, 4.0, 4.0]  # ~15s total
                self.log(f"  (post-recovery mode: up to {len(backoff_schedule) + 1} attempts with re-detection)")
            else:
                backoff_schedule = [1.0, 1.0]  # 3 attempts total
            max_attempts = len(backoff_schedule) + 1

            for attempt in range(1, max_attempts + 1):
                try:
                    self.camera = self.asi.Camera(camera_index)
                    break  # Success - exit retry loop
                except Exception as e:
                    if attempt < max_attempts:
                        self.log(f"⚠ Attempt {attempt}/{max_attempts} failed: {e}")
                        if attempt == 1 and not post_recovery:
                            self.log(f"⚠ If you recently used ASICap or other ZWO software, please wait 10-15 seconds before retrying")

                        # On "Invalid ID" during post-recovery, re-enumerate to
                        # pick up the camera's (possibly shifted) new index.
                        if post_recovery and "Invalid ID" in str(e) and expected_camera_name:
                            self.log("  Re-detecting cameras (index may have shifted after recovery)...")
                            try:
                                refreshed = self.detect_cameras()
                                if refreshed:
                                    new_idx = self._find_camera_index_by_name(refreshed, expected_camera_name)
                                    if new_idx is not None and new_idx != camera_index:
                                        self.log(f"  Target now at index {new_idx} (was {camera_index})")
                                        camera_index = new_idx
                            except Exception as detect_err:
                                self.log(f"  Re-detect failed: {detect_err}")

                        wait = backoff_schedule[attempt - 1]
                        self.log(f"Waiting {wait}s before retry...")
                        time.sleep(wait)
                    else:
                        # Final attempt failed - re-raise the exception
                        raise

            camera_info = self.camera.get_camera_property()
            actual_name = camera_info['Name']

            # Validate camera identity — if we expected a specific camera, make sure
            # the SDK gave us the right one.  SDK indices can silently shift when
            # cameras are hot-plugged, so this catches cross-wiring.
            if expected_camera_name and expected_camera_name not in actual_name:
                self.log(
                    f"✗ Camera identity mismatch! Expected '{expected_camera_name}' "
                    f"at index {camera_index}, but SDK returned '{actual_name}' "
                    f"({camera_info['MaxWidth']}x{camera_info['MaxHeight']}, "
                    f"{camera_info['PixelSize']}µm)"
                )
                self.log("  Closing wrong camera and failing connection.")
                try:
                    self.camera.close()
                except Exception:
                    pass
                self.camera = None
                return False

            # Store camera name for future reconnection
            self.camera_name = actual_name
            self.camera_index = camera_index

            # Save camera name to config for persistence across restarts
            if self.config_callback:
                self.config_callback('zwo_selected_camera_name', self.camera_name)
                self.log(f"Saved camera name to config: {self.camera_name}")

            # Store camera capabilities for later access
            self.camera_info = camera_info
            self.supports_raw16 = 2 in camera_info.get('SupportedVideoFormat', [0])  # ASI_IMG_RAW16 = 2
            self.bit_depth = camera_info.get('BitDepth', 8)

            self.log(f"✓ Connected to camera: {actual_name}")
            self.log(f"  Camera ID: {camera_info.get('CameraID', 'N/A')}")
            self.log(f"  Max Resolution: {camera_info['MaxWidth']}x{camera_info['MaxHeight']}")
            self.log(f"  Pixel Size: {camera_info['PixelSize']} µm")
            self.log(f"  Sensor ADC: {self.bit_depth}-bit")
            self.log(f"  RAW16 Support: {'Yes' if self.supports_raw16 else 'No'}")

            # Get controls info
            controls = self.camera.get_controls()
            self.log(f"  Available controls: {len(controls)}")

            # Brief stabilization delay - camera needs time to fully initialize
            # This helps prevent "Camera closed" errors immediately after connection
            time.sleep(0.3)

            # Apply settings if provided (sets ROI, gain, exposure, etc.)
            if settings:
                self.configure(settings)
            else:
                # Set ROI to full frame when no settings provided.
                # The SDK can retain a stale ROI from a previous session (this process
                # or another app).  Without an explicit set_roi(), captured data size
                # won't match MaxWidth*MaxHeight, causing numpy reshape failures.
                self.log("No settings provided - setting ROI to full frame (default)")
                self.camera.set_roi(
                    start_x=0, start_y=0,
                    width=camera_info['MaxWidth'],
                    height=camera_info['MaxHeight'],
                    bins=1,
                    image_type=self.asi.ASI_IMG_RAW8
                )
                self.camera.set_image_type(self.asi.ASI_IMG_RAW8)
                self.log(f"  ROI: Full frame {camera_info['MaxWidth']}x{camera_info['MaxHeight']}")

            self.log(f"✓ Camera connection successful")
            return True

        except Exception as e:
            self.log(f"✗ ERROR connecting to camera: {e}")
            import traceback
            self.log(f"Stack trace: {traceback.format_exc()}")

            # Add diagnostic information for "Invalid ID" errors
            if "Invalid ID" in str(e):
                self._log_invalid_id_diagnostics(camera_index)

            return False

    def _log_invalid_id_diagnostics(self, camera_index: int) -> None:
        """Log diagnostic information for Invalid ID errors"""
        self.log("=== Diagnostic Information ===")
        self.log(f"Attempted camera index: {camera_index}")
        self.log("This error typically occurs when:")
        self.log("  1. Camera was not properly closed by another process")
        self.log("  2. SDK is in an inconsistent state")
        self.log("  3. Camera index changed (hot plug event)")
        self.log("Recommended action: Try stopping/restarting the application")

        # Try to get current camera list for diagnostics
        try:
            num_cameras = self.asi.get_num_cameras()
            self.log(f"Current SDK state: {num_cameras} camera(s) reported by SDK")
            listed_cameras = self.asi.list_cameras()
            for idx, name in enumerate(listed_cameras):
                self.log(f"  Camera {idx}: {name}")
        except Exception as diag_err:
            self.log(f"  Could not query camera list for diagnostics: {diag_err}")

    def _wait_for_stable_detection(self, camera_name: str,
                                    timeout_sec: float = 10.0,
                                    poll_interval: float = 2.0) -> Optional[int]:
        """
        Poll detect_cameras() until the target camera appears at the same index
        on two consecutive polls, or timeout.

        Right after a USB disable/enable cycle, the SDK can briefly show the
        camera before the driver is fully bound — `connect()` then fails with
        "Invalid ID". Waiting for a stable detection avoids that race.

        Args:
            camera_name: Name substring to search for
            timeout_sec: Max wait time
            poll_interval: Seconds between polls

        Returns:
            Stable camera index, or None if not seen stably within timeout.
        """
        from .camera_reconnect import wait_for_stable_detection
        return wait_for_stable_detection(self, camera_name, timeout_sec, poll_interval)

    def _find_camera_index_by_name(self, cameras: List[Dict[str, Any]], camera_name: str) -> Optional[int]:
        """
        Find camera index by exact name match.

        Args:
            cameras: List of detected cameras with 'index' and 'name' keys
            camera_name: Name of camera to find

        Returns:
            Camera index if found, None if not found
        """
        for cam in cameras:
            if camera_name in cam['name']:
                self.log(f"✓ Found camera '{camera_name}' at index {cam['index']}")
                return cam['index']

        # Log available cameras for debugging
        self.log(f"✗ Camera '{camera_name}' not found in detected cameras:")
        for cam in cameras:
            self.log(f"  - [{cam['index']}] {cam['name']}")
        return None

    def _find_camera_index(self, cameras: List[Dict[str, Any]], camera_name: Optional[str]) -> int:
        """
        Find camera index by name, or return first camera index if no name specified.

        NOTE: This method is for initial connection when no specific camera is required.
        For reconnection, use _find_camera_index_by_name() which enforces strict matching.
        """
        if camera_name:
            for cam in cameras:
                if camera_name in cam['name']:
                    self.log(f"✓ Found camera '{camera_name}' at index {cam['index']}")
                    return cam['index']
            self.log(f"⚠ Warning: Could not find camera '{camera_name}' by name")

        # Fall back to first camera (only for initial connection, not reconnection)
        self.log(f"Using first available camera at index {cameras[0]['index']}: {cameras[0]['name']}")
        return cameras[0]['index']

    def reconnect_safe(self, target_camera_name: Optional[str] = None,
                       settings: Optional[Dict[str, Any]] = None,
                       allow_fallback: bool = False) -> bool:
        """
        Safely reconnect to camera by re-detecting available cameras first.

        Args:
            target_camera_name: Name of camera to reconnect to (defaults to last connected)
            settings: Camera settings dict to apply after reconnection (ROI, gain, etc.)
                     CRITICAL: Without settings, camera may use default ROI causing
                     resolution mismatch and reshape errors during capture.
            allow_fallback: If False (default), reconnection fails if target camera not found.
                           If True, falls back to first available camera.

        Returns:
            True if reconnection successful, False otherwise

        IMPORTANT: By default, this method will NOT connect to a different camera
        if the target camera is not found. This prevents accidentally connecting
        to the wrong camera when the originally-selected camera loses power or
        is disconnected.
        """
        from .camera_reconnect import reconnect_safe as reconnect_safe_impl
        return reconnect_safe_impl(self, target_camera_name, settings, allow_fallback)

    # =========================================================================
    # Camera Configuration
    # =========================================================================

    def verify_identity(self) -> bool:
        """Verify the open camera handle still points to the expected physical camera."""
        return verify_camera_identity(self.camera, self.camera_name, self.log)

    def configure(self, settings: Dict[str, Any]) -> None:
        """Configure camera settings."""
        if not self.camera:
            self.log("Cannot configure: camera not connected")
            return
        self.log("Configuring camera settings...")
        try:
            if not verify_camera_identity(self.camera, self.camera_name, self.log):
                self.log("Aborting configure — camera identity mismatch")
                return
            image_type, bit_depth = configure_camera(
                self.camera, self.asi, settings, self.supports_raw16, self.log
            )
            self.current_image_type = image_type
            self.current_bit_depth = bit_depth
        except Exception as e:
            self.log(f"Error configuring camera: {e}")

    # =========================================================================
    # Camera Disconnection
    # =========================================================================

    def disconnect(self, stop_exposure_callback: Optional[Callable[[], None]] = None) -> None:
        """
        Disconnect from camera gracefully (idempotent - safe to call multiple times).

        Stops any in-progress exposure and closes the camera handle.
        camera.close() is sufficient cleanup — the SDK releases all state for the
        handle.  Previously we reset ROI/controls before closing, but that raced
        with the capture thread and could contaminate other cameras' SDK state when
        multiple ZWO cameras share the same process.

        Args:
            stop_exposure_callback: Optional callback to stop any in-progress exposure
        """
        with self._cleanup_lock:
            if not self.camera:
                self.log("Disconnect called but camera already disconnected")
                return

            self.log("=== Disconnecting Camera ===")

            try:
                # Call stop exposure callback if provided
                if stop_exposure_callback:
                    try:
                        stop_exposure_callback()
                    except Exception as e:
                        self.log(f"Exposure stop callback error: {e}")

                # Acquire SDK lock so we don't race with capture_single_frame
                with self.sdk_lock:
                    # Stop any in-progress exposure before closing.
                    try:
                        self.camera.stop_exposure()
                    except Exception:
                        pass  # Ignore — camera may already be idle

                    # Close camera connection
                    try:
                        self.log("Closing camera connection...")
                        self.camera.close()
                        self.log("✓ Camera disconnected successfully")

                        # Brief delay for SDK to fully release USB device
                        try:
                            time.sleep(0.5)
                        except OSError:
                            pass  # WinError 6: handle invalid during interpreter shutdown

                    except Exception as e:
                        self.log(f"⚠ Warning during camera close: {e}")
                        if self._usb_reset_available and self.camera_name:
                            self.log("  Attempting USB reset due to close failure...")
                            try:
                                self._usb_reset_func(camera_name=self.camera_name, logger=self.log)
                            except Exception as usb_err:
                                self.log(f"  USB reset error: {usb_err}")

            except Exception as e:
                self.log(f"✗ ERROR during camera disconnect: {e}")
                import traceback
                self.log(f"Stack trace: {traceback.format_exc()}")
            finally:
                # Always clear camera reference even if close failed
                self.camera = None
                self.log("Camera reference cleared")

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def is_connected(self) -> bool:
        """Check if camera is connected"""
        return self.camera is not None

    def get_camera_property(self) -> Optional[Dict[str, Any]]:
        """Get camera properties if connected"""
        if self.camera:
            return self.camera.get_camera_property()
        return None

    def get_controls(self) -> Optional[Dict[str, Any]]:
        """Get camera controls if connected"""
        if self.camera:
            return self.camera.get_controls()
        return None

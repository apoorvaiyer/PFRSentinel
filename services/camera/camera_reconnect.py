"""
Camera reconnection and USB recovery logic for ZWO ASI cameras.

Module-level functions that operate on a CameraConnection instance.
Used internally by CameraConnection — do not import directly from other modules.
"""
import time
from typing import Optional


def wait_for_stable_detection(conn, camera_name: str,
                               timeout_sec: float = 10.0,
                               poll_interval: float = 2.0) -> Optional[int]:
    """
    Poll detect_cameras() until the target camera appears at the same index
    on two consecutive polls, or timeout.

    Right after a USB disable/enable cycle, the SDK can briefly show the
    camera before the driver is fully bound — connect() then fails with
    "Invalid ID". Waiting for a stable detection avoids that race.

    Args:
        conn: CameraConnection instance
        camera_name: Name substring to search for
        timeout_sec: Max wait time
        poll_interval: Seconds between polls

    Returns:
        Stable camera index, or None if not seen stably within timeout.
    """
    deadline = time.time() + timeout_sec
    last_index: Optional[int] = None

    while time.time() < deadline:
        detected = conn.detect_cameras()
        if detected:
            idx = conn._find_camera_index_by_name(detected, camera_name)
            if idx is not None and idx == last_index:
                conn.log(f"  ✓ Target stable at index {idx}")
                return idx
            last_index = idx
        else:
            last_index = None
        try:
            time.sleep(poll_interval)
        except OSError:
            return None

    conn.log(f"  ⚠ Target not stably detected within {timeout_sec}s")
    return last_index  # Best effort — caller can still try this index


def reconnect_safe(conn, target_camera_name: Optional[str] = None,
                   settings=None, allow_fallback: bool = False) -> bool:
    """
    Safely reconnect to camera by re-detecting available cameras first.

    Args:
        conn: CameraConnection instance
        target_camera_name: Name of camera to reconnect to (defaults to last connected)
        settings: Camera settings dict to apply after reconnection (ROI, gain, etc.)
                 CRITICAL: Without settings, camera may use default ROI causing
                 resolution mismatch and reshape errors during capture.
        allow_fallback: If False (default), reconnection fails if target camera not found.
                       If True, falls back to first available camera.

    Returns:
        True if reconnection successful, False otherwise
    """
    conn.log("=== Safe Camera Reconnection ===")
    camera_to_find = target_camera_name or conn.camera_name

    if camera_to_find:
        conn.log(f"Target camera: '{camera_to_find}'")
    else:
        conn.log("No target camera name specified - will use first available")

    # --- Detection Phase ---
    # Detect cameras, then check if our TARGET camera is present.
    # Other cameras being visible (e.g. guide camera) doesn't help us.
    detected = conn.detect_cameras()
    target_found = False
    # post_recovery flips True only if we needed disable/enable and it worked;
    # tells connect() to use the longer retry schedule for driver settle.
    post_recovery = False

    if detected and camera_to_find:
        target_index = conn._find_camera_index_by_name(detected, camera_to_find)
        target_found = target_index is not None
    elif detected:
        target_found = True  # No specific target, any camera will do

    # --- Recovery Phase ---
    # Trigger recovery if target camera is NOT in the detected list,
    # even if other cameras (guide cam, etc.) are visible.
    if not target_found and camera_to_find:
        other_cams = [c['name'] for c in detected] if detected else []
        if other_cams:
            conn.log(f"⚠ Target camera '{camera_to_find}' not found (other cameras visible: {', '.join(other_cams)})")
        else:
            conn.log("✗ No cameras detected at all")
        conn.log("Attempting recovery steps for missing camera...")

        # Step 1: Try soft USB re-enumeration (Windows only)
        if conn._usb_reset_available:
            conn.log("Step 1: Attempting USB device soft reset...")
            try:
                if conn._usb_reset_func(camera_name=camera_to_find, logger=conn.log):
                    conn.log("✓ USB soft reset completed, waiting for re-enumeration...")
                    time.sleep(10)
                else:
                    conn.log("⚠ USB soft reset failed or not applicable")
            except Exception as e:
                conn.log(f"⚠ USB soft reset error: {e}")

        # Step 2: SDK reset + re-detect
        conn.log("Step 2: Attempting SDK reset...")
        conn.asi = None
        if conn.initialize_sdk():
            detected = conn.detect_cameras()
            if detected:
                target_index = conn._find_camera_index_by_name(detected, camera_to_find)
                target_found = target_index is not None
                if target_found:
                    conn.log(f"✓ Target camera recovered after SDK reset")

        # Step 3: Aggressive USB disable/enable (Device Manager style)
        if not target_found and conn._usb_disable_enable_func:
            conn.log("Step 3: Attempting USB device disable/enable (Device Manager reset)...")
            conn.log("This replicates: Device Manager > Disable > Wait 15s > Enable")
            try:
                if conn._usb_disable_enable_func(
                    camera_name=camera_to_find,
                    disable_seconds=15,
                    logger=conn.log
                ):
                    conn.log("✓ USB disable/enable completed, reinitializing SDK...")
                    conn.asi = None
                    if conn.initialize_sdk():
                        # Detection-settle: SDK may briefly report the
                        # camera before it's stable. Poll until we see
                        # the target name twice in a row at the same
                        # index, or give up after ~10s.
                        target_index = wait_for_stable_detection(
                            conn, camera_to_find, timeout_sec=10
                        )
                        target_found = target_index is not None
                        if target_found:
                            conn.log(f"✓ Camera recovered via disable/enable!")
                            post_recovery = True
                            # Refresh the local `detected` view so the
                            # post-recovery index lookup below doesn't
                            # key off the stale pre-recovery list (which
                            # didn't include the target).
                            detected = conn.cameras
                else:
                    conn.log("⚠ USB disable/enable failed or not applicable")
            except Exception as e:
                conn.log(f"⚠ USB disable/enable error: {e}")

        # All recovery failed
        if not target_found:
            conn.log("✗ All recovery attempts failed")
            if conn._usb_disable_enable_func and not conn._is_running_as_admin():
                conn.log("⚠ USB disable/enable was skipped because app is not running as Administrator")
                conn.log("  To enable full recovery: right-click app shortcut > Properties > Compatibility > 'Run as administrator'")
            if not allow_fallback:
                conn.log(f"✗ RECONNECTION FAILED: Target camera '{camera_to_find}' not found")
                conn.log("The originally-selected camera is not available.")
                conn.log("⚠ Camera may require physical disconnect/reconnect or")
                conn.log("  Device Manager > Disable device > wait 15s > Enable device")
                return False
            else:
                if detected:
                    conn.log(f"⚠ Falling back to first available camera")
                    target_index = detected[0]['index']
                else:
                    conn.log("✗ No cameras available at all")
                    return False

    elif not detected:
        # No cameras at all and no specific target to recover
        conn.log("✗ No cameras detected")
        return False

    # If we have no target_index yet (no recovery was needed), find it
    if camera_to_find and target_found:
        target_index = conn._find_camera_index_by_name(detected, camera_to_find)
    elif not camera_to_find:
        target_index = detected[0]['index']
        conn.log(f"Using first available camera at index {target_index}")

    conn.camera_index = target_index

    # Connect with settings (with SDK reset fallback on failure).
    # post_recovery extends the per-open retry budget when we just came
    # out of a disable/enable cycle — the driver may still be binding.
    if conn.connect(target_index, settings,
                    expected_camera_name=camera_to_find,
                    post_recovery=post_recovery):
        return True

    conn.log("⚠ Connection failed, attempting complete SDK reset...")
    if not conn.reset_sdk_completely():
        return False

    detected = conn.detect_cameras()
    if not detected:
        conn.log("✗ No cameras detected after SDK reset")
        return False

    # Strict matching again after SDK reset
    if camera_to_find:
        target_index = conn._find_camera_index_by_name(detected, camera_to_find)
        if target_index is None:
            if allow_fallback:
                target_index = detected[0]['index']
            else:
                conn.log(f"✗ Target camera '{camera_to_find}' still not found after SDK reset")
                return False
    else:
        target_index = detected[0]['index']

    conn.camera_index = target_index
    return conn.connect(target_index, settings, expected_camera_name=camera_to_find)

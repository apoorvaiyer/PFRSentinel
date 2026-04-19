"""ZWO camera capture worker.

Holds the single-frame capture routine and the long-running capture loop with
reconnect/backoff logic. Split out of services/zwo_camera.py to keep both
files under the project size cap. Functions take a ZWOCamera instance and
operate on its attributes — they are not standalone; they rely on the camera's
connection, calibration manager, and callbacks.
"""
from __future__ import annotations

import time
import traceback
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from .camera_utils import (
    apply_white_balance,
    calculate_image_stats,
    debayer_raw_image,
)
from .posthog_service import capture_error

if TYPE_CHECKING:
    from .zwo_camera import ZWOCamera


def _get_temperature(camera: "ZWOCamera"):
    try:
        temp_value = camera.camera.get_control_value(camera.asi.ASI_TEMPERATURE)[0]
        temp_celsius = temp_value / 10.0
        temp_fahrenheit = (temp_celsius * 9 / 5) + 32
        return {
            'display': f"{temp_celsius:.1f} C",
            'celsius_str': f"{temp_celsius:.1f}°C",
            'fahrenheit_str': f"{temp_fahrenheit:.1f}°F",
        }
    except Exception:
        return {'display': "N/A", 'celsius_str': "N/A", 'fahrenheit_str': "N/A"}


def capture_single_frame(camera: "ZWOCamera"):
    """Capture a single frame and return (PIL image, metadata dict)."""
    if not camera.camera:
        raise Exception("Camera not connected")

    sdk_lock = camera._connection.sdk_lock

    try:
        # Enforce max exposure limit as a safety net.
        # The auto-exposure algorithm should already clamp, but this catches
        # edge cases (stale calibration manager, manual set_exposure, etc.).
        if camera.auto_exposure and camera.exposure_seconds > camera.max_exposure:
            camera.log(
                f"⚠ Exposure {camera.exposure_seconds*1000:.0f}ms exceeds "
                f"max {camera.max_exposure*1000:.0f}ms — clamping"
            )
            camera.exposure_seconds = camera.max_exposure

        # Hold SDK lock while sending commands to the camera.
        # Released before the exposure wait loop so disconnect can proceed.
        with sdk_lock:
            if not camera.camera:
                raise Exception("Camera disconnected before exposure")
            camera.camera.set_control_value(
                camera.asi.ASI_EXPOSURE, int(camera.exposure_seconds * 1000000)
            )
            camera.camera.set_control_value(camera.asi.ASI_GAIN, camera.gain)

        # Retry ASI_EXP_FAILED once before tearing down — a single failed
        # status is frequently just a USB bandwidth hiccup and recovers on
        # immediate restart. The expensive full-reconnect path stays for
        # persistent failures.
        max_exp_attempts = 2
        exposure_succeeded = False
        for exp_attempt in range(1, max_exp_attempts + 1):
            with sdk_lock:
                if not camera.camera:
                    raise Exception("Camera disconnected before exposure")
                camera.camera.start_exposure()

            # Wait for exposure to complete (lock released so disconnect can run)
            timeout = camera.exposure_seconds + 5.0
            start_time = time.time()
            camera.exposure_start_time = start_time
            transient_failure = False

            while time.time() - start_time < timeout:
                if not camera.is_capturing:
                    try:
                        camera.camera.stop_exposure()
                    except Exception:
                        pass
                    raise Exception("Capture stopped during exposure")
                if camera.camera is None:
                    raise Exception("Camera disconnected during exposure")

                status = camera.camera.get_exposure_status()
                if status == camera.asi.ASI_EXP_SUCCESS:
                    exposure_succeeded = True
                    break
                elif status == camera.asi.ASI_EXP_FAILED:
                    if exp_attempt < max_exp_attempts:
                        camera.log(
                            f"⚠ ASI_EXP_FAILED on attempt {exp_attempt}/{max_exp_attempts} — "
                            "retrying exposure (likely USB bandwidth hiccup)"
                        )
                        try:
                            with sdk_lock:
                                if camera.camera:
                                    camera.camera.stop_exposure()
                        except Exception:
                            pass
                        time.sleep(0.3)
                        transient_failure = True
                        break
                    raise Exception("Exposure failed (camera returned ASI_EXP_FAILED status)")
                elif status == camera.asi.ASI_EXP_IDLE:
                    raise Exception("Exposure error: camera returned to IDLE state unexpectedly")

                elapsed = time.time() - start_time
                camera.exposure_remaining = max(0, camera.exposure_seconds - elapsed)
                time.sleep(0.05)

            if exposure_succeeded:
                break
            if not transient_failure:
                # Fell out of wait loop via timeout — don't retry, let the
                # timeout check below raise.
                break

        if not exposure_succeeded and time.time() - start_time >= timeout:
            camera.exposure_remaining = 0.0
            camera.exposure_start_time = None
            raise Exception(
                f"Exposure timeout: camera did not complete {camera.exposure_seconds}s "
                f"exposure within {timeout}s"
            )

        camera.exposure_remaining = 0.0
        camera.exposure_start_time = None

        with sdk_lock:
            if not camera.camera:
                raise Exception("Camera disconnected before data readout")
            img_data = camera.camera.get_data_after_exposure()
            camera_info = camera.camera.get_camera_property()
            width = camera_info['MaxWidth']
            height = camera_info['MaxHeight']

        temp_info = _get_temperature(camera)

        # Pass bit_depth for RAW16 mode support, request raw16 for dev mode
        img_rgb, img_rgb_raw16 = debayer_raw_image(
            img_data, width, height, camera.bayer_pattern,
            bit_depth=camera.current_bit_depth,
            return_raw16=(camera.current_bit_depth == 16),
        )
        img_rgb_no_wb = img_rgb.copy()
        img_rgb = apply_white_balance(img_rgb, camera.wb_config)
        img = Image.fromarray(img_rgb, mode='RGB')

        stats = calculate_image_stats(np.array(img))

        metadata = {
            'CAMERA': camera_info['Name'],
            'EXPOSURE': f"{camera.exposure_seconds}s",
            'GAIN': str(camera.gain),
            'TEMP': temp_info['display'],
            'TEMPERATURE': temp_info['display'],
            'TEMP_C': temp_info['celsius_str'],
            'TEMP_F': temp_info['fahrenheit_str'],
            'RES': f"{width}x{height}",
            'CAPTURE AREA SIZE': f"{width} * {height}",
            'FILENAME': f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            'SESSION': datetime.now().strftime('%Y-%m-%d'),
            'DATETIME': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'BRIGHTNESS': f"{stats['mean']:.1f}",
            'MEAN': f"{stats['mean']:.1f}",
            'MEDIAN': f"{stats['median']:.1f}",
            'MIN': f"{stats['min']}",
            'MAX': f"{stats['max']}",
            'STD_DEV': f"{stats['std_dev']:.2f}",
            'P25': f"{stats['p25']:.1f}",
            'P75': f"{stats['p75']:.1f}",
            'P95': f"{stats['p95']:.1f}",
            'RAW_RGB_NO_WB': img_rgb_no_wb,
            'RAW_RGB_16BIT': img_rgb_raw16,
            'CAMERA_BIT_DEPTH': camera_info.get('BitDepth', 8),
            'IMAGE_BIT_DEPTH': camera.current_bit_depth,
            'BAYER_PATTERN': camera.bayer_pattern,
            'PIXEL_SIZE': camera_info.get('PixelSize', 0),
            'ELEC_PER_ADU': camera_info.get('ElecPerADU', 1.0),
        }

        return img, metadata

    except Exception as e:
        camera.log(f"ERROR capturing frame: {e}")
        raise


def capture_loop(camera: "ZWOCamera"):
    """Background capture loop with automatic recovery and scheduled capture support."""
    camera.log("=== Capture Loop Started ===")
    if camera.scheduled_capture_enabled:
        camera.log(
            f"Scheduled capture enabled: {camera.scheduled_start_time} - {camera.scheduled_end_time}"
        )
    else:
        camera.log("Scheduled capture disabled: will run continuously")

    consecutive_errors = 0
    max_reconnect_attempts = 5
    # Long-interval retry state: after max_reconnect_attempts, rather than
    # permanently exiting (which strands a 24/7 unattended rig), we sleep
    # for an escalating interval and then run the whole recovery cycle
    # again. The flag is only used to gate one-shot Discord alerts so we
    # don't spam on every cycle.
    long_retry_mode = False
    # Backoff schedule: 5m -> 15m -> 1h -> stay at 1h. Reduces Discord/log
    # noise for a permanently dead camera while still trying often enough
    # to self-recover after a transient outage.
    long_retry_schedule = [300, 900, 3600]
    long_retry_cycle = 0
    last_schedule_log = None
    frames_captured = 0
    # Heartbeat + state flags observed by the UI watchdog. _last_frame_time
    # is updated after every successful capture. long_retry_mode_public
    # mirrors the local long_retry_mode so the watchdog can skip bogus
    # "wedged" alerts while we're intentionally sleeping between retries.
    camera._last_frame_time = time.time()
    camera.long_retry_mode_public = False

    # Recalibration rate limiting to prevent infinite loops
    # (e.g., someone turning lights on/off repeatedly)
    last_recalibration_time = 0
    recalibration_cooldown_sec = 60
    recalibration_count = 0
    recalibration_window_start = time.time()
    max_recalibrations_per_window = 3
    recalibration_window_sec = 600

    try:
        if camera.auto_exposure and not camera.calibration_complete:
            try:
                camera.run_calibration()
            except Exception as e:
                camera.log(f"Calibration failed: {e}. Continuing with current settings.")
                camera.calibration_complete = True
    except Exception as e:
        camera.log(f"Error during calibration: {e}")

    try:
        while camera.is_capturing:
            try:
                within_window = camera.is_within_scheduled_window()

                if not within_window:
                    current_status = "outside_window"
                    if last_schedule_log != current_status:
                        camera.log(
                            f"⏸ Outside scheduled capture window "
                            f"({camera.scheduled_start_time} - {camera.scheduled_end_time})"
                        )
                        camera.log(
                            "Entering off-peak mode: disconnecting camera to reduce hardware load..."
                        )
                        last_schedule_log = current_status

                        if camera.camera:
                            try:
                                was_capturing = camera.is_capturing
                                camera.is_capturing = False

                                try:
                                    if camera.exposure_start_time is not None:
                                        camera.camera.stop_exposure()
                                        camera.exposure_start_time = None
                                        camera.exposure_remaining = 0.0
                                except Exception:
                                    pass

                                camera._connection.disconnect()
                                camera.log(
                                    "✓ Camera disconnected for off-peak hours (reducing hardware load)"
                                )

                                camera.is_capturing = was_capturing

                                if camera.status_callback:
                                    camera.status_callback(
                                        f"Idle (off-peak until {camera.scheduled_start_time})"
                                    )
                            except Exception as e:
                                camera.log(f"Error disconnecting camera: {e}")
                                camera.is_capturing = was_capturing

                    wait_end = time.time() + 10.0
                    while camera.is_capturing and time.time() < wait_end:
                        time.sleep(0.2)
                    continue
                else:
                    if last_schedule_log == "outside_window":
                        camera.log(
                            f"▶ Entered scheduled capture window "
                            f"({camera.scheduled_start_time} - {camera.scheduled_end_time})"
                        )
                        camera.log("Transitioning to active capture mode: reconnecting camera...")
                        last_schedule_log = "inside_window"

                        if camera.status_callback:
                            camera.status_callback("Reconnecting for scheduled window...")

                        if not camera.camera:
                            camera.log("Attempting to reconnect camera (re-detecting cameras)...")
                            if not camera.reconnect_camera_safe():
                                camera.log("✗ ERROR: Failed to reconnect camera for scheduled window")
                                camera.log("Will retry in 5 seconds...")
                                wait_end = time.time() + 5.0
                                while camera.is_capturing and time.time() < wait_end:
                                    time.sleep(0.2)
                                continue
                            camera.log("✓ Camera reconnected successfully for scheduled captures")
                            # Suppress watchdog during the first post-reconnect exposure.
                            camera._last_frame_time = time.time()

                if not camera.camera:
                    raise Exception("Camera disconnected")

                img, metadata = camera.capture_single_frame()

                consecutive_errors = 0
                camera._last_frame_time = time.time()
                if long_retry_mode:
                    long_retry_mode = False
                    camera.long_retry_mode_public = False
                    long_retry_cycle = 0
                    camera.log("✓ Capture recovered from long-retry mode")
                    if hasattr(camera, 'on_error_callback') and camera.on_error_callback:
                        try:
                            camera.on_error_callback("Camera recovered — capture resumed")
                        except Exception:
                            pass

                if camera.auto_exposure:
                    img_array = np.array(img)
                    exposure_result = camera.adjust_exposure_auto(img_array)
                    if exposure_result and exposure_result.get('needs_recalibration', False):
                        current_time = time.time()

                        if current_time - recalibration_window_start > recalibration_window_sec:
                            recalibration_count = 0
                            recalibration_window_start = current_time

                        time_since_last = current_time - last_recalibration_time
                        can_recalibrate = (
                            time_since_last >= recalibration_cooldown_sec
                            and recalibration_count < max_recalibrations_per_window
                        )

                        if can_recalibrate:
                            camera.log("⚠ Drastic scene change detected - running rapid calibration")
                            camera.log(
                                f"  (Recalibration {recalibration_count + 1}/"
                                f"{max_recalibrations_per_window} in current window)"
                            )

                            if camera.on_calibration_callback:
                                camera.on_calibration_callback(True)

                            try:
                                camera.run_calibration()
                                last_recalibration_time = time.time()
                                recalibration_count += 1
                            except Exception as cal_error:
                                camera.log(
                                    f"Recalibration error: {cal_error} - continuing with adjusted exposure"
                                )

                            if camera.on_calibration_callback:
                                camera.on_calibration_callback(False)

                            # Skip publishing this badly-exposed frame;
                            # next iteration will capture with calibrated exposure.
                            continue
                        else:
                            if time_since_last < recalibration_cooldown_sec:
                                wait_time = int(recalibration_cooldown_sec - time_since_last)
                                camera.log(
                                    f"⚠ Scene change detected but recalibration on cooldown "
                                    f"({wait_time}s remaining)"
                                )
                            else:
                                camera.log(
                                    f"⚠ Scene change detected but max recalibrations reached "
                                    f"({max_recalibrations_per_window} per "
                                    f"{recalibration_window_sec//60}min window)"
                                )
                            camera.log("  Using aggressive auto-exposure adjustment instead")

                if camera.on_frame_callback:
                    camera.on_frame_callback(img, metadata)

                frames_captured += 1
                if frames_captured == 1 or frames_captured % 100 == 0:
                    camera.log(f"Captured {frames_captured} frames (latest: {metadata['FILENAME']})")

                try:
                    dropped = camera.camera.get_dropped_frames()
                    if dropped > 0:
                        camera.log(f"⚠ USB performance warning: {dropped} dropped frames detected")
                        camera.log(
                            "  Consider: reducing bandwidth_overload, lowering frame rate, "
                            "or checking USB connection"
                        )
                except Exception:
                    pass

                if camera.is_capturing:
                    wait_end = time.time() + camera.capture_interval
                    while camera.is_capturing and time.time() < wait_end:
                        time.sleep(0.2)

            except Exception as e:
                consecutive_errors += 1
                error_msg = str(e)
                camera.log(f"✗ ERROR in capture loop: {error_msg}")
                camera.log(f"Consecutive errors: {consecutive_errors}/{max_reconnect_attempts}")
                camera.log(f"Stack trace: {traceback.format_exc()}")

                capture_error(e, context='camera_capture_loop')

                if (
                    consecutive_errors == 1
                    and hasattr(camera, 'on_error_callback')
                    and camera.on_error_callback
                ):
                    camera.on_error_callback(
                        f"Capture error: {error_msg} - attempting recovery..."
                    )

                if consecutive_errors <= max_reconnect_attempts:
                    camera.log(
                        f"Initiating reconnection attempt "
                        f"{consecutive_errors}/{max_reconnect_attempts}..."
                    )
                    try:
                        # Abort any running calibration before disconnecting so it
                        # doesn't keep calling SDK methods on a dying camera handle.
                        if camera.calibration_manager:
                            camera.calibration_manager.abort()

                        if camera.camera:
                            camera.log("Cleaning up existing camera connection...")
                            camera._connection.disconnect()

                        # ZWO SDK docs recommend waiting 10-15s before reopening
                        # a camera after an error. 0.5s almost guaranteed
                        # "Invalid ID" on first reopen, wasting one of five
                        # recovery attempts. 8s interruptible keeps Stop
                        # responsive.
                        pre_reconnect_wait = 8.0
                        camera.log(
                            f"Waiting {pre_reconnect_wait:.0f}s before reconnection attempt "
                            "(USB bus settle)..."
                        )
                        wait_end = time.time() + pre_reconnect_wait
                        while camera.is_capturing and time.time() < wait_end:
                            time.sleep(0.2)

                        if camera.reconnect_camera_safe():
                            camera.log("✓ Camera reconnected successfully")
                            consecutive_errors = 0
                            # With multiple USB cameras the bus needs time to settle.
                            # Probe the camera before resuming to confirm it's live.
                            camera.log("Waiting 3s for USB bus to stabilise...")
                            wait_end = time.time() + 3.0
                            while camera.is_capturing and time.time() < wait_end:
                                time.sleep(0.2)
                            try:
                                camera.camera.get_camera_property()
                            except Exception as probe_err:
                                raise Exception(
                                    f"Camera not responding after reconnect: {probe_err}"
                                )
                            # Suppress watchdog during the first post-reconnect exposure.
                            camera._last_frame_time = time.time()
                            continue
                        else:
                            raise Exception("Failed to reconnect camera")
                    except Exception as reconnect_error:
                        camera.log(f"✗ Reconnection attempt failed: {reconnect_error}")
                        camera.log(f"Stack trace: {traceback.format_exc()}")
                        backoff_time = min(2 ** consecutive_errors, 32)
                        camera.log(
                            f"Using exponential backoff: waiting {backoff_time}s "
                            f"before next recovery cycle "
                            f"(attempt {consecutive_errors}/{max_reconnect_attempts} failed)..."
                        )
                        wait_end = time.time() + backoff_time
                        while camera.is_capturing and time.time() < wait_end:
                            time.sleep(0.2)
                else:
                    # Max attempts reached — enter long-interval retry mode
                    # instead of permanently exiting. The rig is unattended;
                    # we'd rather keep trying every few minutes than leave
                    # the user staring at a stale image all night.
                    interval = long_retry_schedule[
                        min(long_retry_cycle, len(long_retry_schedule) - 1)
                    ]
                    if not long_retry_mode:
                        long_retry_mode = True
                        camera.long_retry_mode_public = True
                        camera.log(
                            f"✗ Maximum reconnection attempts ({max_reconnect_attempts}) reached"
                        )
                        camera.log(
                            f"⏳ Entering long-interval retry mode — first retry in {interval}s "
                            "(backoff: 5m → 15m → 1h)"
                        )
                        camera.log("Troubleshooting steps:")
                        camera.log("  1. Check USB cable connection")
                        camera.log("  2. Check camera power supply")
                        camera.log("  3. Try: Physically disconnect USB, wait 5 seconds, reconnect")
                        camera.log("  4. Check Windows Device Manager for USB errors")
                        camera.log(
                            "  5. If persistent: Update ZWO drivers from astronomy-imaging-camera.com"
                        )
                        if hasattr(camera, 'on_error_callback') and camera.on_error_callback:
                            try:
                                camera.on_error_callback(
                                    f"Camera unreachable — retrying every {interval // 60} min"
                                )
                            except Exception:
                                pass
                    else:
                        camera.log(
                            f"⏳ Long-retry cycle {long_retry_cycle + 1} — "
                            f"next retry in {interval}s"
                        )

                    wait_end = time.time() + interval
                    while camera.is_capturing and time.time() < wait_end:
                        time.sleep(0.2)

                    long_retry_cycle += 1
                    consecutive_errors = 0
    finally:
        camera.log("Capture loop exiting - cleaning up...")
        # Note: snapshot mode (start_exposure/get_data_after_exposure),
        # NOT video mode. Camera cleanup handled by disconnect_camera()
        # via stop_capture().

        # If capture is exiting while is_capturing is still True, something
        # fatal (unhandled exception) forced us out — tell the UI so it can
        # tear down state, not sit pretending we're still running.
        if (
            camera.is_capturing
            and hasattr(camera, 'on_error_callback')
            and camera.on_error_callback
        ):
            camera.is_capturing = False
            try:
                camera.on_error_callback(
                    "Capture loop terminated unexpectedly",
                    is_fatal=True,
                )
            except TypeError:
                try:
                    camera.on_error_callback("Capture loop terminated unexpectedly")
                except Exception:
                    pass
            except Exception:
                pass

    camera.log("Capture loop stopped")

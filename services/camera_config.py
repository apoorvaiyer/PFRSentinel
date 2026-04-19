"""
Camera configuration helpers for ZWO ASI cameras.

Pure helper functions used by CameraConnection.configure() — do not import directly
from other modules; call through the CameraConnection interface.
"""
from typing import Any, Dict, Optional, Tuple


def validate_control(controls, control_type, value, name, log) -> Tuple[Any, Optional[Dict]]:
    """Validate and clamp a control value to the camera's supported range."""
    ctrl = next((c for c in controls.values() if c['ControlType'] == control_type), None)
    if ctrl:
        validated = max(ctrl['MinValue'], min(ctrl['MaxValue'], value))
        if validated != value:
            log(f"  ⚠ {name} {value} out of range [{ctrl['MinValue']}-{ctrl['MaxValue']}], using {validated}")
        return validated, ctrl
    return value, None


def verify_camera_identity(camera, camera_name: Optional[str], log) -> bool:
    """
    Verify the open camera handle still points to the expected physical camera.
    Returns True if identity matches, False if mismatch or no camera.
    """
    if not camera or not camera_name:
        return False
    try:
        actual_name = camera.get_camera_property()['Name']
        if camera_name not in actual_name:
            log(
                f"✗ Camera identity MISMATCH: expected '{camera_name}', "
                f"SDK returned '{actual_name}'. Refusing operation."
            )
            return False
        return True
    except Exception as e:
        log(f"✗ Camera identity check failed: {e}")
        return False


def configure_white_balance(camera, asi, settings: Dict[str, Any], log) -> None:
    """Configure white balance based on mode."""
    wb_mode = settings.get('wb_mode', 'asi_auto')

    if wb_mode == 'asi_auto':
        try:
            camera.set_control_value(asi.ASI_AUTO_MAX_BRIGHTNESS, 1)
            log("  White balance: ASI Auto")
        except Exception:
            pass
    else:
        try:
            camera.set_control_value(asi.ASI_AUTO_MAX_BRIGHTNESS, 0)
        except Exception:
            pass

        if wb_mode == 'manual':
            wb_r, wb_b = settings.get('wb_r', 75), settings.get('wb_b', 99)
            camera.set_control_value(asi.ASI_WB_R, wb_r)
            camera.set_control_value(asi.ASI_WB_B, wb_b)
            log(f"  White balance: Manual (R={wb_r}, B={wb_b})")
        elif wb_mode == 'gray_world':
            camera.set_control_value(asi.ASI_WB_R, 50)
            camera.set_control_value(asi.ASI_WB_B, 50)
            log("  White balance: Gray World (software)")


def configure_camera(camera, asi, settings: Dict[str, Any], supports_raw16: bool, log) -> Tuple[Any, int]:
    """
    Apply settings to a connected camera.

    Returns (image_type, current_bit_depth) for the caller to store on the
    CameraConnection instance.  Raises on SDK errors — callers should wrap in try/except.
    """
    camera_info = camera.get_camera_property()
    controls = camera.get_controls()

    if 'gain' in settings:
        gain, ctrl = validate_control(controls, asi.ASI_GAIN, settings['gain'], "Gain", log)
        camera.set_control_value(asi.ASI_GAIN, gain)
        rng = f" (range: {ctrl['MinValue']}-{ctrl['MaxValue']})" if ctrl else ""
        log(f"  Gain: {gain}{rng}")

    if 'exposure_sec' in settings:
        exposure_us = int(settings['exposure_sec'] * 1000000)
        exposure_us, ctrl = validate_control(controls, asi.ASI_EXPOSURE, exposure_us, "Exposure", log)
        camera.set_control_value(asi.ASI_EXPOSURE, exposure_us)
        log(f"  Exposure: {exposure_us/1000000}s ({exposure_us/1000}ms)")

    configure_white_balance(camera, asi, settings, log)

    camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, 40)
    if 'offset' in settings:
        camera.set_control_value(asi.ASI_BRIGHTNESS, settings['offset'])

    flip = settings.get('flip', 0)
    if flip in (1, 3):
        camera.set_control_value(asi.ASI_FLIP, 1)
    if flip in (2, 3):
        camera.set_control_value(asi.ASI_FLIP, 2)

    use_raw16 = settings.get('use_raw16', False) and supports_raw16
    image_type = asi.ASI_IMG_RAW16 if use_raw16 else asi.ASI_IMG_RAW8
    current_bit_depth = 16 if use_raw16 else 8

    # Set ROI to full frame — required on every connect because the SDK can
    # retain a stale ROI from a prior session, causing reshape failures.
    camera.set_roi(start_x=0, start_y=0, width=camera_info['MaxWidth'],
                   height=camera_info['MaxHeight'], bins=1, image_type=image_type)
    camera.set_image_type(image_type)

    mode_str = "RAW16" if use_raw16 else "RAW8"
    log(f"  ROI: Full frame {camera_info['MaxWidth']}x{camera_info['MaxHeight']} ({mode_str})")
    log("Camera configuration applied")

    return image_type, current_bit_depth

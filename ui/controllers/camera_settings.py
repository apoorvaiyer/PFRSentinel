from services.logger import app_logger
from services.config import DEFAULT_CAMERA_PROFILE


def apply_camera_settings(zwo_camera, config):
    """Push all mutable settings to a running ZWOCamera instance (no stop/restart needed).

    The caller is responsible for checking zwo_camera is not None before calling.
    """
    camera_name = config.get('zwo_selected_camera_name', '')
    if '(Index:' in camera_name:
        camera_name = camera_name.split('(Index:')[0].strip()

    profile = (
        config.get_camera_profile(camera_name) if camera_name
        else dict(DEFAULT_CAMERA_PROFILE)
    )

    exposure_ms = profile.get('exposure_ms', DEFAULT_CAMERA_PROFILE['exposure_ms'])
    gain = profile.get('gain', DEFAULT_CAMERA_PROFILE['gain'])
    auto_exposure = config.get('zwo_auto_exposure', False)
    target_brightness = profile.get('target_brightness', DEFAULT_CAMERA_PROFILE['target_brightness'])
    max_exposure_ms = profile.get('max_exposure_ms', DEFAULT_CAMERA_PROFILE['max_exposure_ms'])

    zwo_camera.auto_exposure = auto_exposure
    zwo_camera.target_brightness = target_brightness
    zwo_camera.max_exposure = max_exposure_ms / 1000.0

    if not auto_exposure:
        zwo_camera.set_exposure(exposure_ms / 1000.0)
    else:
        # Clamp auto-exposure immediately if max was lowered below current value.
        max_sec = max_exposure_ms / 1000.0
        if zwo_camera.exposure_seconds > max_sec:
            app_logger.info(
                f"Clamping auto-exposure from "
                f"{zwo_camera.exposure_seconds*1000:.0f}ms to "
                f"new max {max_sec*1000:.0f}ms"
            )
            zwo_camera.set_exposure(max_sec)

    zwo_camera.set_gain(gain)

    if zwo_camera.calibration_manager:
        # When auto-exposure is active, do NOT override the current exposure —
        # let the algorithm keep its computed value.
        cal_exposure = None if auto_exposure else exposure_ms / 1000.0
        zwo_camera.calibration_manager.update_settings(
            exposure_seconds=cal_exposure,
            gain=gain,
            target_brightness=target_brightness,
            max_exposure_sec=max_exposure_ms / 1000.0,
        )

    zwo_camera.set_capture_interval(config.get('zwo_interval', 5.0))

    offset = profile.get('offset', DEFAULT_CAMERA_PROFILE['offset'])
    zwo_camera.offset = offset
    if zwo_camera.camera and zwo_camera.asi:
        try:
            zwo_camera.camera.set_control_value(zwo_camera.asi.ASI_BRIGHTNESS, offset)
        except Exception as e:
            app_logger.debug(f"Could not set offset live: {e}")

    flip = profile.get('flip', DEFAULT_CAMERA_PROFILE['flip'])
    if flip != zwo_camera.flip:
        zwo_camera.flip = flip
        if zwo_camera.camera and zwo_camera.asi:
            try:
                zwo_camera.camera.set_control_value(zwo_camera.asi.ASI_FLIP, flip)
            except Exception as e:
                app_logger.debug(f"Could not set flip live: {e}")

    bayer = profile.get('bayer_pattern', DEFAULT_CAMERA_PROFILE['bayer_pattern'])
    zwo_camera.bayer_pattern = bayer

    mode = config.get('scheduled_capture_mode', 'always')
    zwo_camera.scheduled_capture_mode = mode
    zwo_camera.scheduled_capture_enabled = mode != 'always'
    zwo_camera.scheduled_start_time = config.get('scheduled_start_time', '17:00')
    zwo_camera.scheduled_end_time = config.get('scheduled_end_time', '09:00')
    zwo_camera.scheduled_window_interval = config.get('scheduled_window_interval', 5.0)

    wb_settings = config.get('white_balance', {})
    zwo_camera.wb_config = dict(wb_settings)
    zwo_camera.wb_config.setdefault('mode', 'asi_auto')
    zwo_camera.wb_mode = wb_settings.get('mode', 'asi_auto')

    app_logger.debug(
        f"Settings updated live: exposure={exposure_ms}ms, gain={gain}, "
        f"max_exposure={max_exposure_ms}ms, interval={zwo_camera.capture_interval}s, "
        f"offset={offset}, flip={flip}, bayer={bayer}"
    )

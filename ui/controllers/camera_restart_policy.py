"""Restart policy + boot-loop guard for camera auto-recovery.

When a ZWO SDK wedge can't be cleared in-process (corrupted DLL, or USB reset
unavailable because the app isn't Administrator), the only cure is relaunching
the app — a fresh process loads a clean copy of ASICamera2.dll. This module
decides WHEN that's safe: only inside the capture window, only after a real
capture this session, and capped per rolling hour (persisted to config so the
cap survives the relaunch). Kept out of camera_controller.py for the size cap
and because restart policy is a distinct concern from capture orchestration.
"""
import time

from services.logger import app_logger

# Cap auto-restarts within a rolling hour so a flapping camera can't boot-loop.
_MAX_RESTARTS_PER_HOUR = 3


def within_capture_window(config) -> bool:
    """True when capture should be running now — restart only fires in-window so
    we never churn during intentional off-peak idle. 'always' mode is 24/7."""
    try:
        mode = config.get('scheduled_capture_mode', 'always')
        if mode == 'always':
            return True
        from services.camera.camera_utils import is_within_scheduled_window
        return bool(is_within_scheduled_window(
            True,
            config.get('scheduled_start_time', '17:00'),
            config.get('scheduled_end_time', '09:00'),
        ))
    except Exception:
        return False


def load_restart_history(config) -> list:
    """Stored auto-restart timestamps from config (sanitized to floats).

    The rolling-hour cap lives solely in attempt_restart, which re-trims at
    decision time — sessions can run for hours, so trimming here too would just
    be a stale duplicate of that authoritative filter."""
    try:
        hist = config.get('camera_restart_history', []) or []
        return [float(t) for t in hist]
    except Exception:
        return []


def attempt_restart(controller, reason: str) -> bool:
    """Relaunch the app to clear a wedged SDK, if the guards allow.

    Returns True if a restart was launched (the app is now quitting), False if
    a guard blocked it (caller should fall back to alert-and-wait).
    """
    if not within_capture_window(controller.config):
        app_logger.warning(
            "Camera unrecoverable but outside the capture window — not "
            "restarting; alerting and waiting for the next window."
        )
        return False
    if controller._last_successful_frame_ts <= 0:
        app_logger.warning(
            "Camera unrecoverable but no successful capture this session — "
            "not restarting (would risk a boot loop)."
        )
        return False
    now = time.time()
    controller._restart_times = [
        t for t in controller._restart_times if now - t < 3600.0
    ]
    if len(controller._restart_times) >= _MAX_RESTARTS_PER_HOUR:
        app_logger.error(
            f"Auto-restart cap ({_MAX_RESTARTS_PER_HOUR}/hr) reached — not "
            "restarting; alerting instead."
        )
        return False
    restart = getattr(controller.main_window, 'restart_application', None)
    if not callable(restart):
        return False
    controller._restart_times.append(now)
    try:
        controller.config.set('camera_restart_history', controller._restart_times)
        controller.config.save()
    except Exception:
        pass
    app_logger.error(
        f"Camera unrecoverable inside the capture window — restarting app: {reason}"
    )
    try:
        return bool(restart(f"camera recovery: {reason}"))
    except Exception as e:
        app_logger.error(f"Restart call failed: {e}")
        return False

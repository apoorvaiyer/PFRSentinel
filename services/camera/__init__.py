"""
services.camera — ZWO ASI camera subpackage.

Re-exports the public interface so existing callers using
``from services.camera_utils import X`` or ``from services.zwo_camera import ZWOCamera``
can migrate to ``from services.camera import X`` at their own pace.

Direct imports from services.camera_xxx are kept working via the
backwards-compat shims in services/camera_xxx.py (which remain in place).
"""
from .zwo_camera import ZWOCamera
from .camera_connection import CameraConnection
from .camera_calibration import CameraCalibration
from .camera_utils import (
    clean_camera_name,
    is_within_scheduled_window,
    calculate_brightness,
    check_clipping,
    debayer_raw_image,
    apply_white_balance,
    calculate_image_stats,
)

__all__ = [
    'ZWOCamera',
    'CameraConnection',
    'CameraCalibration',
    'clean_camera_name',
    'is_within_scheduled_window',
    'calculate_brightness',
    'check_clipping',
    'debayer_raw_image',
    'apply_white_balance',
    'calculate_image_stats',
]

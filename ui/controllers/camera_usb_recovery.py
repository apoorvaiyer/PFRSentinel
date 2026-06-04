"""Background USB device reset for wedged/missing ZWO cameras.

Extracted from CameraControllerQt to keep that file under the size cap and to
give the USB-reset concern its own home. A single UsbResetWorker instance is
shared by both recovery-triggered resets and the user's "Revive" button, so its
in-progress flag serializes them — two concurrent pnputil / CM_* device toggles
race on the same hardware.

A USB disable/enable is an OS-level operation (not a ZWO SDK call), so it is
safe to run even while a capture thread is wedged inside the SDK — and it often
forces that stuck call to error out so the thread can finally unwind.
"""
import sys
import threading

from services.logger import app_logger
from services.camera import clean_camera_name


class UsbResetWorker:
    """Runs a ZWO camera USB disable/enable on a daemon thread.

    Windows-only in practice (the disable/enable uses the Windows CM_* API).
    On other platforms, or with no camera name, it reports failure via on_done
    rather than raising — callers decide how to escalate.
    """

    def __init__(self):
        self._in_progress = False

    @property
    def in_progress(self) -> bool:
        return self._in_progress

    def run_async(self, camera_name: str, on_done) -> None:
        """Toggle the camera's USB device, then call ``on_done(ok, name)``.

        on_done always fires exactly once (so a UI button never stays greyed
        out). For the trivial-reject cases (non-Windows, no name, already
        running) it fires synchronously on the calling thread; for a real reset
        it fires from the worker thread. Callers that touch Qt widgets should
        marshal to the main thread inside on_done (e.g. via a signal emit).
        """
        name = clean_camera_name(camera_name or '')
        if sys.platform != 'win32' or not name:
            if sys.platform != 'win32':
                app_logger.info("USB reset unavailable: not on Windows.")
            else:
                app_logger.warning("USB reset skipped: no camera name.")
            on_done(False, name)
            return
        if self._in_progress:
            app_logger.warning(
                f"USB reset already in progress — ignoring request for '{name}'"
            )
            on_done(False, name)
            return
        self._in_progress = True

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
                self._in_progress = False
                on_done(ok, name)

        threading.Thread(target=worker, daemon=True).start()

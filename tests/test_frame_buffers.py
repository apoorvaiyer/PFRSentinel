"""Tests for the ping-pong frame buffer pool on ZWOCamera and the dst= path
in debayer_raw_image.  No hardware required."""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_camera():
    """Build a ZWOCamera with CameraConnection mocked out."""
    from services.camera.zwo_camera import ZWOCamera
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(asi=None, camera=None, sdk_lock=MagicMock())
        cam = ZWOCamera(sdk_path=None)
    cam.on_log_callback = lambda _: None
    return cam


# ---------------------------------------------------------------------------
# _ensure_frame_buffers
# ---------------------------------------------------------------------------

class TestEnsureFrameBuffers:

    def test_allocates_two_slots_on_first_call(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 8)
        assert len(cam._frame_bufs) == 2
        assert cam._frame_bufs[0]['rgb8'].shape == (80, 100, 3)
        assert cam._frame_bufs[0]['rgb8'].dtype == np.uint8

    def test_raw16_slot_has_rgb16_buffer(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 16)
        assert cam._frame_bufs[0]['rgb16'] is not None
        assert cam._frame_bufs[0]['rgb16'].shape == (80, 100, 3)
        assert cam._frame_bufs[0]['rgb16'].dtype == np.uint16

    def test_raw8_slot_has_no_rgb16_buffer(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 8)
        assert cam._frame_bufs[0]['rgb16'] is None

    def test_noop_when_geometry_unchanged(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 8)
        ref = cam._frame_bufs[0]['rgb8']
        cam._frame_buf_idx = 0  # reset so we get slot 0 again
        cam._ensure_frame_buffers(100, 80, 8)
        assert cam._frame_bufs[0]['rgb8'] is ref  # same object, no realloc

    def test_reallocates_on_width_change(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 8)
        ref = cam._frame_bufs[0]['rgb8']
        cam._ensure_frame_buffers(200, 80, 8)
        assert cam._frame_bufs[0]['rgb8'] is not ref
        assert cam._frame_bufs[0]['rgb8'].shape == (80, 200, 3)

    def test_reallocates_on_depth_change(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 8)
        cam._ensure_frame_buffers(100, 80, 16)
        assert cam._frame_bufs[0]['rgb16'] is not None

    def test_ping_pong_index_alternates(self):
        cam = _make_camera()
        buf0 = cam._ensure_frame_buffers(100, 80, 8)
        buf1 = cam._ensure_frame_buffers(100, 80, 8)
        buf2 = cam._ensure_frame_buffers(100, 80, 8)
        assert buf0 is not buf1
        assert buf0 is buf2  # wraps back to slot 0


class TestReleaseFrameBuffers:

    def test_clears_all_state(self):
        cam = _make_camera()
        cam._ensure_frame_buffers(100, 80, 16)
        cam._release_frame_buffers()
        assert cam._frame_bufs == []
        assert cam._frame_buf_width == 0
        assert cam._frame_buf_height == 0
        assert cam._frame_buf_depth == 0
        assert cam._frame_buf_idx == 0

    def test_safe_to_call_when_empty(self):
        cam = _make_camera()
        cam._release_frame_buffers()  # must not raise


# ---------------------------------------------------------------------------
# debayer_raw_image dst= parameter
# ---------------------------------------------------------------------------

try:
    import cv2 as _cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


@pytest.mark.skipif(not HAS_CV2, reason="cv2 not installed")
class TestDebayerDstBuffers:

    def _bayer_bytes(self, h, w, dtype=np.uint8):
        rng = np.random.default_rng(42)
        return rng.integers(0, 256, (h * w,), dtype=dtype).tobytes()

    def test_dst_rgb8_written_for_raw8(self):
        from services.camera.camera_utils import debayer_raw_image
        h, w = 64, 64
        dst = np.zeros((h, w, 3), dtype=np.uint8)
        rgb, _ = debayer_raw_image(self._bayer_bytes(h, w), w, h, 'BGGR', bit_depth=8, dst_rgb8=dst)
        assert rgb is dst
        assert dst.max() > 0  # data was actually written

    def test_dst_rgb16_written_for_raw16(self):
        from services.camera.camera_utils import debayer_raw_image
        h, w = 64, 64
        dst16 = np.zeros((h, w, 3), dtype=np.uint16)
        raw16 = self._bayer_bytes(h, w, dtype=np.uint16)
        _, rgb16 = debayer_raw_image(raw16, w, h, 'BGGR', bit_depth=16, return_raw16=True, dst_rgb16=dst16)
        assert rgb16 is dst16
        assert dst16.max() > 0

    def test_no_dst_still_works(self):
        from services.camera.camera_utils import debayer_raw_image
        h, w = 64, 64
        rgb, _ = debayer_raw_image(self._bayer_bytes(h, w), w, h, 'BGGR', bit_depth=8)
        assert rgb.shape == (h, w, 3)
        assert rgb.dtype == np.uint8

    def test_dst_none_raw16_returns_independent_copy(self):
        from services.camera.camera_utils import debayer_raw_image
        h, w = 32, 32
        raw16 = self._bayer_bytes(h, w, dtype=np.uint16)
        rgb8, rgb16 = debayer_raw_image(raw16, w, h, 'BGGR', bit_depth=16, return_raw16=True)
        assert rgb16 is not None
        assert rgb16.dtype == np.uint16
        assert rgb8.dtype == np.uint8

    def test_dst_rgb8_values_match_no_dst_path(self):
        """Pixel values written into dst_rgb8 must equal the no-dst return value."""
        from services.camera.camera_utils import debayer_raw_image
        h, w = 64, 64
        raw = self._bayer_bytes(h, w)
        dst = np.zeros((h, w, 3), dtype=np.uint8)
        rgb_with_dst, _ = debayer_raw_image(raw, w, h, 'BGGR', bit_depth=8, dst_rgb8=dst)
        rgb_no_dst, _ = debayer_raw_image(raw, w, h, 'BGGR', bit_depth=8)
        np.testing.assert_array_equal(rgb_with_dst, rgb_no_dst)

    def test_dst_rgb16_values_match_no_dst_path(self):
        """16-bit values written into dst_rgb16 must equal the no-dst return value."""
        from services.camera.camera_utils import debayer_raw_image
        h, w = 32, 32
        raw16 = self._bayer_bytes(h, w, dtype=np.uint16)
        dst16 = np.zeros((h, w, 3), dtype=np.uint16)
        _, rgb16_dst = debayer_raw_image(raw16, w, h, 'BGGR', bit_depth=16,
                                         return_raw16=True, dst_rgb16=dst16)
        _, rgb16_no_dst = debayer_raw_image(raw16, w, h, 'BGGR', bit_depth=16,
                                             return_raw16=True)
        np.testing.assert_array_equal(rgb16_dst, rgb16_no_dst)


# ---------------------------------------------------------------------------
# simple_debayer_rggb fallback also honours dst_rgb8
# ---------------------------------------------------------------------------

class TestDebayerFallbackDst:
    """cv2 import is patched out to exercise the simple_debayer_rggb code path."""

    def test_fallback_writes_into_dst_rgb8(self):
        import sys
        import importlib
        from unittest.mock import patch
        with patch.dict(sys.modules, {'cv2': None}):
            import services.camera.camera_utils as cu
            importlib.reload(cu)
            h, w = 16, 16
            rng = np.random.default_rng(7)
            raw = rng.integers(0, 256, (h * w,), dtype=np.uint8).tobytes()
            dst = np.zeros((h, w, 3), dtype=np.uint8)
            rgb, _ = cu.debayer_raw_image(raw, w, h, 'BGGR', bit_depth=8, dst_rgb8=dst)
            assert rgb is dst
            assert dst.max() > 0
        importlib.reload(cu)  # restore for other tests


# ---------------------------------------------------------------------------
# Deep-copy guard at cache boundary (source-level)
# ---------------------------------------------------------------------------

class TestCachedMetadataDeepCopy:

    def test_on_image_captured_deep_copies_large_arrays(self):
        import inspect
        from ui.main_window import output as mw_output
        src = inspect.getsource(mw_output._MainWindowOutputMixin.on_image_captured)
        # Both keys must be deep-copied (either via explicit assignment or a loop)
        assert "'RAW_RGB_16BIT'" in src or '"RAW_RGB_16BIT"' in src, (
            "RAW_RGB_16BIT must be referenced in on_image_captured for deep-copy."
        )
        assert "'RAW_RGB_NO_WB'" in src or '"RAW_RGB_NO_WB"' in src, (
            "RAW_RGB_NO_WB must be referenced in on_image_captured for deep-copy."
        )
        assert ".copy()" in src, (
            "on_image_captured must call .copy() on the numpy arrays so the "
            "camera's ping-pong buffer is free for the next frame immediately."
        )

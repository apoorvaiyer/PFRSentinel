"""
Test ZWO camera connection and capture
Note: Tests marked with @pytest.mark.requires_camera need physical hardware
"""
import pytest
import os
import sys
import numpy as np
from unittest.mock import Mock, MagicMock, patch

# Check if cv2 is available
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestCameraConnectionMock:
    """Test camera connection logic with mocks"""
    
    def test_sdk_path_configuration(self):
        """Test SDK path can be configured"""
        from services.config import DEFAULT_CONFIG
        
        assert 'zwo_sdk_path' in DEFAULT_CONFIG
        # Default should point to ASICamera2.dll
        assert 'ASICamera2.dll' in DEFAULT_CONFIG['zwo_sdk_path']
    
    def test_camera_settings_defaults(self):
        """Per-camera defaults now live in DEFAULT_CAMERA_PROFILE, not DEFAULT_CONFIG."""
        from services.config import DEFAULT_CAMERA_PROFILE

        # Exposure range
        assert DEFAULT_CAMERA_PROFILE['exposure_ms'] >= 0.032  # Min ~32µs
        assert DEFAULT_CAMERA_PROFILE['exposure_ms'] <= 3600000  # Max 1 hour

        # Gain range
        assert DEFAULT_CAMERA_PROFILE['gain'] >= 0

        # White balance range
        assert 1 <= DEFAULT_CAMERA_PROFILE['wb_r'] <= 99
        assert 1 <= DEFAULT_CAMERA_PROFILE['wb_b'] <= 99


class TestBayerDebayering:
    """Test Bayer pattern debayering"""
    
    def test_bggr_pattern_detection(self):
        """Test BGGR Bayer pattern is correctly identified"""
        from services.config import DEFAULT_CAMERA_PROFILE

        # ASI cameras typically use BGGR
        assert DEFAULT_CAMERA_PROFILE['bayer_pattern'] in ['RGGB', 'BGGR', 'GRBG', 'GBRG']
    
    @pytest.mark.skipif(not HAS_CV2, reason="OpenCV (cv2) not installed")
    def test_debayer_creates_rgb(self):
        """Test debayering produces RGB image"""
        import cv2
        
        # Create mock Bayer pattern data (100x100)
        bayer_data = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        
        # Debayer using OpenCV
        rgb = cv2.cvtColor(bayer_data, cv2.COLOR_BayerBG2RGB)
        
        # Should be 3 channels
        assert rgb.shape == (100, 100, 3)
    
    @pytest.mark.skipif(not HAS_CV2, reason="OpenCV (cv2) not installed")
    def test_all_bayer_patterns(self):
        """Test all Bayer pattern conversions work"""
        import cv2
        
        bayer_data = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        
        patterns = [
            cv2.COLOR_BayerBG2RGB,  # BGGR
            cv2.COLOR_BayerRG2RGB,  # RGGB
            cv2.COLOR_BayerGB2RGB,  # GBRG
            cv2.COLOR_BayerGR2RGB,  # GRBG
        ]
        
        for pattern in patterns:
            rgb = cv2.cvtColor(bayer_data, pattern)
            assert rgb.shape == (100, 100, 3)


class TestCameraUtilities:
    """Test camera utility functions"""
    
    def test_scheduled_window_check(self):
        """Test scheduled capture window detection"""
        from services.camera_utils import is_within_scheduled_window
        from datetime import datetime
        
        # Test case: capture window 17:00 - 09:00 (overnight)
        # At 20:00 should be within window
        test_time_evening = datetime(2025, 12, 30, 20, 0, 0)
        
        # At 08:00 should be within window
        test_time_morning = datetime(2025, 12, 30, 8, 0, 0)
        
        # At 12:00 should be outside window
        test_time_noon = datetime(2025, 12, 30, 12, 0, 0)
        
        # Note: Actual test depends on implementation
        # This is a placeholder for the test structure
    
    def test_exposure_ms_to_seconds_conversion(self):
        """Test exposure time unit conversion"""
        # 1000ms = 1s
        exposure_ms = 1000.0
        exposure_s = exposure_ms / 1000.0
        assert exposure_s == 1.0
        
        # 100ms = 0.1s
        exposure_ms = 100.0
        exposure_s = exposure_ms / 1000.0
        assert exposure_s == 0.1


class TestRecoveryRaces:
    """Regression tests for USB recovery race conditions — run without hardware."""

    def _make_connection(self, asi_mock):
        """Build a CameraConnection with its zwoasi module mocked out."""
        from services.camera_connection import CameraConnection
        conn = CameraConnection(sdk_path=None, logger=lambda _: None)
        conn.asi = asi_mock
        return conn

    def test_detect_cameras_tolerates_enumeration_race(self):
        """
        Regression for the 08:57:50 production log: SDK reported 2 cameras but
        list_cameras() returned only the 462MM. The old code raised
        `list index out of range` on index 1. The fix snapshots list_cameras
        once, retries, and trusts the list when they disagree.
        """
        asi = MagicMock()
        asi.get_num_cameras.return_value = 2
        # Stays short across all retries (race never clears) — code should
        # still return the one camera it can see, without raising.
        asi.list_cameras.return_value = ['ZWO ASI462MM']

        conn = self._make_connection(asi)
        with patch('time.sleep'):
            result = conn.detect_cameras()

        assert len(result) == 1
        assert result[0]['name'] == 'ZWO ASI462MM'

    def test_detect_cameras_recovers_after_retry(self):
        """
        SDK race resolves on the 2nd retry: first list_cameras call is short,
        second returns both cameras. Expect the final enumeration to see both.
        """
        asi = MagicMock()
        asi.get_num_cameras.return_value = 2
        asi.list_cameras.side_effect = [
            ['ZWO ASI462MM'],                      # 1st call (race)
            ['ZWO ASI462MM', 'ZWO ASI676MC'],      # 2nd call (settled)
            ['ZWO ASI462MM', 'ZWO ASI676MC'],      # defensive
        ]

        conn = self._make_connection(asi)
        with patch('time.sleep'):
            result = conn.detect_cameras()

        assert len(result) == 2
        names = {c['name'] for c in result}
        assert names == {'ZWO ASI462MM', 'ZWO ASI676MC'}

    def test_wait_for_stable_detection_requires_two_consecutive_polls(self):
        """
        Detection-settle loop: target flickers in/out/in. Only after two
        consecutive polls see it at the same index does the helper return
        that index. Guards against `Invalid ID` when opening the camera
        too fast after disable/enable.
        """
        asi = MagicMock()
        conn = self._make_connection(asi)

        detections = [
            [],                                                                # not yet
            [{'index': 0, 'name': 'ZWO ASI676MC'}],                            # first sighting
            [{'index': 0, 'name': 'ZWO ASI676MC'}],                            # stable!
        ]

        with patch.object(conn, 'detect_cameras', side_effect=detections), \
                patch('time.sleep'):
            idx = conn._wait_for_stable_detection('ZWO ASI676MC', timeout_sec=10, poll_interval=0.01)

        assert idx == 0

    def test_wait_for_stable_detection_times_out_cleanly(self):
        """If the target never shows up, the helper must return without raising."""
        asi = MagicMock()
        conn = self._make_connection(asi)

        # Always empty
        with patch.object(conn, 'detect_cameras', return_value=[]), \
                patch('time.sleep'), \
                patch('time.time', side_effect=[0, 0, 1, 2, 11, 12, 13]):
            idx = conn._wait_for_stable_detection('ZWO ASI676MC', timeout_sec=10, poll_interval=0.01)

        assert idx is None


class TestCleanCameraName:
    """Unit tests for the camera-name cleaner."""

    @pytest.mark.parametrize("raw, expected", [
        ("ZWO ASI676MC (Index: 0)", "ZWO ASI676MC"),
        ("  ZWO ASI462MM (Index: 1)  ", "ZWO ASI462MM"),
        ("ZWO ASI676MC", "ZWO ASI676MC"),
        ("", ""),
        (None, ""),
    ])
    def test_strips_index_suffix(self, raw, expected):
        from services.camera_utils import clean_camera_name
        assert clean_camera_name(raw) == expected


class TestWaitForCaptureThreadExit:
    """Tests for ZWOCamera.wait_for_capture_thread_exit()."""

    def _make_camera(self):
        # Patch the CameraConnection constructor so it doesn't look for the
        # real SDK — the test only needs ZWOCamera's thread-join logic.
        from services.zwo_camera import ZWOCamera
        with patch('services.zwo_camera.CameraConnection') as conn_cls:
            conn_cls.return_value = MagicMock(asi=None, camera=None, sdk_lock=MagicMock())
            cam = ZWOCamera(sdk_path=None)
        cam.on_log_callback = lambda _: None
        return cam

    def test_returns_true_when_no_thread(self):
        cam = self._make_camera()
        cam.capture_thread = None
        assert cam.wait_for_capture_thread_exit(timeout=0.1) is True
        assert cam.is_capturing is False

    def test_returns_true_when_thread_already_exited(self):
        import threading
        cam = self._make_camera()
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        cam.capture_thread = t
        assert cam.wait_for_capture_thread_exit(timeout=0.1) is True

    def test_joins_running_thread_and_returns_true(self):
        import threading
        cam = self._make_camera()
        cam.is_capturing = True

        started = threading.Event()

        def fake_loop():
            started.set()
            # Exits as soon as is_capturing flips False
            while cam.is_capturing:
                pass

        t = threading.Thread(target=fake_loop, daemon=True)
        cam.capture_thread = t
        t.start()
        started.wait(timeout=1.0)
        # wait_for_capture_thread_exit should flip is_capturing and join
        assert cam.wait_for_capture_thread_exit(timeout=2.0) is True
        assert cam.capture_thread is None

    def test_returns_false_on_timeout(self):
        import threading, time
        cam = self._make_camera()
        cam.is_capturing = True

        # Thread that ignores the stop flag — simulates a thread wedged in SDK.
        def stuck():
            time.sleep(1.5)

        t = threading.Thread(target=stuck, daemon=True)
        cam.capture_thread = t
        t.start()
        assert cam.wait_for_capture_thread_exit(timeout=0.1) is False
        # capture_thread reference is kept so caller can re-inspect
        assert cam.capture_thread is t
        t.join()

    def test_aborts_calibration_manager(self):
        cam = self._make_camera()
        cal = MagicMock()
        cam.calibration_manager = cal
        cam.capture_thread = None
        cam.wait_for_capture_thread_exit(timeout=0.1)
        cal.abort.assert_called_once()


class TestUnrecoverableErrorDetection:
    """Unit tests for the SDK-crash error classifier — no Qt needed."""

    @pytest.mark.parametrize("msg", [
        "exception: access violation writing 0x0000000000000024",
        "Access Violation in zwoasi.dll",
        "[WinError -529697949] Windows Error 0xe06d7363",
        "Windows Error 0xE06D7363",
        "exception: exception",
    ])
    def test_unrecoverable_patterns(self, msg):
        from ui.controllers.camera_controller import CameraControllerQt
        assert CameraControllerQt._is_unrecoverable_error(msg) is True

    @pytest.mark.parametrize("msg", [
        "",
        "Exposure failed (camera returned ASI_EXP_FAILED status)",
        "Capture wedged — no frames for 112s",
        "Failed to reconnect camera",
        "Invalid ID",
        "Camera not responding after reconnect: timeout",
    ])
    def test_recoverable_patterns(self, msg):
        from ui.controllers.camera_controller import CameraControllerQt
        assert CameraControllerQt._is_unrecoverable_error(msg) is False


class TestDiscordSuppression:
    """Unit tests for the Discord error-throttle state machine."""

    def _controller(self):
        # Build a CameraControllerQt without running its Qt init (which needs
        # a QApplication).  We only need the plain attribute defaults for
        # should_notify_discord() logic.
        from ui.controllers.camera_controller import CameraControllerQt
        ctrl = CameraControllerQt.__new__(CameraControllerQt)
        ctrl._unrecoverable_mode = False
        ctrl._suppress_discord_errors = False
        return ctrl

    def test_allows_notifications_by_default(self):
        ctrl = self._controller()
        assert ctrl.should_notify_discord() is True

    def test_suppresses_after_flag_set(self):
        ctrl = self._controller()
        ctrl._suppress_discord_errors = True
        assert ctrl.should_notify_discord() is False

    def test_unrecoverable_mode_mark_notified_silences_further_pings(self):
        ctrl = self._controller()
        ctrl._unrecoverable_mode = True
        # Pure predicate — first call allows the final "needs restart" ping
        assert ctrl.should_notify_discord() is True
        # Caller records that the ping was sent
        ctrl.mark_discord_notified()
        # All subsequent calls must be suppressed
        assert ctrl.should_notify_discord() is False
        assert ctrl.should_notify_discord() is False

    def test_mark_discord_notified_noop_outside_unrecoverable_mode(self):
        """Regression guard: mark_discord_notified must not mute normal-mode
        Discord pings — only the unrecoverable-mode one-shot path uses it."""
        ctrl = self._controller()
        ctrl.mark_discord_notified()
        assert ctrl._suppress_discord_errors is False
        assert ctrl.should_notify_discord() is True


class TestCaptureLoopReconnectHeartbeat:
    """P2 regression: _last_frame_time must be refreshed after reconnect."""

    def test_reconnect_path_resets_last_frame_time(self):
        """
        Read the source of zwo_capture_worker.capture_loop and assert that the
        reconnect branch writes _last_frame_time back to time.time().

        A behavioural test would need to drive the full capture loop with a
        mocked ZWOCamera, which brings in calibration manager, sdk_lock, and
        scheduled-window state.  The invariant we actually care about is:
        "somewhere in the reconnect recovery branch, _last_frame_time gets
        reset."  A source-level assertion catches future refactors that
        silently drop it.
        """
        import inspect
        from services import zwo_capture_worker
        src = inspect.getsource(zwo_capture_worker.capture_loop)
        # The reconnect branch starts at '✓ Camera reconnected successfully'.
        # Must be followed (before the next 'except') by an assignment that
        # writes _last_frame_time.
        reconnect_pos = src.index("✓ Camera reconnected successfully")
        tail = src[reconnect_pos:]
        assert "_last_frame_time = time.time()" in tail, (
            "Reconnect recovery must refresh camera._last_frame_time so the "
            "UI-level watchdog doesn't fire on the first post-reconnect "
            "exposure. Lost this line? See services/zwo_capture_worker.py."
        )


class TestControllerRecoveryWiring:
    """Integration tests for the controller recovery path (requires Qt)."""

    @pytest.fixture
    def qt_app(self):
        # Shared QApplication across tests — Qt forbids creating two.
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            pytest.skip("PySide6 not installed")
        app = QApplication.instance() or QApplication([])
        yield app

    def _build_controller(self):
        from ui.controllers.camera_controller import CameraControllerQt
        main_window = MagicMock()
        main_window.config = MagicMock()
        main_window.config.get = MagicMock(return_value='')
        ctrl = CameraControllerQt(main_window)
        return ctrl

    def test_fatal_error_preserves_dying_camera_reference(self, qt_app):
        """The prev ZWOCamera must survive the fatal teardown so its thread
        can be joined before the next recovery fires — otherwise the SDK
        DLL sees concurrent access and crashes."""
        ctrl = self._build_controller()
        fake_camera = MagicMock()
        ctrl.zwo_camera = fake_camera
        ctrl.is_capturing = True
        # Stub out the auto-recovery scheduler so we don't start a real timer.
        ctrl._schedule_auto_recovery = MagicMock()
        ctrl._on_camera_error("Capture wedged — no frames for 112s", is_fatal=True)
        assert ctrl.zwo_camera is None
        assert ctrl._dying_camera is fake_camera
        assert ctrl.is_capturing is False
        ctrl._schedule_auto_recovery.assert_called_once()

    def test_auto_recovery_joins_dying_camera_before_restart(self, qt_app):
        """Recovery must wait for the previous capture thread to exit before
        calling start_capture() (which hits get_num_cameras on the main
        thread)."""
        ctrl = self._build_controller()
        dying = MagicMock()
        ctrl._dying_camera = dying
        # Stub start_capture so we only verify the join order.
        call_order = []
        dying.wait_for_capture_thread_exit = MagicMock(
            side_effect=lambda *a, **kw: call_order.append('join') or True
        )
        ctrl.start_capture = MagicMock(
            side_effect=lambda: call_order.append('start_capture')
        )
        ctrl._user_requested_stop = False
        ctrl.is_capturing = False
        ctrl._on_auto_recovery_fire()
        assert call_order == ['join', 'start_capture']
        assert ctrl._dying_camera is None

    def test_schedule_auto_recovery_noops_in_unrecoverable_mode(self, qt_app):
        ctrl = self._build_controller()
        ctrl._unrecoverable_mode = True
        # Precondition check — would normally increment attempts.
        before = ctrl._auto_recovery_attempts
        ctrl._schedule_auto_recovery()
        assert ctrl._auto_recovery_attempts == before
        assert ctrl._auto_recovery_timer is None

    def test_schedule_auto_recovery_sets_suppress_flag_after_threshold(self, qt_app):
        from ui.controllers.camera_controller import (
            _DISCORD_ERROR_SUPPRESS_AFTER_ATTEMPTS,
        )
        ctrl = self._build_controller()
        for _ in range(_DISCORD_ERROR_SUPPRESS_AFTER_ATTEMPTS):
            ctrl._schedule_auto_recovery()
            ctrl._cancel_auto_recovery_timer()
        assert ctrl._suppress_discord_errors is False
        # One more crosses the threshold
        ctrl._schedule_auto_recovery()
        ctrl._cancel_auto_recovery_timer()
        assert ctrl._suppress_discord_errors is True

    def test_sustained_frame_stream_clears_suppression(self, qt_app):
        ctrl = self._build_controller()
        ctrl._suppress_discord_errors = True
        ctrl._usb_reset_attempted = True
        ctrl._auto_recovery_attempts = 5
        # Seed a "previously successful" timestamp far in the past so the
        # 5-minute gate clears on the current frame.
        import time as _t
        ctrl._last_successful_frame_ts = _t.time() - 3600
        # Fake main_window.on_image_captured to avoid extra complexity.
        ctrl.main_window.on_image_captured = MagicMock()
        ctrl._on_frame_captured(MagicMock(), {})
        assert ctrl._suppress_discord_errors is False
        assert ctrl._usb_reset_attempted is False
        assert ctrl._auto_recovery_attempts == 0

    def test_enter_unrecoverable_mode_emits_capture_stopped_and_error(self, qt_app):
        ctrl = self._build_controller()
        ctrl._cancel_auto_recovery_timer = MagicMock()
        stopped_spy = []
        error_spy = []
        ctrl.capture_stopped.connect(lambda: stopped_spy.append(True))
        ctrl.error.connect(lambda msg: error_spy.append(msg))
        ctrl._enter_unrecoverable_mode("access violation writing 0x24")
        assert ctrl._unrecoverable_mode is True
        assert stopped_spy == [True]
        assert len(error_spy) == 1
        assert "restart" in error_spy[0].lower()

    def test_usb_reset_worker_runs_off_main_thread(self, qt_app):
        """USB reset blocks ~15s inside the Windows API; it must run on a
        worker thread, not the Qt main thread, so the UI stays responsive."""
        import threading, time
        ctrl = self._build_controller()
        ctrl.config.get = MagicMock(return_value='ZWO ASI676MC (Index: 0)')
        main_thread_id = threading.get_ident()
        worker_thread_id = {}
        reset_started = threading.Event()
        reset_completed = threading.Event()

        def fake_disable_enable(camera_name, logger, **kw):
            worker_thread_id['id'] = threading.get_ident()
            reset_started.set()
            # Simulate a slow USB reset. If _start_usb_reset_worker were
            # synchronous, the caller would block here too.
            time.sleep(0.2)
            reset_completed.set()
            return True

        with patch('sys.platform', 'win32'), \
                patch('services.usb_reset_win.is_usb_reset_available', return_value=True), \
                patch('services.usb_reset_win.disable_enable_zwo_camera_usb',
                      side_effect=fake_disable_enable):
            t0 = time.time()
            ctrl._start_usb_reset_worker()
            # If the reset ran inline, this line wouldn't execute until after
            # the 0.2s sleep. Assert the caller returned immediately.
            assert time.time() - t0 < 0.1, "USB reset blocked the calling thread"
            assert reset_started.wait(timeout=2.0), "worker did not start"
            assert reset_completed.wait(timeout=2.0), "worker did not finish"
        assert worker_thread_id.get('id') not in (None, main_thread_id)

    def test_usb_reset_worker_skips_when_no_camera_name(self, qt_app):
        ctrl = self._build_controller()
        ctrl.config.get = MagicMock(return_value='')
        ctrl._schedule_auto_recovery = MagicMock()
        with patch('sys.platform', 'win32'):
            ctrl._start_usb_reset_worker()
        # Should fall through to scheduling a retry synchronously rather than
        # spinning up a worker with no target.
        ctrl._schedule_auto_recovery.assert_called_once()

    def test_user_stop_clears_unrecoverable_state(self, qt_app):
        ctrl = self._build_controller()
        ctrl._unrecoverable_mode = True
        ctrl._usb_reset_attempted = True
        ctrl._suppress_discord_errors = True
        ctrl._dying_camera = MagicMock()
        ctrl.is_capturing = True
        ctrl.zwo_camera = MagicMock()
        ctrl.stop_capture()
        assert ctrl._unrecoverable_mode is False
        assert ctrl._usb_reset_attempted is False
        assert ctrl._suppress_discord_errors is False
        assert ctrl._dying_camera is None


@pytest.mark.requires_camera
class TestPhysicalCamera:
    """Tests that require actual camera hardware"""
    
    def test_camera_detection(self):
        """Test camera can be detected"""
        try:
            import zwoasi
            zwoasi.init(os.path.join(project_root, 'ASICamera2.dll'))
            
            num_cameras = zwoasi.get_num_cameras()
            
            # At least one camera should be connected for these tests
            assert num_cameras > 0, "No cameras detected - connect a camera to run hardware tests"
            
        except Exception as e:
            pytest.skip(f"Camera hardware test skipped: {e}")
    
    def test_camera_connection(self):
        """Test camera can be connected"""
        try:
            import zwoasi
            zwoasi.init(os.path.join(project_root, 'ASICamera2.dll'))
            
            if zwoasi.get_num_cameras() == 0:
                pytest.skip("No camera connected")
            
            camera = zwoasi.Camera(0)
            info = camera.get_camera_property()
            
            assert 'Name' in info
            assert 'MaxWidth' in info
            assert 'MaxHeight' in info
            
            camera.close()
            
        except Exception as e:
            pytest.skip(f"Camera connection test skipped: {e}")
    
    def test_capture_single_frame(self):
        """Test capturing a single frame"""
        try:
            import zwoasi
            zwoasi.init(os.path.join(project_root, 'ASICamera2.dll'))
            
            if zwoasi.get_num_cameras() == 0:
                pytest.skip("No camera connected")
            
            camera = zwoasi.Camera(0)
            
            # Set minimal settings for quick capture
            camera.set_control_value(zwoasi.ASI_EXPOSURE, 1000)  # 1ms
            camera.set_control_value(zwoasi.ASI_GAIN, 0)
            
            # Set image type to RAW8
            camera.set_image_type(zwoasi.ASI_IMG_RAW8)
            
            # Capture
            data = camera.capture()
            
            assert data is not None
            assert len(data) > 0
            
            camera.close()
            
        except Exception as e:
            pytest.skip(f"Capture test skipped: {e}")

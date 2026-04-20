import os
import re
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog

from services.logger import app_logger


# Seconds after the first watchdog fire before we declare UI-fatal.
# Gives the self-heal nudge time to land; escalates when it clearly hasn't.
_WATCHDOG_UI_FATAL_GRACE_SEC = 120


class _MainWindowCaptureMixin:

    # =========================================================================
    # CAMERA DETECTION
    # =========================================================================

    def _auto_detect_cameras(self):
        sdk_path = self.config.get('zwo_sdk_path', '')
        if sdk_path and os.path.exists(sdk_path):
            app_logger.info("Auto-detecting cameras on startup...")
            self._on_detect_cameras()

    def _on_detect_cameras(self):
        app_logger.info("=== Camera Detection Initiated ===")

        sdk_path = self.config.get('zwo_sdk_path', '')

        if not sdk_path:
            self.capture_panel.set_detection_error("SDK path not specified")
            return

        if not os.path.exists(sdk_path):
            self.capture_panel.set_detection_error(f"SDK not found: {sdk_path}")
            return

        self.capture_panel.set_detecting(True)

        main_window = self

        def detect_thread():
            cameras = []
            try:
                import zwoasi as asi

                try:
                    asi.init(sdk_path)
                    app_logger.info(f"ASI SDK initialized: {sdk_path}")
                except Exception as e:
                    if "already" not in str(e).lower():
                        main_window.cameras_detected.emit([], f"SDK init failed: {e}")
                        return

                num_cameras = asi.get_num_cameras()
                app_logger.info(f"SDK reports {num_cameras} camera(s)")

                if num_cameras == 0:
                    # SDK may be in a stale state from a previous session —
                    # force a full re-init and retry once before giving up
                    app_logger.warning("No cameras found, retrying with fresh SDK init...")
                    try:
                        import importlib
                        importlib.reload(asi)
                        asi.init(sdk_path)
                    except Exception as e:
                        if "already" not in str(e).lower():
                            app_logger.debug(f"SDK re-init note: {e}")

                    time.sleep(1.0)
                    num_cameras = asi.get_num_cameras()
                    app_logger.info(f"SDK retry reports {num_cameras} camera(s)")

                    if num_cameras == 0:
                        main_window.cameras_detected.emit([], "No cameras detected")
                        return

                # Snapshot list_cameras() once and retry if it disagrees with
                # get_num_cameras — the SDK has a race during hot-plug where
                # get_num_cameras briefly reports N but list_cameras returns
                # fewer names. Filling the missing slot with a placeholder
                # like "Camera 0" used to auto-save the placeholder as the
                # user's selected camera, clobbering the real camera_name in
                # config (see production log 2026-04-20 10:15).
                camera_list = []
                for poll_attempt in range(3):
                    camera_list = list(asi.list_cameras())
                    if len(camera_list) >= num_cameras:
                        break
                    app_logger.warning(
                        f"SDK enumeration race: get_num_cameras={num_cameras} "
                        f"but list_cameras returned {len(camera_list)} — "
                        f"retrying in 1s ({poll_attempt + 1}/3)"
                    )
                    time.sleep(1.0)

                for i, name in enumerate(camera_list):
                    cameras.append(f"{name} (Index: {i})")
                    app_logger.info(f"Camera {i}: {name}")

                if len(camera_list) != num_cameras:
                    app_logger.warning(
                        f"Enumeration still inconsistent after retries: "
                        f"num_cameras={num_cameras}, enumerated={len(camera_list)} "
                        "— trusting the enumerated list"
                    )

                app_logger.info(f"Detection complete: {len(cameras)} camera(s)")
                main_window.cameras_detected.emit(cameras, "")

            except Exception as e:
                app_logger.error(f"Detection failed: {e}")
                main_window.cameras_detected.emit([], str(e))

        threading.Thread(target=detect_thread, daemon=True).start()

    def _on_cameras_detected(self, cameras: list, error: str):
        self.capture_panel.set_detecting(False)

        if error:
            self.capture_panel.set_detection_error(error)
            app_logger.error(f"Camera detection error: {error}")
            self._notify(f"Camera detection: {error}", "error")
            self.app_bar.camera_chip.set_status('idle')
            self.app_bar.camera_chip.set_label('Camera')
        else:
            self.capture_panel.set_cameras(cameras)
            self._notify(f"{len(cameras)} camera(s) detected")

            self.config.set('available_cameras', cameras)

            if cameras:
                self.app_bar.camera_chip.set_status('connected')
                self.app_bar.camera_chip.set_label('Ready')

            saved_name = self.config.get('zwo_selected_camera_name', '')

            self.capture_panel.camera_widget.camera_combo.blockSignals(True)

            if '(Index:' in saved_name:
                saved_name = saved_name.split('(Index:')[0].strip()
                self.config.set('zwo_selected_camera_name', saved_name)

            # Placeholder names like "Camera 0" came from a previous detection
            # bug (fixed 2026-04-20) and must be cleared — otherwise the user
            # is locked out of auto-recovery on this rig forever.
            if saved_name and re.fullmatch(r'Camera \d+', saved_name.strip()):
                app_logger.warning(
                    f"Clearing placeholder camera name '{saved_name}' from config "
                    "(artefact of a previous detection bug)"
                )
                self.config.set('zwo_selected_camera_name', '')
                self.config.save()
                saved_name = ''

            found = False
            if saved_name and cameras:
                for i, cam in enumerate(cameras):
                    cam_clean = cam.split(' (Index:')[0] if '(Index:' in cam else cam
                    if saved_name == cam_clean:
                        self.capture_panel.camera_widget.camera_combo.setCurrentIndex(i)
                        actual_index = i
                        if '(Index: ' in cam:
                            try:
                                actual_index = int(cam.split('(Index: ')[1].rstrip(')'))
                            except (IndexError, ValueError):
                                pass
                        self.config.set('zwo_selected_camera', actual_index)
                        self.config.save()
                        app_logger.info(
                            f"Restored camera by name: '{saved_name}' "
                            f"(SDK Index: {actual_index})"
                        )
                        found = True
                        break

            if saved_name and not found:
                # Multi-camera rigs (guide cam, NINA imaging cam, etc.) share
                # the USB bus. Silently swapping to a different camera could
                # hijack another process's session or capture the wrong sky.
                # Keep the saved selection; the user must explicitly choose.
                app_logger.error(
                    f"Saved camera '{saved_name}' not found in detected cameras "
                    f"— refusing to auto-select a different camera on a "
                    f"multi-camera rig. Pick one manually on the Capture tab."
                )
                self._notify(
                    f"Saved camera '{saved_name}' not detected — select a camera manually",
                    "error",
                )
                self.capture_panel.camera_widget.camera_combo.setCurrentIndex(-1)
            elif not saved_name and cameras:
                # Fresh install (no prior selection): auto-pick the first so
                # the user isn't staring at an empty combo.
                cam = cameras[0]
                cam_clean = cam.split(' (Index:')[0] if '(Index:' in cam else cam
                actual_index = 0
                if '(Index: ' in cam:
                    try:
                        actual_index = int(cam.split('(Index: ')[1].rstrip(')'))
                    except (IndexError, ValueError):
                        pass
                self.capture_panel.camera_widget.camera_combo.setCurrentIndex(0)
                self.config.set('zwo_selected_camera', actual_index)
                self.config.set('zwo_selected_camera_name', cam_clean)
                self.config.save()
                app_logger.info(
                    f"Auto-selected camera (first install, no saved name): "
                    f"'{cam_clean}' (SDK Index: {actual_index})"
                )

            self.capture_panel.camera_widget.camera_combo.blockSignals(False)
            self.capture_panel.camera_widget.load_from_config(self.config)

        self._update_start_button()

    # =========================================================================
    # CAPTURE WATCHDOG
    # =========================================================================

    def _check_capture_watchdog(self):
        """Two-stage watchdog for wedged capture loops.

        Stage 1 (soft): frames are stale past ``threshold`` — nudge the
        capture thread via ``_recovery_requested`` and fire a non-fatal
        error for Discord/toast visibility. Healthy-but-slow recoveries
        self-heal here without the UI ever going through teardown.

        Stage 2 (hard): still stale ``_WATCHDOG_UI_FATAL_GRACE_SEC`` after
        stage 1 — the thread is genuinely wedged inside a C SDK call and
        can't see our flag. Declare fatal so the UI reflects reality; the
        dying_camera + _join_or_skip_dying_camera machinery handles the
        wedged thread asynchronously so we don't race its SDK call.
        """
        if not self.is_capturing or not self.camera_controller:
            self._reset_watchdog_state()
            return

        cam = getattr(self.camera_controller, 'zwo_camera', None)
        if not cam:
            self._reset_watchdog_state()
            return

        if getattr(cam, 'is_capturing', False) is False:
            self._reset_watchdog_state()
            return

        last_frame = getattr(cam, '_last_frame_time', None)
        if last_frame is None:
            return

        interval = getattr(cam, 'capture_interval', 5.0) or 5.0
        exposure_sec = getattr(cam, 'exposure_seconds', 0.0) or 0.0
        threshold = max(3 * interval, 180.0, exposure_sec + 60.0)
        stale_for = time.time() - last_frame

        if stale_for < threshold:
            self._reset_watchdog_state()
            return

        if getattr(cam, 'long_retry_mode_public', False):
            return

        if not self._watchdog_alerted:
            self._watchdog_alerted = True
            self._watchdog_first_fire_ts = time.time()
            app_logger.error(
                f"⚠ Capture watchdog: no frames for {stale_for:.0f}s "
                f"(threshold {threshold:.0f}s) — nudging capture thread to self-heal"
            )
            cam._recovery_requested = True
            try:
                self.camera_controller._on_camera_error(
                    f"Capture wedged — no frames for {int(stale_for)}s; "
                    f"requesting capture thread to self-heal",
                    is_fatal=False,
                )
            except TypeError:
                self.camera_controller._on_camera_error(
                    f"Capture wedged — no frames for {int(stale_for)}s"
                )
            return

        # Stage 2: still stale long enough after the first fire that the
        # nudge clearly didn't take — the capture thread is most likely
        # stuck inside a C SDK call (seen with hung get_num_cameras after
        # USB churn). Declare fatal so the UI reflects reality; the
        # dying_camera machinery handles the wedged thread asynchronously.
        if self._watchdog_ui_fatal_sent:
            return
        since_first = time.time() - (self._watchdog_first_fire_ts or time.time())
        if since_first >= _WATCHDOG_UI_FATAL_GRACE_SEC:
            self._watchdog_ui_fatal_sent = True
            app_logger.error(
                f"⚠ Capture still stalled after {int(since_first)}s since first "
                "alert — SDK call not returning. Syncing UI state."
            )
            try:
                self.camera_controller._on_camera_error(
                    "Capture thread appears permanently wedged inside the ZWO SDK. "
                    "Auto-recovery will keep trying; the app may need a manual "
                    "restart if this persists.",
                    is_fatal=True,
                )
            except TypeError:
                self.camera_controller._on_camera_error(
                    "Capture thread appears permanently wedged inside the ZWO SDK."
                )

    def _reset_watchdog_state(self):
        self._watchdog_alerted = False
        self._watchdog_first_fire_ts = None
        self._watchdog_ui_fatal_sent = False

    # =========================================================================
    # CAPTURE CONTROL
    # =========================================================================

    def _wait_for_timelapse_finalization(self, timeout_sec: float = 75.0):
        """Show a non-cancelable progress dialog while the timelapse finalizes.

        ffmpeg's +faststart rewrite can take 10–40 s on a long session; killing
        it mid-rewrite truncates the mp4. We block the close with a visible
        dialog rather than letting the window vanish silently.
        """
        if not self.timelapse_controller or not self.timelapse_controller.is_finalizing():
            return

        dlg = QProgressDialog(
            "Saving timelapse video, please wait…",
            None,
            0, 0,
            self,
        )
        dlg.setWindowTitle("PFR Sentinel")
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.show()
        QApplication.processEvents()

        deadline = time.monotonic() + timeout_sec
        while self.timelapse_controller.is_finalizing() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.1)

        dlg.close()

    def _send_discord_capture_started(self):
        try:
            discord_config = self.config.get('discord', {})
            if not discord_config.get('enabled', False):
                return

            if not discord_config.get('post_startup_shutdown', False):
                return

            from services.discord_alerts import DiscordAlerts
            alerts = DiscordAlerts(self.config)

            if alerts.is_enabled():
                alerts.send_capture_started_message()
                app_logger.info("Discord capture started notification sent")
        except Exception as e:
            app_logger.error(f"Failed to send Discord capture started notification: {e}")

    def _update_start_button(self):
        if self.is_capturing:
            return
        mode = self.config.get('capture_mode', 'camera')
        if mode == 'camera':
            cameras = self.config.get('available_cameras', [])
            if not cameras:
                self.app_bar.set_start_enabled(False, "No ZWO cameras detected — click Detect Cameras on the Capture tab")
                return
        else:
            watch_dir = self.config.get('watch_directory', '')
            if not watch_dir or not os.path.isdir(watch_dir):
                self.app_bar.set_start_enabled(False, "Set a valid watch directory on the Capture tab")
                return
        self.app_bar.set_start_enabled(True)

    def start_capture(self):
        mode = self.config.get('capture_mode', 'camera')

        try:
            self._ensure_output_servers_started()

            if mode == 'camera':
                self._start_camera_capture()
                if self.camera_controller and not self.camera_controller.is_capturing:
                    app_logger.error("Camera capture failed to start")
                    return
            else:
                self._start_watch_mode()

            self.is_capturing = True
            self.app_bar.set_capturing(True)
            self.app_bar.set_status('waiting')
            self.capture_started.emit()
            self._notify(f"Capture started ({mode} mode)")

            self._send_posthog_capture_started(mode)

            # Faster status updates while capturing
            self.status_timer.setInterval(200)

            self._send_discord_capture_started()

        except Exception as e:
            app_logger.error(f"Failed to start capture: {e}")
            self.is_capturing = False
            self.app_bar.set_capturing(False)
            self._notify(f"Capture failed: {e}", "error")
            self._send_discord_error(f"Failed to start capture: {e}")

    def stop_capture(self):
        try:
            # Update UI immediately for responsive feedback
            self.is_capturing = False
            self.app_bar.set_capturing(False)

            mode = self.config.get('capture_mode', 'camera')

            if mode == 'camera' and self.camera_controller:
                self.camera_controller.stop_capture()
                if hasattr(self, 'capture_panel'):
                    self.capture_panel.reset_camera_capabilities()
            elif self.watch_controller:
                self.watch_controller.stop_watching()

            self.capture_stopped.emit()
            self._notify("Capture stopped")

            if self.timelapse_controller:
                self.timelapse_controller.on_capture_stopped()

            if self.meteor_controller:
                self.meteor_controller.on_capture_stopped()

            # Slower status updates when idle
            self.status_timer.setInterval(1000)

            self.app_bar.camera_chip.set_status('connected')
            self.app_bar.camera_chip.set_label('Ready')

            self._update_start_button()

            app_logger.info("Capture stopped")

            from services.posthog_service import capture_event
            capture_event('capture_stopped', {
                'mode': mode,
                'images_processed': self.image_count,
            })

        except Exception as e:
            app_logger.error(f"Error stopping capture: {e}")

    def _send_posthog_capture_started(self, mode: str):
        try:
            from services.posthog_service import capture_event
            from version import __version__

            output_cfg = self.config.get('output', {})
            discord_cfg = self.config.get('discord', {})
            timelapse_cfg = self.config.get('timelapse', {})
            ml_cfg = self.config.get('ml_models', {})
            rtsp_cfg = self.config.get('rtsp', {})

            props = {
                'version': __version__,
                'mode': mode,
                'camera_name': self.config.get('zwo_selected_camera_name', '') if mode == 'camera' else None,
                'auto_exposure': self.config.get('zwo_auto_exposure', False) if mode == 'camera' else None,
                'output_file_enabled': True,
                'output_format': self.config.get('output_format', 'jpg'),
                'output_web_enabled': output_cfg.get('webserver_enabled', False),
                'output_discord_enabled': discord_cfg.get('enabled', False),
                'output_discord_interval_min': discord_cfg.get('periodic_interval_minutes', 30) if discord_cfg.get('periodic_enabled') else None,
                'output_rtsp_enabled': rtsp_cfg.get('enabled', False),
                'weather_enabled': self.weather_service is not None,
                'timelapse_enabled': timelapse_cfg.get('enabled', False),
                'ml_enabled': ml_cfg.get('enabled', False),
                'overlay_count': len(self.config.get('overlays', [])),
                'auto_stretch_enabled': self.config.get('auto_stretch', {}).get('enabled', False),
                'scheduled_capture': self.config.get('scheduled_capture_enabled', False),
            }

            overlays = self.config.get('overlays', [])
            tokens_used = set()
            for ov in overlays:
                tokens_used.update(t.upper() for t in re.findall(r'\{([^}]+)\}', ov.get('text', '')))
            if tokens_used:
                props['overlay_tokens'] = sorted(tokens_used)
            props = {k: v for k, v in props.items() if v is not None}
            capture_event('capture_started', props)
        except Exception:
            pass

    def _start_camera_capture(self):
        # Import here to avoid circular imports
        from .controllers.camera_controller import CameraControllerQt

        if not self.camera_controller:
            self.camera_controller = CameraControllerQt(self)
            self.camera_controller.calibration_status.connect(self.on_calibration_status)
            self.camera_controller.error.connect(self._on_camera_error)
            # Emitted on the camera worker thread; AutoConnection marshals to
            # the main thread so on_image_captured can safely touch Qt widgets
            # (StatusSprite's QTimer in particular has thread affinity to the GUI).
            self.camera_controller.frame_ready.connect(self.on_image_captured)
            # When the capture loop terminates itself (fatal error), sync the
            # main window state so the AppBar, tray menu, etc. don't keep
            # pretending capture is running.
            self.camera_controller.capture_stopped.connect(self._on_camera_capture_stopped)
            # When auto-recovery restarts capture successfully, the controller
            # fires this signal. Without wiring it up, the main window's own
            # is_capturing stays False and the AppBar shows "Start Capture"
            # while capture is actually running.
            self.camera_controller.capture_started.connect(self._on_camera_capture_started)

        self.camera_controller.start_capture()

        if self.camera_controller.is_capturing:
            self.app_bar.camera_chip.set_status('connected')
            self.app_bar.camera_chip.set_label('Connected')
            app_logger.info("Camera capture started")

            if self.camera_controller.zwo_camera and hasattr(self, 'capture_panel'):
                try:
                    supports_raw16 = self.camera_controller.zwo_camera.supports_raw16
                    bit_depth = self.camera_controller.zwo_camera.sensor_bit_depth
                    self.capture_panel.update_camera_capabilities(supports_raw16, bit_depth)
                except Exception as e:
                    app_logger.debug(f"Could not update camera capabilities: {e}")
        else:
            self.app_bar.camera_chip.set_status('error')
            self.app_bar.camera_chip.set_label('Connection Failed')

    def _start_watch_mode(self):
        from .controllers.watch_controller import WatchControllerQt

        if not self.watch_controller:
            self.watch_controller = WatchControllerQt(self)
            self.watch_controller.image_processed.connect(
                lambda img, path: self._on_image_processed(img, {}, path)
            )

        watch_dir = self.config.get('watch_directory', '')
        if not watch_dir or not os.path.isdir(watch_dir):
            raise ValueError("Invalid watch directory")

        self.watch_controller.start_watching(watch_dir)
        if self.watch_controller.is_watching:
            app_logger.info(f"Watch mode started: {watch_dir}")

    def _on_camera_error(self, error_msg: str):
        app_logger.error(f"Camera error received: {error_msg}")
        self._notify(f"Camera error: {error_msg}", "error")

        if hasattr(self, 'app_bar') and self.app_bar:
            self.app_bar.camera_chip.set_status('error')
            self.app_bar.camera_chip.set_label('Camera Error')

        should_notify = (
            self.camera_controller is None
            or self.camera_controller.should_notify_discord()
        )
        if should_notify:
            self._send_discord_error(f"Camera Error: {error_msg}")
            if self.camera_controller is not None:
                self.camera_controller.mark_discord_notified()
        else:
            app_logger.debug("Discord error suppressed")

    def _on_camera_capture_stopped(self):
        """Handle controller capture_stopped signal.

        Fires when the capture loop has terminated on its own (fatal error).
        Mirrors the state changes that stop_capture() performs, so the UI
        (AppBar buttons, tray menu Start/Stop enablement, status chips) stays
        consistent with reality instead of claiming we're still capturing.
        """
        if not self.is_capturing:
            return

        app_logger.warning("Capture ended unexpectedly — syncing UI state")
        self.stop_capture()

    def _on_camera_capture_started(self):
        """Handle controller capture_started signal.

        Fires when the controller starts capture — usually from the user's
        own button click (where we've already mirrored the state), but also
        from auto-recovery, which otherwise leaves the AppBar's Start/Stop
        button showing "Start" while capture is actually running.
        """
        if self.is_capturing:
            return
        app_logger.info("Capture resumed by auto-recovery — syncing UI state")
        self.is_capturing = True
        self.app_bar.set_capturing(True)
        self.app_bar.set_status('waiting')
        self.app_bar.camera_chip.set_status('connected')
        self.app_bar.camera_chip.set_label('Connected')

    def _on_raw16_mode_changed(self, enabled: bool):
        if not self.camera_controller or not self.camera_controller.is_capturing:
            return

        if self.camera_controller.zwo_camera:
            success = self.camera_controller.zwo_camera.set_raw16_mode(enabled)
            if not success:
                if hasattr(self, 'capture_panel'):
                    self.capture_panel._loading_config = True
                    self.capture_panel.raw16_switch.set_checked(not enabled)
                    self.capture_panel._loading_config = False

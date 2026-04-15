"""
Main Window for PFR Sentinel
Control Console layout with navigation rail and dual-panel design
"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QSplitter, QFrame, QSizePolicy, QApplication
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QIcon, QAction
from qfluentwidgets import (
    FluentWindow, NavigationInterface, NavigationItemPosition,
    FluentIcon, setTheme, Theme, setThemeColor, isDarkTheme,
    NavigationWidget, PushButton, ToolButton, SplitFluentWindow
)

import os
import random
import sys

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_config import APP_DISPLAY_NAME, APP_SUBTITLE, APP_AUTHOR
from services.config import Config
from services.logger import app_logger
from services.web_output import WebOutputServer
from services.ml_data_collector import init_ml_collector
from services.update_checker import get_update_checker, UpdateInfo
from version import __version__

from .theme import apply_theme, apply_accent_theme, get_stylesheet
from .theme.tokens import Colors, Typography, Spacing, Layout
from .components.app_bar import AppBar
from .components.nav_rail import NavRail
from .panels.live_monitoring import LiveMonitoringPanel
from .panels.capture_settings import CaptureSettingsPanel
from .panels.output_settings import OutputSettingsPanel
from .panels.image_processing import ImageProcessingPanel
from .panels.overlay_settings import OverlaySettingsPanel
from .panels.timelapse_panel import TimelapsePanel
from .panels.settings_panel import SettingsPanel
from .panels.logs_panel import LogsPanel
from .panels.allsky_settings import AllSkySettingsPanel
from .panels.meteor_panel import MeteorPanel
from .controllers.image_processor import ImageProcessor
from .controllers.timelapse_controller import TimelapseController
from .controllers.allsky_controller import AllSkyController
from .controllers.meteor_controller import MeteorController


class MainWindow(QMainWindow):
    """
    Main application window with Control Console layout:
    - Top: App bar with status chips and primary action
    - Left: Live monitoring panel (always visible)
    - Right: Navigation + Inspector panels (contextual)
    """
    
    # Signals for cross-component communication
    capture_started = Signal()
    capture_stopped = Signal()
    config_changed = Signal()
    image_captured = Signal(object)  # PIL Image
    cameras_detected = Signal(list, str)  # cameras list, error string
    
    def __init__(self):
        super().__init__()
        
        # Apply theme first
        apply_theme()
        
        # Load config
        self.config = Config()
        
        # Application state
        self.is_capturing = False
        self.image_count = 0
        self.is_loading_config = False  # Prevent saves during load
        self._cached_raw_image = None      # Last raw frame for instant reprocess
        self._cached_raw_metadata = None
        
        # Service references (will be initialized later)
        self.camera_controller = None
        self.watch_controller = None
        self.output_manager = None
        self.discord_alerts = None
        self.web_server = None
        self.weather_service = None
        self.timelapse_controller = None
        self.meteor_controller = None
        self.system_tray = None  # Set by main_pyside.py when in tray mode
        
        # Initialize weather service from config
        self._init_weather_service()
        
        # Initialize ML data collector for contribution feature
        init_ml_collector(lambda: self.config.data)
        
        # Image processor (background thread for processing)
        self.image_processor = ImageProcessor(self)
        self.image_processor.set_main_window(self)
        self.image_processor.start()
        
        # Setup window
        self._setup_window()
        self._setup_ui()
        self._setup_connections()
        self._apply_styles()
        
        # Configure cursor shapes for all widgets
        from .theme import configure_widget_cursors
        configure_widget_cursors(self)
        
        # Start periodic updates
        self._start_timers()
        
        # Initialize update checker (checks 24h after startup to be rate-limit friendly)
        self._init_update_checker()
        
        # Load config into UI panels
        self.load_config()
        
        # Auto-detect cameras after UI is ready (delay to ensure window is shown)
        QTimer.singleShot(500, self._auto_detect_cameras)
        
        # Send Discord startup notification if enabled
        QTimer.singleShot(1000, self._send_discord_startup)
        
        # Validate config and log any warnings
        self._validate_config_on_startup()

        app_logger.info(f"PFR Sentinel v{__version__} initialized")
    
    def _setup_window(self):
        """Configure main window properties"""
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{__version__}")
        
        # Load saved geometry or set default
        geometry = self.config.get('window_geometry', '1400x900')
        try:
            w, h = map(int, geometry.lower().split('x')[:2])
            self.resize(w, h)
        except (ValueError, IndexError):
            self.resize(1400, 900)
        
        self.setMinimumSize(900, 600)
        
        # Set window icon
        try:
            from utils_paths import resource_path
            icon_path = resource_path('assets/app_icon.ico')
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            app_logger.debug(f"Could not set window icon: {e}")
    
    def _setup_ui(self):
        """Build the main UI layout"""
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        
        # Main vertical layout (app bar + content)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # === APP BAR (Top) ===
        self.app_bar = AppBar(self)
        main_layout.addWidget(self.app_bar)
        
        # === CONTENT AREA (Below app bar) ===
        # Stored as instance attribute so InfoBars can parent to it (below the app bar)
        self.content_area = QWidget()
        content_widget = self.content_area
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        main_layout.addWidget(content_widget, 1)  # Stretch factor 1
        
        # --- Navigation Rail (Left edge) ---
        self.nav_rail = NavRail(self)
        content_layout.addWidget(self.nav_rail)
        
        # --- Main Content Splitter ---
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(6)  # Wider handle for easier dragging
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {Colors.border_subtle};
            }}
            QSplitter::handle:hover {{
                background-color: {Colors.accent_default};
            }}
        """)
        content_layout.addWidget(self.splitter, 1)
        
        # --- Live Monitoring Panel (Left) ---
        self.live_panel = LiveMonitoringPanel(self)
        self.live_panel.setMinimumWidth(250)  # Minimum for preview
        self.splitter.addWidget(self.live_panel)
        
        # --- Inspector Panel Stack (Right) ---
        self.inspector_stack = QStackedWidget()
        self.inspector_stack.setMinimumWidth(300)
        self.splitter.addWidget(self.inspector_stack)
        
        # Set resize cursor on splitter handle (after widgets added)
        for i in range(self.splitter.count()):
            handle = self.splitter.handle(i)
            if handle:
                handle.setCursor(Qt.SplitHCursor)
        
        # Create inspector panels
        self.capture_panel = CaptureSettingsPanel(self)
        self.output_panel = OutputSettingsPanel(self)
        self.processing_panel = ImageProcessingPanel(self)
        self.overlay_panel = OverlaySettingsPanel(self)
        self.timelapse_panel = TimelapsePanel(self)
        self.allsky_panel = AllSkySettingsPanel(self)
        self.meteor_panel = MeteorPanel(self)
        self.logs_panel = LogsPanel(self)
        self.settings_panel = SettingsPanel(self)

        # Timelapse controller (owns TimelapseWriter, wired to image processor below)
        self.timelapse_controller = TimelapseController(self)

        # All-sky overlay controller
        self.allsky_controller = AllSkyController(self)
        self.allsky_controller.status_changed.connect(self.allsky_panel.set_status)
        self.allsky_controller.quality_changed.connect(self.allsky_panel.set_quality)
        self.allsky_controller.settings_changed.connect(self._on_allsky_settings_changed)
        self.allsky_panel.settings_changed.connect(self._on_allsky_panel_changed)

        # Wire background calibration service to image processor
        self.image_processor.set_calibration_service(
            self.allsky_controller.calibration_service,
        )

        # Meteor tracker controller
        self.meteor_controller = MeteorController(self)
        self.meteor_controller.status_updated.connect(self.meteor_panel.update_status)

        # Add to stack
        self.inspector_stack.addWidget(self.capture_panel)       # Index 0
        self.inspector_stack.addWidget(self.output_panel)        # Index 1
        self.inspector_stack.addWidget(self.processing_panel)    # Index 2
        self.inspector_stack.addWidget(self.overlay_panel)       # Index 3
        self.inspector_stack.addWidget(self.timelapse_panel)     # Index 4
        self.inspector_stack.addWidget(self.allsky_panel)        # Index 5
        self.inspector_stack.addWidget(self.meteor_panel)        # Index 6
        self.inspector_stack.addWidget(self.logs_panel)          # Index 7
        self.inspector_stack.addWidget(self.settings_panel)      # Index 8
        
        # Defer splitter restoration until window is shown
        # This ensures we have accurate available width
        QTimer.singleShot(100, self._restore_splitter_sizes)
        
        # Restore inspector visibility from config
        inspector_visible = self.config.get('inspector_visible', True)
        if not inspector_visible:
            self.inspector_stack.hide()
        
        # Restore last navigation section
        last_section = self.config.get('last_nav_section', 'capture')
        if last_section:
            # Set nav rail selection without emitting signal
            self.nav_rail.set_active_section(last_section)
            # Manually trigger the layout update
            self._on_nav_changed(last_section)
        else:
            # Default to Capture panel
            self.inspector_stack.setCurrentIndex(0)
    
    def _setup_connections(self):
        """Connect signals and slots"""
        # Save splitter position when moved
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        # Connect signals between components
        # Navigation
        self.nav_rail.section_changed.connect(self._on_nav_changed)
        
        # App bar actions
        self.app_bar.start_clicked.connect(self.start_capture)
        self.app_bar.stop_clicked.connect(self.stop_capture)
        
        # Config changes from panels
        self.capture_panel.settings_changed.connect(self._on_settings_changed)
        self.output_panel.settings_changed.connect(self._on_settings_changed)
        self.processing_panel.settings_changed.connect(self._on_settings_changed)
        self.overlay_panel.settings_changed.connect(self._on_settings_changed)
        self.timelapse_panel.settings_changed.connect(self._on_settings_changed)
        self.meteor_panel.settings_changed.connect(self._on_settings_changed)
        self.settings_panel.settings_changed.connect(self._on_settings_changed)

        # Reprocess last frame instantly when image processing or overlay settings change
        self.processing_panel.settings_changed.connect(self.reprocess_last_frame)
        self.overlay_panel.settings_changed.connect(self.reprocess_last_frame)
        self.settings_panel.accent_changed.connect(self.set_accent_theme)

        # Processing time → live panel performance bar
        self.image_processor.processing_time.connect(
            self.live_panel.update_processing_time
        )

        # Timelapse: image processor → controller → panel status
        self.image_processor.timelapse_ready.connect(
            self.timelapse_controller.on_timelapse_ready
        )
        self.timelapse_controller.status_updated.connect(
            self.timelapse_panel.update_status
        )

        # Meteor tracker: image processor → controller → panel status
        self.image_processor.timelapse_ready.connect(
            self.meteor_controller.on_frame_ready
        )
        # User rejection: panel "Not a Meteor" → controller adds exclusion zone
        self.meteor_panel.detection_rejected.connect(
            self.meteor_controller.on_detection_rejected
        )
        
        # RAW16 mode toggle - update camera on the fly if capturing
        self.capture_panel.raw16_mode_changed.connect(self._on_raw16_mode_changed)
        
        # Camera panel actions
        self.capture_panel.detect_cameras_clicked.connect(self._on_detect_cameras)
        
        # Camera detection results (from background thread)
        self.cameras_detected.connect(self._on_cameras_detected)
        
        # Output panel actions
        self.output_panel.test_discord_requested.connect(self._on_test_discord)
        
        # Image processor signals
        # Camera controller signals
        self.image_processor.processing_complete.connect(self._on_image_processed)
        self.image_processor.preview_ready.connect(self._on_preview_ready)
        self.image_processor.error_occurred.connect(self._on_processing_error)
    
    def _auto_detect_cameras(self):
        """Auto-detect cameras on startup if SDK path is configured"""
        sdk_path = self.config.get('zwo_sdk_path', '')
        if sdk_path and os.path.exists(sdk_path):
            app_logger.info("Auto-detecting cameras on startup...")
            self._on_detect_cameras()
    
    def _on_detect_cameras(self):
        """Handle camera detection request from capture panel"""
        app_logger.info("=== Camera Detection Initiated ===")
        
        sdk_path = self.config.get('zwo_sdk_path', '')
        
        if not sdk_path:
            self.capture_panel.set_detection_error("SDK path not specified")
            return
        
        if not os.path.exists(sdk_path):
            self.capture_panel.set_detection_error(f"SDK not found: {sdk_path}")
            return
        
        # Show spinner/loading state
        self.capture_panel.set_detecting(True)
        
        # Store reference to self for use in thread
        main_window = self
        
        # Run detection in thread
        import threading
        def detect_thread():
            cameras = []
            error = None
            try:
                import zwoasi as asi
                
                try:
                    asi.init(sdk_path)
                    app_logger.info(f"ASI SDK initialized: {sdk_path}")
                except Exception as e:
                    if "already" not in str(e).lower():
                        error = f"SDK init failed: {e}"
                        main_window.cameras_detected.emit([], error)
                        return
                
                num_cameras = asi.get_num_cameras()
                app_logger.info(f"SDK reports {num_cameras} camera(s)")

                if num_cameras == 0:
                    # SDK may be in a stale state from a previous session —
                    # force a full re-init and retry once before giving up
                    app_logger.warning("No cameras found, retrying with fresh SDK init...")
                    try:
                        # Force reimport to clear cached SDK state
                        import importlib
                        importlib.reload(asi)
                        asi.init(sdk_path)
                    except Exception as e:
                        if "already" not in str(e).lower():
                            app_logger.debug(f"SDK re-init note: {e}")

                    import time
                    time.sleep(1.0)
                    num_cameras = asi.get_num_cameras()
                    app_logger.info(f"SDK retry reports {num_cameras} camera(s)")

                    if num_cameras == 0:
                        main_window.cameras_detected.emit([], "No cameras detected")
                        return
                
                for i in range(num_cameras):
                    try:
                        name = asi.list_cameras()[i]
                        cameras.append(f"{name} (Index: {i})")
                        app_logger.info(f"Camera {i}: {name}")
                    except Exception:
                        cameras.append(f"Camera {i}")
                
                app_logger.info(f"Detection complete: {len(cameras)} camera(s)")
                main_window.cameras_detected.emit(cameras, "")
                
            except Exception as e:
                app_logger.error(f"Detection failed: {e}")
                main_window.cameras_detected.emit([], str(e))
        
        threading.Thread(target=detect_thread, daemon=True).start()
    
    def _on_cameras_detected(self, cameras: list, error: str):
        """Handle camera detection results (called via signal from thread)"""
        self.capture_panel.set_detecting(False)
        
        if error:
            self.capture_panel.set_detection_error(error)
            app_logger.error(f"Camera detection error: {error}")
            self._notify(f"Camera detection: {error}", "error")
            # Update camera chip to show error/idle
            self.app_bar.camera_chip.set_status('idle')
            self.app_bar.camera_chip.set_label('Camera')
        else:
            self.capture_panel.set_cameras(cameras)
            self._notify(f"{len(cameras)} camera(s) detected")

            # Store detected cameras in config to prevent re-detection in start_capture
            self.config.set('available_cameras', cameras)
            
            # Update camera chip to show ready
            if cameras:
                self.app_bar.camera_chip.set_status('connected')
                self.app_bar.camera_chip.set_label('Ready')
            
            # Restore camera selection - prioritize name match over index
            # (index can change if cameras are plugged in different order)
            saved_name = self.config.get('zwo_selected_camera_name', '')
            
            self.capture_panel.camera_combo.blockSignals(True)
            
            # Strip any old "(Index: N)" suffix from saved name for clean matching
            if '(Index:' in saved_name:
                saved_name = saved_name.split('(Index:')[0].strip()
                # Persist the cleaned name so we don't have to strip again
                self.config.set('zwo_selected_camera_name', saved_name)

            if saved_name and cameras:
                # Try to find camera by name (name is embedded in the combo text)
                found = False
                for i, cam in enumerate(cameras):
                    # cam format: "ZWO ASI676MC (Index: 2)"
                    cam_clean = cam.split(' (Index:')[0] if '(Index:' in cam else cam
                    if saved_name == cam_clean:
                        self.capture_panel.camera_combo.setCurrentIndex(i)
                        # Extract actual camera SDK index from the combo text
                        actual_index = i
                        if '(Index: ' in cam:
                            try:
                                actual_index = int(cam.split('(Index: ')[1].rstrip(')'))
                            except (IndexError, ValueError):
                                pass
                        self.config.set('zwo_selected_camera', actual_index)
                        self.config.save()
                        app_logger.info(f"Restored camera by name: '{saved_name}' (SDK Index: {actual_index})")
                        found = True
                        break

                if not found:
                    app_logger.warning(f"Saved camera '{saved_name}' not found in detected cameras")

            if (not saved_name or not found) and cameras:
                # No saved name (fresh install) or saved camera not found —
                # auto-select the first detected camera so capture works immediately
                cam = cameras[0]
                cam_clean = cam.split(' (Index:')[0] if '(Index:' in cam else cam
                actual_index = 0
                if '(Index: ' in cam:
                    try:
                        actual_index = int(cam.split('(Index: ')[1].rstrip(')'))
                    except (IndexError, ValueError):
                        pass
                self.capture_panel.camera_combo.setCurrentIndex(0)
                self.config.set('zwo_selected_camera', actual_index)
                self.config.set('zwo_selected_camera_name', cam_clean)
                self.config.save()
                app_logger.info(f"Auto-selected camera: '{cam_clean}' (SDK Index: {actual_index})")

            self.capture_panel.camera_combo.blockSignals(False)

        self._update_start_button()

    def _on_test_discord(self):
        """Test Discord webhook"""
        # Get discord config from config
        discord_config = self.config.get('discord', {})
        webhook_url = discord_config.get('webhook_url', '')
        
        if not webhook_url:
            self.output_panel.set_discord_test_result(False, "Webhook URL required")
            return
        
        try:
            from services.discord_alerts import DiscordAlerts
            
            # Create alerts with proper config structure
            test_config = {
                'discord': {
                    'enabled': True,
                    'webhook_url': webhook_url,
                    'embed_color_hex': discord_config.get('embed_color_hex', '#0EA5E9'),
                    'username_override': discord_config.get('username_override', ''),
                    'avatar_url': discord_config.get('avatar_url', ''),
                    'include_latest_image': False  # Don't include image for test
                }
            }
            alerts = DiscordAlerts(test_config)
            
            # Send test message using correct method
            success = alerts.send_discord_message(
                title="🧪 Webhook Test",
                description="PFR Sentinel webhook test successful!",
                level="success"
            )
            
            if success:
                self.output_panel.set_discord_test_result(True, "Test message sent!")
                app_logger.info("Discord test message sent successfully")
            else:
                self.output_panel.set_discord_test_result(False, alerts.last_send_status)
                app_logger.warning(f"Discord test failed: {alerts.last_send_status}")
                
        except Exception as e:
            app_logger.error(f"Discord test error: {e}")
            self.output_panel.set_discord_test_result(False, str(e)[:50])
    
    def _apply_styles(self):
        """Apply stylesheet and saved accent theme to window."""
        saved_accent = self.config.get('ui_accent', 'iris')
        apply_accent_theme(saved_accent)
        self.setStyleSheet(get_stylesheet())
        self.nav_rail.refresh_styles()

    def set_accent_theme(self, name: str) -> None:
        """Switch accent colour at runtime and refresh all styled widgets."""
        apply_accent_theme(name)
        self.setStyleSheet(get_stylesheet())
        if hasattr(self, 'nav_rail'):
            self.nav_rail.refresh_styles()
    
    def _start_timers(self):
        """Start periodic update timers"""
        # Status update timer (fast when capturing)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(1000)  # 1 second default
        
        # Log polling timer
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._poll_logs)
        self.log_timer.start(250)  # 250ms for responsive logs
    
    def _init_update_checker(self):
        """Initialize the update checker with automatic checks."""
        self.update_checker = get_update_checker(on_update_available=self._on_update_available)
        
        # Check on startup after a short delay (respects 24h cache to avoid API spam)
        # This will only hit the API if it's been >24h since last check
        QTimer.singleShot(3000, self._do_startup_update_check)
        
        # Also start the background delayed check for users who leave app running
        self.update_checker.start_delayed_check(delay_hours=24.0)
        app_logger.debug("Update checker initialized")
    
    def _do_startup_update_check(self):
        """Perform startup update check and handle result."""
        result = self.update_checker.check_for_update(force=False)
        # If update found, the callback _on_update_available will be triggered
        # by check_for_update internally, so we don't need to do anything here
    
    def _on_update_available(self, update_info: UpdateInfo):
        """Callback when update is available - show dialog on main thread."""
        # Use QTimer to show dialog and set badge on main thread
        QTimer.singleShot(0, lambda: self._handle_update_available(update_info))
    
    def _handle_update_available(self, update_info: UpdateInfo):
        """Handle update available (must be called on main thread)."""
        self._notify(f"Update available: v{update_info.latest_version}")
        from services.posthog_service import capture_event
        capture_event('update_available', {
            'current_version': __version__,
            'latest_version': update_info.latest_version,
        })
        # Show badge on Settings nav button
        if hasattr(self, 'nav_rail'):
            self.nav_rail.set_badge('settings', True, "!")
        
        # Show the dialog
        self._show_update_dialog(update_info)
    
    def _show_update_dialog(self, update_info: UpdateInfo):
        """Show update dialog (must be called on main thread)."""
        from .dialogs.update_dialog import show_update_dialog
        
        # If window is hidden (tray mode), show tray notification first
        if not self.isVisible() and self.system_tray:
            try:
                from PySide6.QtWidgets import QSystemTrayIcon
                if hasattr(self.system_tray, 'tray_icon') and self.system_tray.tray_icon:
                    self.system_tray.tray_icon.showMessage(
                        "Update Available",
                        f"PFR Sentinel v{update_info.latest_version} is available",
                        QSystemTrayIcon.Information,
                        5000
                    )
            except Exception:
                pass
        
        # Show the dialog (will also make window visible if needed)
        if not self.isVisible():
            self.show()
            self.activateWindow()
        
        show_update_dialog(self, update_info)
    
    def check_for_updates_now(self):
        """Manually trigger an update check (for settings panel button)."""
        if hasattr(self, 'update_checker'):
            result = self.update_checker.check_for_update(force=True)
            if result is None:
                # No update available - show info bar
                from qfluentwidgets import InfoBar, InfoBarPosition
                bar = InfoBar.success(
                    title="Up to Date",
                    content=f"You're running the latest version (v{__version__})",
                    parent=self.content_area,
                    position=InfoBarPosition.TOP,
                    duration=3000
                )
                bar.raise_()
            else:
                # Update found - badge is set in _handle_update_available via callback
                # but also set it here in case callback didn't fire
                if hasattr(self, 'nav_rail'):
                    self.nav_rail.set_badge('settings', True, "!")
    
    # =========================================================================
    # UI STATE MANAGEMENT
    # =========================================================================
    
    def _on_splitter_moved(self, pos, index):
        """Save splitter sizes when user adjusts divider"""
        sizes = self.splitter.sizes()
        total = sum(sizes) if sizes else 1
        
        # Save both absolute sizes and ratio for better restoration
        if total > 0 and len(sizes) >= 2:
            ratio = sizes[0] / total
            self.config.set('splitter_sizes', sizes)
            self.config.set('splitter_ratio', ratio)
    
    def _restore_splitter_sizes(self):
        """Restore splitter sizes after window is shown
        
        Uses saved ratio as primary method, falls back to absolute sizes.
        This prevents dramatic shifts when window size differs from saved state.
        """
        try:
            # Get current available width
            available_width = self.splitter.width()
            if available_width <= 0:
                # Splitter not ready yet, use defaults
                self.splitter.setSizes([400, 500])
                return
            
            # Try ratio-based restoration first (more reliable across window sizes)
            saved_ratio = self.config.get('splitter_ratio', None)
            if saved_ratio is not None and 0.1 <= saved_ratio <= 0.9:
                # Apply ratio to current width
                left_size = int(available_width * saved_ratio)
                right_size = available_width - left_size
                
                # Ensure minimum sizes
                left_size = max(250, left_size)
                right_size = max(300, right_size)
                
                self.splitter.setSizes([left_size, right_size])
                app_logger.debug(f"Splitter restored via ratio: {saved_ratio:.2f} -> [{left_size}, {right_size}]")
                return
            
            # Fallback to absolute sizes with validation
            saved_sizes = self.config.get('splitter_sizes', None)
            if saved_sizes and isinstance(saved_sizes, list) and len(saved_sizes) >= 2:
                # Validate sizes are reasonable
                if all(s >= 100 for s in saved_sizes[:2]):
                    # Scale sizes proportionally if they don't fit current width
                    total_saved = sum(saved_sizes[:2])
                    if total_saved > available_width * 1.5 or total_saved < available_width * 0.5:
                        # Saved sizes are way off, use ratio instead
                        ratio = saved_sizes[0] / total_saved
                        left_size = int(available_width * ratio)
                        right_size = available_width - left_size
                        self.splitter.setSizes([max(250, left_size), max(300, right_size)])
                    else:
                        self.splitter.setSizes(saved_sizes)
                    return
            
            # Default: 40% left, 60% right
            left_size = int(available_width * 0.4)
            right_size = available_width - left_size
            self.splitter.setSizes([left_size, right_size])
            
        except Exception as e:
            app_logger.warning(f"Error restoring splitter sizes: {e}")
            self.splitter.setSizes([400, 500])

    # =========================================================================
    # NAVIGATION
    # =========================================================================
    
    def _on_nav_changed(self, section: str):
        """Handle navigation section change"""
        section_map = {
            'monitoring': -1,  # Special: hide inspector panel, show live panel
            'capture': 0,
            'output': 1,
            'processing': 2,
            'overlays': 3,
            'timelapse': 4,
            'allsky': 5,       # All-sky overlay settings
            'meteor': 6,       # Meteor tracker
            'logs': 7,
            'settings': 8,     # Settings panel (hide live panel)
        }
        
        index = section_map.get(section, 0)
        
        # Clear badge when navigating to settings (user has seen the update)
        if section == 'settings':
            self.nav_rail.set_badge('settings', False)
        
        if index == -1:
            # Live Monitoring: hide the inspector panel, show live panel (preview only)
            self.live_panel.show()
            self.live_panel.set_preview_only(True)
            self.inspector_stack.hide()
            self.config.set('inspector_visible', False)
            self.config.set('last_nav_section', section)
            self.config.save()
        elif section in ('overlays', 'settings'):
            # Overlays/Settings: hide live panel, show panel full width
            self.live_panel.hide()
            self.live_panel.set_preview_only(False)
            self.inspector_stack.show()
            self.inspector_stack.setCurrentIndex(index)
            self.config.set('inspector_visible', True)
            self.config.set('last_nav_section', section)
            self.config.save()
        else:
            # Normal tabs: show both panels with histogram and activity log
            self.live_panel.show()
            self.live_panel.set_preview_only(False)
            self.inspector_stack.show()
            self.inspector_stack.setCurrentIndex(index)
            self.config.set('inspector_visible', True)
            self.config.set('last_nav_section', section)
            self.config.save()
        
        app_logger.debug(f"Navigation: {section}")

        from services.posthog_service import capture_event
        capture_event('$pageview', {'$current_url': f'/app/{section}'})
    
    # =========================================================================
    # CAPTURE CONTROL
    # =========================================================================
    
    def _notify(self, message, category='info'):
        """Add a notification to the in-app notification store."""
        try:
            from services.notification_store import get_notification_store
            get_notification_store().add(message, category)
        except Exception:
            pass

    def _validate_config_on_startup(self):
        """Run config validation and log any warnings."""
        try:
            warnings = self.config.validate()
            for w in warnings:
                app_logger.warning(f"Config: {w}")
                self._notify(w, "warning")
        except Exception as e:
            app_logger.debug(f"Config validation skipped: {e}")

    def _send_discord_startup(self):
        """Send Discord startup notification if enabled"""
        try:
            discord_config = self.config.get('discord', {})
            if not discord_config.get('enabled', False):
                return
            
            if not discord_config.get('startup_enabled', True):
                return
            
            from services.discord_alerts import DiscordAlerts
            alerts = DiscordAlerts(self.config)
            
            if alerts.is_enabled():
                alerts.send_startup_message()
                app_logger.info("Discord startup notification sent")
        except Exception as e:
            app_logger.error(f"Failed to send Discord startup notification: {e}")
    
    def _send_discord_error(self, error_msg: str):
        """Send Discord error notification if enabled"""
        try:
            discord_config = self.config.get('discord', {})
            if not discord_config.get('enabled', False):
                return
            
            # Check post_errors setting (config key is 'post_errors', not 'error_enabled')
            if not discord_config.get('post_errors', False):
                return
            
            from services.discord_alerts import DiscordAlerts
            alerts = DiscordAlerts(self.config)
            
            if alerts.is_enabled():
                alerts.send_error_message(error_msg)
                app_logger.debug("Discord error notification sent")
        except Exception as e:
            app_logger.error(f"Failed to send Discord error notification: {e}")
    
    def _on_camera_error(self, error_msg: str):
        """Handle camera error signal - update UI and send Discord notification"""
        app_logger.error(f"Camera error received: {error_msg}")
        self._notify(f"Camera error: {error_msg}", "error")

        # Update UI status
        if hasattr(self, 'app_bar') and self.app_bar:
            self.app_bar.camera_chip.set_status('error')
            self.app_bar.camera_chip.set_label('Camera Error')

        # Send Discord notification
        self._send_discord_error(f"Camera Error: {error_msg}")
    
    def _send_discord_shutdown(self):
        """Send Discord shutdown notification if enabled"""
        try:
            discord_config = self.config.get('discord', {})
            if not discord_config.get('enabled', False):
                return
            
            if not discord_config.get('post_startup_shutdown', False):
                return
            
            from services.discord_alerts import DiscordAlerts
            alerts = DiscordAlerts(self.config)
            
            if alerts.is_enabled():
                alerts.send_shutdown_message()
                app_logger.info("Discord shutdown notification sent")
        except Exception as e:
            app_logger.error(f"Failed to send Discord shutdown notification: {e}")
    
    def _send_discord_capture_started(self):
        """Send Discord capture started notification if enabled"""
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
        """Enable/disable Start Capture based on current mode and readiness."""
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
        """Start capture (camera or watch mode)"""
        mode = self.config.get('capture_mode', 'camera')
        
        try:
            # Ensure output servers are started if configured
            self._ensure_output_servers_started()
            
            if mode == 'camera':
                self._start_camera_capture()
                # Check if camera capture actually started successfully
                if self.camera_controller and not self.camera_controller.is_capturing:
                    app_logger.error("Camera capture failed to start")
                    return
            else:
                self._start_watch_mode()
            
            self.is_capturing = True
            self.app_bar.set_capturing(True)
            self.app_bar.set_status('waiting')  # Show waiting status until first image
            self.capture_started.emit()
            self._notify(f"Capture started ({mode} mode)")

            # PostHog: capture session config snapshot
            self._send_posthog_capture_started(mode)
            
            # Faster status updates while capturing
            self.status_timer.setInterval(200)
            
            # Send Discord capture started notification
            self._send_discord_capture_started()
            
        except Exception as e:
            app_logger.error(f"Failed to start capture: {e}")
            self.is_capturing = False
            self.app_bar.set_capturing(False)
            self._notify(f"Capture failed: {e}", "error")

            # Send Discord error notification
            self._send_discord_error(f"Failed to start capture: {e}")
    
    def stop_capture(self):
        """Stop capture"""
        try:
            # Update UI immediately for responsive feedback
            self.is_capturing = False
            self.app_bar.set_capturing(False)
            
            mode = self.config.get('capture_mode', 'camera')
            
            if mode == 'camera' and self.camera_controller:
                self.camera_controller.stop_capture()
                # Reset camera capabilities in capture settings panel
                if hasattr(self, 'capture_panel'):
                    self.capture_panel.reset_camera_capabilities()
            elif self.watch_controller:
                self.watch_controller.stop_watching()
            
            self.capture_stopped.emit()
            self._notify("Capture stopped")

            # Stop timelapse session when capture stops
            if self.timelapse_controller:
                self.timelapse_controller.on_capture_stopped()

            # Reset meteor session counters when capture stops
            if self.meteor_controller:
                self.meteor_controller.on_capture_stopped()

            # Slower status updates when idle
            self.status_timer.setInterval(1000)
            
            # Reset camera chip to Ready (if cameras detected) or Idle
            self.app_bar.camera_chip.set_status('connected')
            self.app_bar.camera_chip.set_label('Ready')

            self._update_start_button()

            app_logger.info("Capture stopped")

            # PostHog: session summary
            from services.posthog_service import capture_event
            capture_event('capture_stopped', {
                'mode': mode,
                'images_processed': self.image_count,
            })

        except Exception as e:
            app_logger.error(f"Error stopping capture: {e}")
    
    def _send_posthog_capture_started(self, mode: str):
        """Send a PostHog event with the full session config snapshot."""
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
                # Camera
                'camera_name': self.config.get('zwo_selected_camera_name', '') if mode == 'camera' else None,
                'auto_exposure': self.config.get('zwo_auto_exposure', False) if mode == 'camera' else None,
                # Outputs
                'output_file_enabled': True,
                'output_format': self.config.get('output_format', 'jpg'),
                'output_web_enabled': output_cfg.get('webserver_enabled', False),
                'output_discord_enabled': discord_cfg.get('enabled', False),
                'output_discord_interval_min': discord_cfg.get('periodic_interval_minutes', 30) if discord_cfg.get('periodic_enabled') else None,
                'output_rtsp_enabled': rtsp_cfg.get('enabled', False),
                # Features
                'weather_enabled': self.weather_service is not None,
                'timelapse_enabled': timelapse_cfg.get('enabled', False),
                'ml_enabled': ml_cfg.get('enabled', False),
                'overlay_count': len(self.config.get('overlays', [])),
                'auto_stretch_enabled': self.config.get('auto_stretch', {}).get('enabled', False),
                'scheduled_capture': self.config.get('scheduled_capture_enabled', False),
            }

            # Extract which overlay tokens are in use across all overlays
            import re
            overlays = self.config.get('overlays', [])
            tokens_used = set()
            for ov in overlays:
                tokens_used.update(t.upper() for t in re.findall(r'\{([^}]+)\}', ov.get('text', '')))
            if tokens_used:
                props['overlay_tokens'] = sorted(tokens_used)
            # Strip None values for cleaner events
            props = {k: v for k, v in props.items() if v is not None}
            capture_event('capture_started', props)
        except Exception:
            pass

    def _start_camera_capture(self):
        """Initialize and start camera capture"""
        # Import here to avoid circular imports
        from .controllers.camera_controller import CameraControllerQt
        
        if not self.camera_controller:
            self.camera_controller = CameraControllerQt(self)
            # Connect calibration signal
            self.camera_controller.calibration_status.connect(self.on_calibration_status)
            # Connect error signal to send Discord alerts
            self.camera_controller.error.connect(self._on_camera_error)
        
        self.camera_controller.start_capture()
        
        # Only update status if capture actually started successfully
        if self.camera_controller.is_capturing:
            # Update camera chip to show connected
            self.app_bar.camera_chip.set_status('connected')
            self.app_bar.camera_chip.set_label('Connected')
            app_logger.info("Camera capture started")
            
            # Update capture settings panel with camera capabilities (RAW16 support)
            if self.camera_controller.zwo_camera and hasattr(self, 'capture_panel'):
                try:
                    supports_raw16 = self.camera_controller.zwo_camera.supports_raw16
                    bit_depth = self.camera_controller.zwo_camera.sensor_bit_depth
                    self.capture_panel.update_camera_capabilities(supports_raw16, bit_depth)
                except Exception as e:
                    app_logger.debug(f"Could not update camera capabilities: {e}")
        else:
            # Connection failed - camera_controller already logged the error
            self.app_bar.camera_chip.set_status('error')
            self.app_bar.camera_chip.set_label('Connection Failed')
    
    def _start_watch_mode(self):
        """Initialize and start directory watch mode"""
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
    
    # =========================================================================
    # STATUS UPDATES
    # =========================================================================
    
    def _update_status(self):
        """Periodic status update"""
        try:
            # Update app bar status
            self.app_bar.update_status(
                is_capturing=self.is_capturing,
                image_count=self.image_count,
                camera_controller=self.camera_controller,
                live_panel=self.live_panel
            )

            # Update live monitoring if capturing
            if self.is_capturing and self.camera_controller:
                self.live_panel.update_from_camera(self.camera_controller)

            # Sync sprite with actual camera exposure state
            if self.is_capturing and self.camera_controller:
                zwo = getattr(self.camera_controller, 'zwo_camera', None)
                sprite_state = self.app_bar.status_sprite._state
                if zwo and zwo.exposure_start_time is not None:
                    # Shutter is open — show capturing only if we're in a passive state
                    if sprite_state == 'waiting':
                        self.app_bar.set_status('capturing')
                elif sprite_state == 'capturing':
                    # Exposure just finished, image hasn't arrived yet — keep showing capturing
                    pass

            # Timelapse recording badge
            if self.timelapse_controller:
                recording = self.timelapse_controller.get_status().get('recording', False)
                self.nav_rail.set_badge('timelapse', recording)

            # Notification badge
            self.app_bar.update_notification_badge()

        except Exception as e:
            app_logger.debug(f"Status update error: {e}")
    
    def _poll_logs(self):
        """Poll log queue and update displays"""
        messages = app_logger.get_messages()
        if messages:
            # Update live monitoring mini-log
            self.live_panel.append_logs(messages)
            
            # Update logs panel
            self.logs_panel.append_logs(messages)
    
    # =========================================================================
    # SETTINGS
    # =========================================================================
    
    def _on_settings_changed(self):
        """Handle settings change from any panel"""
        # Don't save during config load
        if self.is_loading_config:
            return
        self.save_config()
        
        # Re-init weather service in case weather config changed
        self._init_weather_service(from_settings_save=True)
        
        # Update ML display in live panel if ML settings changed
        ml_config = self.config.get('ml_models', {})
        ml_enabled = ml_config.get('enabled', False) and ml_config.get('show_in_preview', True)
        self.live_panel.metadata.set_ml_enabled(ml_enabled)
        
        # Update status chips based on new settings
        self._update_service_status()

        # Enable/disable Start button based on mode readiness
        self._update_start_button()
        
        # Live update camera settings if capturing (e.g., target brightness, auto-exposure)
        # Debounced to avoid spamming SDK calls during slider drags
        if self.is_capturing and self.camera_controller:
            if not hasattr(self, '_settings_update_timer'):
                from PySide6.QtCore import QTimer
                self._settings_update_timer = QTimer(self)
                self._settings_update_timer.setSingleShot(True)
                self._settings_update_timer.timeout.connect(
                    self.camera_controller.update_settings
                )
            self._settings_update_timer.start(300)
        
        self.config_changed.emit()
    
    def _on_raw16_mode_changed(self, enabled: bool):
        """Handle RAW16 mode toggle from capture settings panel"""
        if not self.camera_controller or not self.camera_controller.is_capturing:
            # Not capturing - setting will apply on next capture start
            return
        
        # Camera is capturing - apply change immediately
        if self.camera_controller.zwo_camera:
            success = self.camera_controller.zwo_camera.set_raw16_mode(enabled)
            if not success:
                # Revert toggle if mode change failed
                if hasattr(self, 'capture_panel'):
                    self.capture_panel._loading_config = True
                    self.capture_panel.raw16_switch.set_checked(not enabled)
                    self.capture_panel._loading_config = False
    
    def _on_allsky_panel_changed(self, cfg: dict) -> None:
        """Panel emitted a settings change — persist to config or trigger calibration."""
        if cfg.get('_action') == 'calibrate':
            self.allsky_controller.start_calibration()
            return
        # Preserve calibration_file from existing config
        existing = self.config.get('allsky_overlay', {})
        cfg['calibration_file'] = existing.get('calibration_file', '')
        self.config.set('allsky_overlay', cfg)
        self.save_config()

    def _on_allsky_settings_changed(self) -> None:
        """Controller saved calibration — reload panel status."""
        self.allsky_panel.load_from_config(self.config.get('allsky_overlay', {}))

    def save_config(self):
        """Save current configuration"""
        # Don't save during config load
        if self.is_loading_config:
            return
        try:
            self.config.save()
            app_logger.debug("Configuration saved")
        except Exception as e:
            app_logger.error(f"Failed to save config: {e}")
    
    def load_config(self):
        """Load configuration and update all panels"""
        self.is_loading_config = True
        try:
            self.capture_panel.load_from_config(self.config)
            self.output_panel.load_from_config(self.config)
            self.processing_panel.load_from_config(self.config)
            self.overlay_panel.load_from_config(self.config)
            self.timelapse_panel.load_from_config(self.config)
            self.allsky_panel.load_from_config(self.config.get('allsky_overlay', {}))
            self.meteor_panel.load_from_config(self.config.get('meteor', {}))
            self.allsky_controller.load_from_config()
            self.settings_panel.load_from_config(self.config)
            
            # Update ML display in live panel based on config
            ml_config = self.config.get('ml_models', {})
            ml_enabled = ml_config.get('enabled', False) and ml_config.get('show_in_preview', True)
            self.live_panel.metadata.set_ml_enabled(ml_enabled)
            
            # Set output directory for disk space monitoring
            output_dir = self.config.get('output_directory', '')
            self.live_panel.set_output_directory(output_dir)

            # Update status chips based on config
            self._update_service_status()
            
            # Re-initialize weather service with updated config
            self._init_weather_service()
            
            app_logger.debug("Configuration loaded")
        except Exception as e:
            app_logger.error(f"Failed to load config: {e}")
        finally:
            self.is_loading_config = False
            self._update_start_button()
    
    def _init_weather_service(self, from_settings_save=False):
        """Initialize weather service from config"""
        try:
            from services.weather import WeatherService
            
            weather_config = self.config.get('weather', {})
            api_key = weather_config.get('api_key', '')
            location = weather_config.get('location', '')
            latitude = weather_config.get('latitude', '')
            longitude = weather_config.get('longitude', '')
            units = weather_config.get('units', 'metric')
            
            # Need API key AND (coordinates OR location)
            has_coords = bool(latitude and longitude)
            has_location = bool(location)
            
            if api_key and (has_coords or has_location):
                self.weather_service = WeatherService(
                    api_key, location, units,
                    latitude=latitude if latitude else None,
                    longitude=longitude if longitude else None
                )
                loc_info = f"({latitude}, {longitude})" if has_coords else location
                app_logger.info(f"Weather service initialized: {loc_info}, {units} units")
                if from_settings_save:
                    from services.posthog_service import capture_event
                    capture_event('weather_configured', {'units': units})
            else:
                self.weather_service = None
                app_logger.debug("Weather service not configured (missing API key or location/coordinates)")
        except Exception as e:
            app_logger.error(f"Failed to initialize weather service: {e}")
            self.weather_service = None
    
    def _update_service_status(self):
        """Update app bar status chips based on current config"""
        # Web server status
        output_config = self.config.get('output', {})
        web_enabled = output_config.get('webserver_enabled', False)
        web_running = self.web_server is not None and self.web_server.running
        self.app_bar.set_web_status(web_enabled, web_running)
        
        # Discord status
        discord_config = self.config.get('discord', {})
        discord_enabled = discord_config.get('enabled', False)
        self.app_bar.set_discord_status(discord_enabled)
    
    # =========================================================================
    # IMAGE HANDLING
    # =========================================================================
    
    def on_image_captured(self, pil_image, metadata: dict):
        """Handle new captured image from camera or watch mode

        This receives RAW images and sends them to the image processor
        for auto-stretch, brightness, overlays, and saving.
        """
        self.image_count += 1
        self.app_bar.update_image_count(self.image_count)

        # Cache raw frame so image-processing settings changes can
        # reprocess without waiting for the next exposure.
        # The metadata dict is shallow-copied so processor pops don't
        # remove keys from our cache (numpy arrays are shared, not duped).
        self._cached_raw_image = pil_image.copy()
        self._cached_raw_metadata = metadata.copy()

        # Show status based on whether auto-stretch is enabled
        config = self.config
        auto_stretch_enabled = config.get('auto_stretch', {}).get('enabled', False)
        if auto_stretch_enabled:
            self.app_bar.set_status('stretching')
        else:
            self.app_bar.set_status('processing')

        # Send to image processor for processing and saving
        self.image_processor.process_and_save(pil_image, metadata)

        # Emit signal for other components
        self.image_captured.emit(pil_image)

    def reprocess_last_frame(self):
        """Reprocess the cached raw frame with current settings.

        Called when image-processing or overlay settings change so the user
        sees the effect immediately instead of waiting for the next exposure.
        Debounced to 500ms so slider drags don't queue dozens of reprocesses.
        """
        if self._cached_raw_image is None:
            return

        # Debounce: restart the timer on every call, fire only once after 500ms idle
        if not hasattr(self, '_reprocess_timer'):
            from PySide6.QtCore import QTimer
            self._reprocess_timer = QTimer(self)
            self._reprocess_timer.setSingleShot(True)
            self._reprocess_timer.timeout.connect(self._do_reprocess)
        self._reprocess_timer.start(500)

    def _do_reprocess(self):
        """Actually reprocess the cached frame (called by debounce timer)."""
        if self._cached_raw_image is None:
            return

        from services.logger import app_logger
        app_logger.debug("Reprocessing last frame with updated settings")

        auto_stretch_enabled = self.config.get('auto_stretch', {}).get('enabled', False)
        if auto_stretch_enabled:
            self.app_bar.set_status('stretching')
        else:
            self.app_bar.set_status('processing')

        # Pass copies — processor will gather fresh config from UI automatically
        self.image_processor.process_and_save(
            self._cached_raw_image, self._cached_raw_metadata
        )
    
    def _on_image_processed(self, processed_image, metadata: dict, output_path: str):
        """Handle processed image from image processor"""
        # Store for preview access
        self.last_processed_image = output_path
        self.preview_metadata = metadata
        
        # Update preview with FINAL processed image (with overlays)
        self.live_panel.update_preview(processed_image, metadata)
        
        # Check if any output servers are enabled
        config = self.config
        output_config = config.get('output', {})
        discord_config = config.get('discord', {})
        has_outputs = (
            output_config.get('webserver_enabled', False) or
            discord_config.get('enabled', False)
        )
        
        if has_outputs:
            # Show sending status briefly
            self.app_bar.set_status('sending')
            # Push to output servers (web, Discord)
            self._push_to_output_servers(output_path, processed_image)
            
            # After sending, set to waiting if capturing
            from PySide6.QtCore import QTimer
            if self.is_capturing:
                QTimer.singleShot(300, lambda: self.app_bar.set_status('waiting'))
            else:
                QTimer.singleShot(300, lambda: self.app_bar.set_status(None))
        else:
            # No outputs, go to waiting if capturing
            if self.is_capturing:
                self.app_bar.set_status('waiting')
            else:
                self.app_bar.set_status(None)
        
        app_logger.debug(f"Image processed: {os.path.basename(output_path)}")
    
    def _on_preview_ready(self, preview_image, hist_data: dict):
        """Handle histogram data from image processor (RAW histogram)"""
        # Update histogram with pre-calculated RAW histogram data
        if hist_data:
            app_logger.debug(f"Histogram data received: r={len(hist_data.get('r', []))}, auto_exposure={hist_data.get('auto_exposure')}, target={hist_data.get('target_brightness')}")
            self.live_panel.histogram.update_from_data(hist_data)
        else:
            app_logger.warning("No histogram data received from processor")
    
    def _on_processing_error(self, error_msg: str):
        """Handle processing error"""
        self.app_bar.set_status(None)
        app_logger.error(f"Image processing error: {error_msg}")
    
    def on_calibration_status(self, is_calibrating: bool):
        """Handle calibration status change from camera
        
        Args:
            is_calibrating: True when calibration starts, False when complete
        """
        if is_calibrating:
            self.app_bar.set_status('calibrating')
            app_logger.debug("Calibration started")
        else:
            self.app_bar.set_status('waiting')
            app_logger.debug("Calibration complete")
    
    # =========================================================================
    # OUTPUT SERVER MANAGEMENT
    # =========================================================================
    
    def _ensure_output_servers_started(self):
        """Ensure output servers are started if configured (called when capture begins)"""
        output_config = self.config.get('output', {})

        # Start web server if enabled and not running
        if output_config.get('webserver_enabled', False):
            if not self.web_server or not self.web_server.running:
                self._start_web_server()

    def _start_web_server(self):
        """Start web server with current settings"""
        output_config = self.config.get('output', {})
        
        host = output_config.get('webserver_host', '127.0.0.1')
        port = output_config.get('webserver_port', 8080)
        image_path = output_config.get('webserver_path', '/latest')
        status_path = output_config.get('webserver_status_path', '/status')
        
        self.web_server = WebOutputServer(host, port, image_path, status_path)
        if self.web_server.start():
            url = self.web_server.get_url()
            status_url = self.web_server.get_status_url()
            app_logger.info(f"Web server started: {url}")
            app_logger.info(f"Status endpoint: {status_url}")
            self._notify(f"Web server started: {url}")

            # Update status chip
            self.app_bar.set_web_status(True, True)
        else:
            app_logger.error("Failed to start web server")
            self._notify("Web server failed to start", "error")
            self.web_server = None
            self.app_bar.set_web_status(True, False)
    
    def _stop_web_server(self):
        """Stop the web server if running"""
        if self.web_server:
            try:
                self.web_server.stop()
                self.web_server = None
                app_logger.info("Web server stopped")
                self.app_bar.set_web_status(False, False)
            except Exception as e:
                app_logger.error(f"Error stopping web server: {e}")

    def _push_to_output_servers(self, image_path: str, processed_img):
        """Push processed image to active output servers
        
        Args:
            image_path: Path to the saved image file
            processed_img: PIL Image object
        """
        import io
        
        try:
            # Push to web server if running
            if self.web_server and self.web_server.running:
                img_bytes = io.BytesIO()
                
                # Use configured output format and quality
                output_config = self.config.get('output', {})
                output_format = output_config.get('output_format', 'PNG').upper()
                
                if output_format in ('JPG', 'JPEG'):
                    quality = output_config.get('jpg_quality', 85)
                    processed_img.save(img_bytes, format='JPEG', quality=quality, optimize=True)
                    content_type = 'image/jpeg'
                else:
                    processed_img.save(img_bytes, format='PNG', optimize=True)
                    content_type = 'image/png'
                
                self.web_server.update_image(
                    image_path,
                    img_bytes.getvalue(),
                    metadata=self.preview_metadata,
                    content_type=content_type
                )
                app_logger.debug(f"Pushed image to web server ({content_type})")
            
            # Send to Discord if enabled and periodic posting is on
            discord_config = self.config.get('discord', {})
            discord_enabled = discord_config.get('enabled', False)
            periodic_enabled = discord_config.get('periodic_enabled', False)
            
            if discord_enabled and periodic_enabled:
                # Post first image immediately, then based on interval
                should_post = False
                
                if not hasattr(self, 'first_image_posted_to_discord'):
                    self.first_image_posted_to_discord = False
                if not hasattr(self, '_discord_jitter_seconds'):
                    self._discord_jitter_seconds = 0

                if not self.first_image_posted_to_discord:
                    should_post = True
                    app_logger.info(f"Posting first image to Discord: {image_path}")
                else:
                    # Check interval with jitter to reduce network load
                    interval_minutes = max(30, discord_config.get('periodic_interval_minutes', 30))

                    if not hasattr(self, 'last_discord_post_time'):
                        self.last_discord_post_time = None

                    if self.last_discord_post_time is None:
                        should_post = True
                    else:
                        from datetime import datetime, timedelta
                        elapsed_seconds = (datetime.now() - self.last_discord_post_time).total_seconds()
                        target_seconds = (interval_minutes * 60) - self._discord_jitter_seconds
                        if elapsed_seconds >= target_seconds:
                            should_post = True
                            actual_min = elapsed_seconds / 60
                            app_logger.info(
                                f"Posting periodic Discord update "
                                f"(interval: {interval_minutes}m, jitter: -{self._discord_jitter_seconds}s, "
                                f"actual: {actual_min:.1f}m)"
                            )
                
                if should_post:
                    success = self._send_discord_periodic_update(image_path)
                    if success:
                        if not self.first_image_posted_to_discord:
                            self.first_image_posted_to_discord = True
                        # Recalculate jitter for next cycle (0–300 seconds / 0–5 minutes)
                        self._discord_jitter_seconds = random.randint(0, 300)
                        app_logger.debug(f"Next Discord jitter: -{self._discord_jitter_seconds}s")
                
        except Exception as e:
            app_logger.error(f"Error pushing to output servers: {e}")
    
    def _send_discord_periodic_update(self, image_path: str):
        """Send periodic update to Discord with latest image
        
        Returns:
            bool: True if sent successfully, False otherwise
        """
        try:
            from services.discord_alerts import DiscordAlerts
            from datetime import datetime
            
            alerts = DiscordAlerts(self.config)
            
            if not alerts.is_enabled():
                return False
            
            # Build status message
            mode = "ZWO Camera" if self.is_capturing else "Directory Watch"
            count = self.image_count
            
            # Get camera info if capturing
            camera_info = ""
            if self.is_capturing and self.camera_controller and self.camera_controller.zwo_camera:
                from services.discord_alerts import format_exposure_time
                camera_settings = self.config.get('zwo_camera', {})
                exposure_seconds = self.camera_controller.zwo_camera.exposure_seconds
                gain = self.camera_controller.zwo_camera.gain
                exposure_formatted = format_exposure_time(exposure_seconds)
                camera_info = f"\n**Exposure:** {exposure_formatted}\n**Gain:** {gain}"
            
            message = f"""**Periodic Status Update**

**Mode:** {mode}
**Images Processed:** {count}{camera_info}
**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
            
            # Send with image if configured
            discord_config = self.config.get('discord', {})
            include_image = discord_config.get('include_image', True)
            
            attach_image = image_path if include_image else None
            
            success = alerts.send_discord_message(
                title=f"{self.config.get('app_name', 'PFRSentinel')} - Status Update",
                description=message,
                level="info",
                image_path=attach_image
            )
            
            if success:
                self.last_discord_post_time = datetime.now()
                app_logger.info("Discord update sent successfully")
                from services.posthog_service import capture_event
                capture_event('discord_post_sent', {
                    'interval_minutes': discord_config.get('periodic_interval_minutes', 30),
                    'include_image': include_image,
                })
                return True
            else:
                app_logger.warning(f"Discord update failed: {alerts.last_send_status}")
                return False
                
        except Exception as e:
            app_logger.error(f"Error sending Discord update: {e}")
            return False
    
    # =========================================================================
    # WINDOW EVENTS
    # =========================================================================
    
    def closeEvent(self, event):
        """Handle window close"""
        # If in tray mode, hide to tray instead of closing
        if self.system_tray is not None:
            event.ignore()  # Don't close the window
            self.hide()     # Hide it instead
            app_logger.debug("Window minimized to system tray")
            return
        
        # Normal close: send shutdown notification and cleanup
        # Send Discord shutdown notification first
        self._send_discord_shutdown()
        
        # Stop all timers first to prevent callbacks during shutdown
        if hasattr(self, 'status_timer') and self.status_timer:
            self.status_timer.stop()
        if hasattr(self, 'log_timer') and self.log_timer:
            self.log_timer.stop()
        
        # Stop update checker
        if hasattr(self, 'update_checker') and self.update_checker:
            self.update_checker.stop()
        
        # Save geometry
        geo = f"{self.width()}x{self.height()}"
        self.config.set('window_geometry', geo)
        
        # Save splitter sizes
        sizes = self.splitter.sizes()
        self.config.set('splitter_sizes', sizes)
        
        # Save inspector visibility
        inspector_visible = self.inspector_stack.isVisible()
        self.config.set('inspector_visible', inspector_visible)
        
        # Save all changes
        self.config.save()
        
        # Stop capture if running
        if self.is_capturing:
            self.stop_capture()
        
        # Stop image processor
        if self.image_processor:
            self.image_processor.stop()
        
        # Stop output servers
        if self.web_server:
            try:
                self.web_server.stop()
            except Exception:
                pass

        if self.timelapse_controller:
            try:
                self.timelapse_controller.shutdown()
            except Exception:
                pass

        if self.allsky_controller:
            try:
                self.allsky_controller.shutdown()
            except Exception:
                pass

        if self.meteor_controller:
            try:
                self.meteor_controller.shutdown()
            except Exception:
                pass

        # Save config
        self.save_config()
        
        app_logger.info("Application closing")
        event.accept()
        
        # Force quit the application to ensure all threads exit
        QApplication.quit()
    
    def quit_application(self):
        """Properly quit application (called from tray exit)"""
        # Stop the tray icon thread first
        if self.system_tray and hasattr(self.system_tray, 'tray_icon') and self.system_tray.tray_icon:
            try:
                self.system_tray.tray_icon.stop()
            except Exception:
                pass
        
        # Disable tray mode to allow normal close
        self.system_tray = None
        # Close window (will trigger normal closeEvent)
        self.close()
    
    def set_tray_mode(self, enabled: bool):
        """Enable or disable system tray mode
        
        Args:
            enabled: True to enable tray mode, False to disable
        """
        if enabled and self.system_tray is None:
            # Enable tray mode - initialize system tray
            try:
                from .system_tray_qt import SystemTrayQt, PYSTRAY_AVAILABLE
                
                if not PYSTRAY_AVAILABLE:
                    app_logger.warning("System tray mode requires pystray package")
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self,
                        "Missing Dependency",
                        "System tray mode requires the 'pystray' package.\n\n"
                        "Install with: pip install pystray"
                    )
                    # Reset the setting
                    self.config.set('tray_mode_enabled', False)
                    # Update the UI switch if settings panel exists
                    if hasattr(self, 'settings_panel'):
                        self.settings_panel.tray_enabled_switch.setChecked(False)
                    return
                
                # Create system tray (hidden initially since window is already visible)
                self.system_tray = SystemTrayQt(self, QApplication.instance(), auto_start=False)
                self.system_tray._is_visible = True  # Window is currently visible
                app_logger.info("System tray enabled - window will minimize to tray on close")
                
            except Exception as e:
                app_logger.error(f"Failed to enable system tray: {e}")
                self.system_tray = None
                # Reset the setting
                self.config.set('tray_mode_enabled', False)
                if hasattr(self, 'settings_panel'):
                    self.settings_panel.tray_enabled_switch.setChecked(False)
        
        elif not enabled and self.system_tray is not None:
            # Disable tray mode - stop and remove tray
            try:
                if hasattr(self.system_tray, 'tray_icon') and self.system_tray.tray_icon:
                    self.system_tray.tray_icon.stop()
                self.system_tray = None
                app_logger.info("System tray disabled - window will close normally")
            except Exception as e:
                app_logger.error(f"Error disabling system tray: {e}")
                self.system_tray = None

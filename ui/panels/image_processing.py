"""
Image Processing Settings Panel
Settings for resize, brightness, saturation, timestamp, and auto-stretch
"""
import webbrowser
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame,
    QSizePolicy, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QTimer
from qfluentwidgets import (
    CardWidget, SubtitleLabel, BodyLabel, CaptionLabel,
    PushButton, ComboBox, SpinBox, DoubleSpinBox, 
    SwitchButton, FluentIcon, LineEdit, PrimaryPushButton
)

from ..theme.tokens import Colors, Typography, Spacing, Layout
from ..components.cards import SettingsCard, FormRow, SwitchRow, CollapsibleCard, ClickSlider
from services.dev_mode_config import is_dev_mode_available


class ImageProcessingPanel(QScrollArea):
    """
    Image processing settings panel with:
    - Resize settings
    - Brightness/saturation adjustments
    - Timestamp overlay
    - Auto-stretch (MTF)
    """
    
    settings_changed = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._loading_config = True  # Block signals during init
        self._setup_ui()
        self._loading_config = False
        
        # Timer to refresh ML contribution status periodically
        self._ml_status_timer = QTimer(self)
        self._ml_status_timer.timeout.connect(self._update_ml_contrib_status)
        self._ml_status_timer.start(5000)  # Update every 5 seconds
    
    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Colors.bg_app};
                border: none;
            }}
        """)
        
        content = QWidget()
        self.setWidget(content)
        
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        layout.setSpacing(Spacing.card_gap)
        
        # === RESIZE ===
        resize_card = SettingsCard(
            "Image Resize",
            "Scale output images to reduce file size"
        )
        
        # Resize percentage with slider
        resize_row = QHBoxLayout()
        resize_row.setSpacing(Spacing.md)
        
        self.resize_slider = ClickSlider(Qt.Horizontal)
        self.resize_slider.setRange(10, 100)
        self.resize_slider.setValue(85)
        self.resize_slider.setToolTip("Image scale: 85%")
        self.resize_slider.valueChanged.connect(self._on_resize_changed)
        self.resize_slider.valueChanged.connect(lambda v: self.resize_slider.setToolTip(f"Image scale: {v}%"))
        resize_row.addWidget(self.resize_slider, 1)
        
        self.resize_label = BodyLabel("85%")
        self.resize_label.setFixedWidth(50)
        self.resize_label.setStyleSheet(f"color: {Colors.text_primary};")
        resize_row.addWidget(self.resize_label)
        
        resize_widget = QWidget()
        resize_widget.setLayout(resize_row)
        resize_card.add_row("Scale", resize_widget, "10% to 100%")
        
        layout.addWidget(resize_card)
        
        # === ADJUSTMENTS ===
        adjust_card = SettingsCard(
            "Adjustments",
            "Fine-tune image brightness and saturation"
        )
        
        # Brightness
        self.auto_brightness_switch = SwitchRow(
            "Auto Brightness",
            "Automatically adjust brightness based on image content"
        )
        self.auto_brightness_switch.toggled.connect(self._on_auto_brightness_changed)
        adjust_card.add_widget(self.auto_brightness_switch)
        
        # Brightness factor
        brightness_row = QHBoxLayout()
        brightness_row.setSpacing(Spacing.md)
        
        self.brightness_slider = ClickSlider(Qt.Horizontal)
        self.brightness_slider.setRange(50, 200)
        self.brightness_slider.setValue(100)
        self.brightness_slider.setToolTip("Brightness: 1.0x")
        self.brightness_slider.valueChanged.connect(self._on_brightness_changed)
        self.brightness_slider.valueChanged.connect(lambda v: self.brightness_slider.setToolTip(f"Brightness: {v/100.0:.1f}x"))
        brightness_row.addWidget(self.brightness_slider, 1)
        
        self.brightness_label = BodyLabel("1.0x")
        self.brightness_label.setFixedWidth(50)
        self.brightness_label.setStyleSheet(f"color: {Colors.text_primary};")
        brightness_row.addWidget(self.brightness_label)
        
        brightness_widget = QWidget()
        brightness_widget.setLayout(brightness_row)
        adjust_card.add_row("Brightness", brightness_widget, "0.5x to 2.0x")
        
        # Saturation factor
        saturation_row = QHBoxLayout()
        saturation_row.setSpacing(Spacing.md)
        
        self.saturation_slider = ClickSlider(Qt.Horizontal)
        self.saturation_slider.setRange(0, 200)
        self.saturation_slider.setValue(100)
        self.saturation_slider.setToolTip("Saturation: 1.0x")
        self.saturation_slider.valueChanged.connect(self._on_saturation_changed)
        self.saturation_slider.valueChanged.connect(lambda v: self.saturation_slider.setToolTip(f"Saturation: {v/100.0:.1f}x"))
        saturation_row.addWidget(self.saturation_slider, 1)
        
        self.saturation_label = BodyLabel("1.0x")
        self.saturation_label.setFixedWidth(50)
        self.saturation_label.setStyleSheet(f"color: {Colors.text_primary};")
        saturation_row.addWidget(self.saturation_label)
        
        saturation_widget = QWidget()
        saturation_widget.setLayout(saturation_row)
        adjust_card.add_row("Saturation", saturation_widget, "0.0x to 2.0x")
        
        layout.addWidget(adjust_card)
        
        # === TIMESTAMP ===
        timestamp_card = SettingsCard(
            "Timestamp Overlay",
            "Add timestamp to image corner"
        )
        
        self.timestamp_switch = SwitchRow(
            "Show Timestamp",
            "Display capture time in corner of image"
        )
        self.timestamp_switch.toggled.connect(self._on_timestamp_changed)
        timestamp_card.add_widget(self.timestamp_switch)
        
        layout.addWidget(timestamp_card)
        
        # === AUTO STRETCH ===
        stretch_card = CollapsibleCard("Auto Stretch (MTF)", FluentIcon.BRIGHTNESS)
        
        self.stretch_enabled_switch = SwitchRow(
            "Enable Auto Stretch",
            "Apply Midtone Transfer Function for dynamic range optimization"
        )
        self.stretch_enabled_switch.toggled.connect(self._on_stretch_enabled_changed)
        stretch_card.add_widget(self.stretch_enabled_switch)
        
        # Target median
        target_row = QHBoxLayout()
        target_row.setSpacing(Spacing.md)
        
        self.target_median_slider = ClickSlider(Qt.Horizontal)
        self.target_median_slider.setRange(10, 50)
        self.target_median_slider.setValue(25)
        self.target_median_slider.setToolTip("Target median: 0.25")
        self.target_median_slider.valueChanged.connect(self._on_stretch_settings_changed)
        self.target_median_slider.valueChanged.connect(lambda v: self.target_median_slider.setToolTip(f"Target median: {v/100.0:.2f}"))
        target_row.addWidget(self.target_median_slider, 1)
        
        self.target_median_label = BodyLabel("0.25")
        self.target_median_label.setFixedWidth(50)
        self.target_median_label.setStyleSheet(f"color: {Colors.text_primary};")
        target_row.addWidget(self.target_median_label)
        
        target_widget = QWidget()
        target_widget.setLayout(target_row)
        stretch_card.add_row("Target Median", target_widget, "0.1 to 0.5")
        
        # Linked stretch
        self.linked_stretch_switch = SwitchRow(
            "Linked Channels",
            "Apply same stretch to all RGB channels"
        )
        self.linked_stretch_switch.set_checked(True)
        self.linked_stretch_switch.toggled.connect(self._on_stretch_settings_changed)
        stretch_card.add_widget(self.linked_stretch_switch)
        
        # Preserve blacks
        self.preserve_blacks_switch = SwitchRow(
            "Preserve Blacks",
            "Keep true blacks dark instead of lifting to grey"
        )
        self.preserve_blacks_switch.set_checked(True)
        self.preserve_blacks_switch.toggled.connect(self._on_stretch_settings_changed)
        stretch_card.add_widget(self.preserve_blacks_switch)
        
        # Normalize channels (dark scene fix)
        self.normalize_channels_switch = SwitchRow(
            "Dark Scene Color Fix",
            "Equalize R/G/B medians before stretch (fixes purple/magenta in dark images)"
        )
        self.normalize_channels_switch.set_checked(True)
        self.normalize_channels_switch.toggled.connect(self._on_stretch_settings_changed)
        stretch_card.add_widget(self.normalize_channels_switch)
        
        # Dark scene threshold
        threshold_row = QHBoxLayout()
        threshold_row.setSpacing(Spacing.md)
        
        self.dark_threshold_slider = ClickSlider(Qt.Horizontal)
        self.dark_threshold_slider.setRange(1, 15)  # 0.01 to 0.15 scaled by 100
        self.dark_threshold_slider.setValue(5)  # 0.05 default
        self.dark_threshold_slider.setToolTip("Dark scene threshold: 0.05")
        self.dark_threshold_slider.valueChanged.connect(self._on_stretch_settings_changed)
        self.dark_threshold_slider.valueChanged.connect(lambda v: self.dark_threshold_slider.setToolTip(f"Dark scene threshold: {v/100.0:.2f}"))
        threshold_row.addWidget(self.dark_threshold_slider, 1)
        
        self.dark_threshold_label = BodyLabel("0.05")
        self.dark_threshold_label.setFixedWidth(50)
        self.dark_threshold_label.setStyleSheet(f"color: {Colors.text_primary};")
        threshold_row.addWidget(self.dark_threshold_label)
        
        threshold_widget = QWidget()
        threshold_widget.setLayout(threshold_row)
        stretch_card.add_row("Dark Threshold", threshold_widget, "Median below this enables color fix")
        
        # Shadow aggressiveness
        shadow_row = QHBoxLayout()
        shadow_row.setSpacing(Spacing.md)
        
        self.shadow_slider = ClickSlider(Qt.Horizontal)
        self.shadow_slider.setRange(15, 40)  # 1.5 to 4.0 scaled by 10
        self.shadow_slider.setValue(28)
        self.shadow_slider.setToolTip("Shadow aggressiveness: 2.8")
        self.shadow_slider.valueChanged.connect(self._on_stretch_settings_changed)
        self.shadow_slider.valueChanged.connect(lambda v: self.shadow_slider.setToolTip(f"Shadow aggressiveness: {v/10.0:.1f}"))
        shadow_row.addWidget(self.shadow_slider, 1)
        
        self.shadow_label = BodyLabel("2.8")
        self.shadow_label.setFixedWidth(50)
        self.shadow_label.setStyleSheet(f"color: {Colors.text_primary};")
        shadow_row.addWidget(self.shadow_label)
        
        shadow_widget = QWidget()
        shadow_widget.setLayout(shadow_row)
        stretch_card.add_row("Shadow Aggressiveness", shadow_widget, "1.5 (aggressive) to 4.0 (gentle)")
        
        # Saturation boost
        boost_row = QHBoxLayout()
        boost_row.setSpacing(Spacing.md)
        
        self.sat_boost_slider = ClickSlider(Qt.Horizontal)
        self.sat_boost_slider.setRange(10, 20)  # 1.0 to 2.0 scaled by 10
        self.sat_boost_slider.setValue(15)
        self.sat_boost_slider.setToolTip("Saturation boost: 1.5x")
        self.sat_boost_slider.valueChanged.connect(self._on_stretch_settings_changed)
        self.sat_boost_slider.valueChanged.connect(lambda v: self.sat_boost_slider.setToolTip(f"Saturation boost: {v/10.0:.1f}x"))
        boost_row.addWidget(self.sat_boost_slider, 1)
        
        self.sat_boost_label = BodyLabel("1.5x")
        self.sat_boost_label.setFixedWidth(50)
        self.sat_boost_label.setStyleSheet(f"color: {Colors.text_primary};")
        boost_row.addWidget(self.sat_boost_label)
        
        boost_widget = QWidget()
        boost_widget.setLayout(boost_row)
        stretch_card.add_row("Saturation Boost", boost_widget, "Post-stretch saturation enhancement")

        # SCNR (green cast removal) — standard astro technique for airglow
        scnr_row = QHBoxLayout()
        scnr_row.setContentsMargins(0, 0, 0, 0)
        scnr_row.setSpacing(8)

        self.scnr_slider = ClickSlider(Qt.Horizontal)
        self.scnr_slider.setRange(0, 100)
        self.scnr_slider.setValue(0)
        self.scnr_slider.setToolTip("SCNR green removal: 0% (off)")
        self.scnr_slider.valueChanged.connect(self._on_stretch_settings_changed)
        self.scnr_slider.valueChanged.connect(
            lambda v: self.scnr_slider.setToolTip(f"SCNR green removal: {v}%")
        )
        scnr_row.addWidget(self.scnr_slider, 1)

        self.scnr_label = BodyLabel("Off")
        self.scnr_label.setFixedWidth(50)
        self.scnr_label.setStyleSheet(f"color: {Colors.text_primary};")
        scnr_row.addWidget(self.scnr_label)

        scnr_widget = QWidget()
        scnr_widget.setLayout(scnr_row)
        stretch_card.add_row("Green Removal (SCNR)", scnr_widget, "Removes airglow / LP green cast per frame")

        layout.addWidget(stretch_card)

        # === ML MODELS (Beta) ===
        ml_card = CollapsibleCard("ML Models (Beta)", FluentIcon.IOT)
        
        self.ml_enabled_switch = SwitchRow(
            "Enable ML Analysis",
            "Use machine learning to analyze observatory conditions"
        )
        self.ml_enabled_switch.toggled.connect(self._on_ml_enabled_changed)
        ml_card.add_widget(self.ml_enabled_switch)
        
        # Info about what ML models do
        ml_info = CaptionLabel(
            "🔭 Roof Classifier: Detects if observatory roof is open or closed\n"
            "🌤️ Sky Classifier: Identifies sky conditions (Clear, Cloudy, etc.) when roof is open\n\n"
            "New overlay tokens available:\n"
            "• {ROOF_STATUS} - \"Open (95%)\" or \"Closed (98%)\"\n"
            "• {SKY_CONDITION} - \"Clear (87%)\" or \"Partly Cloudy (72%)\"\n"
            "• {STARS_VISIBLE} - \"Yes\" or \"No\"\n"
            "• {STAR_DENSITY} - \"High (0.85)\" or \"Low (0.12)\""
        )
        ml_info.setStyleSheet(f"color: {Colors.text_secondary}; padding: 8px;")
        ml_info.setWordWrap(True)
        ml_card.add_widget(ml_info)
        
        # ML status label
        self.ml_status_label = CaptionLabel("Status: Not initialized")
        self.ml_status_label.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
        ml_card.add_widget(self.ml_status_label)
        
        # ASCOM Safety Monitor file output
        self.ascom_safety_switch = SwitchRow(
            "ASCOM Safety File",
            "Write roof status to file for NINA GenericFile safety monitor"
        )
        self.ascom_safety_switch.toggled.connect(self._on_ascom_safety_changed)
        ml_card.add_widget(self.ascom_safety_switch)
        
        # File path input
        ascom_path_row = QHBoxLayout()
        ascom_path_row.setSpacing(Spacing.sm)
        
        self.ascom_file_path = LineEdit()
        self.ascom_file_path.setPlaceholderText("%LOCALAPPDATA%\\PFRSentinel\\RoofStatusFile.txt")
        self.ascom_file_path.editingFinished.connect(self._on_ascom_path_changed)
        ascom_path_row.addWidget(self.ascom_file_path, 1)
        
        self.ascom_browse_btn = PushButton("Browse")
        self.ascom_browse_btn.setFixedWidth(70)
        self.ascom_browse_btn.clicked.connect(self._browse_ascom_path)
        ascom_path_row.addWidget(self.ascom_browse_btn)
        
        ascom_path_widget = QWidget()
        ascom_path_widget.setLayout(ascom_path_row)
        ml_card.add_row("File Path", ascom_path_widget, "Path where NINA monitors for status")
        
        # ASCOM info
        ascom_info = CaptionLabel(
            "Configure NINA GenericFile with:\n"
            "• Preamble: \"Roof Status:\"\n"
            "• Safe trigger: \"OPEN\"\n"
            "• Unsafe trigger: \"CLOSED\""
        )
        ascom_info.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
        ascom_info.setWordWrap(True)
        ml_card.add_widget(ascom_info)
        
        layout.addWidget(ml_card)
        
        # === ML DATA CONTRIBUTION ===
        contrib_card = CollapsibleCard("Community Data Contribution", FluentIcon.PEOPLE)
        
        # Hero info section
        contrib_info = CaptionLabel(
            "🧠 Help improve scene detection for everyone!\n\n"
            "When enabled, PFR Sentinel collects anonymous training samples that help "
            "improve the ML models for all users. Data is stored locally until you "
            "choose to upload it."
        )
        contrib_info.setStyleSheet(f"color: {Colors.text_secondary}; padding: 8px;")
        contrib_info.setWordWrap(True)
        contrib_card.add_widget(contrib_info)
        
        # Enable contribution
        self.ml_contrib_switch = SwitchRow(
            "Enable Data Contribution",
            "Collect anonymous training samples every 30 minutes"
        )
        self.ml_contrib_switch.toggled.connect(self._on_ml_contrib_changed)
        contrib_card.add_widget(self.ml_contrib_switch)
        
        # What's collected info
        collected_info = CaptionLabel(
            "📦 What's collected:\n"
            "• Downscaled image (256×256) - ~80 KB\n"
            "• Camera settings & image statistics\n"
            "• Time/moon context (no GPS data)\n"
            "• Roof/weather status if available"
        )
        collected_info.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
        collected_info.setWordWrap(True)
        contrib_card.add_widget(collected_info)
        
        # Status display with better styling
        self.ml_contrib_status_label = CaptionLabel("Samples: 0 / 500 (0 MB)")
        self.ml_contrib_status_label.setStyleSheet(f"""
            color: {Colors.text_muted}; 
            padding: 12px; 
            background: {Colors.bg_card}; 
            border-radius: 6px;
            font-weight: 500;
        """)
        contrib_card.add_widget(self.ml_contrib_status_label)
        
        # Action buttons row
        contrib_btn_row = QHBoxLayout()
        contrib_btn_row.setSpacing(Spacing.sm)
        
        self.ml_export_btn = PrimaryPushButton("Export for Upload")
        self.ml_export_btn.setIcon(FluentIcon.ZIP_FOLDER)
        self.ml_export_btn.clicked.connect(self._export_ml_data)
        contrib_btn_row.addWidget(self.ml_export_btn)
        
        self.ml_upload_btn = PushButton("Open Upload Form")
        self.ml_upload_btn.setIcon(FluentIcon.LINK)
        self.ml_upload_btn.clicked.connect(self._open_ml_upload_form)
        contrib_btn_row.addWidget(self.ml_upload_btn)
        
        self.ml_clear_btn = PushButton("Clear")
        self.ml_clear_btn.setIcon(FluentIcon.DELETE)
        self.ml_clear_btn.clicked.connect(self._clear_ml_samples)
        contrib_btn_row.addWidget(self.ml_clear_btn)
        
        contrib_btn_row.addStretch()
        
        contrib_btn_widget = QWidget()
        contrib_btn_widget.setLayout(contrib_btn_row)
        contrib_card.add_widget(contrib_btn_widget)
        
        # How to contribute steps
        steps_info = CaptionLabel(
            "📤 How to contribute:\n"
            "1. Enable data contribution above\n"
            "2. Let it collect while you capture normally\n"
            "3. Click 'Export for Upload' when ready\n"
            "4. Upload the ZIP via the Google Form\n"
            "5. Click 'Clear' after successful upload"
        )
        steps_info.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
        steps_info.setWordWrap(True)
        contrib_card.add_widget(steps_info)
        
        layout.addWidget(contrib_card)
        
        # === DEV MODE === (Only show in development builds)
        if is_dev_mode_available():
            dev_card = CollapsibleCard("Developer Mode", FluentIcon.DEVELOPER_TOOLS)
            
            self.dev_mode_switch = SwitchRow(
                "Enable Dev Mode",
                "Save raw images to raw_debug folder for troubleshooting"
            )
            self.dev_mode_switch.toggled.connect(self._on_dev_mode_changed)
            dev_card.add_widget(self.dev_mode_switch)
            
            self.dev_stats_switch = SwitchRow(
                "Log Channel Statistics",
                "Log detailed per-channel histogram stats (R, G, B medians, MAD, etc.)"
            )
            self.dev_stats_switch.set_checked(True)
            self.dev_stats_switch.toggled.connect(self._on_dev_stats_changed)
            dev_card.add_widget(self.dev_stats_switch)
            
            # Info label
            dev_info = CaptionLabel(
                "When enabled, raw images are saved before any processing (stretch, overlays). "
                "Check logs for per-channel statistics to diagnose color balance issues."
            )
            dev_info.setStyleSheet(f"color: {Colors.text_secondary}; padding: 8px;")
            dev_info.setWordWrap(True)
            dev_card.add_widget(dev_info)
            
            layout.addWidget(dev_card)
        
        layout.addStretch()
    
    # === EVENT HANDLERS ===
    
    def _on_resize_changed(self, value):
        self.resize_label.setText(f"{value}%")
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            self.main_window.config.set('resize_percent', value)
            self.settings_changed.emit()
    
    def _on_auto_brightness_changed(self, checked):
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            self.main_window.config.set('auto_brightness', checked)
            self.settings_changed.emit()
    
    def _on_brightness_changed(self, value):
        factor = value / 100.0
        self.brightness_label.setText(f"{factor:.1f}x")
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            self.main_window.config.set('brightness_factor', factor)
            self.settings_changed.emit()
    
    def _on_saturation_changed(self, value):
        factor = value / 100.0
        self.saturation_label.setText(f"{factor:.1f}x")
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            self.main_window.config.set('saturation_factor', factor)
            self.settings_changed.emit()
    
    def _on_timestamp_changed(self, checked):
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            self.main_window.config.set('timestamp_corner', checked)
            self.settings_changed.emit()
    
    def _on_stretch_enabled_changed(self, checked):
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            stretch = self.main_window.config.get('auto_stretch', {})
            stretch['enabled'] = checked
            self.main_window.config.set('auto_stretch', stretch)
            self.settings_changed.emit()
    
    def _on_stretch_settings_changed(self):
        # Update labels
        self.target_median_label.setText(f"{self.target_median_slider.value() / 100:.2f}")
        self.shadow_label.setText(f"{self.shadow_slider.value() / 10:.1f}")
        self.sat_boost_label.setText(f"{self.sat_boost_slider.value() / 10:.1f}x")
        self.dark_threshold_label.setText(f"{self.dark_threshold_slider.value() / 100:.2f}")
        scnr_val = self.scnr_slider.value()
        self.scnr_label.setText(f"{scnr_val}%" if scnr_val > 0 else "Off")

        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            stretch = self.main_window.config.get('auto_stretch', {})
            stretch['target_median'] = self.target_median_slider.value() / 100
            stretch['linked_stretch'] = self.linked_stretch_switch.is_checked()
            stretch['preserve_blacks'] = self.preserve_blacks_switch.is_checked()
            stretch['normalize_channels'] = self.normalize_channels_switch.is_checked()
            stretch['dark_scene_threshold'] = self.dark_threshold_slider.value() / 100
            stretch['shadow_aggressiveness'] = self.shadow_slider.value() / 10
            stretch['saturation_boost'] = self.sat_boost_slider.value() / 10
            stretch['scnr_amount'] = scnr_val / 100.0
            self.main_window.config.set('auto_stretch', stretch)
            self.settings_changed.emit()
    
    def _on_dev_mode_changed(self, checked):
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            dev_mode = self.main_window.config.get('dev_mode', {})
            dev_mode['enabled'] = checked
            self.main_window.config.set('dev_mode', dev_mode)
            self.main_window.config.save()  # CRITICAL: Save immediately so setting persists
            self.settings_changed.emit()
            from services.logger import app_logger
            app_logger.info(f"Dev Mode {'enabled' if checked else 'disabled'}: raw images will {'be saved to raw_debug/' if checked else 'not be saved'}")
    
    def _on_dev_stats_changed(self, checked):
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            dev_mode = self.main_window.config.get('dev_mode', {})
            dev_mode['save_histogram_stats'] = checked
            self.main_window.config.set('dev_mode', dev_mode)
            self.settings_changed.emit()
    
    def _on_ml_enabled_changed(self, checked):
        """Handle ML Models enable/disable toggle"""
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            ml_config = self.main_window.config.get('ml_models', {})
            ml_config['enabled'] = checked
            self.main_window.config.set('ml_models', ml_config)
            self.main_window.config.save()
            self.settings_changed.emit()
            
            from services.logger import app_logger
            if checked:
                app_logger.info("ML Models enabled - initializing classifiers...")
                self._initialize_ml_service()
            else:
                app_logger.info("ML Models disabled")
                self.ml_status_label.setText("Status: Disabled")
    
    def _initialize_ml_service(self):
        """Initialize ML service and update status label"""
        try:
            from services.ml_service import get_ml_service
            ml = get_ml_service()
            ml.initialize()
            
            status = ml.get_status()
            roof_ok = status['roof_classifier']['available']
            sky_ok = status['sky_classifier']['available']
            
            if roof_ok and sky_ok:
                self.ml_status_label.setText("Status: ✓ Both models loaded")
                self.ml_status_label.setStyleSheet(f"color: #4ade80; padding: 8px;")  # Green
            elif roof_ok:
                self.ml_status_label.setText("Status: ✓ Roof model only")
                self.ml_status_label.setStyleSheet(f"color: #facc15; padding: 8px;")  # Yellow
            elif sky_ok:
                self.ml_status_label.setText("Status: ✓ Sky model only")
                self.ml_status_label.setStyleSheet(f"color: #facc15; padding: 8px;")  # Yellow
            else:
                roof_err = status['roof_classifier'].get('error', 'Unknown')
                self.ml_status_label.setText(f"Status: ✗ No models available ({roof_err})")
                self.ml_status_label.setStyleSheet(f"color: #f87171; padding: 8px;")  # Red
                
        except Exception as e:
            self.ml_status_label.setText(f"Status: ✗ Error: {str(e)[:50]}")
            self.ml_status_label.setStyleSheet(f"color: #f87171; padding: 8px;")
    
    def _on_ascom_safety_changed(self, checked):
        """Handle ASCOM Safety file toggle"""
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            ml_config = self.main_window.config.get('ml_models', {})
            ascom_config = ml_config.get('ascom_safety_file', {})
            ascom_config['enabled'] = checked
            ml_config['ascom_safety_file'] = ascom_config
            self.main_window.config.set('ml_models', ml_config)
            self.main_window.config.save()
            self.settings_changed.emit()
            
            from services.logger import app_logger
            if checked:
                path = ascom_config.get('file_path', '')
                app_logger.info(f"ASCOM Safety file output enabled: {path or '(no path set)'}")
            else:
                app_logger.info("ASCOM Safety file output disabled")
    
    def _on_ascom_path_changed(self):
        """Handle ASCOM file path change"""
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            ml_config = self.main_window.config.get('ml_models', {})
            ascom_config = ml_config.get('ascom_safety_file', {})
            ascom_config['file_path'] = self.ascom_file_path.text().strip()
            ml_config['ascom_safety_file'] = ascom_config
            self.main_window.config.set('ml_models', ml_config)
            self.main_window.config.save()
            self.settings_changed.emit()
    
    def _browse_ascom_path(self):
        """Browse for ASCOM safety file location"""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select ASCOM Safety File Location",
            self.ascom_file_path.text() or "",
            "Text Files (*.txt);;All Files (*.*)"
        )
        if path:
            self.ascom_file_path.setText(path)
            self._on_ascom_path_changed()

    # === ML DATA CONTRIBUTION HANDLERS ===
    
    def _on_ml_contrib_changed(self, checked):
        """Handle ML contribution enable/disable toggle"""
        if self._loading_config:
            return
        if self.main_window and hasattr(self.main_window, 'config'):
            ml_contrib = self.main_window.config.get('ml_contribution', {})
            ml_contrib['enabled'] = checked
            self.main_window.config.set('ml_contribution', ml_contrib)
            self.main_window.config.save()
            self.settings_changed.emit()
            self._update_ml_contrib_status()
            
            from services.logger import app_logger
            if checked:
                app_logger.info("ML Data Contribution enabled - collecting samples every 30 min")
            else:
                app_logger.info("ML Data Contribution disabled")
    
    def _update_ml_contrib_status(self):
        """Update ML contribution status display"""
        try:
            from services.ml_data_collector import get_ml_collector
            collector = get_ml_collector()
            if collector:
                stats = collector.get_stats()
                samples = stats.get('total_samples', 0)
                max_samples = stats.get('max_samples', 500)
                mb = stats.get('disk_usage_mb', 0)
                
                if stats.get('enabled'):
                    status = f"✓ Collecting: {samples} / {max_samples} samples ({mb:.1f} MB)"
                    self.ml_contrib_status_label.setStyleSheet(f"color: #4ade80; padding: 8px;")
                else:
                    status = f"Samples: {samples} / {max_samples} ({mb:.1f} MB)"
                    self.ml_contrib_status_label.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
                
                self.ml_contrib_status_label.setText(status)
                
                # Enable/disable buttons based on sample count
                has_samples = samples > 0
                self.ml_export_btn.setEnabled(has_samples)
                self.ml_clear_btn.setEnabled(has_samples)
        except Exception as e:
            self.ml_contrib_status_label.setText("Status unavailable")
            self.ml_contrib_status_label.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
    
    def _export_ml_data(self):
        """Export ML data for upload"""
        try:
            from services.ml_data_collector import get_ml_collector
            collector = get_ml_collector()
            if collector:
                zip_path = collector.export_for_upload()
                if zip_path and zip_path.exists():
                    # Show success dialog with instructions
                    msg = QMessageBox(self)
                    msg.setWindowTitle("Export Complete")
                    msg.setIcon(QMessageBox.Information)
                    msg.setText("Data exported successfully!")
                    msg.setInformativeText(
                        f"ZIP file created:\n{zip_path}\n\n"
                        "Next steps:\n"
                        "1. Click 'Open Upload Form' to open the Google Form\n"
                        "2. Upload the ZIP file\n"
                        "3. Click 'Clear' after successful upload"
                    )
                    msg.setStandardButtons(QMessageBox.Ok)
                    
                    # Open folder containing ZIP
                    import os
                    os.startfile(zip_path.parent)
                    
                    msg.exec()
                else:
                    QMessageBox.warning(self, "Export Failed", "No samples to export or export failed.")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")
    
    def _open_ml_upload_form(self):
        """Open the Google Form for ML data upload"""
        from services.ml_data_collector import UPLOAD_FORM_URL
        webbrowser.open(UPLOAD_FORM_URL)
    
    def _clear_ml_samples(self):
        """Clear all ML contribution samples"""
        result = QMessageBox.question(
            self,
            "Clear Samples",
            "Are you sure you want to clear all collected samples?\n\n"
            "Only do this after you've successfully uploaded the data.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if result == QMessageBox.Yes:
            try:
                from services.ml_data_collector import get_ml_collector
                collector = get_ml_collector()
                if collector and collector.clear_samples():
                    self._update_ml_contrib_status()
                    QMessageBox.information(self, "Cleared", "All samples have been cleared.")
            except Exception as e:
                QMessageBox.critical(self, "Clear Error", f"Failed to clear samples: {e}")

    # === CONFIG LOADING ===
    
    def load_from_config(self, config):
        """Load settings from config object"""
        self._loading_config = True
        try:
            # Resize
            resize = config.get('resize_percent', 85)
            self.resize_slider.setValue(resize)
            self.resize_label.setText(f"{resize}%")
            
            # Brightness/saturation
            self.auto_brightness_switch.set_checked(config.get('auto_brightness', False))
            
            brightness = int(config.get('brightness_factor', 1.0) * 100)
            self.brightness_slider.setValue(brightness)
            self.brightness_label.setText(f"{brightness / 100:.1f}x")
            
            saturation = int(config.get('saturation_factor', 1.0) * 100)
            self.saturation_slider.setValue(saturation)
            self.saturation_label.setText(f"{saturation / 100:.1f}x")
            
            # Timestamp
            self.timestamp_switch.set_checked(config.get('timestamp_corner', False))
            
            # Auto stretch
            stretch = config.get('auto_stretch', {})
            self.stretch_enabled_switch.set_checked(stretch.get('enabled', False))
            
            target = int(stretch.get('target_median', 0.25) * 100)
            self.target_median_slider.setValue(target)
            self.target_median_label.setText(f"{target / 100:.2f}")
            
            self.linked_stretch_switch.set_checked(stretch.get('linked_stretch', True))
            self.preserve_blacks_switch.set_checked(stretch.get('preserve_blacks', True))
            
            # Dark scene color fix
            self.normalize_channels_switch.set_checked(stretch.get('normalize_channels', True))
            
            dark_threshold = int(stretch.get('dark_scene_threshold', 0.05) * 100)
            self.dark_threshold_slider.setValue(dark_threshold)
            self.dark_threshold_label.setText(f"{dark_threshold / 100:.2f}")
            
            shadow = int(stretch.get('shadow_aggressiveness', 2.8) * 10)
            self.shadow_slider.setValue(shadow)
            self.shadow_label.setText(f"{shadow / 10:.1f}")
            
            boost = int(stretch.get('saturation_boost', 1.5) * 10)
            self.sat_boost_slider.setValue(boost)
            self.sat_boost_label.setText(f"{boost / 10:.1f}x")

            scnr = int(stretch.get('scnr_amount', 0.0) * 100)
            self.scnr_slider.setValue(scnr)
            self.scnr_label.setText(f"{scnr}%" if scnr > 0 else "Off")

            # Dev mode
            dev_mode = config.get('dev_mode', {})
            if hasattr(self, 'dev_mode_switch'):
                self.dev_mode_switch.set_checked(dev_mode.get('enabled', False))
                self.dev_stats_switch.set_checked(dev_mode.get('save_histogram_stats', True))
            
            # ML Models
            ml_config = config.get('ml_models', {})
            self.ml_enabled_switch.set_checked(ml_config.get('enabled', False))
            
            # ASCOM Safety file settings
            ascom_config = ml_config.get('ascom_safety_file', {})
            self.ascom_safety_switch.set_checked(ascom_config.get('enabled', False))
            self.ascom_file_path.setText(ascom_config.get('file_path', ''))
            
            # Initialize ML service if enabled
            if ml_config.get('enabled', False):
                self._initialize_ml_service()
            else:
                self.ml_status_label.setText("Status: Disabled")
                self.ml_status_label.setStyleSheet(f"color: {Colors.text_muted}; padding: 8px;")
            
            # ML Data Contribution
            ml_contrib = config.get('ml_contribution', {})
            self.ml_contrib_switch.set_checked(ml_contrib.get('enabled', False))
        finally:
            self._loading_config = False
        
        # Update ML contribution status (after _loading_config is False)
        self._update_ml_contrib_status()

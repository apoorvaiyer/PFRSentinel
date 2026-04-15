"""
All-Sky Overlay Settings Panel.

Provides:
  - Calibration status + "Calibrate Now" button
  - Layer toggles: Grid, Constellations, Messier, NGC, Planets
  - Per-layer color, opacity, line width controls
  - Enabled/disabled master toggle
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QGridLayout, QSizePolicy, QProgressBar,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from qfluentwidgets import (
    PushButton, SwitchButton, SpinBox, Slider,
    CardWidget, CaptionLabel, BodyLabel, SubtitleLabel,
    ColorPickerButton, FluentIcon,
)

from ..theme.tokens import Colors, Typography, Spacing, Layout


def _section_card(title: str) -> tuple:
    """Create a labelled card widget. Returns (card, inner_layout)."""
    card = CardWidget()
    card.setStyleSheet(f"CardWidget {{ border-radius: {Layout.radius_md}px; }}")
    vl = QVBoxLayout(card)
    vl.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
    vl.setSpacing(Spacing.sm)

    lbl = SubtitleLabel(title)
    vl.addWidget(lbl)
    return card, vl


class LayerToggleRow(QWidget):
    """A row with a label + SwitchButton for a single layer toggle."""

    toggled = Signal(bool)

    def __init__(self, label: str, default: bool = True, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = BodyLabel(label)
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._switch = SwitchButton()
        self._switch.setChecked(default)
        self._switch.checkedChanged.connect(self.toggled)
        layout.addWidget(lbl)
        layout.addWidget(self._switch)

    def is_checked(self) -> bool:
        return self._switch.isChecked()

    def set_checked(self, v: bool) -> None:
        self._switch.setChecked(v)


class QualityBadge(QFrame):
    """Colored pill badge showing the current calibration quality level."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(4)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        layout.addWidget(self._dot)

        self._label = CaptionLabel("Not calibrated")
        layout.addWidget(self._label)

        self.setFixedHeight(26)
        self.set_quality('none')

    def set_quality(self, level: str) -> None:
        from services.allsky.calibration_service import CalibrationQuality
        bg, text = CalibrationQuality.badge_colors(level)
        desc = CalibrationQuality.description(level)
        label = level.capitalize() if level != 'none' else 'None'

        self._label.setText(label)
        self.setToolTip(desc)
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border-radius: 13px; }}"
        )
        self._label.setStyleSheet(
            f"color: {text}; font-size: 11px; font-weight: 600;"
        )
        self._dot.setStyleSheet(
            f"background: {text}; border-radius: 4px;"
        )


class AllSkySettingsPanel(QScrollArea):
    """
    Scrollable settings panel for the All-Sky overlay feature.
    Panels are UI-only — business logic is in AllSkyController.
    """

    # Emitted whenever a setting changes so controller can save config
    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        self.setWidget(inner)
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        self._layout.setSpacing(Spacing.sm)

        self._build_header()
        self._build_calibration_card()
        self._build_master_toggle()
        self._build_grid_card()
        self._build_constellations_card()
        self._build_messier_card()
        self._build_ngc_card()
        self._build_planets_card()

        self._layout.addStretch()

    # ------------------------------------------------------------------
    # Build sections
    # ------------------------------------------------------------------

    def _build_header(self):
        title = SubtitleLabel("All-Sky Overlay")
        self._layout.addWidget(title)
        desc = CaptionLabel(
            "Overlay constellation lines, DSO labels, and planet positions on each frame.\n"
            "Requires fisheye lens calibration before first use."
        )
        desc.setWordWrap(True)
        self._layout.addWidget(desc)

    def _build_calibration_card(self):
        card, vl = _section_card("Lens Calibration")

        # Quality badge + status label row
        badge_row = QHBoxLayout()
        self._quality_badge = QualityBadge()
        badge_row.addWidget(self._quality_badge)
        badge_row.addStretch()
        vl.addLayout(badge_row)

        self._status_label = BodyLabel("Not calibrated")
        self._status_label.setWordWrap(True)
        vl.addWidget(self._status_label)

        self._calibrate_btn = PushButton("Calibrate Now", icon=FluentIcon.SYNC)
        self._calibrate_btn.clicked.connect(self._on_calibrate_clicked)
        vl.addWidget(self._calibrate_btn)

        self._layout.addWidget(card)

    def _build_master_toggle(self):
        card, vl = _section_card("Enable Overlay")
        self._master_toggle = LayerToggleRow("All-Sky overlay enabled", default=False)
        self._master_toggle.toggled.connect(self._on_setting_changed)
        vl.addWidget(self._master_toggle)
        self._layout.addWidget(card)

    def _build_grid_card(self):
        card, vl = _section_card("AltAz Grid")
        self._grid_enabled = LayerToggleRow("Show grid", default=True)
        self._grid_horizon = LayerToggleRow("Horizon circle", default=True)
        self._grid_rings = LayerToggleRow("Altitude rings", default=True)
        self._grid_cardinals = LayerToggleRow("Cardinal labels", default=True)
        for row in (self._grid_enabled, self._grid_horizon,
                    self._grid_rings, self._grid_cardinals):
            row.toggled.connect(self._on_setting_changed)
            vl.addWidget(row)
        self._grid_alt_step = self._spin_row(vl, "Altitude step (°)", 10, 90, 30, 10)
        self._layout.addWidget(card)

    def _build_constellations_card(self):
        card, vl = _section_card("Constellations")
        self._con_enabled = LayerToggleRow("Show constellations", default=True)
        self._con_lines = LayerToggleRow("Lines", default=True)
        self._con_labels = LayerToggleRow("Labels", default=True)
        for row in (self._con_enabled, self._con_lines, self._con_labels):
            row.toggled.connect(self._on_setting_changed)
            vl.addWidget(row)
        self._layout.addWidget(card)

    def _build_messier_card(self):
        card, vl = _section_card("Messier Objects")
        self._messier_enabled = LayerToggleRow("Show Messier objects", default=True)
        self._messier_enabled.toggled.connect(self._on_setting_changed)
        vl.addWidget(self._messier_enabled)
        self._layout.addWidget(card)

    def _build_ngc_card(self):
        card, vl = _section_card("NGC/IC Objects")
        self._ngc_enabled = LayerToggleRow("Show NGC objects (mag filtered)", default=False)
        self._ngc_enabled.toggled.connect(self._on_setting_changed)
        vl.addWidget(self._ngc_enabled)
        self._ngc_max_mag = self._spin_row(vl, "Max magnitude", 5, 12, 8, 1)
        self._layout.addWidget(card)

    def _build_planets_card(self):
        card, vl = _section_card("Planets & Moon")
        self._planets_enabled = LayerToggleRow("Show planets & Moon", default=True)
        self._planets_enabled.toggled.connect(self._on_setting_changed)
        vl.addWidget(self._planets_enabled)
        self._layout.addWidget(card)

    def _spin_row(self, layout, label: str, min_v: int, max_v: int,
                  default: int, step: int) -> SpinBox:
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label))
        spin = SpinBox()
        spin.setRange(min_v, max_v)
        spin.setValue(default)
        spin.setSingleStep(step)
        spin.valueChanged.connect(self._on_setting_changed)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    # ------------------------------------------------------------------
    # Public API (called by controller)
    # ------------------------------------------------------------------

    def set_status(self, message: str) -> None:
        self._status_label.setText(message)

    def set_quality(self, level: str) -> None:
        """Update the calibration quality badge."""
        self._quality_badge.set_quality(level)

    def set_calibrating(self, active: bool) -> None:
        self._calibrate_btn.setEnabled(not active)
        self._calibrate_btn.setText("Calibrating…" if active else "Calibrate Now")

    def load_from_config(self, config: dict) -> None:
        """Populate all controls from the given allsky_overlay config dict."""
        c = config
        self._master_toggle.set_checked(c.get('enabled', False))

        grid = c.get('grid', {})
        self._grid_enabled.set_checked(grid.get('enabled', True))
        self._grid_horizon.set_checked(grid.get('horizon', True))
        self._grid_rings.set_checked(grid.get('altitude_rings', True))
        self._grid_cardinals.set_checked(grid.get('cardinal_labels', True))
        self._grid_alt_step.setValue(int(grid.get('altitude_step', 30)))

        con = c.get('constellations', {})
        self._con_enabled.set_checked(con.get('enabled', True))
        self._con_lines.set_checked(con.get('lines', True))
        self._con_labels.set_checked(con.get('labels', True))

        self._messier_enabled.set_checked(c.get('messier', {}).get('enabled', True))

        ngc = c.get('ngc', {})
        self._ngc_enabled.set_checked(ngc.get('enabled', False))
        self._ngc_max_mag.setValue(int(ngc.get('min_magnitude', 8)))

        self._planets_enabled.set_checked(c.get('planets', {}).get('enabled', True))

    def get_config(self) -> dict:
        """Collect current UI state into allsky_overlay config dict."""
        return {
            'enabled': self._master_toggle.is_checked(),
            'calibration_file': '',  # Preserved by controller from actual config
            'grid': {
                'enabled': self._grid_enabled.is_checked(),
                'horizon': self._grid_horizon.is_checked(),
                'altitude_rings': self._grid_rings.is_checked(),
                'cardinal_labels': self._grid_cardinals.is_checked(),
                'altitude_step': self._grid_alt_step.value(),
                'color': '#336633', 'line_width': 1,
                'label_size': 14, 'opacity': 120,
            },
            'constellations': {
                'enabled': self._con_enabled.is_checked(),
                'lines': self._con_lines.is_checked(),
                'labels': self._con_labels.is_checked(),
                'color': '#4488FF', 'line_width': 1,
                'label_size': 12, 'opacity': 180,
            },
            'messier': {
                'enabled': self._messier_enabled.is_checked(),
                'color': '#FF8844', 'marker_size': 8,
                'label_size': 10, 'opacity': 200,
            },
            'ngc': {
                'enabled': self._ngc_enabled.is_checked(),
                'min_magnitude': float(self._ngc_max_mag.value()),
                'color': '#88FF44', 'marker_size': 6,
                'label_size': 9, 'opacity': 150,
            },
            'planets': {
                'enabled': self._planets_enabled.is_checked(),
                'label_size': 14, 'marker_size': 10, 'opacity': 255,
                'colors': {
                    'Mercury': '#B0B0B0', 'Venus': '#FFFFCC', 'Mars': '#FF6644',
                    'Jupiter': '#FFCC88', 'Saturn': '#FFDDAA',
                    'Uranus': '#88DDFF', 'Neptune': '#4466FF', 'Moon': '#FFFFEE',
                },
            },
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_calibrate_clicked(self):
        """Signal controller to start calibration (controller is wired in main_window)."""
        # Controller is connected externally; emit settings_changed as a trigger
        self.settings_changed.emit({'_action': 'calibrate'})

    def _on_setting_changed(self, *_):
        self.settings_changed.emit(self.get_config())

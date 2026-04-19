#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QCheckBox,
    QDoubleSpinBox, QPushButton, QScrollArea, QComboBox, QLineEdit,
    QTextEdit, QFrame,
)
from PySide6.QtCore import Qt, Signal

from .review_tab import to_bool


class LabelsWidget(QWidget):
    """Right-panel widget: navigation, context, model predictions, label form, save."""

    prev_requested = Signal()
    next_requested = Signal()
    save_requested = Signal()
    save_next_requested = Signal()
    skip_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.unsaved_changes = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Navigation
        nav_group = QGroupBox("Navigation")
        nav_layout = QHBoxLayout(nav_group)

        self.prev_btn = QPushButton("← Previous (A)")
        self.prev_btn.clicked.connect(self.prev_requested)
        nav_layout.addWidget(self.prev_btn)

        self.sample_label = QLabel("0 / 0")
        self.sample_label.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(self.sample_label)

        self.next_btn = QPushButton("Next (D) →")
        self.next_btn.clicked.connect(self.next_requested)
        nav_layout.addWidget(self.next_btn)

        nav_layout.addSpacing(20)

        self.skip_labeled = QCheckBox("Skip labeled")
        self.skip_labeled.setToolTip("Only show unlabeled samples")
        self.skip_labeled.stateChanged.connect(self.skip_changed)
        nav_layout.addWidget(self.skip_labeled)

        self.unlabeled_count = QLabel("")
        self.unlabeled_count.setStyleSheet("color: #888;")
        nav_layout.addWidget(self.unlabeled_count)

        layout.addWidget(nav_group)

        self.timestamp_label = QLabel("")
        self.timestamp_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #0EA5E9;")
        layout.addWidget(self.timestamp_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        context_group = QGroupBox("Auto-Populated Context (from APIs/sensors)")
        context_layout = QVBoxLayout(context_group)
        self.context_text = QTextEdit()
        self.context_text.setReadOnly(True)
        self.context_text.setMaximumHeight(150)
        self.context_text.setStyleSheet("background: #2a2a2a; font-family: monospace;")
        context_layout.addWidget(self.context_text)
        scroll_layout.addWidget(context_group)

        model_group = QGroupBox("🤖 ML Model Predictions")
        model_group.setStyleSheet("QGroupBox { font-weight: bold; color: #8b5cf6; }")
        model_layout = QVBoxLayout(model_group)
        self.model_text = QTextEdit()
        self.model_text.setReadOnly(True)
        self.model_text.setMaximumHeight(220)
        self.model_text.setStyleSheet("background: #1e1b2e; font-family: monospace; border: 2px solid #8b5cf6;")
        model_layout.addWidget(self.model_text)
        scroll_layout.addWidget(model_group)

        labels_group = QGroupBox("Manual Labels (Edit These)")
        labels_group.setStyleSheet("QGroupBox { font-weight: bold; color: #10b981; }")
        labels_layout = QVBoxLayout(labels_group)

        roof_frame = QFrame()
        roof_frame.setStyleSheet("background: #2d1f3d; border-radius: 5px; padding: 8px;")
        roof_layout = QVBoxLayout(roof_frame)
        roof_layout.addWidget(QLabel("ROOF STATE (from pier camera view):"))
        roof_row = QHBoxLayout()
        self.roof_open = QCheckBox("Roof is OPEN (sky visible)")
        self.roof_open.setStyleSheet("font-weight: bold; font-size: 14px;")
        roof_row.addWidget(self.roof_open)
        roof_row.addStretch()
        roof_layout.addLayout(roof_row)
        labels_layout.addWidget(roof_frame)

        sky_frame = QFrame()
        sky_frame.setStyleSheet("background: #1e3a5f; border-radius: 5px; padding: 8px;")
        sky_layout = QVBoxLayout(sky_frame)
        sky_layout.addWidget(QLabel("SKY CONDITIONS (label from all-sky, applies when roof open):"))
        sky_row = QHBoxLayout()
        sky_row.addWidget(QLabel("Overall sky:"))
        self.sky_condition = QComboBox()
        self.sky_condition.addItems(["", "Clear", "Mostly Clear", "Partly Cloudy", "Mostly Cloudy", "Overcast", "Fog/Haze"])
        sky_row.addWidget(self.sky_condition)
        sky_row.addStretch()
        sky_layout.addLayout(sky_row)
        self.clouds_visible = QCheckBox("Clouds visible")
        sky_layout.addWidget(self.clouds_visible)
        labels_layout.addWidget(sky_frame)

        celestial_frame = QFrame()
        celestial_frame.setStyleSheet("background: #1e293b; border-radius: 5px; padding: 8px;")
        celestial_layout = QVBoxLayout(celestial_frame)
        celestial_layout.addWidget(QLabel("CELESTIAL OBJECTS:"))
        self.stars_visible = QCheckBox("Stars visible")
        celestial_layout.addWidget(self.stars_visible)
        star_row = QHBoxLayout()
        star_row.addWidget(QLabel("Star density (0=none, 0.5=moderate, 1=milky way):"))
        self.star_density = QDoubleSpinBox()
        self.star_density.setRange(0, 1)
        self.star_density.setSingleStep(0.1)
        self.star_density.setDecimals(2)
        star_row.addWidget(self.star_density)
        star_row.addStretch()
        celestial_layout.addLayout(star_row)
        self.moon_visible = QCheckBox("Moon visible")
        celestial_layout.addWidget(self.moon_visible)
        labels_layout.addWidget(celestial_frame)

        notes_frame = QFrame()
        notes_frame.setStyleSheet("background: #1e293b; border-radius: 5px; padding: 5px;")
        notes_layout = QVBoxLayout(notes_frame)
        notes_layout.addWidget(QLabel("Notes (optional):"))
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Edge cases, anomalies...")
        notes_layout.addWidget(self.notes_edit)
        labels_layout.addWidget(notes_frame)

        scroll_layout.addWidget(labels_group)

        mode_group = QGroupBox("Classified Mode")
        mode_layout = QHBoxLayout(mode_group)
        self.mode_label = QLabel("")
        self.mode_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #f59e0b;")
        mode_layout.addWidget(self.mode_label)
        scroll_layout.addWidget(mode_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        save_layout = QHBoxLayout()
        self.save_btn = QPushButton("💾 Save Labels (S)")
        self.save_btn.setStyleSheet("background: #10b981; color: white; font-weight: bold; padding: 10px;")
        self.save_btn.clicked.connect(self.save_requested)
        save_layout.addWidget(self.save_btn)

        self.save_next_btn = QPushButton("Save & Next (Space)")
        self.save_next_btn.setStyleSheet("background: #0EA5E9; color: white; font-weight: bold; padding: 10px;")
        self.save_next_btn.clicked.connect(self.save_next_requested)
        save_layout.addWidget(self.save_next_btn)

        layout.addLayout(save_layout)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        self.label_state = QLabel("")
        layout.addWidget(self.label_state)

        for widget in [self.roof_open, self.stars_visible, self.moon_visible, self.clouds_visible]:
            widget.stateChanged.connect(self.mark_unsaved)
        self.star_density.valueChanged.connect(self.mark_unsaved)
        self.sky_condition.currentIndexChanged.connect(self.mark_unsaved)
        self.notes_edit.textChanged.connect(self.mark_unsaved)

    # ── Navigation helpers ────────────────────────────────────────────────────

    def set_navigation(self, index: int, total: int, prev_enabled: bool, next_enabled: bool,
                       folder: str, timestamp: str):
        self.sample_label.setText(f"{index + 1} / {total}")
        self.timestamp_label.setText(f"[{folder}]  Timestamp: {timestamp}")
        self.prev_btn.setEnabled(prev_enabled)
        self.next_btn.setEnabled(next_enabled)

    def update_unlabeled_count(self, samples: list):
        total = len(samples)
        unlabeled = sum(1 for s in samples if not self.is_sample_labeled(s))
        self.unlabeled_count.setText(f"({total - unlabeled}/{total} labeled)")

    def is_sample_labeled(self, sample: dict) -> bool:
        if 'calibration' not in sample:
            return False
        try:
            with open(sample['calibration'], 'r') as f:
                cal = json.load(f)
            return bool(cal.get('labels', {}).get('labeled_at'))
        except Exception:
            return False

    # ── Display setters ───────────────────────────────────────────────────────

    def set_model_text(self, text: str):
        self.model_text.setText(text)

    def mark_unsaved(self):
        self.unsaved_changes = True
        self.update_status()

    def mark_saved(self):
        self.unsaved_changes = False
        self.update_status()

    @property
    def skip_labeled_checked(self) -> bool:
        return self.skip_labeled.isChecked()

    def update_status(self):
        if self.unsaved_changes:
            self.status_label.setText("⚠️ Unsaved changes")
            self.status_label.setStyleSheet("color: #f59e0b;")
        else:
            self.status_label.setText("✓ Saved")
            self.status_label.setStyleSheet("color: #10b981;")

    # ── Core field logic ──────────────────────────────────────────────────────

    def populate_fields(self, cal: dict, roof_pred, sky_pred):
        """Populate all fields from calibration data and ML predictions."""
        context_lines = []

        tc = cal.get('time_context', {})
        context_lines.append(f"Time: {tc.get('period', '?')} ({tc.get('detailed_period', '?')})")
        context_lines.append(f"  Daylight: {tc.get('is_daylight', '?')}, Astro Night: {tc.get('is_astronomical_night', '?')}")

        mc = cal.get('moon_context', {})
        if mc.get('available'):
            context_lines.append(f"Moon: {mc.get('phase_name', '?')} ({mc.get('illumination_pct', 0):.0f}%)")
            context_lines.append(f"  Moon up: {mc.get('moon_is_up', '?')}, Bright: {mc.get('is_bright_moon', '?')}")

        rs = cal.get('roof_state', {})
        if rs.get('available'):
            roof_str = "OPEN" if to_bool(rs.get('roof_open')) else "CLOSED"
            context_lines.append(f"Roof: {roof_str} (from {rs.get('source', '?')})")
        else:
            context_lines.append(f"Roof: Unknown ({rs.get('reason', 'no data')})")

        wc = cal.get('weather_context', {})
        if wc.get('available'):
            context_lines.append(f"Weather: {wc.get('condition', '?')} - {wc.get('description', '?')}")
            context_lines.append(f"  Clouds: {wc.get('cloud_coverage_pct', '?')}%, Humidity: {wc.get('humidity_pct', '?')}%")
            context_lines.append(f"  Clear: {wc.get('is_clear', '?')}")

        se = cal.get('seeing_estimate', {})
        if se.get('available'):
            context_lines.append(f"Seeing: {se.get('quality', '?')} (score: {se.get('overall_score', 0):.2f})")

        ml = cal.get('ml_prediction')
        if ml:
            ml_roof = "OPEN" if to_bool(ml.get('roof_open')) else "CLOSED"
            ml_conf = ml.get('confidence', 0) * 100
            context_lines.append(f"ML Prediction: {ml_roof} ({ml_conf:.1f}% conf) [{ml.get('model_version', '?')}]")

        st = cal.get('stretch', {})
        context_lines.append(f"Image: median_lum={st.get('median_lum', 0):.4f}, dark_scene={st.get('is_dark_scene', '?')}")

        ca = cal.get('corner_analysis', {})
        context_lines.append(f"Corner ratio: {ca.get('corner_to_center_ratio', 0):.4f}")

        self.context_text.setText("\n".join(context_lines))
        self.mode_label.setText(self.classify_mode(cal))

        labels = cal.get('labels', {})
        has_labels = bool(labels.get('labeled_at'))

        for widget in [self.roof_open, self.stars_visible, self.moon_visible,
                       self.clouds_visible, self.star_density, self.sky_condition,
                       self.notes_edit]:
            widget.blockSignals(True)

        if has_labels:
            self.label_state.setText("✓ Previously labeled")
            self.label_state.setStyleSheet("color: #10b981; font-weight: bold;")

            self.roof_open.setChecked(to_bool(labels.get('roof_open', False)))
            self.stars_visible.setChecked(to_bool(labels.get('stars_visible', False)))
            self.star_density.setValue(labels.get('star_density', 0) or 0)
            self.moon_visible.setChecked(labels.get('moon_visible', False) or False)
            self.clouds_visible.setChecked(labels.get('clouds_visible', False) or False)

            sky_cond = labels.get('sky_condition', '')
            idx = self.sky_condition.findText(sky_cond)
            self.sky_condition.setCurrentIndex(idx if idx >= 0 else 0)

            self.notes_edit.setText(labels.get('notes', '') or '')
        else:
            ml_prefilled = False

            if roof_pred is not None:
                self.roof_open.setChecked(bool(roof_pred.roof_open))
                ml_prefilled = True
            else:
                if rs.get('available') and rs.get('source') == 'nina_api':
                    self.roof_open.setChecked(to_bool(rs.get('roof_open', False)))
                else:
                    ratio = ca.get('corner_to_center_ratio', 1.0)
                    self.roof_open.setChecked(ratio < 0.95)

            if sky_pred is not None:
                idx = self.sky_condition.findText(sky_pred.sky_condition)
                self.sky_condition.setCurrentIndex(idx if idx >= 0 else 0)
                cloudy_conditions = ['Partly Cloudy', 'Mostly Cloudy', 'Overcast']
                self.clouds_visible.setChecked(sky_pred.sky_condition in cloudy_conditions)
                self.stars_visible.setChecked(bool(sky_pred.stars_visible))
                self.star_density.setValue(sky_pred.star_density if sky_pred.stars_visible else 0)
                self.moon_visible.setChecked(bool(sky_pred.moon_visible))
                ml_prefilled = True
            else:
                if wc.get('available'):
                    cloud_pct = wc.get('cloud_coverage_pct', 0)
                    self.clouds_visible.setChecked(cloud_pct > 10)
                    if cloud_pct <= 10:
                        sky_cond = "Clear"
                    elif cloud_pct <= 25:
                        sky_cond = "Mostly Clear"
                    elif cloud_pct <= 50:
                        sky_cond = "Partly Cloudy"
                    elif cloud_pct <= 75:
                        sky_cond = "Mostly Cloudy"
                    else:
                        sky_cond = "Overcast"
                    idx = self.sky_condition.findText(sky_cond)
                    self.sky_condition.setCurrentIndex(idx if idx >= 0 else 0)
                else:
                    self.clouds_visible.setChecked(False)
                    self.sky_condition.setCurrentIndex(0)

                self.moon_visible.setChecked(mc.get('moon_is_up', False) if mc.get('available') else False)

                is_night = tc.get('is_astronomical_night', False)
                is_clear = wc.get('is_clear', False) if wc.get('available') else True
                stars_likely = is_night and self.roof_open.isChecked() and is_clear
                self.stars_visible.setChecked(stars_likely)
                self.star_density.setValue(0.5 if stars_likely else 0)

            self.notes_edit.setText('')

            if ml_prefilled:
                self.label_state.setText("🤖 ML-suggested (review & save)")
                self.label_state.setStyleSheet("color: #8b5cf6; font-weight: bold;")
            else:
                self.label_state.setText("⚡ API-suggested (review & save)")
                self.label_state.setStyleSheet("color: #f59e0b; font-weight: bold;")

        for widget in [self.roof_open, self.stars_visible, self.moon_visible,
                       self.clouds_visible, self.star_density, self.sky_condition,
                       self.notes_edit]:
            widget.blockSignals(False)

    def save_labels(self, current_cal: dict, calibration_path: Path) -> bool:
        """Write form values into current_cal and save to disk. Returns True on success."""
        if not current_cal:
            return False

        if 'labels' not in current_cal:
            current_cal['labels'] = {}

        labels = current_cal['labels']
        labels['roof_open'] = self.roof_open.isChecked()
        labels['stars_visible'] = self.stars_visible.isChecked()
        labels['star_density'] = self.star_density.value() if self.stars_visible.isChecked() else 0
        labels['moon_visible'] = self.moon_visible.isChecked()
        labels['clouds_visible'] = self.clouds_visible.isChecked()

        sky_cond = self.sky_condition.currentText()
        if sky_cond:
            labels['sky_condition'] = sky_cond

        notes = self.notes_edit.text().strip()
        if notes:
            labels['notes'] = notes

        labels['labeled_at'] = datetime.now().isoformat()

        try:
            with open(calibration_path, 'w') as f:
                json.dump(current_cal, f, indent=2)
        except OSError:
            return False

        self.mark_saved()
        self.label_state.setText("✓ Previously labeled")
        self.label_state.setStyleSheet("color: #10b981; font-weight: bold;")
        return True

    def classify_mode(self, cal: dict) -> str:
        """Classify image mode from calibration data."""
        tc = cal.get('time_context', {})
        rs = cal.get('roof_state', {})
        ca = cal.get('corner_analysis', {})

        if tc.get('is_daylight'):
            time_period = 'day'
        elif tc.get('is_astronomical_night'):
            time_period = 'night'
        elif tc.get('period') == 'twilight':
            return 'twilight'
        else:
            hour = tc.get('hour', 12)
            time_period = 'night' if (hour >= 20 or hour < 6) else 'day'

        if rs.get('available') and rs.get('source') == 'nina_api':
            roof_open = to_bool(rs.get('roof_open', False))
        else:
            ratio = ca.get('corner_to_center_ratio', 0.95)
            roof_open = ratio < 0.95

        return f"{time_period}_{'roof_open' if roof_open else 'roof_closed'}"

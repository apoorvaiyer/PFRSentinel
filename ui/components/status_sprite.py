"""
Status Sprite Widget
Procedural QPainter animations for each processing state.
Night sky / astrophotography themed — no external assets required.
Text is omitted; hover the widget to see the state label as a tooltip.
"""
import math
import random

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QFontMetrics

from ..theme.tokens import Colors


class StatusSpriteWidget(QWidget):
    """
    Animated status sprite — 44 × 44 px square, pure animation, no text.
    The state name is surfaced as a QToolTip on hover.

    States: idle, waiting, capturing, stretching, processing, sending
    Call set_state(state_str | None) to switch / stop.
    """

    # Fun astrophotography words shown periodically in the waiting speech bubble
    WAITING_WORDS = [
        "Photons!", "Dark skies...", "Seeing: poor", "Collimated!", "No clouds!",
        "PHD2 locked", "Focus!", "Drift aligned", "Sky clear!", "Plate solved",
        "Flip time!", "Dew heater on", "Cold night!", "Tracking...", "Bias frames",
        "Flat frames", "Dark frames", "SNR rising", "Stars sharp!", "Galaxy time!",
        "Nebula mode", "Slewing...", "Polar aligned", "Cosmic rays!", "Orion rising",
        "Milky Way!", "Andromeda!", "Horsehead!", "Crab Nebula", "Ring Nebula",
        "Pleiades up!", "Globular!", "Double star!", "Red dwarf!", "Black holes!",
        "Quasar!", "Exoplanet?", "Perihelion!", "Zenith!", "RA drift: 0",
        "Dec: stable", "Seeing: 2\"", "Lucky imaging", "Airy disk!", "Sub-arcsec!",
        "Clear skies!", "Nebula time!", "Scope cooling", "No dew yet!", "On target!",
    ]

    STATE_TOOLTIPS = {
        'idle':         'Idle — session not running',
        'waiting':      'Waiting for new image',
        'capturing':    'Capturing exposure',
        'calibrating':  'Calibrating camera',
        'stretching':   'Applying histogram stretch',
        'processing':   'Processing image',
        'sending':      'Sending to outputs',
    }

    MIN_SIZE = 44   # minimum widget side length (px)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._frame = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(40)   # 25 fps

        self._waiting_cycle = -1      # tracks which 750-frame cycle we're in
        self._waiting_word = ""       # current word shown in speech bubble
        self._waiting_word_pool = []  # shuffled queue — drains before reshuffling

        self.setMinimumSize(self.MIN_SIZE, self.MIN_SIZE)
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(sp)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def sizeHint(self):
        return QSize(self.MIN_SIZE, self.MIN_SIZE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state):
        """Set animation state.  Pass None to stop."""
        self._state = state.lower() if state else None
        self._frame = 0
        self._waiting_cycle = -1  # force fresh word pick on next waiting paint
        self.setToolTip(self.STATE_TOOLTIPS.get(self._state, '') if self._state else '')
        if self._state is not None:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def _tick(self):
        self._frame = (self._frame + 1) % 3600
        self.update()

    def paintEvent(self, event):
        if self._state is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        draw = {
            'idle':        self._draw_idle,
            'waiting':     self._draw_waiting,
            'capturing':   self._draw_capturing,
            'calibrating': self._draw_calibrating,
            'stretching':  self._draw_stretching,
            'processing':  self._draw_processing,
            'sending':     self._draw_sending,
        }.get(self._state)
        if draw:
            draw(p)
        p.end()

    # ==================================================================
    # Animation painters — each reads Colors tokens directly so theme
    # changes are reflected automatically at paint time
    # ==================================================================

    def _draw_idle(self, p):
        """Crescent moon + three slowly twinkling background stars."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        t = self._frame * 0.012

        r = s * 0.32
        outer = QPainterPath()
        outer.addEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        bite = QPainterPath()
        bite.addEllipse(QRectF(cx - r + r * 0.57, cy - r - r * 0.14, r * 2, r * 2))
        crescent = outer.subtracted(bite)

        moon_c = QColor("#FFD166")
        moon_c.setAlphaF(0.70 + 0.30 * math.sin(t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(moon_c))
        p.drawPath(crescent)

        star_base = QColor(Colors.text_primary)
        for sx, sy, phase in (
            (s * 0.16, s * 0.20, 0.0),
            (s * 0.82, s * 0.16, 1.6),
            (s * 0.77, s * 0.77, 2.9),
        ):
            alpha = 0.25 + 0.35 * (math.sin(t * 0.7 + phase) + 1) / 2
            c = QColor(star_base)
            c.setAlphaF(alpha)
            p.setBrush(c)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(sx - 1.5, sy - 1.5, 3.0, 3.0))

    def _draw_waiting(self, p):
        """Three star-dots pulsing in sequence, with periodic astrophotography speech bubble."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        t = self._frame * 0.055
        c_iris = QColor(Colors.accent_text)

        # Speech bubble: visible for first 200 frames of every 750-frame cycle (~8s on, ~22s off)
        SHOW_FRAMES = 200
        CYCLE = 750
        frame_in_cycle = self._frame % CYCLE
        bubble_visible = frame_in_cycle < SHOW_FRAMES

        # Shift dots into lower third when bubble is showing
        dot_cy = cy + (h * 0.22 if bubble_visible else 0.0)

        for i, sx in enumerate((cx - s * 0.20, cx, cx + s * 0.20)):
            alpha = (math.sin(t - i * math.pi / 2.0) + 1) / 2
            r = 2.5 + alpha * 2.0
            self._draw_star4(p, sx, dot_cy, r, c_iris, 0.20 + alpha * 0.80)

        if not bubble_visible:
            return

        # Fade in (0-15) / hold (15-185) / fade out (185-200)
        if frame_in_cycle < 15:
            fade = frame_in_cycle / 15.0
        elif frame_in_cycle > 185:
            fade = (SHOW_FRAMES - frame_in_cycle) / 15.0
        else:
            fade = 1.0

        current_cycle = self._frame // CYCLE
        if current_cycle != self._waiting_cycle:
            self._waiting_cycle = current_cycle
            if not self._waiting_word_pool:
                pool = list(self.WAITING_WORDS)
                random.shuffle(pool)
                self._waiting_word_pool = pool
            self._waiting_word = self._waiting_word_pool.pop()
        word = self._waiting_word

        # Bubble geometry: sits above the dots, tail points down to the center dot
        margin = 5
        bubble_h = h * 0.44
        bubble_w = w - margin * 2
        bx, by = float(margin), 1.0
        br = 5.0
        tail_half = 5.0
        tail_tip_y = dot_cy - 2.0   # just above center dot

        # Bubble fill
        bg = QColor(Colors.bg_card)
        bg.setAlphaF(0.93 * fade)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(QRectF(bx, by, bubble_w, bubble_h), br, br)

        # Tail triangle (pointing down from bottom-center of bubble)
        tail_root_y = by + bubble_h
        tail_path = QPainterPath()
        tail_path.moveTo(cx - tail_half, tail_root_y)
        tail_path.lineTo(cx + tail_half, tail_root_y)
        tail_path.lineTo(cx, tail_tip_y)
        tail_path.closeSubpath()
        p.setBrush(QBrush(bg))
        p.drawPath(tail_path)

        # Bubble border
        border_c = QColor(Colors.accent_default)
        border_c.setAlphaF(0.45 * fade)
        p.setPen(QPen(border_c, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bubble_w, bubble_h), br, br)

        # Word text — scale font down if the word is wider than the bubble
        max_text_w = bubble_w - 10
        font_px = max(7, int(bubble_h * 0.46))
        font = QFont()
        font.setPixelSize(font_px)
        text_w = QFontMetrics(font).horizontalAdvance(word)
        if text_w > max_text_w:
            font_px = max(7, int(font_px * max_text_w / text_w))
            font.setPixelSize(font_px)
        p.setFont(font)
        text_c = QColor(Colors.accent_text)
        text_c.setAlphaF(fade)
        p.setPen(text_c)
        p.drawText(
            QRectF(bx + 4, by, bubble_w - 8, bubble_h),
            Qt.AlignmentFlag.AlignCenter,
            word,
        )

    def _draw_capturing(self, p):
        """Camera aperture iris — 6 blades rotate while the opening pulses."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        t = self._frame * 0.045
        openness = (math.sin(t * 0.5) + 1) / 2

        ring_c = QColor(Colors.border_focus)
        ring_c.setAlphaF(0.6)
        p.setPen(QPen(ring_c, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        ro = s * 0.41
        p.drawEllipse(QRectF(cx - ro, cy - ro, ro * 2, ro * 2))

        inner_r = s * 0.08 + openness * s * 0.27
        glow_c = QColor(Colors.accent_default)
        glow_c.setAlphaF(0.25 + openness * 0.50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow_c))
        p.drawEllipse(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2))

        blade_len = s * 0.36 * (1.0 - openness * 0.6)
        pen = QPen(QColor(Colors.accent_text), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i in range(6):
            angle = t + i * (math.pi / 3)
            p.drawLine(
                QPointF(cx, cy),
                QPointF(cx + blade_len * math.cos(angle),
                        cy + blade_len * math.sin(angle))
            )

    def _draw_stretching(self, p):
        """Histogram bars compress then stretch into a bell curve — visualises tone mapping."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cx = w / 2.0
        t = self._frame * 0.12  # half-cycle (compress→stretch) ≈ 1 s

        num = 9
        bar_w = s * 0.07
        gap = s * 0.02
        x0 = cx - (num * bar_w + (num - 1) * gap) / 2
        c_iris = QColor(Colors.accent_text)

        # abs(sin) starts at 0 on frame 0 — bars immediately rise from flat,
        # visible even if state only lasts 1-2 seconds
        stretch_factor = abs(math.sin(t))

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(num):
            norm = (i - (num - 1) / 2) / ((num - 1) / 2)  # -1 to +1

            # Compressed: all bars ~15 % of available height
            # Stretched: bell-curve peak (centre tallest, edges shortest)
            compressed_h = (h - 8) * 0.15
            peak_h = math.exp(-0.5 * (norm * 1.5) ** 2) * (h - 8)
            bar_h = max(3.0, compressed_h + stretch_factor * (peak_h - compressed_h))

            c = QColor(c_iris)
            c.setAlphaF(0.35 + stretch_factor * 0.65)
            p.setBrush(c)
            x = x0 + i * (bar_w + gap)
            p.drawRoundedRect(QRectF(x, h - bar_h - 4, bar_w, bar_h), 1.5, 1.5)

    def _draw_processing(self, p):
        """Star-cluster spinner — 8 dots orbit with a trailing fade."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        t = self._frame * 0.14
        r_orbit = s * 0.32
        c_iris = QColor(Colors.accent_text)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            angle = -(i / 8) * 2 * math.pi - t
            alpha = max(0.08, 1.0 - (i / 8) * 0.92)
            dot_r = 1.8 + 1.4 * (1.0 - i / 8)
            dx = cx + r_orbit * math.cos(angle)
            dy = cy + r_orbit * math.sin(angle)
            c = QColor(c_iris)
            c.setAlphaF(alpha)
            p.setBrush(c)
            p.drawEllipse(QRectF(dx - dot_r, dy - dot_r, dot_r * 2, dot_r * 2))

    def _draw_sending(self, p):
        """Shooting star streaks left to right with a fading tail."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cy = h / 2.0
        t = (self._frame * 0.024) % 1.0
        hx = -6.0 + t * (w + 12.0)
        hy = cy + math.sin(t * math.pi) * h * 0.11
        c_iris = QColor(Colors.accent_text)

        p.setPen(Qt.PenStyle.NoPen)
        for j in range(14, 0, -1):
            tx = hx - j * 2.0
            if tx < 0 or tx > s:
                continue
            frac = 1.0 - j / 14.0
            tc = QColor(c_iris)
            tc.setAlphaF(frac * 0.65)
            r = 1.2 + frac * 1.0
            p.setBrush(tc)
            p.drawEllipse(QRectF(tx - r, hy - r, r * 2, r * 2))

        if 0 <= hx <= s:
            self._draw_star4(p, hx, hy, 4.5, QColor("#FFD166"), 1.0)

    def _draw_calibrating(self, p):
        """Pulsing sonar rings + crosshair — camera calibration target."""
        w, h = self.width(), self.height()
        s = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        t = self._frame * 0.05

        # Three rings expanding outward in sequence
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(3):
            phase = (t - i * 0.7) % (math.pi * 2)
            progress = (math.sin(phase * 0.5) + 1) / 2
            ring_r = s * 0.08 + progress * s * 0.36
            alpha = max(0.0, 0.65 * (1.0 - progress))
            ring_c = QColor(Colors.accent_default)
            ring_c.setAlphaF(alpha)
            p.setPen(QPen(ring_c, 1.5))
            p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

        # Crosshair (gap in center)
        ch_c = QColor(Colors.accent_text)
        ch_c.setAlphaF(0.85)
        pen = QPen(ch_c, 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        gap = s * 0.10
        arm = s * 0.24
        p.drawLine(QPointF(cx - arm - gap, cy), QPointF(cx - gap, cy))
        p.drawLine(QPointF(cx + gap, cy), QPointF(cx + arm + gap, cy))
        p.drawLine(QPointF(cx, cy - arm - gap), QPointF(cx, cy - gap))
        p.drawLine(QPointF(cx, cy + gap), QPointF(cx, cy + arm + gap))

        # Wandering dot — drifts around the center as if hunting for calibration lock
        wander = s * 0.10
        dx = cx + math.sin(t * 2.3 + math.cos(t * 0.7)) * wander
        dy = cy + math.cos(t * 1.7 + math.sin(t * 0.4)) * wander
        p.setPen(Qt.PenStyle.NoPen)
        dot_c = QColor(Colors.accent_default)
        p.setBrush(QBrush(dot_c))
        p.drawEllipse(QRectF(dx - 2.5, dy - 2.5, 5.0, 5.0))

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _draw_star4(self, p, cx, cy, r, color, alpha=1.0):
        """4-pointed cross star at (cx, cy) with arm radius r."""
        c = QColor(color)
        c.setAlphaF(alpha)
        pen = QPen(c, max(1.0, r * 0.65))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

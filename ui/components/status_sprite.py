"""
Status Sprite Widget
Procedural QPainter animations for each processing state.
Night sky / astrophotography themed — no external assets required.
"""
import math

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont

from ..theme.tokens import Colors


class StatusSpriteWidget(QWidget):
    """
    Animated status sprite — drop-in replacement for the plain text
    processing_label in AppBar.  Shows a small night-sky themed graphic
    (24 x 24 px) next to the state label.

    States: idle, waiting, capturing, stretching, processing, sending
    Call set_state(state_str | None) to switch / stop.
    """

    STATE_LABELS = {
        'idle':        'Idle',
        'waiting':     'Waiting...',
        'capturing':   'Capturing...',
        'stretching':  'Stretching...',
        'processing':  'Processing...',
        'sending':     'Sending...',
    }

    SPRITE = 24   # sprite canvas side length (px)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._frame = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(40)   # 25 fps

        # Night-sky colour palette (iris purples + warm star tones)
        self._c_iris   = QColor("#B1A9FF")   # lavender iris — main sprite colour
        self._c_blue   = QColor("#5B5BD6")   # deep iris
        self._c_dim    = QColor("#3D3E94")   # dark iris ring / outline
        self._c_gold   = QColor("#FFD166")   # warm star / moon
        self._c_silver = QColor("#EEEEEC")   # moon highlight / faint stars
        self._c_text   = QColor("#A1A09A")   # secondary text label

        self.setFixedSize(130, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state):
        """Set animation state.  Pass None to stop."""
        self._state = state.lower() if state else None
        self._frame = 0
        if self._state is not None:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def _tick(self):
        self._frame = (self._frame + 1) % 3600   # ~2.4 min cycle at 25 fps
        self.update()

    def paintEvent(self, event):
        if self._state is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        sw = self.SPRITE
        cx, cy = sw / 2.0, sw / 2.0

        # --- sprite canvas (left square) ---
        p.save()
        p.setClipRect(0, 0, sw, sw)
        draw = {
            'idle':       self._draw_idle,
            'waiting':    self._draw_waiting,
            'capturing':  self._draw_capturing,
            'stretching': self._draw_stretching,
            'processing': self._draw_processing,
            'sending':    self._draw_sending,
        }.get(self._state)
        if draw:
            draw(p, cx, cy, sw, sw)
        p.restore()

        # --- text label ---
        text = self.STATE_LABELS.get(self._state, self._state.title())
        p.setPen(self._c_text)
        font = QFont("Segoe UI", 9)
        p.setFont(font)
        p.drawText(
            sw + 4, 0, self.width() - sw - 4, self.height(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text
        )
        p.end()

    # ==================================================================
    # Animation painters
    # Each receives (painter, cx, cy, canvas_w, canvas_h)
    # ==================================================================

    def _draw_idle(self, p, cx, cy, w, h):
        """Crescent moon + three slowly twinkling background stars."""
        t = self._frame * 0.012

        # Crescent moon via path subtraction
        r = 7.5
        outer = QPainterPath()
        outer.addEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        bite = QPainterPath()
        bite.addEllipse(QRectF(cx - r + 4.5, cy - r - 1.5, r * 2, r * 2))
        crescent = outer.subtracted(bite)

        moon_alpha = 0.70 + 0.30 * math.sin(t)
        moon_c = QColor(self._c_gold)
        moon_c.setAlphaF(moon_alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(moon_c))
        p.drawPath(crescent)

        # Background stars
        for sx, sy, phase in ((4.0, 5.0, 0.0), (20.0, 4.0, 1.6), (19.0, 18.0, 2.9)):
            alpha = 0.25 + 0.35 * (math.sin(t * 0.7 + phase) + 1) / 2
            sc = QColor(self._c_silver)
            sc.setAlphaF(alpha)
            p.setBrush(sc)
            p.drawEllipse(QRectF(sx - 1.0, sy - 1.0, 2.0, 2.0))

    def _draw_waiting(self, p, cx, cy, w, h):
        """Three star-dots pulsing in sequence — "· · ·" loading."""
        t = self._frame * 0.055
        for i, sx in enumerate((7.0, 12.0, 17.0)):
            alpha = (math.sin(t - i * math.pi / 2.0) + 1) / 2
            r = 1.4 + alpha * 1.2
            self._draw_star4(p, sx, cy, r, self._c_iris, 0.20 + alpha * 0.80)

    def _draw_capturing(self, p, cx, cy, w, h):
        """Camera aperture iris — 6 blades rotate while the opening pulses."""
        t = self._frame * 0.045
        openness = (math.sin(t * 0.5) + 1) / 2   # 0 = closed, 1 = open

        # Outer ring
        ring_c = QColor(self._c_dim)
        ring_c.setAlphaF(0.6)
        p.setPen(QPen(ring_c, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - 10, cy - 10, 20, 20))

        # Aperture glow
        inner_r = 2.0 + openness * 7.0
        glow_c = QColor(self._c_blue)
        glow_c.setAlphaF(0.25 + openness * 0.50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow_c))
        p.drawEllipse(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2))

        # 6 rotating blades
        blade_len = 9.0 * (1.0 - openness * 0.6)
        pen = QPen(self._c_iris, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i in range(6):
            angle = t + i * (math.pi / 3)
            ex = cx + blade_len * math.cos(angle)
            ey = cy + blade_len * math.sin(angle)
            p.drawLine(QPointF(cx, cy), QPointF(ex, ey))

    def _draw_stretching(self, p, cx, cy, w, h):
        """Histogram bars stretch wave — visualises tone mapping."""
        t = self._frame * 0.045
        num = 7
        bar_w, gap = 2.2, 0.8
        x0 = cx - (num * bar_w + (num - 1) * gap) / 2

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(num):
            # Bell-curve natural height
            deviation = (i - (num - 1) / 2) / 2.0
            bell = math.exp(-0.5 * deviation * deviation)
            max_h = bell * (h - 6)

            # Stretch wave propagates left → right
            stretch = (math.sin(i * 0.42 - t) + 1) / 2
            bar_h = max(1.5, max_h * (0.18 + stretch * 0.82))

            alpha = 0.30 + stretch * 0.70
            c = QColor(self._c_iris)
            c.setAlphaF(alpha)
            p.setBrush(c)

            x = x0 + i * (bar_w + gap)
            y = h - bar_h - 2          # bottom-aligned, like a real histogram
            p.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 1.0, 1.0)

    def _draw_processing(self, p, cx, cy, w, h):
        """Star-cluster spinner — 8 dots orbit with a trailing fade."""
        t = self._frame * 0.14
        num = 8
        r_orbit = 7.5
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(num):
            # Dot 0 leads; higher index = further behind
            angle = -(i / num) * 2 * math.pi - t
            alpha = max(0.08, 1.0 - (i / num) * 0.92)
            dot_r = 1.0 + 0.9 * (1.0 - i / num)

            dx = cx + r_orbit * math.cos(angle)
            dy = cy + r_orbit * math.sin(angle)

            c = QColor(self._c_iris)
            c.setAlphaF(alpha)
            p.setBrush(c)
            p.drawEllipse(QRectF(dx - dot_r, dy - dot_r, dot_r * 2, dot_r * 2))

    def _draw_sending(self, p, cx, cy, w, h):
        """Shooting star streaks left to right with a fading tail."""
        t = (self._frame * 0.024) % 1.0
        hx = -4.0 + t * (w + 8.0)
        hy = cy + math.sin(t * math.pi) * 3.0   # gentle arc

        # Tail dots
        p.setPen(Qt.PenStyle.NoPen)
        for j in range(12, 0, -1):
            tx = hx - j * 1.3
            if tx < 0 or tx > w:
                continue
            frac = 1.0 - j / 12.0
            tc = QColor(self._c_iris)
            tc.setAlphaF(frac * 0.65)
            r = 0.7 + frac * 0.6
            p.setBrush(tc)
            p.drawEllipse(QRectF(tx - r, hy - r, r * 2, r * 2))

        # Star head (only when on canvas)
        if 0 <= hx <= w:
            self._draw_star4(p, hx, hy, 2.8, self._c_gold, 1.0)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _draw_star4(self, p, cx, cy, r, color, alpha=1.0):
        """4-pointed cross star at (cx, cy) with arm radius r."""
        c = QColor(color)
        c.setAlphaF(alpha)
        pen = QPen(c, max(0.8, r * 0.65))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

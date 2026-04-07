"""MBESQC ScoreRing -- Circular score gauge widget with count-up animation."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QTimer, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPen, QColor, QFont

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Font
from geoview_pyside6.theme_aware import c

from desktop.widgets.effects import shake_widget


def _ease_out_cubic(t: float) -> float:
    """Cubic ease-out: fast start, slow finish."""
    return 1.0 - (1.0 - t) ** 3


class ScoreRing(QWidget):
    """Circular progress ring showing QC score 0-100 with count-up animation."""

    _FRAME_MS = 16          # ~60fps
    _ANIM_FRAMES = 40       # ~640ms total

    def __init__(self, size: int = 80, parent=None):
        super().__init__(parent)
        self._size = size
        self._score = 0.0          # current display value
        self._target_score = 0.0   # animation target
        self._start_score = 0.0    # animation start
        self._grade = ""
        self._anim_frame = 0
        self._stamp_anim = None    # bounce/shake animation reference
        self.setFixedSize(size, size)
        self.setMinimumSize(size, size)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(self._FRAME_MS)
        self._anim_timer.timeout.connect(self._tick)

    def set_score(self, score: float, grade: str = ""):
        """Animate from current display value to the new score."""
        target = max(0.0, min(100.0, score))
        self._grade = grade
        self._start_score = self._score
        self._target_score = target
        self._anim_frame = 0

        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _tick(self):
        self._anim_frame += 1
        t = min(self._anim_frame / self._ANIM_FRAMES, 1.0)
        eased = _ease_out_cubic(t)
        self._score = self._start_score + (self._target_score - self._start_score) * eased
        self.update()

        if t >= 1.0:
            self._anim_timer.stop()
            self._score = self._target_score
            # Stamp effect on animation completion
            self._play_stamp_effect()

    def _play_stamp_effect(self):
        """PASS bounce or FAIL shake at the end of count-up."""
        score = self._target_score

        if score >= 80:
            # Bounce: scale 1.0 -> 1.12 -> 1.0 via maximumSize (150ms)
            base = self._size
            expanded = int(base * 1.12)

            bounce = QPropertyAnimation(self, b"maximumSize", self)
            bounce.setDuration(150)
            bounce.setStartValue(QSize(base, base))
            bounce.setKeyValueAt(0.5, QSize(expanded, expanded))
            bounce.setEndValue(QSize(base, base))
            bounce.setEasingCurve(QEasingCurve.OutBounce)
            bounce.finished.connect(lambda: self.setFixedSize(base, base))
            bounce.start()
            self._stamp_anim = bounce

        elif score < 50 and score > 0:
            # Shake: 3px, 3 cycles, 200ms
            self._stamp_anim = shake_widget(self, amplitude=3, cycles=3, duration_ms=200)

    def _color_for_score(self) -> str:
        if self._score >= 90:
            return c().GREEN
        if self._score >= 75:
            return c().BLUE
        if self._score >= 60:
            return c().ORANGE
        return c().RED

    def refresh_theme(self):
        """Re-apply theme-dependent colours (resolved at paint time via c())."""
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)

            # Use current widget size for dynamic painting (supports bounce resize)
            w = self.width()
            h = self.height()
            s = min(w, h)

            pen_w = 5
            margin = pen_w + 2
            rect = QRectF(
                (w - s) / 2 + margin,
                (h - s) / 2 + margin,
                s - 2 * margin,
                s - 2 * margin,
            )

            # Background arc
            bg_pen = QPen(QColor(c().SLATE), pen_w)
            bg_pen.setCapStyle(Qt.RoundCap)
            p.setPen(bg_pen)
            p.drawArc(rect, 0, 360 * 16)

            # Foreground arc
            if self._score > 0:
                fg_pen = QPen(QColor(self._color_for_score()), pen_w)
                fg_pen.setCapStyle(Qt.RoundCap)
                p.setPen(fg_pen)
                span = int((self._score / 100.0) * 360 * 16)
                p.drawArc(rect, 90 * 16, -span)

            # Score text
            p.setPen(QColor(c().TEXT_BRIGHT))
            score_font = QFont(Font.SANS, int(s * 0.22))
            score_font.setWeight(QFont.Weight.Bold)
            p.setFont(score_font)
            p.drawText(rect, Qt.AlignCenter, f"{self._score:.0f}")

            # Grade text below score
            if self._grade:
                p.setPen(QColor(self._color_for_score()))
                grade_font = QFont(Font.SANS, int(s * 0.12))
                grade_font.setWeight(QFont.Weight.DemiBold)
                p.setFont(grade_font)
                grade_rect = QRectF(
                    rect.x(), rect.y() + s * 0.25,
                    rect.width(), rect.height(),
                )
                p.drawText(grade_rect, Qt.AlignCenter, self._grade)
        finally:
            p.end()

"""MBESQC ScoreRing -- Circular score gauge widget."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Dark, Font


class ScoreRing(QWidget):
    """Circular progress ring showing QC score 0-100."""

    def __init__(self, size: int = 80, parent=None):
        super().__init__(parent)
        self._size = size
        self._score = 0.0
        self._grade = ""
        self.setFixedSize(size, size)

    def set_score(self, score: float, grade: str = ""):
        self._score = max(0.0, min(100.0, score))
        self._grade = grade
        self.update()

    def _color_for_score(self) -> str:
        if self._score >= 90:
            return Dark.GREEN
        if self._score >= 75:
            return Dark.BLUE
        if self._score >= 60:
            return Dark.ORANGE
        return Dark.RED

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)

            pen_w = 5
            margin = pen_w + 2
            rect = QRectF(margin, margin,
                          self._size - 2 * margin,
                          self._size - 2 * margin)

            # Background arc
            bg_pen = QPen(QColor(Dark.SLATE), pen_w)
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
            p.setPen(QColor(Dark.TEXT_BRIGHT))
            score_font = QFont(Font.SANS, int(self._size * 0.22))
            score_font.setWeight(QFont.Weight.Bold)
            p.setFont(score_font)
            p.drawText(rect, Qt.AlignCenter, f"{self._score:.0f}")

            # Grade text below score
            if self._grade:
                p.setPen(QColor(self._color_for_score()))
                grade_font = QFont(Font.SANS, int(self._size * 0.12))
                grade_font.setWeight(QFont.Weight.DemiBold)
                p.setFont(grade_font)
                grade_rect = QRectF(
                    rect.x(), rect.y() + self._size * 0.25,
                    rect.width(), rect.height(),
                )
                p.drawText(grade_rect, Qt.AlignCenter, self._grade)
        finally:
            p.end()

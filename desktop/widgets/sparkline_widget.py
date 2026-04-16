"""Sparkline widget — 경량 미니 트렌드 차트 (높이 30px).

Phase 1A Step 4: 모듈 트렌드 스파크라인.
KPI 카드 내부, 대시보드 히트맵, Analysis 모듈 카드에 재사용.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath, QLinearGradient
from PySide6.QtWidgets import QWidget

from geoview_pyside6.theme_aware import c


class SparklineWidget(QWidget):
    """미니 트렌드 라인 (30×120 기본, 리사이즈 가능)."""

    def __init__(
        self,
        values: list[float] | None = None,
        *,
        color: str | None = None,
        fill: bool = True,
        show_last_dot: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._values: list[float] = values or []
        self._color = color
        self._fill = fill
        self._show_last_dot = show_last_dot
        self.setMinimumSize(60, 20)
        self.setFixedHeight(30)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if len(self._values) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        pad = 2

        vals = self._values
        lo = min(vals)
        hi = max(vals)
        span = hi - lo if hi != lo else 1.0

        color_str = self._color or c().CYAN
        line_color = QColor(color_str)

        # Build points
        n = len(vals)
        points: list[QPointF] = []
        for i, v in enumerate(vals):
            x = pad + (w - 2 * pad) * i / (n - 1)
            y = h - pad - (h - 2 * pad) * (v - lo) / span
            points.append(QPointF(x, y))

        # Fill gradient
        if self._fill:
            grad = QLinearGradient(0, 0, 0, h)
            fill_c = QColor(color_str)
            fill_c.setAlphaF(0.15)
            grad.setColorAt(0.0, fill_c)
            fill_c2 = QColor(color_str)
            fill_c2.setAlphaF(0.0)
            grad.setColorAt(1.0, fill_c2)

            path = QPainterPath()
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
            path.lineTo(QPointF(points[-1].x(), h))
            path.lineTo(QPointF(points[0].x(), h))
            path.closeSubpath()
            painter.fillPath(path, grad)

        # Line
        pen = QPen(line_color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for i in range(len(points) - 1):
            painter.drawLine(points[i], points[i + 1])

        # Last dot
        if self._show_last_dot and points:
            painter.setBrush(line_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(points[-1], 3, 3)

        painter.end()


class HeatmapCell(QWidget):
    """3×3 미니 히트맵 셀 (9 QC 모듈 상태 시각화)."""

    MODULE_ORDER = [
        "preprocess", "file", "vessel",
        "offset", "motion", "svp",
        "coverage", "crossline", "surface",
    ]

    STATUS_COLORS = {
        "pass": "#34D399",    # emerald
        "warning": "#FBBF24", # amber
        "fail": "#FB7185",    # rose
        "pending": None,      # theme SLATE
        "blocked": None,      # theme SLATE dim
    }

    def __init__(
        self,
        module_scores: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scores: dict[str, str] = module_scores or {}
        self.setFixedSize(54, 54)

    def set_scores(self, scores: dict[str, str]) -> None:
        self._scores = dict(scores)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cell_w = self.width() / 3
        cell_h = self.height() / 3
        gap = 1

        for idx, mod in enumerate(self.MODULE_ORDER):
            row = idx // 3
            col = idx % 3
            x = col * cell_w + gap
            y = row * cell_h + gap
            w = cell_w - 2 * gap
            h = cell_h - 2 * gap

            status = self._scores.get(mod, "pending")
            color_str = self.STATUS_COLORS.get(status)
            if color_str:
                color = QColor(color_str)
            else:
                color = QColor(c().SLATE)

            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(x, y, w, h), 2, 2)

        painter.end()


__all__ = ["SparklineWidget", "HeatmapCell"]

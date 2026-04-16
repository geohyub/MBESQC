"""Project tile — 대시보드 히트맵 카드.

각 프로젝트를 타일로 표시: 이름 + 미니 9모듈 히트맵 + 점수 + 상태.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from geoview_pyside6.constants import Font, Radius, Space
from geoview_pyside6.theme_aware import c

from desktop.widgets.sparkline_widget import HeatmapCell


class ProjectTile(QFrame):
    """프로젝트 히트맵 타일 — 대시보드용."""

    clicked = Signal(int)  # project_id

    def __init__(
        self,
        project_id: int,
        name: str,
        vessel: str = "",
        score: float = 0.0,
        grade: str = "---",
        file_count: int = 0,
        module_scores: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_id = project_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(88)
        self._build_ui(name, vessel, score, grade, file_count, module_scores)
        self._apply_styles()

    def _build_ui(
        self,
        name: str,
        vessel: str,
        score: float,
        grade: str,
        file_count: int,
        module_scores: dict[str, str] | None,
    ) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        layout.setSpacing(Space.MD)

        # Left: text info
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        self._name_label = QLabel(name)
        self._name_label.setStyleSheet(
            f"font-size: {Font.SM}px; font-weight: {Font.SEMIBOLD}; "
            f"color: {c().TEXT_BRIGHT}; background: transparent;"
        )
        text_col.addWidget(self._name_label)

        if vessel:
            vessel_label = QLabel(vessel)
            vessel_label.setStyleSheet(
                f"font-size: {Font.XS}px; color: {c().MUTED}; background: transparent;"
            )
            text_col.addWidget(vessel_label)

        meta = QLabel(f"{file_count} files")
        meta.setStyleSheet(
            f"font-size: {Font.XS}px; color: {c().MUTED}; background: transparent;"
        )
        text_col.addWidget(meta)
        text_col.addStretch()

        layout.addLayout(text_col, 1)

        # Center: heatmap
        self._heatmap = HeatmapCell(module_scores)
        layout.addWidget(self._heatmap)

        # Right: score + grade
        score_col = QVBoxLayout()
        score_col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        score_text = f"{score:.0f}" if score > 0 else "---"
        self._score_label = QLabel(score_text)
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._score_label.setStyleSheet(
            f"font-size: 20px; font-weight: {Font.SEMIBOLD}; "
            f"color: {c().TEXT_BRIGHT}; background: transparent;"
        )
        score_col.addWidget(self._score_label)

        self._grade_label = QLabel(grade)
        self._grade_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grade_label.setStyleSheet(
            f"font-size: {Font.XS}px; font-weight: {Font.MEDIUM}; "
            f"color: {c().CYAN}; background: transparent;"
        )
        score_col.addWidget(self._grade_label)

        layout.addLayout(score_col)

    def _apply_styles(self) -> None:
        self.setObjectName("project_tile")
        self.setStyleSheet(f"""
            #project_tile {{
                background: {c().BG_ALT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.BASE}px;
            }}
            #project_tile:hover {{
                border-color: {c().CYAN};
                background: {c().SLATE};
            }}
        """)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._project_id)
        super().mousePressEvent(event)

    def refresh_theme(self) -> None:
        self._apply_styles()


__all__ = ["ProjectTile"]

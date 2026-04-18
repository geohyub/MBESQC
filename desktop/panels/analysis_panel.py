"""MBESQC AnalysisPanel -- 9-module QC results + charts."""

from __future__ import annotations

import logging
import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QGridLayout,
    QSizePolicy, QStackedWidget, QProgressBar,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QThread, Slot, QObject, QTimer
from PySide6.QtGui import QPainter, QColor

from geoview_pyside6.constants import Font, Space, Radius, STATUS_ICONS
from geoview_pyside6.effects import reveal_widget, stagger_reveal
from geoview_pyside6.theme_aware import c
from geoview_pyside6.widgets import SuccessOverlay

from desktop.services.data_service import DataService
from desktop.services.analysis_service import AnalysisWorker, compute_score, QC_WEIGHTS
from desktop.services.insight_service import (
    build_history_story,
    build_module_story,
    build_result_overview,
    describe_result_snapshot,
    format_number,
    pick_focus_module,
)
# BL-034 lazy-load: heavy widgets (matplotlib / pyqtgraph) are
# imported inside __init__ so app startup doesn't pay the cost
# until the analysis panel is actually shown.
#
# `from __future__ import annotations` (line 3) already defers
# type-hint evaluation, so references like `self._chart:
# MBESChartWidget` remain valid strings.
if False:  # typing-only — lets IDEs follow the names
    from desktop.widgets.score_ring import ScoreRing
    from desktop.widgets.mbes_chart import MBESChartWidget
    from desktop.widgets.crossline_map import CrosslineMap
    from geoview_pyside6.widgets.track_plot import TrackPlot, LineRoute

logger = logging.getLogger(__name__)


def _table_qss() -> str:
    """c()-based table QSS -- theme-aware replacement for TABLE_STYLE."""
    return f"""
        QTableWidget {{
            background: {c().BG};
            alternate-background-color: {c().BG_ALT};
            color: {c().TEXT};
            border: 1px solid {c().BORDER};
            border-radius: {Radius.BASE}px;
            font-size: {Font.SM}px;
            gridline-color: {c().BORDER};
        }}
        QTableWidget::item {{
            padding: 6px 12px;
        }}
        QTableWidget::item:selected {{
            background: {c().SLATE};
        }}
        QTableWidget::item:hover {{
            background: {c().DARK};
        }}
        QHeaderView::section {{
            background: {c().NAVY};
            color: {c().MUTED};
            font-size: {Font.XS}px;
            font-weight: {Font.SEMIBOLD};
            border: none;
            border-bottom: 1px solid {c().BORDER};
            padding: 8px 12px;
            letter-spacing: 0.3px;
        }}
    """


def _btn_primary_qss() -> str:
    """c()-based primary button QSS."""
    return f"""
        QPushButton {{
            background: {c().CYAN};
            color: {c().TEXT_BRIGHT};
            border: none;
            border-radius: {Radius.BASE}px;
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            padding: 7px 18px;
        }}
        QPushButton:hover {{ background: {c().CYAN_H}; }}
        QPushButton:disabled {{
            background: {c().SLATE};
            color: {c().MUTED};
        }}
    """


def _btn_danger_qss() -> str:
    """c()-based danger button QSS."""
    return f"""
        QPushButton {{
            background: {c().RED};
            color: white;
            border: none;
            border-radius: {Radius.BASE}px;
            font-size: {Font.XS}px;
            padding: 5px 14px;
        }}
        QPushButton:hover {{ background: {c().RED_H}; }}
    """


QC_LABELS = {
    "preprocess": "Pre-Processing",
    "file":      "File QC",
    "vessel":    "Vessel QC",
    "offset":    "Offset QC",
    "motion":    "Motion QC",
    "svp":       "SVP QC",
    "coverage":  "Coverage QC",
    "crossline": "Cross-line QC",
    "surface":   "Surface",
}

LABEL_TO_QC_ID = {
    label: qc_id
    for qc_id, label in QC_LABELS.items()
}

QC_HINTS = {
    "preprocess": "전처리 게이트, 항법/설정/초기 메타데이터 검증",
    "file":      "파일 무결성, 네이밍 규칙, 시간 연속성 검사",
    "vessel":    "PDS 설정과 HVF 오프셋 비교 검증",
    "offset":    "빔 패턴에서 Roll/Pitch bias 추정",
    "motion":    "IMU 자세 데이터 품질 (스파이크, 갭, 드리프트)",
    "svp":       "음속 프로파일 적용 여부 및 타당성",
    "coverage":  "스와스 커버리지 및 라인 오버랩 분석",
    "crossline": "IHO S-44 기준 교차 라인 깊이 차이",
    "surface":   "DTM/Density/Std 그리드 품질",
}


def _grade_for_score(score: float) -> str:
    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"


def _color_for_status(status: str) -> str:
    return {
        "PASS": c().GREEN,
        "WARNING": c().ORANGE,
        "FAIL": c().RED,
        "N/A": c().MUTED,
    }.get(status, c().MUTED)


def _score_bar_color(score: float) -> str:
    """Return color based on score threshold."""
    if score >= 80:
        return c().GREEN
    elif score >= 50:
        return c().ORANGE
    return c().RED


def _tint_bg(base_hex: str, tint_hex: str, alpha: float) -> str:
    """Blend a tint color onto a base at given alpha."""
    tint = tint_hex.lstrip("#")
    tr, tg, tb = int(tint[:2], 16), int(tint[2:4], 16), int(tint[4:6], 16)
    base = base_hex.lstrip("#")
    br, bg_, bb = int(base[:2], 16), int(base[2:4], 16), int(base[4:6], 16)
    r = int(br * (1 - alpha) + tr * alpha)
    g = int(bg_ * (1 - alpha) + tg * alpha)
    b = int(bb * (1 - alpha) + tb * alpha)
    return f"rgb({r}, {g}, {b})"


class _AnalysisScoreBar(QWidget):
    """Thin horizontal score bar (4px) with fill proportional to score."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0.0
        self._fill_color = c().BORDER
        self.setFixedHeight(4)

    def set_score(self, score: float, fill_color: str):
        self._score = max(0.0, min(100.0, score))
        self._fill_color = fill_color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        r = h / 2
        bg_color = QColor(c().BORDER)
        painter.setBrush(bg_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, w, h, r, r)
        if self._score > 0:
            fill_w = max(int(w * self._score / 100.0), h)
            painter.setBrush(QColor(self._fill_color))
            painter.drawRoundedRect(0, 0, fill_w, h, r, r)
        painter.end()

    def refresh_theme(self):
        self.update()


class _AnalysisCard(QFrame):
    """Individual QC component result card with score bar and key metric."""

    clicked = Signal(str)  # qc_id

    def __init__(self, qc_id: str, parent=None):
        super().__init__(parent)
        self._qc_id = qc_id
        self._current_status = "---"
        self._current_score = 0.0
        self._key_metric = ""
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setObjectName(f"acard_{qc_id}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        layout.setSpacing(3)

        # Row 1: Name (bold) + weight (muted, right)
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self._name_label = QLabel(QC_LABELS.get(qc_id, qc_id))
        hdr.addWidget(self._name_label)
        hdr.addStretch()

        self._weight_label = QLabel(f"{QC_WEIGHTS.get(qc_id, 0)}pt")
        hdr.addWidget(self._weight_label)
        layout.addLayout(hdr)

        # Row 2: Score bar + score number
        bar_row = QHBoxLayout()
        bar_row.setSpacing(6)
        self._score_bar = _AnalysisScoreBar()
        bar_row.addWidget(self._score_bar, 1)

        self._score_label = QLabel("")
        self._score_label.setFixedWidth(34)
        self._score_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar_row.addWidget(self._score_label)
        layout.addLayout(bar_row)

        # Row 3: Key metric
        self._metric_label = QLabel("")
        self._metric_label.setWordWrap(True)
        layout.addWidget(self._metric_label)

        # Row 4: Status badge
        self._status_label = QLabel("---")
        layout.addWidget(self._status_label)

        self._apply_card_style()

    def set_result(self, status: str, score: float = 0.0,
                   key_metric: str = ""):
        """Update card with QC result data."""
        self._current_status = status.upper() if status else "---"
        self._current_score = score
        self._key_metric = key_metric
        self._apply_card_style()

    def _apply_card_style(self):
        status = self._current_status
        score = self._current_score
        is_empty = status in ("---", "N/A", "")

        # Card background tint + left accent border based on status
        bg = c().DARK
        border_color = c().BORDER
        left_accent = c().BORDER
        if not is_empty:
            if status == "PASS":
                bg = c().DARK
                border_color = c().GREEN
                left_accent = c().GREEN
            elif status == "WARNING":
                bg = _tint_bg(c().DARK, c().ORANGE, 0.05)
                border_color = c().ORANGE
                left_accent = c().ORANGE
            elif status == "FAIL":
                bg = _tint_bg(c().DARK, c().RED, 0.05)
                border_color = c().RED
                left_accent = c().RED

        self.setStyleSheet(f"""
            #{self.objectName()} {{
                background: {bg};
                border: 1px solid {border_color};
                border-left: 3px solid {left_accent};
                border-radius: {Radius.SM}px;
            }}
            #{self.objectName()}:hover {{
                background: {c().NAVY};
            }}
        """)

        # Name
        self._name_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT};
            background: transparent;
        """)

        # Weight
        self._weight_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)

        # Score bar
        if score > 0 and not is_empty:
            bar_color = _score_bar_color(score)
            self._score_bar.set_score(score, bar_color)
            self._score_bar.setVisible(True)
            self._score_label.setText(f"{score:.0f}")
            self._score_label.setStyleSheet(f"""
                font-size: {Font.SM}px;
                font-weight: {Font.BOLD};
                color: {bar_color};
                background: transparent;
            """)
            self._score_label.setVisible(True)
        else:
            self._score_bar.setVisible(False)
            self._score_label.setVisible(False)

        # Key metric
        if self._key_metric and not is_empty:
            self._metric_label.setText(self._key_metric)
            self._metric_label.setVisible(True)
        else:
            self._metric_label.setVisible(False)
        self._metric_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)

        # Status badge
        badge_map = {
            "PASS":    ("V PASS",    c().GREEN),
            "WARNING": ("! WARNING", c().ORANGE),
            "FAIL":    ("X FAIL",    c().RED),
            "N/A":     ("- N/A",     c().MUTED),
        }
        badge_text, badge_color = badge_map.get(
            status, ("- ---", c().MUTED))
        self._status_label.setText(badge_text)
        self._status_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.SEMIBOLD};
            color: {badge_color};
            background: transparent;
        """)

    def refresh_theme(self):
        """Re-apply theme."""
        self._apply_card_style()
        self._score_bar.refresh_theme()

    def mousePressEvent(self, event):
        self.clicked.emit(self._qc_id)


def _slim_scrollbar_qss() -> str:
    """6px slim scrollbar QSS for all QScrollAreas."""
    return f"""
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {c().SLATE};
            border-radius: 3px;
            min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {c().MUTED};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 6px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {c().SLATE};
            border-radius: 3px;
            min-width: 20px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {c().MUTED};
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
    """


class AnalysisPanel(QWidget):
    """QC result display: score ring + 8 QC cards + chart area."""

    panel_title = "QC 분석"

    back_to_project = Signal(int)  # project_id
    toast_requested = Signal(str, str)  # message, level

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_id = None
        self._project_id = None
        self._project = None
        self._result_data = {}
        self._project_results = []
        self._file_counts = {}
        self._worker = None
        self._thread = None
        self._old_workers = []  # GC prevention
        self._chart_pixmap = None
        self._active_qc_id = None
        self._current_detail_items = []
        self._selected_coverage_line: str | None = None
        self._coverage_line_names: list[str] = []
        self._revealed_once = False
        self._build_ui()

    def get_project_id(self) -> int | None:
        return self._project_id

    def _build_ui(self):
        # BL-034 lazy-load: pulls in matplotlib + pyqtgraph only when
        # the analysis panel is actually built (not at module import).
        from desktop.widgets.score_ring import ScoreRing
        from desktop.widgets.mbes_chart import MBESChartWidget
        from desktop.widgets.crossline_map import CrosslineMap
        from geoview_pyside6.widgets.track_plot import TrackPlot

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._main_scroll = scroll
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            {_slim_scrollbar_qss()}
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.XL, Space.LG, Space.XXXL, Space.XL)
        layout.setSpacing(Space.LG)

        # Header: file name + score ring
        header = QHBoxLayout()

        info_col = QVBoxLayout()
        self._file_label = QLabel("---")
        self._file_label.setStyleSheet(f"""
            font-size: {Font.LG}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        info_col.addWidget(self._file_label)

        self._info_label = QLabel("")
        self._info_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            color: {c().MUTED};
            background: transparent;
        """)
        info_col.addWidget(self._info_label)
        info_col.addStretch()
        header.addLayout(info_col)

        header.addStretch()

        # Run QC button
        self._run_btn = QPushButton("QC 실행")
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setFixedSize(100, 36)
        self._run_btn.setStyleSheet(_btn_primary_qss())
        self._run_btn.clicked.connect(self._run_qc)
        header.addWidget(self._run_btn)

        self._score_ring = ScoreRing(100)
        header.addWidget(self._score_ring)

        layout.addLayout(header)

        # Progress bar + cancel button
        progress_row = QHBoxLayout()
        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().CYAN};
            background: transparent;
        """)
        self._progress_label.setVisible(False)
        progress_row.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 12)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setVisible(False)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {c().DARK};
                border: none;
                border-radius: 8px;
                text-align: center;
                color: {c().TEXT};
                font-size: {Font.XS}px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c().CYAN}, stop:1 {c().GREEN});
                border-radius: 7px;
            }}
        """)
        progress_row.addWidget(self._progress_bar, 1)

        self._cancel_btn = QPushButton("취소")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setFixedSize(60, 24)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setStyleSheet(_btn_danger_qss())
        self._cancel_btn.clicked.connect(self._cancel_qc)
        progress_row.addWidget(self._cancel_btn)

        layout.addLayout(progress_row)

        self._overview_frame = QFrame()
        self._overview_frame.setVisible(False)
        self._overview_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        overview_layout = QVBoxLayout(self._overview_frame)
        overview_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        overview_layout.setSpacing(4)

        overview_title = QLabel("현재 QC 요약")
        overview_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        overview_layout.addWidget(overview_title)

        self._overview_headline = QLabel("")
        self._overview_headline.setWordWrap(True)
        self._overview_headline.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        overview_layout.addWidget(self._overview_headline)

        self._overview_body = QLabel("")
        self._overview_body.setWordWrap(True)
        self._overview_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._overview_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        overview_layout.addWidget(self._overview_body)

        # overview_frame is added to layout below (after cards grid)

        self._spotlight_frame = QFrame()
        self._spotlight_frame.setVisible(False)
        self._spotlight_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        spotlight_layout = QVBoxLayout(self._spotlight_frame)
        spotlight_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        spotlight_layout.setSpacing(4)

        spotlight_title = QLabel("핵심 이슈 / 권장 조치")
        spotlight_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        spotlight_layout.addWidget(spotlight_title)

        findings_title = QLabel("핵심 이슈")
        findings_title.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        spotlight_layout.addWidget(findings_title)

        self._spotlight_findings = QLabel("")
        self._spotlight_findings.setWordWrap(True)
        self._spotlight_findings.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._spotlight_findings.setOpenExternalLinks(False)
        self._spotlight_findings.linkActivated.connect(self._on_rich_link_activated)
        self._spotlight_findings.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        spotlight_layout.addWidget(self._spotlight_findings)

        actions_title = QLabel("권장 조치")
        actions_title.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        spotlight_layout.addWidget(actions_title)

        self._spotlight_actions = QLabel("")
        self._spotlight_actions.setWordWrap(True)
        self._spotlight_actions.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._spotlight_actions.setOpenExternalLinks(False)
        self._spotlight_actions.linkActivated.connect(self._on_rich_link_activated)
        self._spotlight_actions.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        spotlight_layout.addWidget(self._spotlight_actions)

        # spotlight_frame is added to layout below (after cards grid)

        self._history_frame = QFrame()
        self._history_frame.setVisible(False)
        self._history_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        history_layout = QVBoxLayout(self._history_frame)
        history_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        history_layout.setSpacing(4)

        history_title = QLabel("최근 추세 / 반복 이슈")
        history_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        history_layout.addWidget(history_title)

        self._history_headline = QLabel("")
        self._history_headline.setWordWrap(True)
        self._history_headline.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        history_layout.addWidget(self._history_headline)

        self._history_body = QLabel("")
        self._history_body.setWordWrap(True)
        self._history_body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._history_body.setOpenExternalLinks(False)
        self._history_body.linkActivated.connect(self._on_rich_link_activated)
        self._history_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        history_layout.addWidget(self._history_body)

        # history_frame is added to layout below (after cards grid)

        self._diff_frame = QFrame()
        self._diff_frame.setVisible(False)
        self._diff_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        diff_layout = QVBoxLayout(self._diff_frame)
        diff_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        diff_layout.setSpacing(4)

        diff_title = QLabel("직전 Run 대비 변화")
        diff_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        diff_layout.addWidget(diff_title)

        self._diff_headline = QLabel("")
        self._diff_headline.setWordWrap(True)
        self._diff_headline.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        diff_layout.addWidget(self._diff_headline)

        self._diff_body = QLabel("")
        self._diff_body.setWordWrap(True)
        self._diff_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        diff_layout.addWidget(self._diff_body)

        self._diff_changes = QLabel("")
        self._diff_changes.setWordWrap(True)
        self._diff_changes.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._diff_changes.setOpenExternalLinks(False)
        self._diff_changes.linkActivated.connect(self._on_rich_link_activated)
        self._diff_changes.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        diff_layout.addWidget(self._diff_changes)

        # diff_frame is added to layout below (after cards grid)

        self._settings_frame = QFrame()
        self._settings_frame.setVisible(False)
        self._settings_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        settings_layout = QVBoxLayout(self._settings_frame)
        settings_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        settings_layout.setSpacing(4)

        settings_title = QLabel("설정 보조 / 재실행 가이드")
        settings_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        settings_layout.addWidget(settings_title)

        self._settings_headline = QLabel("")
        self._settings_headline.setWordWrap(True)
        self._settings_headline.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        settings_layout.addWidget(self._settings_headline)

        self._settings_body = QLabel("")
        self._settings_body.setWordWrap(True)
        self._settings_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._settings_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        settings_layout.addWidget(self._settings_body)

        # settings_frame is added to layout below (after cards grid)

        # QC cards grid (4 columns x 2 rows)
        cards_label = QLabel("QC 모듈 결과")
        cards_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {c().MUTED};
            background: transparent;
        """)
        # ── QC cards grid (MOVED UP -- results first) ──
        layout.addWidget(cards_label)

        self._cards_grid = QGridLayout()
        self._cards_grid.setSpacing(Space.SM)

        self._cards: dict[str, _AnalysisCard] = {}
        qc_ids = list(QC_WEIGHTS.keys())
        for i, qc_id in enumerate(qc_ids):
            card = _AnalysisCard(qc_id)
            card.clicked.connect(self._on_card_clicked)
            row, col = divmod(i, 4)
            self._cards_grid.addWidget(card, row, col)
            self._cards[qc_id] = card

        layout.addLayout(self._cards_grid)

        self._story_frame = QFrame()
        self._story_frame.setVisible(False)
        self._story_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        story_layout = QVBoxLayout(self._story_frame)
        story_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        story_layout.setSpacing(4)

        self._story_title = QLabel("")
        self._story_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        story_layout.addWidget(self._story_title)

        self._story_body = QLabel("")
        self._story_body.setWordWrap(True)
        self._story_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._story_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().TEXT};
            background: transparent;
        """)
        story_layout.addWidget(self._story_body)

        self._story_next = QLabel("")
        self._story_next.setWordWrap(True)
        self._story_next.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._story_next.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        story_layout.addWidget(self._story_next)

        # story_frame is added to layout below (after chart area)

        # ── Module Detail Panel (items table) ──
        self._detail_frame = QFrame()
        self._detail_frame.setVisible(False)
        self._detail_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        detail_layout = QVBoxLayout(self._detail_frame)
        detail_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)

        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet(f"""
            font-size: {Font.SM}px; font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT}; background: transparent;
        """)
        detail_layout.addWidget(self._detail_title)

        self._detail_table = QTableWidget()
        self._detail_table.setColumnCount(3)
        self._detail_table.setHorizontalHeaderLabels(["Status", "항목", "상세"])
        self._detail_table.horizontalHeader().setStretchLastSection(True)
        self._detail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._detail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._detail_table.verticalHeader().setVisible(False)
        self._detail_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._detail_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._detail_table.setMaximumHeight(200)
        self._detail_table.setStyleSheet(_table_qss())
        detail_layout.addWidget(self._detail_table)
        # detail_frame is added to layout below

        # ── Per-line Filter + Table (Coverage/Motion) ──
        self._line_filter_frame = QFrame()
        self._line_filter_frame.setVisible(False)
        lf_layout = QHBoxLayout(self._line_filter_frame)
        lf_layout.setContentsMargins(0, 0, 0, 0)

        lf_label = QLabel("라인 선택:")
        lf_label.setStyleSheet(f"font-size: {Font.XS}px; color: {c().MUTED}; background: transparent;")
        lf_layout.addWidget(lf_label)

        self._line_filter = QComboBox()
        self._line_filter.setFixedWidth(250)
        self._line_filter.setStyleSheet(f"""
            QComboBox {{
                background: {c().DARK};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                padding: 4px 8px;
                font-size: {Font.XS}px;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        self._line_filter.currentIndexChanged.connect(self._on_line_filter_changed)
        lf_layout.addWidget(self._line_filter)

        self._motion_toggle = QPushButton("Per-line")
        self._motion_toggle.setCheckable(True)
        self._motion_toggle.setFixedSize(80, 24)
        self._motion_toggle.setVisible(False)
        self._motion_toggle.setStyleSheet(f"""
            QPushButton {{
                background: {c().SLATE};
                color: {c().TEXT};
                border: none;
                border-radius: {Radius.SM}px;
                font-size: {Font.XS}px;
            }}
            QPushButton:checked {{
                background: {c().CYAN};
                color: {c().BG};
            }}
        """)
        self._motion_toggle.toggled.connect(self._on_motion_toggle)
        lf_layout.addWidget(self._motion_toggle)

        lf_layout.addStretch()

        self._line_table = QTableWidget()
        self._line_table.setVisible(False)
        self._line_table.setColumnCount(6)
        self._line_table.setHorizontalHeaderLabels(
            ["라인", "방향(°)", "길이(m)", "평균수심(m)", "스와스(m)", "핑 수"])
        self._line_table.horizontalHeader().setStretchLastSection(True)
        self._line_table.verticalHeader().setVisible(False)
        self._line_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._line_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._line_table.setMaximumHeight(200)
        self._line_table.setStyleSheet(_table_qss())
        self._line_table.itemSelectionChanged.connect(self._on_line_table_selection_changed)

        # Interactive chart widget
        self._chart = MBESChartWidget()

        # Crossline intersection map (visible only when crossline QC selected)
        self._crossline_map = CrosslineMap()
        self._crossline_map.setVisible(False)

        # Track Plot (interactive multi-line survey route map)
        self._track_plot = TrackPlot(show_legend=True, show_toolbar=True, show_hint=True)
        self._track_plot.setMinimumHeight(280)
        self._track_plot.setMaximumHeight(420)
        self._track_plot.setVisible(False)
        self._track_plot.line_selected.connect(self._on_track_line_clicked)
        self._track_plot.set_hint_text("Click a line to focus it in Coverage QC.")

        # 3D Bathymetry Map
        from desktop.widgets.bathymetry_3d import Bathymetry3D
        self._bathy_3d = Bathymetry3D()
        self._bathy_3d.setMinimumHeight(350)
        self._bathy_3d.setMaximumHeight(500)
        self._bathy_3d.setVisible(False)

        # ────────────────────────────────────────────
        # LAYOUT ORDER (top to bottom):
        #   1. Header + score ring  (already added above)
        #   2. Progress bar         (already added above)
        #   3. QC cards grid        (already added above)
        #   4. Module detail table
        #   5. Track Plot (survey route map / line selection)
        #   6. Line filter + per-line table
        #   7. Chart area
        #   7b. Crossline intersection map
        #   7c. 3D bathymetry
        #   8. Insight frames (overview, spotlight, history, diff, story, settings)
        # ────────────────────────────────────────────
        layout.addWidget(self._detail_frame)
        layout.addWidget(self._track_plot)
        layout.addWidget(self._line_filter_frame)
        layout.addWidget(self._line_table)
        layout.addWidget(self._chart)
        layout.addWidget(self._crossline_map)
        layout.addWidget(self._bathy_3d)
        layout.addWidget(self._overview_frame)
        layout.addWidget(self._spotlight_frame)
        layout.addWidget(self._history_frame)
        layout.addWidget(self._diff_frame)
        layout.addWidget(self._story_frame)
        layout.addWidget(self._settings_frame)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._apply_styles()

    def showEvent(self, event):
        super().showEvent(event)
        if self._revealed_once:
            return
        self._revealed_once = True

        stagger_reveal(
            [self._run_btn, self._score_ring],
            offset_y=6,
            duration_ms=160,
            stagger_ms=36,
        )
        stagger_reveal(
            list(self._cards.values()),
            offset_y=8,
            duration_ms=170,
            stagger_ms=24,
        )
        reveal_widget(self._chart, offset_y=12, duration_ms=220)
        for section in (
            self._detail_frame,
            self._line_filter_frame,
            self._overview_frame,
            self._spotlight_frame,
            self._history_frame,
            self._diff_frame,
            self._story_frame,
            self._settings_frame,
            self._crossline_map,
            self._bathy_3d,
            self._track_plot,
        ):
            if section.isVisible():
                reveal_widget(section, offset_y=14, duration_ms=240)

    # ── Theme ──────────────────────────────────────────

    def _apply_styles(self):
        self._file_label.setStyleSheet(f"""
            font-size: {Font.LG}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        self._info_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            color: {c().MUTED};
            background: transparent;
        """)
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().CYAN};
            background: transparent;
        """)
        for frame in (self._overview_frame, self._spotlight_frame,
                      self._history_frame, self._diff_frame,
                      self._settings_frame, self._story_frame,
                      self._detail_frame):
            frame.setStyleSheet(f"""
                QFrame {{
                    background: {c().DARK};
                    border: none;
                    border-radius: {Radius.SM}px;
                }}
            """)
        # Tables -- ensure theme-aware background, no black/default residue
        self._detail_table.setStyleSheet(_table_qss())
        self._line_table.setStyleSheet(_table_qss())
        # Buttons
        self._run_btn.setStyleSheet(_btn_primary_qss())
        self._cancel_btn.setStyleSheet(_btn_danger_qss())
        # Analysis cards
        for card in self._cards.values():
            card.refresh_theme()
        # Scroll area background
        self._main_scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            {_slim_scrollbar_qss()}
        """)

    def on_theme_changed(self):
        """Re-apply theme to all inline-styled widgets."""
        self._apply_styles()
        for attr_name in dir(self):
            obj = getattr(self, attr_name, None)
            if obj and hasattr(obj, "refresh_theme") and callable(obj.refresh_theme):
                try:
                    obj.refresh_theme()
                except Exception:
                    pass

    def load_file(self, file_id: int, project_id: int):
        """Load QC results for a file."""
        self._file_id = file_id
        self._project_id = project_id
        self._project = DataService.get_project(project_id)
        self._project_results = DataService.get_project_qc_results(project_id)
        project_files = DataService.get_project_files(project_id)
        self._file_counts = {
            "pds_count": sum(1 for item in project_files if item.get("format", "").lower() == "pds"),
            "gsf_count": sum(1 for item in project_files if item.get("format", "").lower() == "gsf"),
            "hvf_count": 1 if self._project and self._project.get("hvf_dir") else 0,
        }

        f = DataService.get_file(file_id)
        if f:
            self._file_label.setText(f["filename"])
            self._info_label.setText(
                f"{f.get('format', '').upper()} | {format_number(f.get('size_mb', 0), 1, ' MB')}")

        # Load latest result
        result = DataService.get_latest_project_result(project_id) or DataService.get_latest_file_result(file_id)
        if result and result.get("result_json"):
            try:
                self._result_data = json.loads(result["result_json"])
            except (json.JSONDecodeError, TypeError):
                self._result_data = {}

            snapshot_note = describe_result_snapshot(
                result,
                anchor_file=DataService.get_file(result.get("file_id", 0)) if result.get("file_id") else None,
            )
            self._info_label.setText(
                f"{f.get('format', '').upper()} | {format_number(f.get('size_mb', 0), 1, ' MB')} | {snapshot_note}"
                if f else snapshot_note
            )

            score = result.get("score", 0)
            grade = _grade_for_score(score)
            self._score_ring.set_score(score, grade)

            self._update_cards()
            self._update_overview_panel()
            self._update_history_panel()
            self._update_diff_panel()
            self._update_settings_panel()
            focus_qc = pick_focus_module(self._result_data)
            if focus_qc:
                self._on_card_clicked(focus_qc)
        else:
            self._project = None
            self._result_data = {}
            self._project_results = []
            self._file_counts = {}
            self._score_ring.set_score(0, "")
            self._reset_cards()
            self._overview_frame.setVisible(False)
            self._spotlight_frame.setVisible(False)
            self._history_frame.setVisible(False)
            self._diff_frame.setVisible(False)
            self._settings_frame.setVisible(False)
            self._story_frame.setVisible(False)
            self._detail_frame.setVisible(False)
            self._line_filter_frame.setVisible(False)
            self._line_table.setVisible(False)
            self._crossline_map.setVisible(False)
            self._chart.clear()
            self._chart.set_title("아직 QC 결과가 없습니다")

    def _update_cards(self):
        """Update all QC cards with result data -- sequential 200ms cascade reveal."""
        items = list(self._cards.items())
        for i, (qc_id, card) in enumerate(items):
            section = self._result_data.get(qc_id, {})
            if section:
                status = section.get("overall", section.get("verdict", "N/A"))
                score = section.get("score", 0)
                key_metric = self._extract_key_metric(qc_id, section)
                # Stagger: 200ms per card, "--" -> real value with delay
                QTimer.singleShot(
                    i * 200,
                    lambda c=card, s=status.upper(), sc=score, km=key_metric: c.set_result(s, sc, key_metric=km),
                )
            else:
                QTimer.singleShot(
                    i * 200,
                    lambda c=card: c.set_result("N/A"),
                )

    @staticmethod
    def _extract_key_metric(qc_id: str, section: dict) -> str:
        """Build a human-readable key metric string from section data."""
        items = section.get("items", [])

        if qc_id == "preprocess":
            total = len(items) if items else 0
            passed = sum(1 for it in (items or []) if str(it.get("status", "")).upper() == "PASS")
            if total > 0:
                return f"게이트 {passed}/{total} 통과"

        elif qc_id == "file":
            total = len(items) if items else 0
            ok = sum(1 for it in (items or []) if str(it.get("status", "")).upper() == "PASS")
            if total > 0:
                return f"무결성 {ok}/{total}"

        elif qc_id == "vessel":
            mismatch = sum(1 for it in (items or []) if str(it.get("status", "")).upper() != "PASS")
            return f"오프셋 불일치 {mismatch}건"

        elif qc_id == "offset":
            roll = section.get("roll_bias_deg", section.get("roll_bias", None))
            if roll is not None:
                return f"Roll bias {roll:.2f}\u00B0"

        elif qc_id == "motion":
            spikes = section.get("spike_count", section.get("total_spikes", None))
            if spikes is not None:
                return f"스파이크 {spikes}개"

        elif qc_id == "svp":
            count = section.get("profile_count", section.get("n_profiles", None))
            if count is not None:
                return f"프로파일 {count}개 적용"

        elif qc_id == "coverage":
            pct = section.get("coverage_pct", section.get("overall_coverage", None))
            if pct is not None:
                return f"커버리지 {pct:.1f}%"

        elif qc_id == "crossline":
            mean_diff = section.get("mean_depth_diff_m", section.get("mean_diff", None))
            if mean_diff is not None:
                return f"깊이 차이 평균 {mean_diff:.3f}m"

        elif qc_id == "surface":
            res = section.get("grid_resolution_m", section.get("resolution", None))
            if res is not None:
                return f"그리드 해상도 {res:.1f}m"

        return ""

    def _reset_cards(self):
        self._active_qc_id = None
        for card in self._cards.values():
            card.set_result("---")

    def _update_overview_panel(self):
        overview = build_result_overview(self._result_data)
        self._overview_frame.setVisible(True)
        self._overview_headline.setText(overview["headline"])
        # Compact: 1 line with top-2 next steps
        next_text = " / ".join(overview.get("next_steps", [])[:2])
        self._overview_body.setText(next_text)

        findings = overview.get("critical_findings", [])
        actions = overview.get("action_items", [])
        if findings or actions:
            # Compact: max 2 findings, max 2 actions
            self._spotlight_findings.setText(
                self._format_spotlight_html(findings[:2]) if findings else ""
            )
            self._spotlight_actions.setText(
                self._format_action_html(actions[:2]) if actions else ""
            )
            self._spotlight_frame.setVisible(True)
        else:
            self._spotlight_frame.setVisible(False)

    def _update_history_panel(self):
        history = build_history_story(self._project_results)
        if not history.get("headline"):
            self._history_frame.setVisible(False)
            return

        # Compact: headline only + 1 key summary line
        parts = []
        if history.get("persistent_modules"):
            parts.append("반복: " + " / ".join(history["persistent_modules"][:2]))
        elif history.get("worsened_modules"):
            parts.append("악화: " + " / ".join(history["worsened_modules"][:2]))
        elif history.get("improved_modules"):
            parts.append("개선: " + " / ".join(history["improved_modules"][:2]))

        self._history_headline.setText(history["headline"])
        self._history_body.setText(" | ".join(parts) if parts else "")
        self._history_frame.setVisible(True)

    def _update_diff_panel(self):
        # diff_frame is hidden by default -- too verbose for most views
        self._diff_frame.setVisible(False)

    def _update_settings_panel(self):
        # settings_frame is hidden by default -- too verbose for most views
        self._settings_frame.setVisible(False)

    def _format_spotlight_html(self, findings: list[dict]) -> str:
        lines = []
        for item in findings[:2]:
            qc_id = item.get("qc_id") or LABEL_TO_QC_ID.get(item["module"], "")
            lines.append(
                f"- [{html.escape(item['status'])}] {html.escape(item['module'])} - "
                f"{html.escape(item['title'])} "
                f"{self._module_link(qc_id, focus_name=item.get('focus_name', ''))}"
            )
        return "<br/>".join(lines)

    def _format_action_html(self, actions: list[str]) -> str:
        lines = []
        for line in actions[:2]:
            module_label, _, detail = line.partition(":")
            qc_id = LABEL_TO_QC_ID.get(module_label.strip(), "")
            text = html.escape(line)
            lines.append(f"- {text} {self._module_link(qc_id)}")
        return "<br/>".join(lines)

    def _format_history_html(self, history: dict, lines: list[str]) -> str:
        parts = []
        if history.get("body"):
            parts.append(html.escape(history["body"]))
        if history.get("persistent_modules"):
            parts.append(
                "반복 이슈: " + " / ".join(
                    self._module_anchor(label)
                    for label in history["persistent_modules"][:4]
                )
            )
        if history.get("worsened_modules"):
            parts.append(
                "직전 대비 악화: " + " / ".join(
                    self._module_anchor(label)
                    for label in history["worsened_modules"][:4]
                )
            )
        if history.get("improved_modules"):
            parts.append(
                "직전 대비 개선: " + " / ".join(
                    self._module_anchor(label)
                    for label in history["improved_modules"][:4]
                )
            )
        if not parts and lines:
            parts = [html.escape(line) for line in lines]
        return "<br/>".join(parts)

    def _module_anchor(self, module_label: str) -> str:
        qc_id = LABEL_TO_QC_ID.get(module_label, "")
        if not qc_id:
            return html.escape(module_label)
        return (
            f"<a href=\"qc:{qc_id}\">"
            f"<span style=\"color:{c().CYAN};\">{html.escape(module_label)}</span>"
            f"</a>"
        )

    def _module_link(self, qc_id: str, label: str = "열기", focus_name: str = "") -> str:
        if not qc_id:
            return ""
        href = f"qc:{html.escape(qc_id)}"
        if focus_name:
            href += f"?item={quote(focus_name)}"
        return (
            f"<a href=\"{href}\">"
            f"<span style=\"color:{c().CYAN};\">{html.escape(label)}</span>"
            f"</a>"
        )

    def _on_rich_link_activated(self, href: str):
        if not href.startswith("qc:"):
            return
        payload = href.split(":", 1)[1].strip()
        query = ""
        if "?" in payload:
            qc_id, query = payload.split("?", 1)
        else:
            qc_id = payload
        params = parse_qs(query)
        focus_name = ""
        if params.get("item"):
            focus_name = unquote(params["item"][0])
        if qc_id in self._cards:
            self._on_card_clicked(qc_id, focus_name=focus_name)

    def _update_story_panel(self, qc_id: str):
        story = build_module_story(qc_id, self._result_data)
        self._story_frame.setVisible(True)
        self._story_title.setText(f"{story['module']} -- {story['status']}")
        # Compact: headline only (1 line)
        self._story_body.setText(story.get("headline", ""))
        # Next steps: max 2 items (1 line)
        next_items = story.get("next_steps", [])[:2]
        self._story_next.setText("다음: " + " / ".join(next_items) if next_items else "")

    def _on_card_clicked(self, qc_id: str, focus_name: str = ""):
        """Render interactive chart for the selected QC module."""
        self._active_qc_id = qc_id
        section = self._result_data.get(qc_id, {})
        self._update_story_panel(qc_id)

        # ── Update detail panel (items) ──
        self._update_detail_panel(qc_id, section)
        self._focus_detail_item(focus_name)

        # ── Line table / filter visibility ──
        show_lines = qc_id == "coverage" and bool(section.get("lines"))
        show_motion_toggle = qc_id == "motion" and bool(section.get("per_line"))
        self._line_table.setVisible(show_lines)
        self._line_filter_frame.setVisible(show_lines or show_motion_toggle)
        self._motion_toggle.setVisible(show_motion_toggle)
        self._line_filter.setVisible(show_lines)

        if show_lines:
            self._populate_line_table(section["lines"])
        else:
            self._selected_coverage_line = None
            self._coverage_line_names = []
        if not show_motion_toggle:
            self._motion_toggle.setChecked(False)

        # ── Crossline map visibility ──
        show_crossline_map = bool(
            qc_id == "crossline"
            and section.get("cell_eastings")
            and len(section.get("cell_eastings", [])) > 0
        )
        self._crossline_map.setVisible(show_crossline_map)
        if show_crossline_map:
            self._crossline_map.set_data(section)
        elif qc_id != "crossline":
            self._crossline_map.setVisible(False)

        # ── Track plot visibility (show for coverage module) ──
        show_track = qc_id == "coverage" and bool(self._build_track_routes(section))
        self._track_plot.setVisible(show_track)
        if show_track:
            self._populate_track_plot(section)
            self._sync_coverage_selection(self._selected_coverage_line)
        else:
            self._track_plot.set_routes([])
            self._track_plot.clear_selection()

        # ── 3D Bathymetry map (show for coverage module) ──
        show_bathy = qc_id == "coverage" and bool(section.get("lines"))
        self._bathy_3d.setVisible(show_bathy)
        if show_bathy:
            try:
                self._bathy_3d.set_data_from_result(self._result_data)
            except Exception as e:
                logger.exception("3D bathy error")
                self._bathy_3d.setVisible(False)

        if not section:
            self._chart.clear()
            self._chart.set_title(f"{QC_LABELS.get(qc_id, qc_id)} -- 데이터 없음")
            return

        # ── Render chart ──
        try:
            if qc_id == "file":
                self._chart.render_file_summary(section)
            elif qc_id == "vessel":
                self._chart.render_vessel(section)
            elif qc_id == "offset":
                self._chart.render_offset(section)
            elif qc_id == "motion":
                if self._motion_toggle.isChecked() and section.get("per_line"):
                    self._chart.render_motion_perline(section)
                else:
                    self._chart.render_motion(section)
            elif qc_id == "svp":
                self._chart.render_svp(section)
            elif qc_id == "coverage":
                selected = self._line_filter.currentText()
                sel = None if selected in ("전체 라인", "") else selected
                self._chart.render_coverage(section, selected_line=sel)
            elif qc_id == "crossline":
                self._chart.render_crossline(section)
            else:
                scores = self._build_score_map()
                if scores:
                    self._chart.render_radar(scores)
                else:
                    self._chart.clear()
                    self._chart.set_title("데이터 없음")
        except Exception as e:
            self._chart.clear()
            self._chart.set_title(f"렌더링 오류: {str(e)[:100]}")

    def _update_detail_panel(self, qc_id: str, section: dict):
        """Populate detail items table for the selected module."""
        items = list(section.get("items", []) or [])
        # Also include offset_validation checks
        if qc_id == "offset":
            ov = self._result_data.get("offset_validation", {})
            for chk in ov.get("config_checks", []):
                items.append({"status": chk.get("status", "N/A"),
                              "name": f"{chk.get('sensor', '')} {chk.get('field', '')}",
                              "detail": f"PDS={chk.get('pds_value', '')} OM={chk.get('om_value', '')}"})
            for chk in ov.get("data_checks", []):
                items.append(chk)
        # Crossline intersection details
        if qc_id == "crossline":
            for det in section.get("intersection_details", []):
                items.append({
                    "status": "INFO",
                    "name": f"Line {det.get('line1', '?')} × {det.get('line2', '?')}",
                    "detail": f"cells={det.get('n_cells', 0)} mean={det.get('mean_diff', 0):.3f}m std={det.get('std_diff', 0):.3f}m",
                })

        if not items:
            self._current_detail_items = []
            self._detail_table.clearSelection()
            self._detail_frame.setVisible(False)
            return

        self._current_detail_items = items
        self._detail_frame.setVisible(True)
        self._detail_title.setText(f"{QC_LABELS.get(qc_id, qc_id)} 상세")
        self._detail_table.setRowCount(len(items))
        for i, item in enumerate(items):
            status = item.get("status", "N/A")
            color = _color_for_status(status)
            icon = STATUS_ICONS.get(status, "")
            s_item = QTableWidgetItem(f"{icon} {status}" if icon else status)
            s_item.setForeground(Qt.GlobalColor.white)
            s_item.setBackground(Qt.GlobalColor.transparent)
            self._detail_table.setItem(i, 0, s_item)
            self._detail_table.setItem(i, 1, QTableWidgetItem(item.get("name", "")))
            self._detail_table.setItem(i, 2, QTableWidgetItem(item.get("detail", "")))

    def _focus_detail_item(self, focus_name: str):
        if not focus_name or not self._current_detail_items:
            self._detail_table.clearSelection()
            return

        needle = focus_name.strip().lower()
        if not needle:
            self._detail_table.clearSelection()
            return

        for row_idx, item in enumerate(self._current_detail_items):
            item_name = str(item.get("name", "")).strip().lower()
            item_detail = str(item.get("detail", "")).strip().lower()
            if (
                needle == item_name
                or needle in item_name
                or item_name in needle
                or needle in item_detail
            ):
                self._detail_table.setCurrentCell(row_idx, 1)
                self._detail_table.selectRow(row_idx)
                self._detail_table.scrollToItem(self._detail_table.item(row_idx, 1))
                return

        self._detail_table.clearSelection()

    def _populate_line_table(self, lines: list[dict]):
        """Fill line summary table from coverage data."""
        self._line_filter.blockSignals(True)
        self._line_filter.clear()
        self._line_filter.addItem("전체 라인")
        self._coverage_line_names = []
        self._line_table.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            name = ln.get("name", f"Line {i+1}")
            self._coverage_line_names.append(name)
            self._line_filter.addItem(name)
            self._line_table.setItem(i, 0, QTableWidgetItem(name))
            self._line_table.setItem(i, 1, QTableWidgetItem(format_number(ln.get("heading_deg", 0), 1)))
            self._line_table.setItem(i, 2, QTableWidgetItem(format_number(ln.get("length_m", 0), 0)))
            self._line_table.setItem(i, 3, QTableWidgetItem(format_number(ln.get("mean_depth_m", 0), 1)))
            self._line_table.setItem(i, 4, QTableWidgetItem(format_number(ln.get("mean_swath_m", 0), 0)))
            self._line_table.setItem(i, 5, QTableWidgetItem(format_number(ln.get("num_pings", 0), 0)))
        self._line_filter.blockSignals(False)

    def _build_track_routes(self, section: dict) -> list[LineRoute]:
        """Build LineRoute objects from the serialized coverage payload."""
        # BL-034 lazy-load
        from geoview_pyside6.widgets.track_plot import LineRoute
        routes: list[LineRoute] = []

        def append_routes(lines: list[dict]):
            for ln in lines:
                lats = ln.get("lats") or ln.get("track_lats") or []
                lons = ln.get("lons") or ln.get("track_lons") or []
                if len(lats) < 2 or len(lons) < 2:
                    continue
                name = str(ln.get("name") or ln.get("filename") or f"Line {len(routes) + 1}")
                routes.append(LineRoute(
                    line_id=name,
                    name=name,
                    lats=lats,
                    lons=lons,
                    score=0.0,
                    grade="--",
                    status="N/A",
                ))

        append_routes(section.get("track_lines", []))
        if not routes:
            append_routes(section.get("lines", []))

        if not routes:
            cov = section.get("coverage", section)
            ovr_lats = cov.get("track_lats", [])
            ovr_lons = cov.get("track_lons", [])
            if len(ovr_lats) >= 2 and len(ovr_lons) >= 2:
                routes.append(LineRoute(
                    line_id="overall",
                    name="Overall Track",
                    lats=ovr_lats,
                    lons=ovr_lons,
                    score=0.0,
                    grade="--",
                    status="N/A",
                ))

        return routes

    def _populate_track_plot(self, section: dict):
        """Build LineRoute objects from coverage data and feed to TrackPlot."""
        self._track_plot.set_routes(self._build_track_routes(section))

    def _on_track_line_clicked(self, line_id):
        """Highlight the selected line in coverage chart and table."""
        if isinstance(line_id, str):
            self._sync_coverage_selection(line_id if line_id != "overall" else None)

    def _on_line_filter_changed(self, idx: int):
        """Re-render coverage chart with selected line highlight."""
        selected = self._line_filter.currentText()
        sel = None if selected in ("전체 라인", "") else selected
        self._sync_coverage_selection(sel)

    def _on_line_table_selection_changed(self):
        """Keep table clicks in sync with combo, chart, and TrackPlot."""
        selected_items = self._line_table.selectedItems()
        if not selected_items:
            return
        row = self._line_table.currentRow()
        if row < 0:
            return
        item = self._line_table.item(row, 0)
        if item is None:
            return
        self._sync_coverage_selection(item.text())

    def _sync_coverage_selection(self, line_id: str | None):
        """Apply one shared coverage-line selection across combo, table, chart, and track plot."""
        if line_id not in set(self._coverage_line_names):
            line_id = None

        self._selected_coverage_line = line_id

        combo_target = 0 if line_id is None else self._line_filter.findText(line_id)
        if combo_target < 0:
            combo_target = 0
        self._line_filter.blockSignals(True)
        self._line_filter.setCurrentIndex(combo_target)
        self._line_filter.blockSignals(False)

        self._line_table.blockSignals(True)
        self._line_table.clearSelection()
        if line_id is not None:
            for row in range(self._line_table.rowCount()):
                item = self._line_table.item(row, 0)
                if item and item.text() == line_id:
                    self._line_table.setCurrentCell(row, 0)
                    self._line_table.selectRow(row)
                    break
        self._line_table.blockSignals(False)

        if line_id is None:
            self._track_plot.clear_selection()
        else:
            self._track_plot.select_line(line_id)

        section = self._result_data.get("coverage", {})
        if section:
            try:
                self._chart.render_coverage(section, selected_line=line_id)
            except Exception:
                pass

    def _on_motion_toggle(self, checked: bool):
        """Toggle between overall and per-line motion chart."""
        section = self._result_data.get("motion", {})
        if not section:
            return
        try:
            if checked and section.get("per_line"):
                self._chart.render_motion_perline(section)
            else:
                self._chart.render_motion(section)
        except Exception:
            pass

    def _build_score_map(self) -> dict[str, float]:
        """Build score map from all module verdicts for radar chart."""
        scores = {}
        for k in QC_WEIGHTS:
            s = self._result_data.get(k, {})
            if s:
                verdict = s.get("verdict", "N/A").upper()
                if verdict == "PASS": scores[QC_LABELS[k]] = 95
                elif verdict == "WARNING": scores[QC_LABELS[k]] = 60
                elif verdict == "FAIL": scores[QC_LABELS[k]] = 20
        return scores

    # ── QC Execution ──

    def _run_qc(self):
        """Start QC analysis using run_full_qc (project-level, GSF-first)."""
        if not self._project_id:
            return

        project = DataService.get_project(self._project_id)
        if not project:
            return

        self._run_btn.setEnabled(False)
        self._progress_label.setVisible(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._cancel_btn.setVisible(True)
        self._progress_label.setText("QC 파이프라인 실행 중...")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().CYAN};
            background: transparent;
        """)

        # Create a project-level snapshot without tying it to a deletable file row.
        result_id = DataService.create_qc_result(None, self._project_id)

        # Preserve old refs for GC
        if self._worker:
            self._old_workers.append((self._worker, self._thread))

        self._worker = AnalysisWorker(
            project_id=self._project_id,
            result_id=result_id,
            gsf_dir=project.get("gsf_dir", ""),
            pds_dir=project.get("pds_dir", ""),
            hvf_path=project.get("hvf_dir", ""),
            max_pings=project.get("max_pings", 0),
            cell_size=project.get("cell_size", 5.0),
            max_gsf_files=project.get("max_gsf_files", 50),
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.stage.connect(self._on_stage)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_qc_done)
        self._worker.error.connect(self._on_qc_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    def _on_stage(self, num: int, name: str):
        self._progress_label.setText(name)

    def _on_progress(self, current: int, total: int):
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)

    def _cancel_qc(self):
        if self._worker:
            self._worker.cancel()
        self._run_btn.setEnabled(True)
        self._progress_label.setText("QC 취소됨")
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)

    def _on_qc_done(self, result_id: int, data: dict):
        self._run_btn.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._project = DataService.get_project(self._project_id)
        self._result_data = data
        self._project_results = DataService.get_project_qc_results(self._project_id)

        score, grade = compute_score(data)
        self._score_ring.set_score(score, grade)
        self._update_cards()
        self._update_overview_panel()
        self._update_history_panel()
        self._update_diff_panel()
        self._update_settings_panel()
        focus_qc = pick_focus_module(self._result_data)
        if focus_qc:
            self._on_card_clicked(focus_qc)

        # Progress bar completion color (GREEN for success)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {c().DARK};
                border: none;
                border-radius: 8px;
                text-align: center;
                color: {c().TEXT};
                font-size: {Font.XS}px;
            }}
            QProgressBar::chunk {{
                background: {c().GREEN};
                border-radius: 7px;
            }}
        """)
        self._progress_label.setText("QC 완료")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().GREEN};
            background: transparent;
        """)
        # Reset progress bar after 600ms
        QTimer.singleShot(600, self._reset_progress_bar)

        # Emit toast + show SuccessOverlay
        self.toast_requested.emit(f"QC 완료 -- Score {score:.1f} ({grade})", "success")
        overlay = SuccessOverlay(
            message=f"QC Complete -- {score:.0f} ({grade})",
            color=c().GREEN,
            parent=self,
        )
        overlay.show()

    def _reset_progress_bar(self):
        """Reset progress bar to default styling and hide."""
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {c().DARK};
                border: none;
                border-radius: 8px;
                text-align: center;
                color: {c().TEXT};
                font-size: {Font.XS}px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c().CYAN}, stop:1 {c().GREEN});
                border-radius: 7px;
            }}
        """)
        self._progress_label.setVisible(False)
        self._progress_bar.setVisible(False)

    def _on_qc_error(self, result_id: int, msg: str):
        self._run_btn.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._progress_label.setText(f"QC 오류: {msg[:100]}")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().RED};
            background: transparent;
        """)
        # Progress bar error color (RED)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {c().DARK};
                border: none;
                border-radius: 8px;
                text-align: center;
                color: {c().TEXT};
                font-size: {Font.XS}px;
            }}
            QProgressBar::chunk {{
                background: {c().RED};
                border-radius: 7px;
            }}
        """)
        # Reset after 600ms
        QTimer.singleShot(600, self._reset_progress_bar)

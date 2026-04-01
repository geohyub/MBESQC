"""MBESQC AnalysisPanel -- 9-module QC results + charts."""

from __future__ import annotations

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
from PySide6.QtCore import Qt, Signal, QThread, Slot, QObject

from geoview_pyside6.constants import Dark, Font, Space, Radius, TABLE_STYLE, STATUS_ICONS, BTN_PRIMARY, BTN_SECONDARY, BTN_DANGER

from desktop.services.data_service import DataService
from desktop.services.analysis_service import AnalysisWorker, compute_score, QC_WEIGHTS
from desktop.services.insight_service import (
    build_history_story,
    build_module_story,
    build_run_diff,
    build_result_overview,
    build_settings_assistant,
    describe_result_snapshot,
    format_number,
    pick_focus_module,
)
from desktop.widgets.score_ring import ScoreRing
from desktop.widgets.mbes_chart import MBESChartWidget


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
        "PASS": Dark.GREEN,
        "WARNING": Dark.ORANGE,
        "FAIL": Dark.RED,
        "N/A": Dark.DIM,
    }.get(status, Dark.DIM)


class _AnalysisCard(QFrame):
    """Individual QC component result card."""

    clicked = Signal(str)  # qc_id

    def __init__(self, qc_id: str, parent=None):
        super().__init__(parent)
        self._qc_id = qc_id
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
            }}
            QFrame:hover {{
                background: {Dark.NAVY};
                border-color: {Dark.CYAN};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        layout.setSpacing(2)

        # Header row: name + weight
        hdr = QHBoxLayout()
        name = QLabel(QC_LABELS.get(qc_id, qc_id))
        name.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT};
            background: transparent;
        """)
        hdr.addWidget(name)
        hdr.addStretch()

        weight = QLabel(f"{QC_WEIGHTS.get(qc_id, 0)}pt")
        weight.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.DIM};
            background: transparent;
        """)
        hdr.addWidget(weight)
        layout.addLayout(hdr)

        # Hint
        hint = QLabel(QC_HINTS.get(qc_id, ""))
        hint.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Status row
        status_row = QHBoxLayout()
        self._status_label = QLabel("---")
        self._status_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.DIM};
            background: transparent;
        """)
        status_row.addWidget(self._status_label)
        status_row.addStretch()

        self._score_label = QLabel("")
        self._score_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.DIM};
            background: transparent;
        """)
        status_row.addWidget(self._score_label)
        layout.addLayout(status_row)

    def set_result(self, status: str, score: float = 0.0):
        color = _color_for_status(status)
        icon = STATUS_ICONS.get(status, "")
        self._status_label.setText(f"{icon} {status}" if icon else status)
        self._status_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {color};
            background: transparent;
        """)
        if score > 0:
            self._score_label.setText(f"{score:.1f}")
            self._score_label.setStyleSheet(f"""
                font-size: {Font.SM}px;
                font-weight: {Font.MEDIUM};
                color: {color};
                background: transparent;
            """)

    def mousePressEvent(self, event):
        self.clicked.emit(self._qc_id)


class AnalysisPanel(QWidget):
    """QC result display: score ring + 8 QC cards + chart area."""

    panel_title = "QC 분석"

    back_to_project = Signal(int)  # project_id

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
        self._build_ui()

    def get_project_id(self) -> int | None:
        return self._project_id

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: {Dark.BG};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {Dark.SLATE};
                border-radius: 4px;
                min-height: 30px;
            }}
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        info_col.addWidget(self._file_label)

        self._info_label = QLabel("")
        self._info_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            color: {Dark.MUTED};
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
        self._run_btn.setStyleSheet(BTN_PRIMARY)
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
            color: {Dark.CYAN};
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
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: 8px;
                text-align: center;
                color: {Dark.TEXT};
                font-size: {Font.XS}px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {Dark.CYAN}, stop:1 {Dark.GREEN});
                border-radius: 7px;
            }}
        """)
        progress_row.addWidget(self._progress_bar, 1)

        self._cancel_btn = QPushButton("취소")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setFixedSize(60, 24)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setStyleSheet(BTN_DANGER)
        self._cancel_btn.clicked.connect(self._cancel_qc)
        progress_row.addWidget(self._cancel_btn)

        layout.addLayout(progress_row)

        self._overview_frame = QFrame()
        self._overview_frame.setVisible(False)
        self._overview_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        overview_layout.addWidget(overview_title)

        self._overview_headline = QLabel("")
        self._overview_headline.setWordWrap(True)
        self._overview_headline.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
            background: transparent;
        """)
        overview_layout.addWidget(self._overview_headline)

        self._overview_body = QLabel("")
        self._overview_body.setWordWrap(True)
        self._overview_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._overview_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        overview_layout.addWidget(self._overview_body)

        layout.addWidget(self._overview_frame)

        self._spotlight_frame = QFrame()
        self._spotlight_frame.setVisible(False)
        self._spotlight_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        spotlight_layout.addWidget(spotlight_title)

        findings_title = QLabel("핵심 이슈")
        findings_title.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
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
            color: {Dark.MUTED};
            background: transparent;
        """)
        spotlight_layout.addWidget(self._spotlight_findings)

        actions_title = QLabel("권장 조치")
        actions_title.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
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
            color: {Dark.MUTED};
            background: transparent;
        """)
        spotlight_layout.addWidget(self._spotlight_actions)

        layout.addWidget(self._spotlight_frame)

        self._history_frame = QFrame()
        self._history_frame.setVisible(False)
        self._history_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        history_layout.addWidget(history_title)

        self._history_headline = QLabel("")
        self._history_headline.setWordWrap(True)
        self._history_headline.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
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
            color: {Dark.MUTED};
            background: transparent;
        """)
        history_layout.addWidget(self._history_body)

        layout.addWidget(self._history_frame)

        self._diff_frame = QFrame()
        self._diff_frame.setVisible(False)
        self._diff_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        diff_layout.addWidget(diff_title)

        self._diff_headline = QLabel("")
        self._diff_headline.setWordWrap(True)
        self._diff_headline.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
            background: transparent;
        """)
        diff_layout.addWidget(self._diff_headline)

        self._diff_body = QLabel("")
        self._diff_body.setWordWrap(True)
        self._diff_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
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
            color: {Dark.MUTED};
            background: transparent;
        """)
        diff_layout.addWidget(self._diff_changes)

        layout.addWidget(self._diff_frame)

        self._settings_frame = QFrame()
        self._settings_frame.setVisible(False)
        self._settings_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        settings_layout.addWidget(settings_title)

        self._settings_headline = QLabel("")
        self._settings_headline.setWordWrap(True)
        self._settings_headline.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
            background: transparent;
        """)
        settings_layout.addWidget(self._settings_headline)

        self._settings_body = QLabel("")
        self._settings_body.setWordWrap(True)
        self._settings_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._settings_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        settings_layout.addWidget(self._settings_body)

        layout.addWidget(self._settings_frame)

        # QC cards grid (4 columns x 2 rows)
        cards_label = QLabel("QC 모듈 결과")
        cards_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.MUTED};
            background: transparent;
        """)
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
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
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
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        story_layout.addWidget(self._story_title)

        self._story_body = QLabel("")
        self._story_body.setWordWrap(True)
        self._story_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._story_body.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.TEXT};
            background: transparent;
        """)
        story_layout.addWidget(self._story_body)

        self._story_next = QLabel("")
        self._story_next.setWordWrap(True)
        self._story_next.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._story_next.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        story_layout.addWidget(self._story_next)

        layout.addWidget(self._story_frame)

        # ── Module Detail Panel (items table) ──
        self._detail_frame = QFrame()
        self._detail_frame.setVisible(False)
        self._detail_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
            }}
        """)
        detail_layout = QVBoxLayout(self._detail_frame)
        detail_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)

        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet(f"""
            font-size: {Font.SM}px; font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT_BRIGHT}; background: transparent;
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
        self._detail_table.setStyleSheet(TABLE_STYLE)
        detail_layout.addWidget(self._detail_table)
        layout.addWidget(self._detail_frame)

        # ── Per-line Filter + Table (Coverage/Motion) ──
        self._line_filter_frame = QFrame()
        self._line_filter_frame.setVisible(False)
        lf_layout = QHBoxLayout(self._line_filter_frame)
        lf_layout.setContentsMargins(0, 0, 0, 0)

        lf_label = QLabel("라인 선택:")
        lf_label.setStyleSheet(f"font-size: {Font.XS}px; color: {Dark.MUTED}; background: transparent;")
        lf_layout.addWidget(lf_label)

        self._line_filter = QComboBox()
        self._line_filter.setFixedWidth(250)
        self._line_filter.setStyleSheet(f"""
            QComboBox {{
                background: {Dark.DARK};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
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
                background: {Dark.SLATE};
                color: {Dark.TEXT};
                border: none;
                border-radius: {Radius.SM}px;
                font-size: {Font.XS}px;
            }}
            QPushButton:checked {{
                background: {Dark.CYAN};
                color: {Dark.BG};
            }}
        """)
        self._motion_toggle.toggled.connect(self._on_motion_toggle)
        lf_layout.addWidget(self._motion_toggle)

        lf_layout.addStretch()
        layout.addWidget(self._line_filter_frame)

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
        self._line_table.setStyleSheet(TABLE_STYLE)
        layout.addWidget(self._line_table)

        # Interactive chart widget
        self._chart = MBESChartWidget()
        layout.addWidget(self._chart)

        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

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
            self._chart.clear()
            self._chart.set_title("아직 QC 결과가 없습니다")

    def _update_cards(self):
        """Update all QC cards with result data."""
        for qc_id, card in self._cards.items():
            section = self._result_data.get(qc_id, {})
            if section:
                status = section.get("overall", section.get("verdict", "N/A"))
                score = section.get("score", 0)
                card.set_result(status.upper(), score)
            else:
                card.set_result("N/A")

    def _reset_cards(self):
        self._active_qc_id = None
        for card in self._cards.values():
            card.set_result("---")

    def _update_overview_panel(self):
        overview = build_result_overview(self._result_data)
        self._overview_frame.setVisible(True)
        self._overview_headline.setText(overview["headline"])
        next_text = " / ".join(overview.get("next_steps", [])[:3])
        body = overview.get("body", "")
        if next_text:
            body = f"{body}\n다음 확인: {next_text}"
        self._overview_body.setText(body)

        findings = overview.get("critical_findings", [])
        actions = overview.get("action_items", [])
        if findings or actions:
            self._spotlight_findings.setText(
                self._format_spotlight_html(findings) if findings else "요약된 핵심 이슈가 없습니다."
            )
            self._spotlight_actions.setText(
                self._format_action_html(actions) if actions else "추가 권장 조치가 없습니다."
            )
            self._spotlight_frame.setVisible(True)
        else:
            self._spotlight_frame.setVisible(False)

    def _update_history_panel(self):
        history = build_history_story(self._project_results)
        if not history.get("headline"):
            self._history_frame.setVisible(False)
            return

        lines = []
        if history.get("body"):
            lines.append(history["body"])
        if history.get("persistent_modules"):
            lines.append("반복 이슈: " + " / ".join(history["persistent_modules"][:4]))
        if history.get("worsened_modules"):
            lines.append("직전 대비 악화: " + " / ".join(history["worsened_modules"][:4]))
        if history.get("improved_modules"):
            lines.append("직전 대비 개선: " + " / ".join(history["improved_modules"][:4]))

        self._history_headline.setText(history["headline"])
        self._history_body.setText(self._format_history_html(history, lines))
        self._history_frame.setVisible(True)

    def _update_diff_panel(self):
        diff = build_run_diff(self._project_results)
        self._diff_headline.setText(diff.get("headline", ""))
        self._diff_body.setText(diff.get("body", ""))

        changes = diff.get("changes", [])
        if changes:
            change_lines = []
            for item in changes[:4]:
                change_lines.append(
                    f"• {html.escape(item['module'])}: "
                    f"{html.escape(item['previous_status'])} -> {html.escape(item['latest_status'])} "
                    f"({html.escape(item['latest_reading'])}) "
                    f"{self._module_link(item['qc_id'])}"
                )
            self._diff_changes.setText("<br/>".join(change_lines))
            self._diff_frame.setVisible(True)
        else:
            self._diff_changes.setText("")
            self._diff_frame.setVisible(bool(diff.get("headline")))

    def _update_settings_panel(self):
        settings = build_settings_assistant(self._project, self._result_data, self._file_counts)
        current_state = settings.get("current_state", [])
        recommendations = settings.get("recommendations", [])
        if not settings.get("headline") and not current_state and not recommendations:
            self._settings_frame.setVisible(False)
            return

        lines = []
        if current_state:
            lines.append("현재 설정: " + " / ".join(current_state))
        if recommendations:
            lines.extend(f"- {line}" for line in recommendations[:4])
        self._settings_headline.setText(settings.get("headline", ""))
        self._settings_body.setText("\n".join(lines))
        self._settings_frame.setVisible(True)

    def _format_spotlight_html(self, findings: list[dict]) -> str:
        lines = []
        for item in findings[:4]:
            qc_id = item.get("qc_id") or LABEL_TO_QC_ID.get(item["module"], "")
            lines.append(
                f"• [{html.escape(item['status'])}] {html.escape(item['module'])} - "
                f"{html.escape(item['title'])}: {html.escape(item['evidence'])} "
                f"{self._module_link(qc_id, focus_name=item.get('focus_name', ''))}"
            )
        return "<br/>".join(lines)

    def _format_action_html(self, actions: list[str]) -> str:
        lines = []
        for line in actions[:4]:
            module_label, _, detail = line.partition(":")
            qc_id = LABEL_TO_QC_ID.get(module_label.strip(), "")
            text = html.escape(line)
            lines.append(f"• {text} {self._module_link(qc_id)}")
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
            f"<span style=\"color:{Dark.CYAN};\">{html.escape(module_label)}</span>"
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
            f"<span style=\"color:{Dark.CYAN};\">{html.escape(label)}</span>"
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
        self._story_title.setText(f"{story['module']} — {story['status']}")
        self._story_body.setText(
            f"{story['headline']}\n"
            f"왜 중요한가: {story['importance']}\n"
            f"현재 판정 근거: {story['current_reading']}\n"
            f"{story['source_text']}"
        )
        self._story_next.setText("다음 확인: " + " / ".join(story.get("next_steps", [])[:3]))

    def _on_card_clicked(self, qc_id: str, focus_name: str = ""):
        """Render interactive chart for the selected QC module."""
        self._active_qc_id = qc_id
        section = self._result_data.get(qc_id, {})
        self._update_story_panel(qc_id)

        # ── Update detail panel (items) ──
        self._update_detail_panel(qc_id, section)
        self._focus_detail_item(focus_name)

        # ── Line table / filter visibility ──
        show_lines = qc_id == "coverage" and section.get("lines")
        show_motion_toggle = qc_id == "motion" and section.get("per_line")
        self._line_table.setVisible(show_lines)
        self._line_filter_frame.setVisible(show_lines or show_motion_toggle)
        self._motion_toggle.setVisible(show_motion_toggle)
        self._line_filter.setVisible(show_lines)

        if show_lines:
            self._populate_line_table(section["lines"])
        if not show_motion_toggle:
            self._motion_toggle.setChecked(False)

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
            for c in ov.get("config_checks", []):
                items.append({"status": c.get("status", "N/A"),
                              "name": f"{c.get('sensor', '')} {c.get('field', '')}",
                              "detail": f"PDS={c.get('pds_value', '')} OM={c.get('om_value', '')}"})
            for c in ov.get("data_checks", []):
                items.append(c)
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
        self._line_table.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            name = ln.get("name", f"Line {i+1}")
            self._line_filter.addItem(name)
            self._line_table.setItem(i, 0, QTableWidgetItem(name))
            self._line_table.setItem(i, 1, QTableWidgetItem(format_number(ln.get("heading_deg", 0), 1)))
            self._line_table.setItem(i, 2, QTableWidgetItem(format_number(ln.get("length_m", 0), 0)))
            self._line_table.setItem(i, 3, QTableWidgetItem(format_number(ln.get("mean_depth_m", 0), 1)))
            self._line_table.setItem(i, 4, QTableWidgetItem(format_number(ln.get("mean_swath_m", 0), 0)))
            self._line_table.setItem(i, 5, QTableWidgetItem(format_number(ln.get("num_pings", 0), 0)))
        self._line_filter.blockSignals(False)

    def _on_line_filter_changed(self, idx: int):
        """Re-render coverage chart with selected line highlight."""
        section = self._result_data.get("coverage", {})
        if not section:
            return
        selected = self._line_filter.currentText()
        sel = None if selected in ("전체 라인", "") else selected
        try:
            self._chart.render_coverage(section, selected_line=sel)
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
            color: {Dark.CYAN};
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
        self._progress_label.setVisible(False)
        self._progress_bar.setVisible(False)
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

    def _on_qc_error(self, result_id: int, msg: str):
        self._run_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._progress_label.setText(f"QC 오류: {msg[:100]}")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.RED};
            background: transparent;
        """)

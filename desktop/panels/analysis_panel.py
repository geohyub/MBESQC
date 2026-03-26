"""MBESQC AnalysisPanel -- 8-module QC results + charts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QGridLayout,
    QSizePolicy, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QThread, Slot, QObject

from geoview_pyside6.constants import Dark, Font, Space, Radius

from desktop.services.data_service import DataService
from desktop.services.analysis_service import AnalysisWorker, compute_score
from desktop.widgets.score_ring import ScoreRing
from desktop.widgets.mbes_chart import MBESChartWidget


# ── QC Component Weights ──
QC_WEIGHTS = {
    "file":      5,
    "vessel":    10,
    "offset":    15,
    "motion":    15,
    "svp":       10,
    "coverage":  15,
    "crossline": 20,
    "surface":   10,
}

QC_LABELS = {
    "file":      "File QC",
    "vessel":    "Vessel QC",
    "offset":    "Offset QC",
    "motion":    "Motion QC",
    "svp":       "SVP QC",
    "coverage":  "Coverage QC",
    "crossline": "Cross-line QC",
    "surface":   "Surface",
}

QC_HINTS = {
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
            _AnalysisCard {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
            }}
            _AnalysisCard:hover {{
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
        self._status_label.setText(status)
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
        self._result_data = {}
        self._worker = None
        self._thread = None
        self._old_workers = []  # GC prevention
        self._chart_pixmap = None
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
        layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.XL)
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
        self._run_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.GREEN};
                color: {Dark.BG};
                border: none;
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
                font-weight: {Font.SEMIBOLD};
            }}
            QPushButton:hover {{
                background: #0ea572;
            }}
            QPushButton:disabled {{
                background: {Dark.SLATE};
                color: {Dark.DIM};
            }}
        """)
        self._run_btn.clicked.connect(self._run_qc)
        header.addWidget(self._run_btn)

        self._score_ring = ScoreRing(100)
        header.addWidget(self._score_ring)

        layout.addLayout(header)

        # Progress label
        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.CYAN};
            background: transparent;
        """)
        self._progress_label.setVisible(False)
        layout.addWidget(self._progress_label)

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

        f = DataService.get_file(file_id)
        if f:
            self._file_label.setText(f["filename"])
            self._info_label.setText(
                f"{f.get('format', '').upper()} | {f.get('size_mb', 0):.1f} MB")

        # Load latest result
        result = DataService.get_latest_file_result(file_id)
        if result and result.get("result_json"):
            try:
                self._result_data = json.loads(result["result_json"])
            except (json.JSONDecodeError, TypeError):
                self._result_data = {}

            score = result.get("score", 0)
            grade = _grade_for_score(score)
            self._score_ring.set_score(score, grade)

            self._update_cards()
        else:
            self._result_data = {}
            self._score_ring.set_score(0, "")
            self._reset_cards()

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
        for card in self._cards.values():
            card.set_result("---")

    def _on_card_clicked(self, qc_id: str):
        """Render interactive chart for the selected QC module."""
        section = self._result_data.get(qc_id, {})
        if not section:
            self._chart.clear()
            self._chart.set_title(f"{QC_LABELS.get(qc_id, qc_id)} -- 데이터 없음")
            return

        try:
            if qc_id == "file":
                self._chart.render_file_summary(section)
            elif qc_id == "vessel":
                self._chart.render_vessel(section)
            elif qc_id == "offset":
                self._chart.render_offset(section)
            elif qc_id == "motion":
                self._chart.render_motion(section)
            elif qc_id == "svp":
                self._chart.render_svp(section)
            elif qc_id == "coverage":
                self._chart.render_coverage(section)
            elif qc_id == "crossline":
                self._chart.render_crossline(section)
            else:
                # Default: radar chart
                scores = self._build_score_map()
                if scores:
                    self._chart.render_radar(scores)
                else:
                    self._chart.clear()
                    self._chart.set_title("데이터 없음")
        except Exception as e:
            self._chart.clear()
            self._chart.set_title(f"렌더링 오류: {str(e)[:100]}")

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
        self._progress_label.setText("QC 파이프라인 실행 중...")

        # Create result record (project-level, not per-file)
        result_id = DataService.create_qc_result(self._file_id, self._project_id)

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
        self._worker.finished.connect(self._on_qc_done)
        self._worker.error.connect(self._on_qc_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    def _on_stage(self, num: int, name: str):
        self._progress_label.setText(f"Stage {num}: {name}")

    def _on_qc_done(self, result_id: int, data: dict):
        self._run_btn.setEnabled(True)
        self._progress_label.setVisible(False)
        self._result_data = data

        score, grade = compute_score(data)
        self._score_ring.set_score(score, grade)
        self._update_cards()

    def _on_qc_error(self, result_id: int, msg: str):
        self._run_btn.setEnabled(True)
        self._progress_label.setText(f"QC 오류: {msg[:100]}")
        self._progress_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.RED};
            background: transparent;
        """)

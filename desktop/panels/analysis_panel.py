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
    QSizePolicy, QStackedWidget, QProgressBar,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QThread, Slot, QObject

from geoview_pyside6.constants import Dark, Font, Space, Radius, TABLE_STYLE, STATUS_ICONS, BTN_PRIMARY, BTN_SECONDARY, BTN_DANGER

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

        # ── Update detail panel (items) ──
        self._update_detail_panel(qc_id, section)

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
        items = section.get("items", [])
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
            self._detail_frame.setVisible(False)
            return

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
            self._line_table.setItem(i, 1, QTableWidgetItem(f"{ln.get('heading_deg', 0):.1f}"))
            self._line_table.setItem(i, 2, QTableWidgetItem(f"{ln.get('length_m', 0):.0f}"))
            self._line_table.setItem(i, 3, QTableWidgetItem(f"{ln.get('mean_depth_m', 0):.1f}"))
            self._line_table.setItem(i, 4, QTableWidgetItem(f"{ln.get('mean_swath_m', 0):.0f}"))
            self._line_table.setItem(i, 5, QTableWidgetItem(str(ln.get("num_pings", 0))))
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
        self._result_data = data

        score, grade = compute_score(data)
        self._score_ring.set_score(score, grade)
        self._update_cards()

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

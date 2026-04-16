"""MBESQC ProjectDetailPanel -- File list + batch QC + QC unlock grid."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QFrame,
    QCheckBox, QSizePolicy, QScrollArea, QLineEdit, QComboBox,
    QSplitter, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor

from geoview_pyside6.constants import Font, Space, Radius, STATUS_ICONS, rgba
from geoview_pyside6.effects import reveal_widget, stagger_reveal
from geoview_pyside6.theme_aware import c
from geoview_pyside6.widgets import KPICard

from desktop.services.data_service import DataService
from desktop.services.analysis_service import QC_WEIGHTS
from desktop.services.insight_service import (
    build_result_overview,
    format_number,
)
from desktop.widgets.qc_unlock_grid import QCUnlockGrid
from geoview_pyside6.widgets.track_plot import TrackPlot, LineRoute


def _resolve_file_result_display(file_id: int, result_map: dict, latest_result: dict | None) -> dict:
    """Return a truth-preserving display model for one file row.

    Never project a project-level snapshot score onto a file row.
    """
    r = result_map.get(file_id)
    if r and r.get("status") == "done":
        return {
            "score": float(r.get("score", 0) or 0),
            "status_text": f"{STATUS_ICONS.get('DONE', '')} DONE",
            "status_kind": "done",
        }
    if r and r.get("status") == "running":
        return {
            "score": None,
            "status_text": f"{STATUS_ICONS.get('RUNNING', '')} RUNNING",
            "status_kind": "running",
        }
    if latest_result and latest_result.get("status") == "done":
        return {
            "score": None,
            "status_text": "SEE PROJECT QC",
            "status_kind": "project_only",
        }
    return {
        "score": None,
        "status_text": "---",
        "status_kind": "none",
    }


def _build_dashboard_route(
    *,
    file_id: int,
    file_name: str,
    lats: list[float],
    lons: list[float],
    score: float = 0.0,
    grade: str = "--",
    status: str = "N/A",
    sampled_preview: bool = False,
) -> LineRoute:
    name = file_name
    route_status = status
    if sampled_preview:
        name = f"{file_name} (Sampled preview)"
        if status == "N/A":
            route_status = "PREVIEW"
    return LineRoute(
        line_id=file_id,
        name=name,
        lats=[float(v) for v in lats],
        lons=[float(v) for v in lons],
        score=score,
        grade=grade,
        status=route_status,
    )


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


def _btn_primary_qss(bg: str | None = None, bg_hover: str | None = None) -> str:
    """c()-based primary button QSS with optional color override."""
    _bg = bg or c().CYAN
    _hover = bg_hover or c().CYAN_H
    return f"""
        QPushButton {{
            background: {_bg};
            color: {c().TEXT_BRIGHT};
            border: none;
            border-radius: {Radius.BASE}px;
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            padding: 7px 18px;
        }}
        QPushButton:hover {{ background: {_hover}; }}
        QPushButton:disabled {{
            background: {c().SLATE};
            color: {c().MUTED};
        }}
    """


class ProjectDetailPanel(QWidget):
    """Project detail view: file list, QC unlock grid, batch QC."""

    panel_title = "프로젝트 상세"

    file_selected = Signal(int, int)      # file_id, project_id
    upload_requested = Signal(int)        # project_id
    edit_requested = Signal(int)          # project_id
    toast_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_id = None
        self._file_ids = []
        self._auto_sync_attempted = False
        self._revealed_once = False
        self._build_ui()

    def get_project_id(self) -> int | None:
        return self._project_id

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._scroll = scroll

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        layout.setSpacing(Space.LG)

        # ── 1. Header with metadata ──
        header_row = QHBoxLayout()
        self._title_label = QLabel("---")
        header_row.addWidget(self._title_label)
        header_row.addStretch()
        self._vessel_label = QLabel("")
        header_row.addWidget(self._vessel_label)
        layout.addLayout(header_row)

        # ── 2. KPIs ──
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(Space.MD)
        self._kpi_files = KPICard("", "0", "파일")
        self._kpi_pds = KPICard("", "0", "PDS")
        self._kpi_gsf = KPICard("", "0", "GSF")
        self._kpi_analyzed = KPICard("", "0", "분석")
        for k in (self._kpi_files, self._kpi_pds, self._kpi_gsf, self._kpi_analyzed):
            kpi_row.addWidget(k)
        layout.addLayout(kpi_row)

        # ── 3. File list header + actions (MOVED UP) ──
        file_header = QHBoxLayout()
        self._file_title = QLabel("파일 목록")
        file_header.addWidget(self._file_title)
        file_header.addStretch()

        self._select_all_cb = QCheckBox("전체 선택")
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        file_header.addWidget(self._select_all_cb)

        self._sync_btn = QPushButton("경로 동기화")
        self._sync_btn.setCursor(Qt.PointingHandCursor)
        self._sync_btn.clicked.connect(self._on_sync_paths)
        file_header.addWidget(self._sync_btn)

        self._batch_btn = QPushButton("프로젝트 QC")
        self._batch_btn.setCursor(Qt.PointingHandCursor)
        self._batch_btn.clicked.connect(self._on_batch_qc)
        file_header.addWidget(self._batch_btn)

        layout.addLayout(file_header)

        self._file_help_label = QLabel(
            "파일을 선택하면 분석 화면으로 이동합니다."
        )
        self._file_help_label.setWordWrap(True)
        layout.addWidget(self._file_help_label)

        # ── 4. Tools row (search + status filter) ──
        tools_row = QHBoxLayout()
        tools_row.setSpacing(Space.SM)

        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("파일명 / 포맷 / 상태 검색")
        self._filter_input.textChanged.connect(self._apply_table_filters)
        tools_row.addWidget(self._filter_input, 1)

        self._status_filter = QComboBox()
        self._status_filter.addItems(["전체 상태", "PROJECT QC", "DONE", "RUNNING", "미분석"])
        self._status_filter.currentIndexChanged.connect(self._apply_table_filters)
        tools_row.addWidget(self._status_filter)

        self._visible_count_label = QLabel("표시 0 / 0")
        tools_row.addWidget(self._visible_count_label)

        layout.addLayout(tools_row)

        # ── 5. File table (MOVED UP) ──
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["", "파일명", "포맷", "크기 (MB)", "점수", "상태"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 36)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for col_idx in range(2, 6):
            self._table.horizontalHeader().setSectionResizeMode(col_idx, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._table.setMinimumHeight(400)
        self._table.setMaximumHeight(600)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table, 1)

        # ── 6. QC summary (compact) ──
        self._context_frame = QFrame()
        context_layout = QVBoxLayout(self._context_frame)
        context_layout.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        context_layout.setSpacing(4)

        self._context_title = QLabel("QC 요약")
        self._context_title.setStyleSheet(f"""
            font-size: {Font.SM}px; font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT}; background: transparent;
        """)
        context_layout.addWidget(self._context_title)

        self._context_label = QLabel("")
        self._context_label.setWordWrap(True)
        self._context_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        context_layout.addWidget(self._context_label)

        self._context_scroll = QScrollArea()
        self._context_scroll.setWidgetResizable(True)
        self._context_scroll.setFrameShape(QFrame.NoFrame)
        self._context_scroll.setMaximumHeight(80)
        self._context_scroll.setWidget(self._context_frame)
        layout.addWidget(self._context_scroll)

        # ── 7. QC Unlock Grid (MOVED DOWN) ──
        self._unlock_header = QLabel("QC 모듈 해금 상태")
        layout.addWidget(self._unlock_header)

        self._unlock_grid = QCUnlockGrid()
        layout.addWidget(self._unlock_grid)

        # ── 8. TrackPlot + Line List ──
        self._track_header = QLabel("Track Plot")
        layout.addWidget(self._track_header)

        track_splitter = QSplitter(Qt.Orientation.Horizontal)
        track_splitter.setHandleWidth(3)

        self._dash_track = TrackPlot(show_legend=False, show_toolbar=True, show_hint=True)
        self._dash_track.line_selected.connect(self._on_dash_track_line_selected)
        self._dash_track.line_activated.connect(self._on_dash_track_line_activated)
        self._dash_track.set_hint_text("Click a line to select it. Double-click to open its analysis.")
        track_splitter.addWidget(self._dash_track)

        self._dash_line_list = QListWidget()
        self._dash_line_list.setMinimumWidth(180)
        self._dash_line_list.setMaximumWidth(280)
        self._dash_line_list.currentRowChanged.connect(self._on_dash_line_list_clicked)
        self._dash_line_list.doubleClicked.connect(self._on_dash_line_list_dblclick)
        track_splitter.addWidget(self._dash_line_list)

        track_splitter.setStretchFactor(0, 3)
        track_splitter.setStretchFactor(1, 1)
        track_splitter.setMinimumHeight(220)
        track_splitter.setMaximumHeight(400)
        layout.addWidget(track_splitter)

        self._dash_track_routes: list[LineRoute] = []
        self._dash_track_loaded = False

        layout.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

        self._apply_styles()

    def showEvent(self, event):
        super().showEvent(event)
        if self._revealed_once:
            return
        self._revealed_once = True

        reveal_widget(self._title_label, offset_y=6, duration_ms=170)
        stagger_reveal(
            [self._kpi_files, self._kpi_pds, self._kpi_gsf, self._kpi_analyzed],
            offset_y=8,
            duration_ms=180,
            stagger_ms=34,
        )
        reveal_widget(self._table, offset_y=12, duration_ms=220)
        reveal_widget(self._context_scroll, offset_y=12, duration_ms=220)
        reveal_widget(self._unlock_grid, offset_y=14, duration_ms=230)
        reveal_widget(self._dash_track, offset_y=14, duration_ms=240)

    # ── Theme ──────────────────────────────────────────

    def _apply_styles(self):
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {c().BORDER_H}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        self._title_label.setStyleSheet(f"""
            font-size: {Font.XL}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        self._vessel_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            color: {c().MUTED};
            background: transparent;
        """)
        self._context_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        self._context_title.setStyleSheet(f"""
            font-size: {Font.SM}px; font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT}; background: transparent;
        """)
        self._context_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().TEXT};
            background: transparent;
        """)
        for w in (self._file_help_label, self._visible_count_label):
            w.setStyleSheet(f"""
                font-size: {Font.XS}px;
                color: {c().MUTED};
                background: transparent;
            """)
        self._context_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
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
        """)
        self._filter_input.setStyleSheet(f"""
            QLineEdit {{
                background: {c().DARK};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                padding: 6px 10px;
                font-size: {Font.XS}px;
            }}
            QLineEdit:focus {{
                border-color: {c().CYAN};
            }}
        """)
        # Status combo
        self._status_filter.setStyleSheet(f"""
            QComboBox {{
                background: {c().DARK};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                padding: 6px 10px;
                font-size: {Font.XS}px;
                min-width: 130px;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        # Select-all checkbox
        self._select_all_cb.setStyleSheet(f"""
            QCheckBox {{
                color: {c().MUTED};
                font-size: {Font.XS}px;
                background: transparent;
            }}
        """)
        # Unlock grid header + file title
        self._unlock_header.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {c().MUTED};
            background: transparent;
        """)
        self._file_title.setStyleSheet(f"""
            font-size: {Font.MD}px;
            font-weight: {Font.MEDIUM};
            color: {c().TEXT};
            background: transparent;
        """)
        # Table
        self._table.setStyleSheet(_table_qss())
        # Buttons
        self._sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {c().MUTED};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                padding: 7px 18px;
                font-size: {Font.SM}px;
            }}
            QPushButton:hover {{
                background: {c().DARK};
                color: {c().TEXT};
                border-color: {c().BORDER_H};
            }}
        """)
        self._batch_btn.setStyleSheet(_btn_primary_qss(bg=c().CYAN, bg_hover=c().CYAN_H))

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
        if hasattr(self, '_dash_line_list'):
            self._apply_dash_line_list_style()

    def load_project(self, project_id: int):
        """Load project data and refresh all views."""
        self._project_id = project_id
        self._auto_sync_attempted = False
        self.refresh()

    def refresh(self):
        if not self._project_id:
            return

        project = DataService.get_project(self._project_id)
        if not project:
            return

        files = DataService.get_project_files(self._project_id)
        if (
            not files
            and not self._auto_sync_attempted
            and any(project.get(key) for key in ("pds_dir", "gsf_dir", "hvf_dir", "s7k_dir", "fau_dir"))
        ):
            self._auto_sync_attempted = True
            sync_summary = DataService.sync_project_files(self._project_id)
            files = DataService.get_project_files(self._project_id)
            if sync_summary.get("added") or sync_summary.get("updated"):
                self.toast_requested.emit(
                    f"경로 기반 파일 {sync_summary['indexed']:,}개를 자동 동기화했습니다.",
                    "info",
                )

        self._title_label.setText(project["name"])
        self._vessel_label.setText(project.get("vessel", ""))

        results = DataService.get_project_qc_results(self._project_id)
        latest_result = DataService.get_latest_project_result(self._project_id)

        # KPIs
        pds_count = sum(1 for f in files if f.get("format", "").lower() == "pds")
        gsf_count = sum(1 for f in files if f.get("format", "").lower() == "gsf")
        done_count = sum(1 for r in results if r["status"] == "done")

        self._kpi_files.set_value(str(len(files)))
        self._kpi_pds.set_value(str(pds_count))
        self._kpi_gsf.set_value(str(gsf_count))
        self._kpi_analyzed.set_value(str(done_count))

        # QC unlock grid
        has_pds = pds_count > 0
        has_gsf = gsf_count > 0
        has_hvf = bool(project.get("hvf_dir"))
        has_om = bool(project.get("om_config_id"))

        latest_data = {}
        if latest_result and latest_result.get("result_json"):
            try:
                latest_data = json.loads(latest_result["result_json"])
            except (TypeError, json.JSONDecodeError):
                latest_data = {}

        # Feed result data FIRST so score bars/badges render before availability animation
        if latest_data:
            self._unlock_grid.update_module_results(latest_data, QC_WEIGHTS)

        # Then animate unlock cascade (skips cards that already have results)
        self._unlock_grid.update_availability(has_pds, has_gsf, gsf_count, has_hvf, has_om)

        # Build compact 2-line QC summary
        overview = build_result_overview(latest_data)
        priority_module = overview.get("headline", "---")
        if latest_result and latest_result.get("status") == "done":
            score_val = format_number(latest_result.get("score", 0), 1)
            grade_val = latest_result.get("grade", "") or "---"
            date_val = latest_result.get("finished_at", "") or "---"
            if isinstance(date_val, str) and len(date_val) > 16:
                date_val = date_val[:16]
            summary = (
                f"현재 우선순위: {priority_module}\n"
                f"최근 QC: {score_val}점 ({grade_val}) / {date_val}"
            )
        else:
            summary = f"현재 우선순위: {priority_module}\n최근 QC: 미실행"
        self._context_label.setText(summary)

        # File table
        self._file_ids = [f["id"] for f in files]
        self._table.setRowCount(len(files))

        # Build result map by file_id
        result_map = {}
        for r in results:
            fid = r.get("file_id")
            if fid and (fid not in result_map or r["status"] == "done"):
                result_map[fid] = r

        for i, f in enumerate(files):
            # Checkbox
            cb = QCheckBox()
            cb.setStyleSheet("background: transparent;")
            self._table.setCellWidget(i, 0, cb)

            name_item = QTableWidgetItem(f["filename"])
            name_item.setToolTip(f.get("filepath", ""))
            self._table.setItem(i, 1, name_item)
            self._table.setItem(i, 2, QTableWidgetItem(f.get("format", "").upper()))
            self._table.setItem(i, 3, QTableWidgetItem(format_number(f.get("size_mb", 0), 1)))

            row_display = _resolve_file_result_display(f["id"], result_map, latest_result)
            score_value = row_display.get("score")
            self._table.setItem(i, 4, QTableWidgetItem(
                format_number(score_value, 1) if score_value is not None else "---"
            ))
            status_item = QTableWidgetItem(row_display["status_text"])
            color_map = {
                "done": QColor(c().GREEN),
                "running": QColor(c().CYAN),
                "project_only": QColor(c().CYAN),
                "none": QColor(c().MUTED),
            }
            status_item.setForeground(color_map.get(row_display["status_kind"], QColor(c().TEXT)))
            self._table.setItem(i, 5, status_item)


        self._apply_table_filters()

        # Dashboard TrackPlot: deferred load
        self._dash_track_loaded = False
        QTimer.singleShot(100, self._load_dash_track)

    def _on_double_click(self, index):
        row = index.row()
        if 0 <= row < len(self._file_ids):
            self.file_selected.emit(self._file_ids[row], self._project_id)

    # ── Dashboard TrackPlot + Line List ──────────────────────

    def _load_dash_track(self):
        """Lazily populate dashboard TrackPlot from all project files.

        Analyzed files use QC score coloring. Unanalyzed GSF files are
        shown as grey tracks by reading a small number of ping headers,
        so the survey map is always populated even before QC is run.
        """
        if not self._project_id or self._dash_track_loaded:
            return
        self._dash_track_loaded = True
        files = DataService.get_project_files(self._project_id)
        results = DataService.get_project_qc_results(self._project_id)
        result_map = {}
        for r in results:
            fid = r.get("file_id")
            if fid and r.get("status") == "done":
                result_map[fid] = r
        routes: list[LineRoute] = []
        for f in files:
            file_id = f["id"]
            r = result_map.get(file_id)
            lats, lons = [], []
            score = 0.0
            has_result = bool(r)

            sampled_preview = False
            if has_result:
                score = float(r.get("score", 0) or 0)
                try:
                    rd = json.loads(r.get("result_json", "{}"))
                except (TypeError, json.JSONDecodeError):
                    rd = {}
                nav = rd.get("navigation", {})
                lats = nav.get("lats", [])
                lons = nav.get("lons", [])
                if len(lats) < 2 or len(lons) < 2:
                    cov = rd.get("coverage", {})
                    lats = cov.get("lats", [])
                    lons = cov.get("lons", [])

            # Fallback: quick read of GSF ping coordinates
            if len(lats) < 2 or len(lons) < 2:
                coords = self._quick_mbes_coords(f)
                if coords:
                    lats, lons = coords
                    sampled_preview = True

            if len(lats) < 2 or len(lons) < 2:
                continue

            if has_result:
                grade = "A" if score >= 80 else ("B" if score >= 60 else "C")
                status = "PASS" if score >= 75 else ("WARN" if score >= 60 else "FAIL")
            else:
                grade = "--"
                status = "N/A"

            routes.append(_build_dashboard_route(
                file_id=file_id,
                file_name=f.get("filename", f"File {file_id}"),
                lats=lats,
                lons=lons,
                score=score,
                grade=grade,
                status=status,
                sampled_preview=sampled_preview,
            ))
        self._dash_track_routes = routes
        self._dash_track.set_routes(routes)
        self._populate_dash_line_list(routes)

    @staticmethod
    def _quick_mbes_coords(file_info: dict) -> tuple[list[float], list[float]] | None:
        """Quick-read a GSF file to extract sampled ping coordinates.

        Uses the GSF reader with minimal options (no arrays, no attitude,
        no SVP, max_pings=100) for fast coordinate extraction.
        Returns (lats, lons) or None on failure.
        """
        filepath = file_info.get("filepath", "")
        if not filepath or not os.path.isfile(filepath):
            return None
        fmt = file_info.get("format", "").lower()
        if fmt not in ("gsf", ""):
            # Only GSF supported for quick coordinate extraction
            ext = os.path.splitext(filepath)[1].lower()
            if ext != ".gsf":
                return None
        try:
            from pds_toolkit.gsf_reader import read_gsf
            gsf = read_gsf(filepath, max_pings=100,
                           load_arrays=False, load_attitude=False, load_svp=False)
            if not gsf.pings or len(gsf.pings) < 2:
                return None
            pairs = [(p.latitude, p.longitude) for p in gsf.pings
                     if abs(p.latitude) > 0.01 and abs(p.longitude) > 0.01]
            if len(pairs) < 2:
                return None
            lats, lons = zip(*pairs)
            return list(lats), list(lons)
        except Exception:
            return None

    def _populate_dash_line_list(self, routes: list[LineRoute]):
        """Fill the line list with route data."""
        self._dash_line_list.blockSignals(True)
        self._dash_line_list.clear()
        for route in routes:
            if route.status == "PREVIEW":
                text = f"[PREVIEW] {route.name}"
            elif route.grade == "--":
                text = f"[--] {route.name}"
            else:
                text = f"[{route.score:.0f}] {route.name}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, route.line_id)
            self._dash_line_list.addItem(item)
        self._dash_line_list.blockSignals(False)
        self._apply_dash_line_list_style()

    def _apply_dash_line_list_style(self):
        """Apply theme style to the dashboard line list."""
        self._dash_line_list.setStyleSheet(f"""
            QListWidget {{
                background: {c().DARK};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
                color: {c().TEXT};
                outline: none;
            }}
            QListWidget::item {{
                padding: 6px {Space.SM}px;
                border-bottom: 1px solid {c().BORDER};
            }}
            QListWidget::item:selected {{
                background: {rgba(c().CYAN, 0.15)};
                color: {c().TEXT_BRIGHT};
            }}
            QListWidget::item:hover:!selected {{
                background: {c().SLATE};
            }}
        """)

    def _on_dash_track_line_selected(self, line_id):
        """TrackPlot click -> highlight line list item."""
        self._dash_line_list.blockSignals(True)
        self._dash_line_list.clearSelection()
        for i in range(self._dash_line_list.count()):
            item = self._dash_line_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == line_id:
                self._dash_line_list.setCurrentRow(i)
                break
        self._dash_line_list.blockSignals(False)

    def _on_dash_track_line_activated(self, line_id):
        """TrackPlot double-click -> navigate to analysis view."""
        if line_id is not None and self._project_id:
            self.file_selected.emit(int(line_id), self._project_id)

    def _on_dash_line_list_clicked(self, row: int):
        """Line list click -> highlight TrackPlot line."""
        if row < 0:
            self._dash_track.clear_selection()
            return
        item = self._dash_line_list.item(row)
        if not item:
            return
        line_id = item.data(Qt.ItemDataRole.UserRole)
        if line_id is not None:
            self._dash_track.select_line(line_id)

    def _on_dash_line_list_dblclick(self, index):
        """Line list double-click -> navigate to analysis view."""
        item = self._dash_line_list.item(index.row())
        if not item:
            return
        line_id = item.data(Qt.ItemDataRole.UserRole)
        if line_id is not None and self._project_id:
            self.file_selected.emit(int(line_id), self._project_id)

    def _on_select_all(self, state):
        checked = state == Qt.Checked
        for i in range(self._table.rowCount()):
            cb = self._table.cellWidget(i, 0)
            if isinstance(cb, QCheckBox):
                cb.setChecked(checked)

    def _apply_table_filters(self):
        query = (self._filter_input.text() if hasattr(self, "_filter_input") else "").strip().lower()
        status_filter = self._status_filter.currentText() if hasattr(self, "_status_filter") else "전체 상태"

        visible = 0
        total = self._table.rowCount()
        for row in range(total):
            filename = (self._table.item(row, 1).text() if self._table.item(row, 1) else "").lower()
            fmt = (self._table.item(row, 2).text() if self._table.item(row, 2) else "").lower()
            score = (self._table.item(row, 4).text() if self._table.item(row, 4) else "").lower()
            status = (self._table.item(row, 5).text() if self._table.item(row, 5) else "").lower()

            matches_query = (
                not query
                or query in filename
                or query in fmt
                or query in score
                or query in status
            )
            if status_filter == "전체 상태":
                matches_status = True
            elif status_filter == "미분석":
                matches_status = status in ("---", "")
            else:
                matches_status = status_filter.lower() in status

            hidden = not (matches_query and matches_status)
            self._table.setRowHidden(row, hidden)
            if not hidden:
                visible += 1

        self._visible_count_label.setText(f"표시 {visible:,} / {total:,}")

    def _on_batch_qc(self):
        """Run project-level QC via the analysis panel."""
        selected = []
        for i in range(self._table.rowCount()):
            cb = self._table.cellWidget(i, 0)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                if i < len(self._file_ids):
                    selected.append(self._file_ids[i])

        if not selected:
            if not self._file_ids:
                self.toast_requested.emit("등록된 파일이 없습니다", "warning")
                return
            selected = [self._file_ids[0]]
            self.toast_requested.emit(
                "프로젝트 QC는 폴더 전체를 대상으로 실행됩니다. 첫 번째 파일을 진입점으로 사용합니다.",
                "info",
            )

        # Navigate to analysis panel with first file (project-level QC)
        self.file_selected.emit(selected[0], self._project_id)
        if len(selected) > 1:
            self.toast_requested.emit(
                "여러 파일을 체크해도 프로젝트 QC는 폴더 전체 스냅샷을 1회 생성합니다. 첫 선택 파일을 진입점으로 사용합니다.",
                "info",
            )
        else:
            self.toast_requested.emit("프로젝트 전체 QC 스냅샷을 실행합니다.", "info")

    def run_batch_qc(self):
        """External trigger for batch QC."""
        self._on_batch_qc()

    def _on_sync_paths(self):
        if not self._project_id:
            return
        sync_summary = DataService.sync_project_files(self._project_id)
        self.refresh()
        indexed = sync_summary.get("indexed", 0)
        if indexed:
            self.toast_requested.emit(
                f"경로 동기화 완료: {indexed:,}개 확인, {sync_summary.get('added', 0):,}개 신규 반영",
                "success",
            )
        else:
            self.toast_requested.emit(
                "동기화할 지원 파일이 없습니다. 경로를 다시 확인해 주세요.",
                "warning",
            )

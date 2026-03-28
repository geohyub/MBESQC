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
)
from PySide6.QtCore import Qt, Signal

from geoview_pyside6.constants import Dark, Font, Space, Radius, TABLE_STYLE, BTN_PRIMARY, STATUS_ICONS
from geoview_pyside6.widgets import KPICard

from desktop.services.data_service import DataService
from desktop.services.insight_service import (
    build_history_story,
    build_project_context,
    build_result_overview,
    build_run_diff,
    build_settings_assistant,
    format_number,
)
from desktop.widgets.qc_unlock_grid import QCUnlockGrid


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
        self._build_ui()

    def get_project_id(self) -> int | None:
        return self._project_id

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        layout.setSpacing(Space.LG)

        # Header with metadata
        header_row = QHBoxLayout()
        self._title_label = QLabel("---")
        self._title_label.setStyleSheet(f"""
            font-size: {Font.XL}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        header_row.addWidget(self._title_label)
        header_row.addStretch()

        self._vessel_label = QLabel("")
        self._vessel_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        header_row.addWidget(self._vessel_label)
        layout.addLayout(header_row)

        # KPIs
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(Space.MD)
        self._kpi_files = KPICard("", "0", "파일")
        self._kpi_pds = KPICard("", "0", "PDS")
        self._kpi_gsf = KPICard("", "0", "GSF")
        self._kpi_analyzed = KPICard("", "0", "분석")
        for k in (self._kpi_files, self._kpi_pds, self._kpi_gsf, self._kpi_analyzed):
            kpi_row.addWidget(k)
        layout.addLayout(kpi_row)

        self._context_frame = QFrame()
        self._context_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
            }}
        """)
        context_layout = QVBoxLayout(self._context_frame)
        context_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        context_layout.setSpacing(Space.SM)

        context_title = QLabel("QC 판단 흐름")
        context_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        context_layout.addWidget(context_title)

        self._context_label = QLabel("")
        self._context_label.setWordWrap(True)
        self._context_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._context_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.TEXT};
            background: transparent;
        """)
        context_layout.addWidget(self._context_label)

        self._readiness_label = QLabel("")
        self._readiness_label.setWordWrap(True)
        self._readiness_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._readiness_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        context_layout.addWidget(self._readiness_label)

        self._snapshot_label = QLabel("")
        self._snapshot_label.setWordWrap(True)
        self._snapshot_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._snapshot_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        context_layout.addWidget(self._snapshot_label)

        self._context_scroll = QScrollArea()
        self._context_scroll.setWidgetResizable(True)
        self._context_scroll.setFrameShape(QFrame.NoFrame)
        self._context_scroll.setMinimumHeight(150)
        self._context_scroll.setMaximumHeight(250)
        self._context_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {Dark.BG};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {Dark.SLATE};
                border-radius: 4px;
                min-height: 24px;
            }}
        """)
        self._context_scroll.setWidget(self._context_frame)
        layout.addWidget(self._context_scroll)

        # QC Unlock Grid
        unlock_header = QLabel("QC 모듈 해금 상태")
        unlock_header.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.MUTED};
            background: transparent;
        """)
        layout.addWidget(unlock_header)

        self._unlock_grid = QCUnlockGrid()
        layout.addWidget(self._unlock_grid)

        # File list header + actions
        file_header = QHBoxLayout()
        file_title = QLabel("파일 목록")
        file_title.setStyleSheet(f"""
            font-size: {Font.MD}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
            background: transparent;
        """)
        file_header.addWidget(file_title)
        file_header.addStretch()

        self._select_all_cb = QCheckBox("전체 선택")
        self._select_all_cb.setStyleSheet(f"""
            QCheckBox {{
                color: {Dark.MUTED};
                font-size: {Font.XS}px;
                background: transparent;
            }}
        """)
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        file_header.addWidget(self._select_all_cb)

        sync_btn = QPushButton("경로 동기화")
        sync_btn.setCursor(Qt.PointingHandCursor)
        sync_btn.setStyleSheet(BTN_PRIMARY.replace(Dark.GREEN, Dark.NAVY).replace(Dark.GREEN_H, Dark.SLATE))
        sync_btn.clicked.connect(self._on_sync_paths)
        file_header.addWidget(sync_btn)

        batch_btn = QPushButton("프로젝트 QC")
        batch_btn.setCursor(Qt.PointingHandCursor)
        batch_btn.setStyleSheet(BTN_PRIMARY.replace(Dark.GREEN, Dark.CYAN).replace(Dark.GREEN_H, Dark.CYAN_H))
        batch_btn.clicked.connect(self._on_batch_qc)
        file_header.addWidget(batch_btn)

        layout.addLayout(file_header)

        self._file_help_label = QLabel(
            "파일 목록은 입력 확인과 분석 진입 파일 선택용입니다. "
            "프로젝트 QC는 체크된 파일 subset이 아니라 현재 프로젝트 폴더 전체 스냅샷을 만듭니다. "
            "경로 동기화는 프로젝트 설정의 PDS/GSF/HVF 파일을 현재 목록에 안전하게 반영합니다. "
            "아래 표는 자체 스크롤과 검색/상태 필터를 지원합니다."
        )
        self._file_help_label.setWordWrap(True)
        self._file_help_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        layout.addWidget(self._file_help_label)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(Space.SM)

        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("파일명 / 포맷 / 상태 검색")
        self._filter_input.setStyleSheet(f"""
            QLineEdit {{
                background: {Dark.DARK};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                padding: 6px 10px;
                font-size: {Font.XS}px;
            }}
            QLineEdit:focus {{
                border-color: {Dark.CYAN};
            }}
        """)
        self._filter_input.textChanged.connect(self._apply_table_filters)
        tools_row.addWidget(self._filter_input, 1)

        self._status_filter = QComboBox()
        self._status_filter.addItems(["전체 상태", "PROJECT QC", "DONE", "RUNNING", "미분석"])
        self._status_filter.setStyleSheet(f"""
            QComboBox {{
                background: {Dark.DARK};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                padding: 6px 10px;
                font-size: {Font.XS}px;
                min-width: 130px;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        self._status_filter.currentIndexChanged.connect(self._apply_table_filters)
        tools_row.addWidget(self._status_filter)

        self._visible_count_label = QLabel("표시 0 / 0")
        self._visible_count_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.MUTED};
            background: transparent;
        """)
        tools_row.addWidget(self._visible_count_label)

        layout.addLayout(tools_row)

        # File table
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["", "파일명", "포맷", "크기 (MB)", "점수", "상태"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 36)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c in range(2, 6):
            self._table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
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
        self._table.setMinimumHeight(320)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._table.setStyleSheet(TABLE_STYLE)

        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table, 1)

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
        self._unlock_grid.update_availability(has_pds, has_gsf, gsf_count, has_hvf, has_om)

        latest_data = {}
        if latest_result and latest_result.get("result_json"):
            try:
                latest_data = json.loads(latest_result["result_json"])
            except (TypeError, json.JSONDecodeError):
                latest_data = {}

        context = build_project_context(
            project,
            latest_result=latest_result,
            om_preview=None,
            file_counts={
                "pds_count": pds_count,
                "gsf_count": gsf_count,
                "hvf_count": 1 if has_hvf else 0,
            },
        )
        overview = build_result_overview(latest_data)
        history_story = build_history_story(results)
        run_diff = build_run_diff(results)
        settings = build_settings_assistant(
            project,
            latest_data,
            {
                "pds_count": pds_count,
                "gsf_count": gsf_count,
                "hvf_count": 1 if has_hvf else 0,
            },
        )
        self._context_label.setText(
            f"{context['flow']}\n"
            f"현재 우선순위: {overview['headline']}"
        )
        self._readiness_label.setText(
            f"{context['readiness_text']}\n{context['offset_text']}"
        )
        snapshot_lines = [context["snapshot_text"], context["export_text"]]
        if overview.get("body"):
            snapshot_lines.append(overview["body"])
        if overview.get("critical_findings"):
            snapshot_lines.append(
                "핵심 이슈: " + " / ".join(
                    f"{item['module']} - {item['title']}"
                    for item in overview["critical_findings"][:3]
                )
            )
        if overview.get("next_steps"):
            snapshot_lines.append("다음 확인 순서: " + " / ".join(overview["next_steps"][:3]))
        if overview.get("action_items"):
            snapshot_lines.append("권장 조치: " + " / ".join(overview["action_items"][:2]))
        if history_story.get("headline"):
            snapshot_lines.append("최근 추세: " + history_story["headline"])
        if history_story.get("body"):
            snapshot_lines.append(history_story["body"])
        if run_diff.get("changes"):
            snapshot_lines.append("직전 대비 변화: " + run_diff["body"])
        if settings.get("current_state"):
            snapshot_lines.append("현재 설정: " + " / ".join(settings["current_state"][:3]))
        if settings.get("recommendations"):
            snapshot_lines.append("설정 가이드: " + " / ".join(settings["recommendations"][:2]))
        self._snapshot_label.setText("\n".join(snapshot_lines))

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

            r = result_map.get(f["id"])
            project_snapshot = False
            if not r and latest_result and latest_result.get("status") == "done":
                r = latest_result
                project_snapshot = True
            if r and r["status"] == "done":
                score = r.get("score", 0)
                self._table.setItem(i, 4, QTableWidgetItem(format_number(score, 1)))
                status_text = "PROJECT QC" if project_snapshot else f"{STATUS_ICONS.get('DONE', '')} DONE"
                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(Qt.cyan if project_snapshot else Qt.green)
                self._table.setItem(i, 5, status_item)
            elif r and r["status"] == "running":
                self._table.setItem(i, 4, QTableWidgetItem("---"))
                status_item = QTableWidgetItem(f"{STATUS_ICONS.get('RUNNING', '')} RUNNING")
                status_item.setForeground(Qt.cyan)
                self._table.setItem(i, 5, status_item)
            else:
                self._table.setItem(i, 4, QTableWidgetItem("---"))
                self._table.setItem(i, 5, QTableWidgetItem("---"))

            self._table.setRowHeight(i, 34)

        self._apply_table_filters()

    def _on_double_click(self, index):
        row = index.row()
        if 0 <= row < len(self._file_ids):
            self.file_selected.emit(self._file_ids[row], self._project_id)

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

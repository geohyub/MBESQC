"""MBESQC ProjectDetailPanel -- File list + batch QC + QC unlock grid."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QFrame,
    QCheckBox, QSizePolicy, QScrollArea, QSplitter,
)
from PySide6.QtCore import Qt, Signal

from geoview_pyside6.constants import Dark, Font, Space, Radius, TABLE_STYLE, BTN_PRIMARY, STATUS_ICONS
from geoview_pyside6.widgets import KPICard

from desktop.services.data_service import DataService
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

        batch_btn = QPushButton("Batch QC")
        batch_btn.setCursor(Qt.PointingHandCursor)
        batch_btn.setStyleSheet(BTN_PRIMARY.replace(Dark.GREEN, Dark.CYAN).replace(Dark.GREEN_H, Dark.CYAN_H))
        batch_btn.clicked.connect(self._on_batch_qc)
        file_header.addWidget(batch_btn)

        layout.addLayout(file_header)

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

        self._table.setStyleSheet(TABLE_STYLE)

        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

    def load_project(self, project_id: int):
        """Load project data and refresh all views."""
        self._project_id = project_id
        self.refresh()

    def refresh(self):
        if not self._project_id:
            return

        project = DataService.get_project(self._project_id)
        if not project:
            return

        self._title_label.setText(project["name"])
        self._vessel_label.setText(project.get("vessel", ""))

        files = DataService.get_project_files(self._project_id)
        results = DataService.get_project_qc_results(self._project_id)

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

            self._table.setItem(i, 1, QTableWidgetItem(f["filename"]))
            self._table.setItem(i, 2, QTableWidgetItem(f.get("format", "").upper()))
            self._table.setItem(i, 3, QTableWidgetItem(f"{f.get('size_mb', 0):.1f}"))

            r = result_map.get(f["id"])
            if r and r["status"] == "done":
                score = r.get("score", 0)
                self._table.setItem(i, 4, QTableWidgetItem(f"{score:.1f}"))
                status_item = QTableWidgetItem(f"{STATUS_ICONS.get('DONE', '')} DONE")
                status_item.setForeground(Qt.green)
                self._table.setItem(i, 5, status_item)
            elif r and r["status"] == "running":
                self._table.setItem(i, 4, QTableWidgetItem("---"))
                status_item = QTableWidgetItem(f"{STATUS_ICONS.get('RUNNING', '')} RUNNING")
                status_item.setForeground(Qt.cyan)
                self._table.setItem(i, 5, status_item)
            else:
                self._table.setItem(i, 4, QTableWidgetItem("---"))
                self._table.setItem(i, 5, QTableWidgetItem("---"))

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

    def _on_batch_qc(self):
        """Run project-level QC via the analysis panel."""
        selected = []
        for i in range(self._table.rowCount()):
            cb = self._table.cellWidget(i, 0)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                if i < len(self._file_ids):
                    selected.append(self._file_ids[i])

        if not selected:
            self.toast_requested.emit("분석할 파일을 선택하세요", "warning")
            return

        # Navigate to analysis panel with first file (project-level QC)
        self.file_selected.emit(selected[0], self._project_id)
        if len(selected) > 1:
            self.toast_requested.emit(
                f"프로젝트 QC 실행 (GSF 디렉토리 전체 분석)", "info")

    def run_batch_qc(self):
        """External trigger for batch QC."""
        self._on_batch_qc()

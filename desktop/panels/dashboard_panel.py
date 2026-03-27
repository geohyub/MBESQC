"""MBESQC DashboardPanel -- Project list + KPI overview."""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from geoview_pyside6.constants import Dark, Font, Space, Radius, TABLE_STYLE, BTN_PRIMARY
from geoview_pyside6.widgets import KPICard

from desktop.services.data_service import DataService


class DashboardPanel(QWidget):
    """Dashboard: project list + KPI summary cards."""

    panel_title = "대시보드"

    project_selected = Signal(int)  # project_id
    new_project = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        layout.setSpacing(Space.LG)

        # KPI row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(Space.MD)

        self._kpi_projects = KPICard("", "0", "프로젝트")
        self._kpi_files = KPICard("", "0", "파일")
        self._kpi_analyzed = KPICard("", "0", "분석 완료")
        self._kpi_score = KPICard("", "---", "평균 점수")

        for kpi in (self._kpi_projects, self._kpi_files,
                    self._kpi_analyzed, self._kpi_score):
            kpi_row.addWidget(kpi)

        layout.addLayout(kpi_row)

        # Section header
        header_row = QHBoxLayout()
        title = QLabel("프로젝트")
        title.setStyleSheet(f"""
            font-size: {Font.LG}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        header_row.addWidget(title)
        header_row.addStretch()

        new_btn = QPushButton("+ 새 프로젝트")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setStyleSheet(BTN_PRIMARY)
        new_btn.clicked.connect(self.new_project.emit)
        header_row.addWidget(new_btn)
        layout.addLayout(header_row)

        # Project table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["이름", "선박", "파일 수", "상태", "생성일"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)

        self._table.setStyleSheet(TABLE_STYLE)

        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

    def _on_double_click(self, index):
        row = index.row()
        if row < 0 or row >= len(self._project_ids):
            return
        self.project_selected.emit(self._project_ids[row])

    def refresh(self):
        """Reload data from DB."""
        # KPIs
        kpis = DataService.get_kpis()
        self._kpi_projects.set_value(str(kpis["total_projects"]))
        self._kpi_files.set_value(str(kpis["total_files"]))
        self._kpi_analyzed.set_value(str(kpis["analyzed"]))
        self._kpi_score.set_value(
            f"{kpis['avg_score']:.1f}" if kpis["avg_score"] > 0 else "---")

        # Project list
        projects = DataService.list_projects()
        self._project_ids = [p["id"] for p in projects]

        self._table.setRowCount(len(projects))
        for i, p in enumerate(projects):
            files = DataService.get_project_files(p["id"])
            results = DataService.get_project_qc_results(p["id"])
            done = sum(1 for r in results if r["status"] == "done")

            self._table.setItem(i, 0, QTableWidgetItem(p["name"]))
            self._table.setItem(i, 1, QTableWidgetItem(p.get("vessel", "")))
            self._table.setItem(i, 2, QTableWidgetItem(str(len(files))))

            status = f"{done}/{len(files)}" if files else "---"
            self._table.setItem(i, 3, QTableWidgetItem(status))

            date_str = p["created_at"][:10] if p.get("created_at") else ""
            self._table.setItem(i, 4, QTableWidgetItem(date_str))

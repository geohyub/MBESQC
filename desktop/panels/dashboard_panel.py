"""MBESQC DashboardPanel -- Project list + KPI overview."""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QFrame, QSizePolicy,
    QScrollArea,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c
from geoview_pyside6.widgets import KPICard
from geoview_pyside6.effects import reveal_widget, stagger_reveal

from desktop.services.data_service import DataService
from desktop.widgets.sparkline_widget import HeatmapCell, SparklineWidget
from desktop.widgets.project_tile import ProjectTile


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


def _slim_scrollbar_qss() -> str:
    """6px slim scrollbar QSS."""
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
    """


def _relative_time(iso_str: str) -> str:
    """Convert ISO datetime to relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "방금"
        if secs < 3600:
            return f"{secs // 60}분 전"
        if secs < 86400:
            return f"{secs // 3600}시간 전"
        return f"{secs // 86400}일 전"
    except Exception:
        return iso_str[:10] if iso_str else ""


class _ActivityItem(QFrame):
    """Single activity feed item: color dot + relative time + project name."""

    def __init__(self, color: str, message: str, time_str: str, parent=None):
        super().__init__(parent)
        self.setObjectName("activity_item")
        self.setFixedHeight(32)
        self.setStyleSheet(f"""
            #activity_item {{
                background: transparent;
                border-radius: {Radius.SM}px;
                padding: 2px 4px;
            }}
            #activity_item:hover {{
                background: {c().SLATE};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.SM, 0, Space.SM, 0)
        layout.setSpacing(Space.SM)

        # Color dot
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"""
            background: {color};
            border-radius: 4px;
            border: none;
        """)
        layout.addWidget(dot)

        # Message
        msg = QLabel(message)
        msg.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().TEXT};
            background: transparent;
        """)
        layout.addWidget(msg, 1)

        # Relative time
        ts = QLabel(time_str)
        ts.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        layout.addWidget(ts)


class DashboardPanel(QWidget):
    """Dashboard: project list + KPI summary cards."""

    panel_title = "대시보드"

    project_selected = Signal(int)  # project_id
    new_project = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._revealed_once = False
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

        # KPI 스파크라인 (각 카드 하단)
        self._sparklines: dict[str, SparklineWidget] = {}
        for key, kpi, color in [
            ("projects", self._kpi_projects, c().BLUE),
            ("files", self._kpi_files, c().CYAN),
            ("analyzed", self._kpi_analyzed, c().GREEN),
            ("score", self._kpi_score, c().CYAN),
        ]:
            spark = SparklineWidget(color=color)
            self._sparklines[key] = spark
            # KPICard 내부 레이아웃에 스파크라인 추가
            if kpi.layout():
                kpi.layout().addWidget(spark)
            kpi_row.addWidget(kpi)

        layout.addLayout(kpi_row)

        # Section header
        header_row = QHBoxLayout()
        self._section_title = QLabel("프로젝트")
        header_row.addWidget(self._section_title)
        header_row.addStretch()

        # View toggle: table / tile
        self._view_table_btn = QPushButton("☰")
        self._view_table_btn.setFixedSize(28, 28)
        self._view_table_btn.setToolTip("테이블 뷰")
        self._view_table_btn.setCursor(Qt.PointingHandCursor)
        self._view_table_btn.clicked.connect(lambda: self._set_view("table"))
        header_row.addWidget(self._view_table_btn)

        self._view_tile_btn = QPushButton("▦")
        self._view_tile_btn.setFixedSize(28, 28)
        self._view_tile_btn.setToolTip("히트맵 뷰")
        self._view_tile_btn.setCursor(Qt.PointingHandCursor)
        self._view_tile_btn.clicked.connect(lambda: self._set_view("tile"))
        header_row.addWidget(self._view_tile_btn)

        self._new_btn = QPushButton("+ 새 프로젝트")
        self._new_btn.setCursor(Qt.PointingHandCursor)
        self._new_btn.setStyleSheet(_btn_primary_qss())
        self._new_btn.clicked.connect(self.new_project.emit)
        header_row.addWidget(self._new_btn)
        layout.addLayout(header_row)

        # Tile grid (히트맵 뷰)
        self._tile_scroll = QScrollArea()
        self._tile_scroll.setWidgetResizable(True)
        self._tile_scroll.setFrameShape(QFrame.NoFrame)
        self._tile_scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            {_slim_scrollbar_qss()}
        """)
        self._tile_container = QWidget()
        self._tile_layout = QVBoxLayout(self._tile_container)
        self._tile_layout.setContentsMargins(0, 0, 0, 0)
        self._tile_layout.setSpacing(Space.SM)
        self._tile_scroll.setWidget(self._tile_container)
        self._tile_scroll.setVisible(False)
        layout.addWidget(self._tile_scroll)

        self._view_mode = "table"  # "table" | "tile"

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

        # Table with hover effect for rows
        self._table.setStyleSheet(_table_qss())

        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        # Activity feed section
        self._feed_header = QLabel("최근 활동")
        layout.addWidget(self._feed_header)

        self._feed_scroll = QScrollArea()
        self._feed_scroll.setWidgetResizable(True)
        self._feed_scroll.setFrameShape(QFrame.NoFrame)
        self._feed_scroll.setMaximumHeight(160)
        self._feed_scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            {_slim_scrollbar_qss()}
        """)

        self._feed_container = QWidget()
        self._feed_layout = QVBoxLayout(self._feed_container)
        self._feed_layout.setContentsMargins(0, 0, 0, 0)
        self._feed_layout.setSpacing(2)
        self._feed_layout.addStretch()
        self._feed_scroll.setWidget(self._feed_container)
        layout.addWidget(self._feed_scroll)

        self._apply_styles()

    def showEvent(self, event):
        super().showEvent(event)
        if self._revealed_once:
            return
        self._revealed_once = True
        stagger_reveal(
            [
                self._kpi_projects,
                self._kpi_files,
                self._kpi_analyzed,
                self._kpi_score,
            ],
            offset_y=8,
            duration_ms=180,
            stagger_ms=40,
        )
        reveal_widget(self._new_btn, offset_y=8, duration_ms=160)
        reveal_widget(self._table, offset_y=12, duration_ms=220)
        reveal_widget(self._feed_scroll, offset_y=14, duration_ms=240)

    # ── Theme ──────────────────────────────────────────

    def _apply_styles(self):
        self._section_title.setStyleSheet(f"""
            font-size: {Font.LG}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        self._table.setStyleSheet(_table_qss())
        self._new_btn.setStyleSheet(_btn_primary_qss())
        self._feed_header.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {c().MUTED};
            background: transparent;
        """)
        self._feed_scroll.setStyleSheet(f"""
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
        # Rebuild activity feed with new theme colors
        projects = DataService.list_projects()
        self._update_activity_feed(projects)

    def _set_view(self, mode: str) -> None:
        """Toggle between table and tile view."""
        self._view_mode = mode
        self._table.setVisible(mode == "table")
        self._tile_scroll.setVisible(mode == "tile")
        # Toggle button styles
        active_qss = f"background: {c().CYAN}; color: {c().BG}; border: none; border-radius: 4px; font-size: 14px;"
        inactive_qss = f"background: {c().SLATE}; color: {c().MUTED}; border: none; border-radius: 4px; font-size: 14px;"
        self._view_table_btn.setStyleSheet(active_qss if mode == "table" else inactive_qss)
        self._view_tile_btn.setStyleSheet(active_qss if mode == "tile" else inactive_qss)

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

        # Update tile grid
        self._update_tile_grid(projects)

        # Update KPI sparklines (최근 7개 프로젝트 점수 트렌드)
        recent_scores = []
        for p in projects[:7]:
            results = DataService.get_project_qc_results(p["id"])
            done_results = [r for r in results if r.get("status") == "done"]
            if done_results:
                avg = sum(r.get("score", 0) for r in done_results) / len(done_results)
                recent_scores.append(avg)
        if len(recent_scores) >= 2:
            self._sparklines["score"].set_values(recent_scores)

        # Update activity feed
        self._update_activity_feed(projects)

        # Apply view mode
        self._set_view(self._view_mode)

    def _update_tile_grid(self, projects: list[dict]) -> None:
        """Populate tile grid from project data."""
        # Clear existing tiles
        while self._tile_layout.count():
            item = self._tile_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for p in projects:
            pid = p["id"]
            files = DataService.get_project_files(pid)
            results = DataService.get_project_qc_results(pid)
            done_results = [r for r in results if r.get("status") == "done"]

            score = 0.0
            grade = "---"
            module_scores: dict[str, str] = {}

            if done_results:
                latest = done_results[-1]
                score = latest.get("score", 0)
                grade_map = {90: "A", 80: "B", 70: "C", 60: "D"}
                grade = "F"
                for threshold, g in sorted(grade_map.items(), reverse=True):
                    if score >= threshold:
                        grade = g
                        break
                # Extract per-module status from latest result
                modules = latest.get("modules", {})
                for mod_key in HeatmapCell.MODULE_ORDER:
                    mod_data = modules.get(mod_key, {})
                    mod_status = mod_data.get("status", "pending")
                    module_scores[mod_key] = mod_status

            tile = ProjectTile(
                project_id=pid,
                name=p.get("name", "Unknown"),
                vessel=p.get("vessel", ""),
                score=score,
                grade=grade,
                file_count=len(files),
                module_scores=module_scores,
            )
            tile.clicked.connect(self.project_selected.emit)
            self._tile_layout.addWidget(tile)

        self._tile_layout.addStretch()

    def _update_activity_feed(self, projects: list[dict]):
        """Populate activity feed with recent QC results and project events."""
        # Clear existing items (keep stretch)
        while self._feed_layout.count() > 1:
            item = self._feed_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        activities: list[tuple[str, str, str, str]] = []  # (time_iso, color, message, project_name)

        for p in projects[:10]:
            pid = p["id"]
            name = p.get("name", "Unknown")
            results = DataService.get_project_qc_results(pid)
            for r in results[-3:]:
                ts = r.get("created_at", "")
                status = r.get("status", "")
                score = r.get("score", 0)
                if status == "done":
                    color = c().GREEN
                    msg = f"{name} -- QC 완료 (Score {score:.0f})"
                elif status == "running":
                    color = c().CYAN
                    msg = f"{name} -- QC 실행 중"
                elif status == "error":
                    color = c().RED
                    msg = f"{name} -- QC 오류"
                else:
                    color = c().MUTED
                    msg = f"{name} -- {status}"
                activities.append((ts, color, msg, name))

            if p.get("created_at"):
                activities.append((
                    p["created_at"],
                    c().BLUE,
                    f"{name} -- 프로젝트 생성",
                    name,
                ))

        # Sort by time descending and take last 8
        activities.sort(key=lambda x: x[0], reverse=True)
        for ts, color, msg, _ in activities[:8]:
            rel = _relative_time(ts)
            item_widget = _ActivityItem(color, msg, rel)
            # Insert before stretch
            self._feed_layout.insertWidget(self._feed_layout.count() - 1, item_widget)

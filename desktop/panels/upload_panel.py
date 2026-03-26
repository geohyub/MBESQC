"""MBESQC UploadPanel -- File upload with drag-drop and directory scan."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFileDialog,
    QFrame, QProgressBar,
)
from PySide6.QtCore import Qt, Signal, QObject, QThread, Slot

from geoview_pyside6.constants import Dark, Font, Space, Radius

from desktop.services.data_service import DataService
from desktop.widgets.drop_zone import DropZone


class _ScanWorker(QObject):
    """Background worker to scan directory for MBES files."""

    file_found = Signal(str, str, float)  # filename, filepath, size_mb
    finished = Signal(int)                 # count
    error = Signal(str)

    def __init__(self, directory: str, project_id: int):
        super().__init__()
        self._directory = directory
        self._project_id = project_id

    @Slot()
    def run(self):
        try:
            count = 0
            exts = {".pds", ".gsf", ".hvf", ".s7k", ".xtf", ".fau", ".gpt"}
            d = Path(self._directory)
            if not d.is_dir():
                self.error.emit(f"디렉토리를 찾을 수 없습니다: {self._directory}")
                return

            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in exts:
                    size_mb = f.stat().st_size / (1024 * 1024)
                    fmt = f.suffix.lower().lstrip(".")
                    DataService.add_file(
                        self._project_id, f.name, str(f), size_mb, fmt)
                    self.file_found.emit(f.name, str(f), size_mb)
                    count += 1

            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))


class UploadPanel(QWidget):
    """File upload panel with drag-drop and directory scanning."""

    panel_title = "파일 업로드"

    upload_complete = Signal(int)  # project_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_id = None
        self._scan_thread = None
        self._scan_worker = None
        self._old_workers = []  # GC prevention
        self._build_ui()

    def set_project(self, project_id: int):
        self._project_id = project_id
        p = DataService.get_project(project_id)
        if p:
            self._project_label.setText(f"프로젝트: {p['name']}")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        layout.setSpacing(Space.LG)

        # Project info
        self._project_label = QLabel("프로젝트: ---")
        self._project_label.setStyleSheet(f"""
            font-size: {Font.LG}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        layout.addWidget(self._project_label)

        # Drop zone
        self._drop_zone = DropZone()
        self._drop_zone.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self._drop_zone)

        # Directory scan button
        btn_row = QHBoxLayout()
        scan_btn = QPushButton("디렉토리 스캔")
        scan_btn.setCursor(Qt.PointingHandCursor)
        scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.NAVY};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER_H};
                border-radius: {Radius.SM}px;
                padding: 6px 16px;
                font-size: {Font.SM}px;
                font-weight: {Font.MEDIUM};
            }}
            QPushButton:hover {{
                background: {Dark.SLATE};
            }}
        """)
        scan_btn.clicked.connect(self._on_scan_directory)
        btn_row.addWidget(scan_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setMaximum(0)  # indeterminate
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: 4px;
                height: 6px;
            }}
            QProgressBar::chunk {{
                background: {Dark.CYAN};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(self._progress)

        # File queue table
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["파일명", "포맷", "크기 (MB)"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)

        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {Dark.DARK};
                alternate-background-color: {Dark.BG_ALT};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
            }}
            QTableWidget::item {{
                padding: 4px 8px;
                border: none;
            }}
            QHeaderView::section {{
                background: {Dark.NAVY};
                color: {Dark.MUTED};
                font-size: {Font.XS}px;
                font-weight: {Font.MEDIUM};
                border: none;
                border-bottom: 1px solid {Dark.BORDER};
                padding: 4px 8px;
            }}
        """)
        layout.addWidget(self._table)

    def _on_files_dropped(self, paths: list[str]):
        if not self._project_id:
            return

        for path in paths:
            if os.path.isdir(path):
                self._start_scan(path)
                return

            f = Path(path)
            size_mb = f.stat().st_size / (1024 * 1024)
            fmt = f.suffix.lower().lstrip(".")
            DataService.add_file(self._project_id, f.name, str(f), size_mb, fmt)
            self._add_table_row(f.name, fmt, size_mb)

        self.upload_complete.emit(self._project_id)

    def _on_scan_directory(self):
        if not self._project_id:
            return
        d = QFileDialog.getExistingDirectory(self, "MBES 데이터 디렉토리 선택")
        if d:
            self._start_scan(d)

    def _start_scan(self, directory: str):
        self._progress.setVisible(True)
        self._table.setRowCount(0)

        # Preserve old worker/thread refs to prevent GC
        if self._scan_worker:
            self._old_workers.append((self._scan_worker, self._scan_thread))

        self._scan_worker = _ScanWorker(directory, self._project_id)
        self._scan_thread = QThread()
        self._scan_worker.moveToThread(self._scan_thread)

        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.file_found.connect(self._on_file_found)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.finished.connect(self._scan_thread.quit)

        self._scan_thread.start()

    def _on_file_found(self, filename: str, filepath: str, size_mb: float):
        fmt = Path(filename).suffix.lower().lstrip(".")
        self._add_table_row(filename, fmt, size_mb)

    def _on_scan_done(self, count: int):
        self._progress.setVisible(False)
        self.upload_complete.emit(self._project_id)

    def _on_scan_error(self, msg: str):
        self._progress.setVisible(False)

    def _add_table_row(self, name: str, fmt: str, size_mb: float):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(name))
        self._table.setItem(row, 1, QTableWidgetItem(fmt.upper()))
        self._table.setItem(row, 2, QTableWidgetItem(f"{size_mb:.1f}"))

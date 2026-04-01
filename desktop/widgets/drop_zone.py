"""MBESQC DropZone -- Drag-and-drop file upload area with glow effect."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QSizePolicy,
)

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Dark, Font, Space, Radius


SUPPORTED_EXTENSIONS = {
    ".pds", ".gsf", ".hvf", ".s7k", ".xtf", ".fau", ".gpt", ".csv",
}


class DropZone(QFrame):
    """Drag-and-drop file upload area with glow hover effect."""

    files_dropped = Signal(list)  # list[str] paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._drag_over = False
        self._update_style()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(Space.MD)

        self._icon = QLabel("\u2B06")
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet(f"""
            font-size: 36px;
            color: {Dark.DIM};
            background: transparent;
            border: none;
        """)
        layout.addWidget(self._icon)

        instr = QLabel("PDS/GSF/HVF 파일을 여기에 드래그하거나")
        instr.setAlignment(Qt.AlignCenter)
        instr.setStyleSheet(f"""
            color: {Dark.MUTED};
            font-size: {Font.SM}px;
            background: transparent;
            border: none;
        """)
        layout.addWidget(instr)

        browse_btn = QPushButton("파일 선택")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setFixedSize(120, 36)
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.NAVY};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER_H};
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
                font-weight: {Font.MEDIUM};
            }}
            QPushButton:hover {{
                background: {Dark.SLATE};
                border-color: {Dark.CYAN};
            }}
        """)
        browse_btn.clicked.connect(self._browse_files)
        layout.addWidget(browse_btn, alignment=Qt.AlignCenter)

        exts = " ".join(sorted(SUPPORTED_EXTENSIONS))
        ext_label = QLabel(exts)
        ext_label.setAlignment(Qt.AlignCenter)
        ext_label.setStyleSheet(f"""
            color: {Dark.DIM};
            font-size: {Font.XS - 1}px;
            background: transparent;
            border: none;
        """)
        layout.addWidget(ext_label)

    def _update_style(self):
        if self._drag_over:
            self.setStyleSheet(f"""
                DropZone {{
                    background: qradialgradient(
                        cx:0.5, cy:0.5, radius:0.8,
                        fx:0.5, fy:0.5,
                        stop:0 rgba(6,182,212,0.08),
                        stop:1 {Dark.DARK}
                    );
                    border: 2px dashed {Dark.CYAN};
                    border-radius: {Radius.LG}px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                DropZone {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 {Dark.DARK},
                        stop:1 {Dark.BG_ALT}
                    );
                    border: 2px dashed {Dark.BORDER_H};
                    border-radius: {Radius.LG}px;
                }}
                DropZone:hover {{
                    border-color: {Dark.MUTED};
                }}
            """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_over = True
            self._update_style()

    def dragLeaveEvent(self, event):
        self._drag_over = False
        self._update_style()

    def dropEvent(self, event):
        self._drag_over = False
        self._update_style()

        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()
                if ext in SUPPORTED_EXTENSIONS or ext == "":
                    paths.append(path)
            elif os.path.isdir(path):
                paths.append(path)

        if paths:
            self.files_dropped.emit(paths)

    def _browse_files(self):
        ext_filter = (
            "MBES Files (*.pds *.gsf *.hvf *.s7k *.xtf *.fau *.gpt *.csv);;"
            "All Files (*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, "파일 선택", "", ext_filter)
        if paths:
            self.files_dropped.emit(paths)

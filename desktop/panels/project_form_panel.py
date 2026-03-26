"""MBESQC ProjectFormPanel -- Create / edit project form."""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QFrame,
    QDoubleSpinBox, QSpinBox, QComboBox, QFormLayout,
    QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from geoview_pyside6.constants import Dark, Font, Space, Radius

from desktop.services.data_service import DataService


_INPUT_STYLE = f"""
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {Dark.DARK};
        color: {Dark.TEXT};
        border: 1px solid {Dark.BORDER};
        border-radius: {Radius.SM}px;
        padding: 6px 10px;
        font-size: {Font.SM}px;
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {Dark.CYAN};
    }}
"""


class ProjectFormPanel(QWidget):
    """Project creation / edit form."""

    panel_title = "프로젝트 설정"

    saved = Signal(int)       # project_id
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_id = None  # None = new, int = edit
        self._build_ui()

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

        # Title
        self._title_label = QLabel("새 프로젝트")
        self._title_label.setStyleSheet(f"""
            font-size: {Font.XL}px;
            font-weight: {Font.SEMIBOLD};
            color: {Dark.TEXT_BRIGHT};
            background: transparent;
        """)
        layout.addWidget(self._title_label)

        # Form
        form = QFormLayout()
        form.setSpacing(Space.MD)
        form.setLabelAlignment(Qt.AlignRight)

        label_style = f"""
            font-size: {Font.SM}px;
            color: {Dark.MUTED};
            background: transparent;
        """

        # Project name
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("프로젝트 이름 (예: EDF Phase 1)")
        self._name_input.setStyleSheet(_INPUT_STYLE)
        name_label = QLabel("프로젝트 이름")
        name_label.setStyleSheet(label_style)
        form.addRow(name_label, self._name_input)

        # Vessel
        self._vessel_input = QLineEdit()
        self._vessel_input.setPlaceholderText("선박명 (예: Fugro Equator)")
        self._vessel_input.setStyleSheet(_INPUT_STYLE)
        vessel_label = QLabel("선박명")
        vessel_label.setStyleSheet(label_style)
        form.addRow(vessel_label, self._vessel_input)

        # PDS directory
        pds_row = QHBoxLayout()
        self._pds_input = QLineEdit()
        self._pds_input.setPlaceholderText("PDS 파일 디렉토리")
        self._pds_input.setStyleSheet(_INPUT_STYLE)
        pds_browse = QPushButton("...")
        pds_browse.setFixedSize(36, 30)
        pds_browse.setStyleSheet(self._browse_btn_style())
        pds_browse.clicked.connect(lambda: self._browse_dir(self._pds_input))
        pds_row.addWidget(self._pds_input)
        pds_row.addWidget(pds_browse)
        pds_label = QLabel("PDS 디렉토리")
        pds_label.setStyleSheet(label_style)
        form.addRow(pds_label, pds_row)

        # GSF directory
        gsf_row = QHBoxLayout()
        self._gsf_input = QLineEdit()
        self._gsf_input.setPlaceholderText("GSF 파일 디렉토리 (선택)")
        self._gsf_input.setStyleSheet(_INPUT_STYLE)
        gsf_browse = QPushButton("...")
        gsf_browse.setFixedSize(36, 30)
        gsf_browse.setStyleSheet(self._browse_btn_style())
        gsf_browse.clicked.connect(lambda: self._browse_dir(self._gsf_input))
        gsf_row.addWidget(self._gsf_input)
        gsf_row.addWidget(gsf_browse)
        gsf_label = QLabel("GSF 디렉토리")
        gsf_label.setStyleSheet(label_style)
        form.addRow(gsf_label, gsf_row)

        # HVF directory
        hvf_row = QHBoxLayout()
        self._hvf_input = QLineEdit()
        self._hvf_input.setPlaceholderText("HVF 파일 디렉토리 (선택)")
        self._hvf_input.setStyleSheet(_INPUT_STYLE)
        hvf_browse = QPushButton("...")
        hvf_browse.setFixedSize(36, 30)
        hvf_browse.setStyleSheet(self._browse_btn_style())
        hvf_browse.clicked.connect(lambda: self._browse_dir(self._hvf_input))
        hvf_row.addWidget(self._hvf_input)
        hvf_row.addWidget(hvf_browse)
        hvf_label = QLabel("HVF 디렉토리")
        hvf_label.setStyleSheet(label_style)
        form.addRow(hvf_label, hvf_row)

        # Cell size
        self._cell_size = QDoubleSpinBox()
        self._cell_size.setRange(0.1, 100.0)
        self._cell_size.setValue(5.0)
        self._cell_size.setSuffix(" m")
        self._cell_size.setStyleSheet(_INPUT_STYLE)
        cell_label = QLabel("Cell Size")
        cell_label.setStyleSheet(label_style)
        form.addRow(cell_label, self._cell_size)

        # Max GSF files (for large datasets)
        self._max_gsf = QSpinBox()
        self._max_gsf.setRange(0, 9999)
        self._max_gsf.setValue(50)
        self._max_gsf.setSpecialValueText("전체")
        self._max_gsf.setStyleSheet(_INPUT_STYLE)
        self._max_gsf.setToolTip("대용량 프로젝트에서 처리할 최대 GSF 파일 수 (0=전체)")
        gsf_limit_label = QLabel("Max GSF Files")
        gsf_limit_label.setStyleSheet(label_style)
        form.addRow(gsf_limit_label, self._max_gsf)

        # Max pings per file
        self._max_pings = QSpinBox()
        self._max_pings.setRange(0, 999999)
        self._max_pings.setValue(0)
        self._max_pings.setSpecialValueText("전체")
        self._max_pings.setStyleSheet(_INPUT_STYLE)
        self._max_pings.setToolTip("파일당 최대 핑 수 (0=전체, 빠른 미리보기: 100)")
        pings_label = QLabel("Max Pings")
        pings_label.setStyleSheet(label_style)
        form.addRow(pings_label, self._max_pings)

        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedSize(100, 36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {Dark.MUTED};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
            }}
            QPushButton:hover {{
                background: {Dark.DARK};
                color: {Dark.TEXT};
            }}
        """)
        cancel_btn.clicked.connect(self.cancelled.emit)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("저장")
        save_btn.setFixedSize(100, 36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(f"""
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
        """)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _browse_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background: {Dark.NAVY};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
            }}
            QPushButton:hover {{
                background: {Dark.SLATE};
            }}
        """

    def _browse_dir(self, line_edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "디렉토리 선택")
        if d:
            line_edit.setText(d)

    def clear_form(self):
        """Reset form for new project."""
        self._edit_id = None
        self._title_label.setText("새 프로젝트")
        self._name_input.clear()
        self._vessel_input.clear()
        self._pds_input.clear()
        self._gsf_input.clear()
        self._hvf_input.clear()
        self._cell_size.setValue(5.0)
        self._max_gsf.setValue(50)
        self._max_pings.setValue(0)

    def set_edit_mode(self, project_id: int):
        """Load project data for editing."""
        self._edit_id = project_id
        self._title_label.setText("프로젝트 편집")

        p = DataService.get_project(project_id)
        if not p:
            return

        self._name_input.setText(p.get("name", ""))
        self._vessel_input.setText(p.get("vessel", ""))
        self._pds_input.setText(p.get("pds_dir", ""))
        self._gsf_input.setText(p.get("gsf_dir", ""))
        self._hvf_input.setText(p.get("hvf_dir", ""))
        self._cell_size.setValue(p.get("cell_size", 5.0))
        self._max_gsf.setValue(p.get("max_gsf_files", 50))
        self._max_pings.setValue(p.get("max_pings", 0))

    def _on_save(self):
        name = self._name_input.text().strip()
        if not name:
            return

        kwargs = dict(
            vessel=self._vessel_input.text().strip(),
            pds_dir=self._pds_input.text().strip(),
            gsf_dir=self._gsf_input.text().strip(),
            hvf_dir=self._hvf_input.text().strip(),
            cell_size=self._cell_size.value(),
            max_gsf_files=self._max_gsf.value(),
            max_pings=self._max_pings.value(),
        )

        if self._edit_id is not None:
            DataService.update_project(self._edit_id, name=name, **kwargs)
            self.saved.emit(self._edit_id)
        else:
            pid = DataService.create_project(name, **kwargs)
            self.saved.emit(pid)

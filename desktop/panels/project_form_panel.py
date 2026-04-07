"""MBESQC ProjectFormPanel -- Create / edit project form."""

from __future__ import annotations

from pathlib import Path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QFrame,
    QDoubleSpinBox, QSpinBox, QComboBox, QFormLayout,
    QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c

from desktop.services.data_service import DataService
from desktop.services.insight_service import build_project_context, format_number


def _btn_primary_qss() -> str:
    """c()-based primary button QSS."""
    return f"""
        QPushButton {{
            background: {c().CYAN};
            color: #ffffff;
            border: none;
            border-radius: {Radius.BASE}px;
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            padding: 7px 18px;
        }}
        QPushButton:hover {{ background: {c().CYAN_H}; }}
        QPushButton:disabled {{
            background: {c().SLATE};
            color: {c().DIM};
        }}
    """


def _btn_secondary_qss() -> str:
    """c()-based secondary button QSS."""
    return f"""
        QPushButton {{
            background: transparent;
            color: {c().MUTED};
            border: 1px solid {c().BORDER};
            border-radius: {Radius.BASE}px;
            font-size: {Font.SM}px;
            padding: 7px 18px;
        }}
        QPushButton:hover {{
            background: {c().DARK};
            color: {c().TEXT};
            border-color: {c().BORDER_H};
        }}
    """


def _input_style() -> str:
    return f"""
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c().DARK};
        color: {c().TEXT};
        border: 1px solid {c().BORDER};
        border-radius: {Radius.SM}px;
        padding: 6px 10px;
        font-size: {Font.SM}px;
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {c().CYAN};
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

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.XL)
        layout.setSpacing(Space.LG)

        # Title
        self._title_label = QLabel("새 프로젝트")
        self._title_label.setStyleSheet(f"""
            font-size: {Font.XL}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        layout.addWidget(self._title_label)

        # Form
        form = QFormLayout()
        form.setSpacing(Space.MD)
        form.setLabelAlignment(Qt.AlignLeft)

        label_style = f"""
            font-size: {Font.SM}px;
            color: {c().MUTED};
            background: transparent;
        """

        # Project name
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("프로젝트 이름 (예: EDF Phase 1)")
        self._name_input.setStyleSheet(_input_style())
        name_label = QLabel("프로젝트 이름")
        name_label.setStyleSheet(label_style)
        form.addRow(name_label, self._name_input)

        # Vessel
        self._vessel_input = QLineEdit()
        self._vessel_input.setPlaceholderText("선박명 (예: Fugro Equator)")
        self._vessel_input.setStyleSheet(_input_style())
        vessel_label = QLabel("선박명")
        vessel_label.setStyleSheet(label_style)
        form.addRow(vessel_label, self._vessel_input)

        # PDS directory
        pds_row = QHBoxLayout()
        self._pds_input = QLineEdit()
        self._pds_input.setPlaceholderText("PDS 파일 디렉토리")
        self._pds_input.setStyleSheet(_input_style())
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
        self._gsf_input.setStyleSheet(_input_style())
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
        self._hvf_input.setStyleSheet(_input_style())
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
        self._cell_size.setStyleSheet(_input_style())
        cell_label = QLabel("Cell Size")
        cell_label.setStyleSheet(label_style)
        form.addRow(cell_label, self._cell_size)

        # Max GSF files (for large datasets)
        self._max_gsf = QSpinBox()
        self._max_gsf.setRange(0, 9999)
        self._max_gsf.setValue(50)
        self._max_gsf.setSpecialValueText("전체")
        self._max_gsf.setStyleSheet(_input_style())
        self._max_gsf.setToolTip("대용량 프로젝트에서 처리할 최대 GSF 파일 수 (0=전체)")
        gsf_limit_label = QLabel("Max GSF Files")
        gsf_limit_label.setStyleSheet(label_style)
        form.addRow(gsf_limit_label, self._max_gsf)

        # Max pings per file
        self._max_pings = QSpinBox()
        self._max_pings.setRange(0, 999999)
        self._max_pings.setValue(0)
        self._max_pings.setSpecialValueText("전체")
        self._max_pings.setStyleSheet(_input_style())
        self._max_pings.setToolTip("파일당 최대 핑 수 (0=전체, 빠른 미리보기: 100)")
        pings_label = QLabel("Max Pings")
        pings_label.setStyleSheet(label_style)
        form.addRow(pings_label, self._max_pings)

        # OffsetManager config
        om_row = QHBoxLayout()
        self._om_combo = QComboBox()
        self._om_combo.setStyleSheet(_input_style())
        self._om_combo.addItem("(연결 안 됨)", -1)
        self._om_combo.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        om_row.addWidget(self._om_combo, 1)
        self._om_refresh_btn = QPushButton("새로고침")
        self._om_refresh_btn.setFixedHeight(30)
        self._om_refresh_btn.setCursor(Qt.PointingHandCursor)
        self._om_refresh_btn.setStyleSheet(self._browse_btn_style())
        self._om_refresh_btn.clicked.connect(self._load_om_configs)
        om_row.addWidget(self._om_refresh_btn)
        om_label = QLabel("OffsetManager")
        om_label.setStyleSheet(label_style)
        form.addRow(om_label, om_row)
        self._load_om_configs()

        layout.addLayout(form)

        # Guidance / preview
        self._preview_frame = QFrame()
        self._preview_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        preview_layout = QVBoxLayout(self._preview_frame)
        preview_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        preview_layout.setSpacing(Space.SM)

        preview_title = QLabel("입력 관계 미리보기")
        preview_title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        preview_layout.addWidget(preview_title)

        self._flow_label = QLabel("")
        self._flow_label.setWordWrap(True)
        self._flow_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._flow_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().TEXT};
            background: transparent;
        """)
        preview_layout.addWidget(self._flow_label)

        self._readiness_label = QLabel("")
        self._readiness_label.setWordWrap(True)
        self._readiness_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._readiness_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        preview_layout.addWidget(self._readiness_label)

        self._offset_label = QLabel("")
        self._offset_label.setWordWrap(True)
        self._offset_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._offset_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)
        preview_layout.addWidget(self._offset_label)

        layout.addWidget(self._preview_frame)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = QPushButton("취소")
        self._cancel_btn.setFixedSize(100, 36)
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setStyleSheet(_btn_secondary_qss())
        self._cancel_btn.clicked.connect(self.cancelled.emit)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("저장")
        self._save_btn.setFixedSize(100, 36)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(_btn_primary_qss())
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        layout.addLayout(btn_row)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        for widget in (self._pds_input, self._gsf_input, self._hvf_input):
            widget.textChanged.connect(lambda *_: self._refresh_preview())
        self._max_gsf.valueChanged.connect(lambda *_: self._refresh_preview())
        self._max_pings.valueChanged.connect(lambda *_: self._refresh_preview())
        self._cell_size.valueChanged.connect(lambda *_: self._refresh_preview())
        self._refresh_preview()
        self._apply_styles()

    # ── Theme ──────────────────────────────────────────

    def _apply_styles(self):
        self._title_label.setStyleSheet(f"""
            font-size: {Font.XL}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().TEXT_BRIGHT};
            background: transparent;
        """)
        self._preview_frame.setStyleSheet(f"""
            QFrame {{
                background: {c().DARK};
                border: none;
                border-radius: {Radius.SM}px;
            }}
        """)
        label_style = f"""
            font-size: {Font.SM}px;
            color: {c().MUTED};
            background: transparent;
        """
        for w in (self._flow_label,):
            w.setStyleSheet(f"""
                font-size: {Font.XS}px;
                color: {c().TEXT};
                background: transparent;
            """)
        for w in (self._readiness_label, self._offset_label):
            w.setStyleSheet(f"""
                font-size: {Font.XS}px;
                color: {c().MUTED};
                background: transparent;
            """)
        # Buttons
        self._save_btn.setStyleSheet(_btn_primary_qss())
        self._cancel_btn.setStyleSheet(_btn_secondary_qss())

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

    def _browse_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background: {c().NAVY};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                font-size: {Font.SM}px;
            }}
            QPushButton:hover {{
                background: {c().SLATE};
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
        self._om_combo.setCurrentIndex(0)
        self._refresh_preview()

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
        self._set_om_selection(p.get("om_config_id"))
        self._refresh_preview()

    def _load_om_configs(self):
        """Load OffsetManager configs into combo box."""
        current_id = self._om_combo.currentData()
        try:
            from desktop.services.om_client import OMClient
            if OMClient.is_available():
                configs = OMClient.list_configs()
                self._om_combo.clear()
                self._om_combo.addItem("(선택 안 함)", -1)
                for cfg in configs:
                    role = cfg.get("role_label", "")
                    review = cfg.get("review_label", "")
                    readiness = cfg.get("readiness_label", "")
                    suffix_parts = [part for part in (role, review, readiness) if part]
                    suffix = f" [{' / '.join(suffix_parts)}]" if suffix_parts else ""
                    label = f"{cfg.get('vessel_name', '?')} - {cfg.get('project_name', '')}{suffix}"
                    self._om_combo.addItem(label, cfg.get("id", -1))
            else:
                self._om_combo.clear()
                self._om_combo.addItem("(OffsetManager 미연결)", -1)
        except Exception:
            self._om_combo.clear()
            self._om_combo.addItem("(OM 로딩 실패)", -1)
        self._set_om_selection(current_id)
        self._refresh_preview()

    def _set_om_selection(self, om_id):
        try:
            target = int(om_id) if om_id not in (None, "", -1) else -1
        except (TypeError, ValueError):
            target = -1
        for idx in range(self._om_combo.count()):
            if self._om_combo.itemData(idx) == target:
                self._om_combo.setCurrentIndex(idx)
                return
        self._om_combo.setCurrentIndex(0)

    def _count_files(self, directory: str, patterns: tuple[str, ...]) -> int | None:
        if not directory:
            return 0
        path = Path(directory)
        if not path.is_dir():
            return None
        count = 0
        for pattern in patterns:
            count += len(list(path.glob(pattern)))
        return count

    def _fetch_om_preview(self) -> dict | None:
        om_id = self._om_combo.currentData()
        if not om_id or om_id == -1:
            return None
        try:
            from desktop.services.om_client import OMClient
            return OMClient.get_config(int(om_id))
        except Exception:
            return None

    def _refresh_preview(self):
        if not hasattr(self, "_flow_label"):
            return
        project = {
            "pds_dir": self._pds_input.text().strip(),
            "gsf_dir": self._gsf_input.text().strip(),
            "hvf_dir": self._hvf_input.text().strip(),
            "om_config_id": self._om_combo.currentData(),
        }
        file_counts = {
            "pds_count": self._count_files(project["pds_dir"], ("*.pds", "*.PDS")),
            "gsf_count": self._count_files(project["gsf_dir"], ("*.gsf", "*.GSF")),
            "hvf_count": self._count_files(project["hvf_dir"], ("*.hvf", "*.HVF")),
        }
        preview = build_project_context(
            project,
            latest_result=None,
            om_preview=self._fetch_om_preview(),
            file_counts=file_counts,
        )

        max_gsf = self._max_gsf.value()
        max_pings = self._max_pings.value()
        gsf_limit = "전체" if max_gsf == 0 else f"{max_gsf:,}개"
        ping_limit = "전체" if max_pings == 0 else f"{max_pings:,}핑"

        self._flow_label.setText(
            f"{preview['flow']}\n"
            f"현재 설정: cell {format_number(self._cell_size.value(), 1, ' m')}, "
            f"GSF 샘플링 {gsf_limit}, 파일당 최대 핑 {ping_limit}."
        )
        self._readiness_label.setText(
            f"{preview['readiness_text']}\n"
            f"Export 기준: 최신 완료 QC 스냅샷.\n"
            f"저장 시 PDS/GSF/HVF 경로의 top-level 파일을 목록과 자동 동기화합니다."
        )
        self._offset_label.setText(preview["offset_text"])

    def _on_save(self):
        name = self._name_input.text().strip()
        if not name:
            return

        om_id = self._om_combo.currentData()
        kwargs = dict(
            vessel=self._vessel_input.text().strip(),
            pds_dir=self._pds_input.text().strip(),
            gsf_dir=self._gsf_input.text().strip(),
            hvf_dir=self._hvf_input.text().strip(),
            cell_size=self._cell_size.value(),
            max_gsf_files=self._max_gsf.value(),
            max_pings=self._max_pings.value(),
            om_config_id=om_id if om_id and om_id > 0 else None,
        )

        if self._edit_id is not None:
            DataService.update_project(self._edit_id, name=name, **kwargs)
            DataService.sync_project_files(self._edit_id)
            self.saved.emit(self._edit_id)
        else:
            pid = DataService.create_project(name, **kwargs)
            DataService.sync_project_files(pid)
            self.saved.emit(pid)

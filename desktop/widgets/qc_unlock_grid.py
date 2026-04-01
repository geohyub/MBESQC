"""MBESQC QCUnlockGrid -- 9-module QC availability grid based on available file types."""

from __future__ import annotations

import os
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QVBoxLayout, QSizePolicy,
)
from PySide6.QtCore import Qt

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Dark, Font, Space, Radius


# QC module definitions: (id, name, description, requirements)
QC_MODULES = [
    ("preprocess", "Pre-Processing", "처리 전 설정/항법/메타데이터 게이트", ["pds"]),
    ("file",      "File QC",      "파일 무결성/네이밍/시간 연속성",     ["pds"]),
    ("vessel",    "Vessel QC",    "PDS vs HVF 오프셋 비교",            ["pds"]),
    ("offset",    "Offset QC",    "Roll/Pitch bias 추정",              ["gsf"]),
    ("motion",    "Motion QC",    "자세 스파이크/갭/드리프트",          ["gsf"]),
    ("svp",       "SVP QC",       "음속 프로파일 적용 확인",            ["gsf"]),
    ("coverage",  "Coverage QC",  "스와스 커버리지/오버랩",             ["gsf_multi"]),
    ("crossline", "Cross-line QC","IHO S-44 깊이 차이",                ["gsf_multi"]),
    ("surface",   "Surface",      "DTM/Density/Std 그리딩",            ["gsf"]),
]


class _QCModuleCard(QFrame):
    """Individual QC module status card."""

    def __init__(self, qc_id: str, name: str, description: str, parent=None):
        super().__init__(parent)
        self._qc_id = qc_id
        self._state = "locked"  # locked / unlocked / done / running / error

        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        layout.setSpacing(2)

        # Name + status indicator
        self._name_label = QLabel(name)
        self._name_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.TEXT};
            background: transparent;
        """)
        layout.addWidget(self._name_label)

        self._desc_label = QLabel(description)
        self._desc_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {Dark.DIM};
            background: transparent;
        """)
        self._desc_label.setWordWrap(True)
        layout.addWidget(self._desc_label)

        self._status_label = QLabel()
        self._status_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            background: transparent;
        """)
        layout.addWidget(self._status_label)

        self._apply_state()

    def set_state(self, state: str):
        self._state = state
        self._apply_state()

    def _apply_state(self):
        state_config = {
            "locked":   (Dark.BORDER,  Dark.DIM,    "LOCKED"),
            "unlocked": (Dark.CYAN,    Dark.CYAN,   "READY"),
            "running":  (Dark.BLUE,    Dark.BLUE,   "RUNNING"),
            "done":     (Dark.GREEN,   Dark.GREEN,  "DONE"),
            "error":    (Dark.RED,     Dark.RED,    "ERROR"),
        }
        border_color, status_color, status_text = state_config.get(
            self._state, state_config["locked"])

        self.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {border_color};
                border-radius: {Radius.SM}px;
            }}
        """)

        dim = self._state == "locked"
        self._name_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.MEDIUM};
            color: {Dark.DIM if dim else Dark.TEXT};
            background: transparent;
        """)
        self._status_label.setText(status_text)
        self._status_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.SEMIBOLD};
            color: {status_color};
            background: transparent;
        """)


class QCUnlockGrid(QFrame):
    """9-module QC availability grid. Updates based on file availability."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QCUnlockGrid {{
                background: transparent;
                border: none;
            }}
        """)

        self._grid = QGridLayout(self)
        self._grid.setSpacing(Space.SM)
        self._grid.setContentsMargins(0, 0, 0, 0)

        self._cards: dict[str, _QCModuleCard] = {}

        for i, (qc_id, name, desc, _reqs) in enumerate(QC_MODULES):
            card = _QCModuleCard(qc_id, name, desc)
            row, col = divmod(i, 4)
            self._grid.addWidget(card, row, col)
            self._cards[qc_id] = card

    def update_availability(self, has_pds: bool, has_gsf: bool,
                            gsf_count: int = 0, has_hvf: bool = False,
                            has_om: bool = False):
        """Update QC module states based on available file types."""
        for qc_id, _name, _desc, reqs in QC_MODULES:
            available = True
            for req in reqs:
                if req == "pds" and not has_pds:
                    available = False
                elif req == "gsf" and not has_gsf:
                    available = False
                elif req == "gsf_multi" and gsf_count < 2:
                    available = False

            self._cards[qc_id].set_state("unlocked" if available else "locked")

    def set_module_state(self, qc_id: str, state: str):
        """Set individual module state (done, running, error)."""
        if qc_id in self._cards:
            self._cards[qc_id].set_state(state)

    def reset_all(self):
        """Reset all modules to locked."""
        for card in self._cards.values():
            card.set_state("locked")

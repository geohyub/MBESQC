"""MBESQC DQR Panel -- Daily QC Report generation with CARIS Batch CLI.

Three tabs:
  1. CLI 자동화 -- carisbatch pipeline (Import→Georeference→Filter→Grid→Export)
  2. Line QC Report -- GUI 전용 안내 (CARIS Tools > Report > Line QC)
  3. Flier Finder -- GUI 전용 안내 (HydrOffice QC Tools)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Slot, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QFrame, QLineEdit, QFileDialog, QScrollArea,
    QDoubleSpinBox, QComboBox, QProgressBar, QTextEdit,
    QSizePolicy, QGroupBox, QGridLayout,
)

from geoview_pyside6.constants import Dark, Font, Space, Radius, BTN_PRIMARY, BTN_SECONDARY

from desktop.services.caris_batch_service import (
    CarisBatchConfig, CarisBatchRunner, find_carisbatch, is_caris_available,
)
from desktop.services.dqr_service import DqrConfig, DqrWorker


class _PathInput(QFrame):
    """Reusable path input with browse button."""

    def __init__(self, label: str, file_filter: str = "", is_dir: bool = False, parent=None):
        super().__init__(parent)
        self._file_filter = file_filter
        self._is_dir = is_dir

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM)

        lbl = QLabel(label)
        lbl.setFixedWidth(120)
        lbl.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        layout.addWidget(lbl)

        self.input = QLineEdit()
        self.input.setPlaceholderText("경로를 선택하세요...")
        self.input.setStyleSheet(f"""
            QLineEdit {{
                background: {Dark.DARK};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                padding: 6px 10px;
                font-size: {Font.SM}px;
            }}
            QLineEdit:focus {{
                border-color: {Dark.CYAN};
            }}
        """)
        layout.addWidget(self.input, 1)

        btn = QPushButton("...")
        btn.setFixedSize(32, 32)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.NAVY};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: {Dark.SLATE}; }}
        """)
        btn.clicked.connect(self._browse)
        layout.addWidget(btn)

    def _browse(self):
        if self._is_dir:
            path = QFileDialog.getExistingDirectory(self, "폴더 선택")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "파일 선택", "", self._file_filter)
        if path:
            self.input.setText(path)

    def value(self) -> str:
        return self.input.text().strip()


class _CLIAutomationTab(QWidget):
    """Tab 1: CARIS Batch CLI pipeline automation."""

    toast_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._runner = None
        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        # CARIS status
        caris_path = find_carisbatch()
        status_frame = QFrame()
        status_frame.setStyleSheet(f"""
            QFrame {{
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.BASE}px;
                padding: {Space.MD}px;
            }}
        """)
        sl = QHBoxLayout(status_frame)

        if caris_path:
            icon = QLabel("CARIS")
            icon.setStyleSheet(f"""
                color: {Dark.GREEN};
                font-size: {Font.SM}px;
                font-weight: {Font.BOLD};
                background: transparent;
            """)
            sl.addWidget(icon)
            path_lbl = QLabel(caris_path)
            path_lbl.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px; background: transparent;")
            sl.addWidget(path_lbl, 1)
        else:
            icon = QLabel("CARIS NOT FOUND")
            icon.setStyleSheet(f"""
                color: {Dark.ORANGE};
                font-size: {Font.SM}px;
                font-weight: {Font.BOLD};
                background: transparent;
            """)
            sl.addWidget(icon)
            hint = QLabel("carisbatch가 PATH에 없습니다. CARIS HIPS and SIPS를 설치하세요.")
            hint.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px; background: transparent;")
            hint.setWordWrap(True)
            sl.addWidget(hint, 1)

        layout.addWidget(status_frame)

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.setSpacing(Space.SM)
        preset_lbl = QLabel("프리셋")
        preset_lbl.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        preset_row.addWidget(preset_lbl)

        self._preset = QComboBox()
        self._preset.setStyleSheet(f"""
            QComboBox {{
                background: {Dark.DARK}; color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER}; border-radius: {Radius.SM}px;
                padding: 6px 10px; font-size: {Font.SM}px; min-width: 200px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {Dark.DARK}; color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER}; selection-background-color: {Dark.NAVY};
            }}
        """)
        self._preset.addItem("-- 선택 --", None)
        self._preset.addItem("폴더 자동 탐지...", "auto")
        self._preset.addItem("EDF Reference (GSF+HVF+GeoTIFF)", "edf")
        self._preset.addItem("G-OCEAN 시운전 (PDS+HVF+Tide)", "gocean")
        self._preset.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset, 1)

        # Drive letter selector
        drive_lbl = QLabel("드라이브")
        drive_lbl.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        preset_row.addWidget(drive_lbl)

        self._drive = QComboBox()
        self._drive.setFixedWidth(70)
        self._drive.setStyleSheet(f"""
            QComboBox {{
                background: {Dark.DARK}; color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER}; border-radius: {Radius.SM}px;
                padding: 6px 8px; font-size: {Font.SM}px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {Dark.DARK}; color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER}; selection-background-color: {Dark.NAVY};
            }}
        """)
        # Detect available drives
        import string
        for letter in string.ascii_uppercase:
            dp = Path(f"{letter}:/")
            if dp.exists():
                self._drive.addItem(f"{letter}:", letter)
        # Default to I: if available
        idx = self._drive.findData("I")
        if idx >= 0:
            self._drive.setCurrentIndex(idx)
        preset_row.addWidget(self._drive)

        layout.addLayout(preset_row)

        # Input configuration
        grp = QGroupBox("입력 설정")
        grp.setStyleSheet(f"""
            QGroupBox {{
                color: {Dark.TEXT};
                font-size: {Font.SM}px;
                font-weight: {Font.SEMIBOLD};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.BASE}px;
                margin-top: 12px;
                padding-top: 18px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }}
        """)
        gl = QVBoxLayout(grp)
        gl.setSpacing(Space.SM)

        # Required inputs
        self._raw_input = _PathInput("원시 데이터 폴더", is_dir=True)
        gl.addWidget(self._raw_input)

        self._vessel_input = _PathInput("Vessel 파일", "Vessel (*.hvf *.vessel)")
        gl.addWidget(self._vessel_input)

        self._output_input = _PathInput("출력 폴더", is_dir=True)
        gl.addWidget(self._output_input)

        # Optional inputs (collapsible)
        from PySide6.QtWidgets import QCheckBox
        self._show_advanced = QCheckBox("고급 설정 (HIPS/GSF/조석 수동 지정)")
        self._show_advanced.setStyleSheet(f"""
            QCheckBox {{
                color: {Dark.DIM}; font-size: {Font.XS}px; spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid {Dark.BORDER}; border-radius: 3px;
                background: {Dark.DARK};
            }}
            QCheckBox::indicator:checked {{
                background: {Dark.CYAN}; border-color: {Dark.CYAN};
            }}
        """)
        self._show_advanced.toggled.connect(self._toggle_advanced)
        gl.addWidget(self._show_advanced)

        self._advanced_frame = QFrame()
        self._advanced_frame.setVisible(False)
        adv_layout = QVBoxLayout(self._advanced_frame)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(Space.SM)

        self._hips_input = _PathInput("HIPS 프로젝트", "HIPS (*.hips)")
        adv_layout.addWidget(self._hips_input)

        hint = QLabel("비워두면 Raw 데이터에서 자동 생성됩니다")
        hint.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px; padding-left: 124px;")
        adv_layout.addWidget(hint)

        self._gsf_input = _PathInput("GSF 폴더", is_dir=True)
        adv_layout.addWidget(self._gsf_input)

        gsf_hint = QLabel("비워두면 CARIS에서 자동 Export 후 탐지됩니다")
        gsf_hint.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px; padding-left: 124px;")
        adv_layout.addWidget(gsf_hint)

        self._tide_input = _PathInput("조석 파일", "Tide (*.tid *.txt)")
        adv_layout.addWidget(self._tide_input)

        gl.addWidget(self._advanced_frame)

        # Project metadata row
        meta_row = QHBoxLayout()
        meta_row.setSpacing(Space.MD)

        name_lbl = QLabel("프로젝트명")
        name_lbl.setFixedWidth(120)
        name_lbl.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        meta_row.addWidget(name_lbl)

        self._project_name = QLineEdit()
        self._project_name.setPlaceholderText("예: TAEAN ECR 2026")
        self._project_name.setStyleSheet(f"""
            QLineEdit {{
                background: {Dark.DARK}; color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER}; border-radius: {Radius.SM}px;
                padding: 6px 10px; font-size: {Font.SM}px;
            }}
        """)
        meta_row.addWidget(self._project_name, 1)

        area_lbl = QLabel("측량 구역")
        area_lbl.setFixedWidth(80)
        area_lbl.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        meta_row.addWidget(area_lbl)

        self._survey_area = QLineEdit()
        self._survey_area.setPlaceholderText("예: Area A")
        self._survey_area.setStyleSheet(f"""
            QLineEdit {{
                background: {Dark.DARK}; color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER}; border-radius: {Radius.SM}px;
                padding: 6px 10px; font-size: {Font.SM}px;
            }}
        """)
        meta_row.addWidget(self._survey_area, 1)

        gl.addLayout(meta_row)

        # Grid settings row
        grid_row = QHBoxLayout()
        grid_row.setSpacing(Space.MD)

        res_lbl = QLabel("Grid 해상도 (m)")
        res_lbl.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        grid_row.addWidget(res_lbl)

        self._resolution = QDoubleSpinBox()
        self._resolution.setRange(0.1, 100.0)
        self._resolution.setValue(1.0)
        self._resolution.setSingleStep(0.5)
        self._resolution.setStyleSheet(f"""
            QDoubleSpinBox {{
                background: {Dark.DARK};
                color: {Dark.TEXT};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                padding: 4px 8px;
            }}
        """)
        grid_row.addWidget(self._resolution)

        grid_row.addStretch()
        gl.addLayout(grid_row)

        # Skip CARIS checkbox
        from PySide6.QtWidgets import QCheckBox
        self._skip_caris = QCheckBox("CARIS 파이프라인 스킵 (기존 서페이스 이미지로 PPTX만 생성)")
        self._skip_caris.setStyleSheet(f"""
            QCheckBox {{
                color: {Dark.MUTED};
                font-size: {Font.SM}px;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 1px solid {Dark.BORDER};
                border-radius: 3px;
                background: {Dark.DARK};
            }}
            QCheckBox::indicator:checked {{
                background: {Dark.CYAN};
                border-color: {Dark.CYAN};
            }}
        """)
        gl.addWidget(self._skip_caris)

        layout.addWidget(grp)

        # Pipeline steps preview
        steps_label = QLabel("파이프라인 단계")
        steps_label.setStyleSheet(f"""
            color: {Dark.TEXT};
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
        """)
        layout.addWidget(steps_label)

        steps_info = [
            ("Phase 1. 사전검증", "HIPS/Vessel/입력 파일 존재 및 carisbatch 확인"),
            ("Phase 2. CARIS 파이프라인", "Import → Georeference(TPU) → Filter → Grid → Render → Export"),
            ("Phase 3. 메타데이터 수집", "GSF/HVF에서 장비정보, 라인정보, 오프셋 추출"),
            ("Phase 4. DQR PPTX 생성", "11슬라이드 Daily QC Report 자동 생성"),
        ]

        for title, desc in steps_info:
            step_frame = QFrame()
            step_frame.setStyleSheet(f"""
                QFrame {{
                    background: {Dark.DARK};
                    border: 1px solid {Dark.BORDER};
                    border-radius: {Radius.SM}px;
                    padding: 8px 12px;
                }}
            """)
            step_layout = QVBoxLayout(step_frame)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(2)

            t = QLabel(title)
            t.setStyleSheet(f"color: {Dark.TEXT}; font-size: {Font.SM}px; font-weight: {Font.MEDIUM}; background: transparent; border: none;")
            step_layout.addWidget(t)

            d = QLabel(desc)
            d.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px; background: transparent; border: none;")
            step_layout.addWidget(d)

            layout.addWidget(step_frame)

        # Progress
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setFixedHeight(4)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: {Dark.BORDER};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {Dark.CYAN};
                border-radius: 2px;
            }}
        """)
        layout.addWidget(self._progress)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px;")
        layout.addWidget(self._status_label)

        # Log output
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setVisible(False)
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: {Dark.DARK};
                color: {Dark.DIM};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: {Font.XS}px;
                padding: 8px;
            }}
        """)
        layout.addWidget(self._log)

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._run_btn = QPushButton("DQR 자동 생성")
        self._run_btn.setFixedSize(180, 38)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(BTN_PRIMARY)
        self._run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self._run_btn)

        layout.addLayout(btn_row)
        layout.addStretch()

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_preset_changed(self, index: int):
        preset_id = self._preset.currentData()
        if not preset_id:
            return

        if preset_id == "auto":
            self._auto_detect()
            self._preset.blockSignals(True)
            self._preset.setCurrentIndex(0)
            self._preset.blockSignals(False)
            return

        drv = self._drive.currentData() or "I"
        root = Path(f"{drv}:/MBES DQR Test")

        if preset_id == "edf":
            base = root / "EDF"
            self._hips_input.input.setText("")
            self._vessel_input.input.setText(str(self._find_vessel(base / "Vessel", "DP-1")))
            self._raw_input.input.setText("")
            self._gsf_input.input.setText(str(base / "GSF"))
            self._tide_input.input.setText("")
            self._output_input.input.setText(str(root / "Output"))
            self._project_name.setText("EDF Wind Farm Survey")
            self._survey_area.setText("EDF-R Block")
            self._skip_caris.setChecked(True)

        elif preset_id == "gocean":
            base = root / "G-OCEAN"
            self._hips_input.input.setText("")
            self._vessel_input.input.setText(str(self._find_vessel(base / "Vessel", "HY_TEST1")))
            self._raw_input.input.setText(str(base / "Raw"))
            self._gsf_input.input.setText("")
            tide_dir = base / "Tide"
            tid_files = sorted(tide_dir.glob("*.tid")) if tide_dir.exists() else []
            self._tide_input.input.setText(str(tid_files[0]) if tid_files else "")
            self._output_input.input.setText(str(root / "Output"))
            self._project_name.setText("G-OCEAN 시운전 테스트")
            self._survey_area.setText("Test Area 1")
            self._skip_caris.setChecked(False)

    @staticmethod
    def _find_vessel(vessel_dir: Path, stem: str) -> Path:
        """Find vessel file by stem, preferring .vessel (v12) over .hvf (v11)."""
        for ext in [".vessel", ".hvf"]:
            p = vessel_dir / f"{stem}{ext}"
            if p.exists():
                return p
        # Fallback: first vessel file in dir
        for ext in [".vessel", ".hvf"]:
            files = sorted(vessel_dir.glob(f"*{ext}"))
            if files:
                return files[0]
        return vessel_dir / f"{stem}.vessel"  # default even if not found

    def _toggle_advanced(self, checked: bool):
        self._advanced_frame.setVisible(checked)

    def _auto_detect(self):
        """루트 폴더 선택 → 하위에서 HIPS/HVF/GSF/PDS/Tide/SVP/GeoTIFF 자동 탐지."""
        root_dir = QFileDialog.getExistingDirectory(self, "프로젝트 루트 폴더 선택")
        if not root_dir:
            return

        root = Path(root_dir)
        found = []

        # .hips (SQLite project file)
        hips = list(root.rglob("*.hips"))
        if hips:
            self._hips_input.input.setText(str(hips[0]))
            found.append(f"HIPS: {hips[0].name}")

        # Vessel (.hvf or .vessel)
        vessels = list(root.rglob("*.hvf")) + list(root.rglob("*.vessel"))
        if vessels:
            self._vessel_input.input.setText(str(vessels[0]))
            found.append(f"Vessel: {vessels[0].name}")

        # GSF directory (find folder containing .gsf files)
        gsf_files = list(root.rglob("*.gsf"))
        if gsf_files:
            gsf_dir = gsf_files[0].parent
            self._gsf_input.input.setText(str(gsf_dir))
            found.append(f"GSF: {len(gsf_files)}개 ({gsf_dir.name}/)")

        # Raw data (PDS/ALL/XTF/S7K)
        raw_exts = {".pds", ".all", ".xtf", ".s7k", ".kmall"}
        raw_files = [f for f in root.rglob("*") if f.suffix.lower() in raw_exts]
        if raw_files:
            raw_dir = raw_files[0].parent
            self._raw_input.input.setText(str(raw_dir))
            found.append(f"Raw: {len(raw_files)}개 ({raw_dir.name}/)")

        # Tide (.tid or .txt with tide pattern)
        tid_files = list(root.rglob("*.tid"))
        if tid_files:
            self._tide_input.input.setText(str(tid_files[0]))
            found.append(f"Tide: {tid_files[0].name}")

        # Output dir
        out_dir = root / "Output"
        if not out_dir.exists():
            out_dir = root / "DQR_Output"
        self._output_input.input.setText(str(out_dir))

        # Project name from folder name
        self._project_name.setText(root.name)

        # Surface dir (folder with .tif/.tiff)
        tif_files = list(root.rglob("*.tif")) + list(root.rglob("*.tiff"))
        if tif_files:
            found.append(f"GeoTIFF: {len(tif_files)}개")

        # Auto-check skip CARIS if no .hips found
        if not hips:
            self._skip_caris.setChecked(True)
            found.append("HIPS 없음 → CARIS 스킵 자동 체크")

        # Show detection results
        if found:
            self.toast_requested.emit(
                f"자동 탐지 완료: {', '.join(found[:4])}", "success")
        else:
            self.toast_requested.emit(
                "탐지된 파일 없음 — 수동으로 경로를 지정하세요", "warning")

    def _on_run(self):
        skip = self._skip_caris.isChecked()

        hips = self._hips_input.value()
        raw_dir = self._raw_input.value()

        if not skip:
            # CARIS 모드: Raw 또는 HIPS 중 하나는 필요
            if not hips and not raw_dir:
                self.toast_requested.emit("원시 데이터 폴더 또는 HIPS 파일을 지정하세요", "warning")
                return
        else:
            # CARIS 스킵: GSF 또는 서페이스 이미지가 있는 출력 폴더 필요
            if not self._gsf_input.value() and not self._output_input.value():
                self.toast_requested.emit("CARIS 스킵 시 GSF 폴더 또는 출력 폴더를 지정하세요", "warning")
                return

        # Collect raw files from directory
        input_files = []
        raw_dir = self._raw_input.value()
        if raw_dir and Path(raw_dir).is_dir():
            exts = {".gsf", ".all", ".xtf", ".s7k", ".kmall", ".pds", ".hsx", ".jsf", ".fau"}
            input_files = [
                str(f) for f in sorted(Path(raw_dir).iterdir())
                if f.is_file() and f.suffix.lower() in exts
            ]

        config = DqrConfig(
            hips_file=hips,
            vessel_file=self._vessel_input.value(),
            input_files=input_files,
            output_dir=self._output_input.value() or "",
            grid_resolution=self._resolution.value(),
            tide_file=self._tide_input.value(),
            project_name=self._project_name.text().strip(),
            survey_area=self._survey_area.text().strip(),
            gsf_dir=self._gsf_input.value(),
            skip_caris=skip,
        )

        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._log.setVisible(True)
        self._log.clear()

        self._worker = DqrWorker(config)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_msg.connect(self._on_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, desc: str):
        self._status_label.setText(f"[{current}/{total}] {desc}")

    @Slot(str)
    def _on_log(self, msg: str):
        self._log.append(msg)

    @Slot(str)
    def _on_finished(self, output_path: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._status_label.setText(f"완료: {output_path}")
        if output_path.endswith(".pptx"):
            self.toast_requested.emit(
                f"DQR 생성 완료: {Path(output_path).name}", "success")
        else:
            self.toast_requested.emit("CARIS 파이프라인 완료", "success")

    @Slot(str)
    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._status_label.setText(f"오류: {msg[:100]}")
        self._log.append(f"[ERROR] {msg}")
        self.toast_requested.emit(f"DQR 오류: {msg[:60]}", "error")


class _GUIOnlyTab(QWidget):
    """Tab for GUI-only CARIS features (Line QC Report, Flier Finder)."""

    def __init__(self, title: str, steps: list[tuple[str, str]],
                 note: str = "", parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        # Badge
        badge = QLabel("GUI 전용")
        badge.setFixedWidth(80)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"""
            background: {Dark.ORANGE};
            color: {Dark.BG};
            font-size: {Font.XS}px;
            font-weight: {Font.BOLD};
            border-radius: {Radius.SM}px;
            padding: 4px 8px;
        """)
        layout.addWidget(badge)

        # Title
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"""
            color: {Dark.TEXT};
            font-size: {Font.LG}px;
            font-weight: {Font.SEMIBOLD};
        """)
        layout.addWidget(title_lbl)

        desc = QLabel(
            "이 기능은 carisbatch CLI로 자동화할 수 없으며,\n"
            "CARIS HIPS and SIPS GUI에서 직접 수행해야 합니다."
        )
        desc.setStyleSheet(f"color: {Dark.MUTED}; font-size: {Font.SM}px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Steps
        for i, (step_title, step_desc) in enumerate(steps, 1):
            step_frame = QFrame()
            step_frame.setStyleSheet(f"""
                QFrame {{
                    background: {Dark.DARK};
                    border: 1px solid {Dark.BORDER};
                    border-radius: {Radius.SM}px;
                    padding: 10px 14px;
                }}
            """)
            sl = QVBoxLayout(step_frame)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(2)

            t = QLabel(f"{i}. {step_title}")
            t.setStyleSheet(f"color: {Dark.TEXT}; font-size: {Font.SM}px; font-weight: {Font.MEDIUM}; background: transparent; border: none;")
            sl.addWidget(t)

            d = QLabel(step_desc)
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {Dark.DIM}; font-size: {Font.XS}px; background: transparent; border: none;")
            sl.addWidget(d)

            layout.addWidget(step_frame)

        if note:
            note_lbl = QLabel(note)
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet(f"""
                color: {Dark.MUTED};
                font-size: {Font.XS}px;
                padding: {Space.SM}px;
                background: {Dark.DARK};
                border: 1px solid {Dark.BORDER};
                border-radius: {Radius.SM}px;
            """)
            layout.addWidget(note_lbl)

        layout.addStretch()


class DQRPanel(QWidget):
    """Daily QC Report panel with 3 tabs."""

    toast_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: {Dark.BG};
            }}
            QTabBar::tab {{
                background: {Dark.DARK};
                color: {Dark.MUTED};
                border: 1px solid {Dark.BORDER};
                border-bottom: none;
                padding: 8px 20px;
                font-size: {Font.SM}px;
                min-width: 120px;
            }}
            QTabBar::tab:selected {{
                background: {Dark.BG};
                color: {Dark.TEXT};
                border-bottom: 2px solid {Dark.CYAN};
            }}
            QTabBar::tab:hover {{
                color: {Dark.TEXT};
                background: {Dark.NAVY};
            }}
        """)

        # Tab 1: CLI Automation
        cli_tab = _CLIAutomationTab()
        cli_tab.toast_requested.connect(self.toast_requested)
        tabs.addTab(cli_tab, "CLI 자동화")

        # Tab 2: Line QC Report (GUI only)
        line_qc_tab = _GUIOnlyTab(
            title="Line QC Report",
            steps=[
                ("CARIS 실행", "HIPS and SIPS를 열고 프로젝트를 로드합니다"),
                ("서페이스 선택", "비교할 HIPS Grid 서페이스를 선택합니다"),
                ("Tools > Report > Line QC", "메뉴에서 Line QC Report를 실행합니다"),
                ("QC Report Wizard", "서페이스 → 라인/기준 → 그룹핑/출력 옵션 순서로 설정"),
                ("결과 확인", "Count, Max, Min, Mean, Std Dev, S-44 적합률 등 통계 확인"),
            ],
            note="XML 템플릿으로 설정을 저장/재사용할 수 있습니다.\n"
                 "Quality Control Statistics: S-44/S-57 표준 적합률을 포함한 통계 분산을 표시합니다.",
        )
        tabs.addTab(line_qc_tab, "Line QC Report (GUI)")

        # Tab 3: Flier Finder (GUI only)
        flier_tab = _GUIOnlyTab(
            title="Flier Finder (HydrOffice QC Tools)",
            steps=[
                ("HydrOffice 설치", "Python 3.9~3.11 + NumPy 환경에 HydrOffice QC Tools 설치"),
                ("QCTOOLS 환경변수", "QCTOOLS 환경변수를 CARIS 설치 경로로 설정"),
                ("CARIS에서 실행", "CARIS HIPS 내 QC Tools 메뉴에서 Flier Finder 실행"),
                ("옵션 설정", "Adjacent, Curvature, Laplacian, Isolated, Margins, Slivers, Edges 체크"),
                ("결과 확인", "Shapefile로 flier 위치 출력, Layers window에서 선택 가능"),
            ],
            note="HydrOffice QC Tools는 UNH CCOM + NOAA OCS가 공동 개발한 외부 Python 도구입니다.\n"
                 "CARIS 자체 batch 명령이 아니므로 carisbatch로 자동화할 수 없습니다.",
        )
        tabs.addTab(flier_tab, "Flier Finder (GUI)")

        layout.addWidget(tabs)

"""
MBES QC v3.0 — CustomTkinter Desktop Application
====================================================
Multibeam Echosounder 데이터 품질 관리 데스크톱 인터페이스 (Sidebar).

Pages: 홈 | 파일 QC | 서피스 | 커버리지 | 보고서
GeoViewApp v3 sidebar navigation.

Copyright (c) 2025-2026 Geoview Co., Ltd.
"""

from __future__ import annotations

import sys
import logging
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Dict, Optional

import customtkinter as ctk

# ── sys.path for _shared and project root ────────────────────────
_shared = Path(__file__).resolve().parents[2] / "_shared"
if not _shared.exists():
    _shared = Path("E:/Software/_shared")
if _shared.exists() and str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))

_project = Path(__file__).resolve().parents[1]
if str(_project) not in sys.path:
    sys.path.insert(0, str(_project))

from geoview_common.styles import colors
from geoview_common.styles.fonts import BASE, MONO
from geoview_common.ctk_widgets.base_app import GeoViewApp
from geoview_common.ctk_widgets.kpi_card import KPICard
from geoview_common.ctk_widgets.data_table import DataTable
from geoview_common.ctk_widgets.activity_log import ActivityLog

logger = logging.getLogger(__name__)


class MBESQCApp(GeoViewApp):
    """MBES QC desktop application v3 — sidebar navigation."""

    APP_TITLE = "MBES QC"
    APP_VERSION = "3.0"
    APP_SUBTITLE = "멀티빔 음향측심 품질 관리"
    WINDOW_SIZE = "1440x920"
    WINDOW_MIN_SIZE = (1200, 700)
    USE_SIDEBAR = True

    def __init__(self):
        # ── State init (before super) ────────────────────────────
        self.gsf_files: List[str] = []
        self.pds_files: List[str] = []
        self.hvf_path: Optional[str] = None
        self._qc_result = None
        self._analysis_running = False
        self._output_dir: Optional[str] = None

        super().__init__()

        # ── Header action buttons (after super) ─────────────────
        ctk.CTkButton(
            self.header_right, text="GSF 열기", width=100, height=30,
            font=(BASE, 11), fg_color=colors.PRIMARY_LIGHT,
            hover_color="#3B6FA0", corner_radius=15,
            command=self._open_gsf_files,
        ).pack(side="right", padx=5)

        ctk.CTkButton(
            self.header_right, text="PDS 폴더", width=100, height=30,
            font=(BASE, 11), fg_color=colors.PRIMARY_LIGHT,
            hover_color="#3B6FA0", corner_radius=15,
            command=self._open_pds_dir,
        ).pack(side="right", padx=5)

        ctk.CTkButton(
            self.header_right, text="▶ QC 실행", width=110, height=30,
            font=(BASE, 11, "bold"), fg_color=colors.ACCENT,
            hover_color="#2F855A", corner_radius=15,
            command=self._run_qc,
        ).pack(side="right", padx=5)

        self.root.bind_all("<Control-o>", lambda e: self._open_gsf_files())

    # ─── NAV ITEMS ───────────────────────────────────────────────

    def get_nav_items(self):
        return [
            ("home", "홈", "home"),
            ("file_qc", "파일 QC", "analysis"),
            ("surface", "서피스", "analysis"),
            ("coverage", "커버리지", "analysis"),
            ("reports", "보고서", "upload"),
        ]

    def build_page(self, page_id, parent):
        builders = {
            "home": self._build_home,
            "file_qc": self._build_file_qc,
            "surface": self._build_surface,
            "coverage": self._build_coverage,
            "reports": self._build_reports,
        }
        builder = builders.get(page_id)
        if builder:
            builder(parent)

    # ─── HOME PAGE ───────────────────────────────────────────────

    def _build_home(self, frame):
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header, text="MBES QC 대시보드",
            font=(BASE, 20, "bold"),
            text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
        ).pack(side="left")

        ctk.CTkLabel(
            header, text="멀티빔 음향측심 데이터 품질 관리",
            font=(BASE, 12),
            text_color=(colors.TEXT_MUTED, colors.DARK_TEXT_MUTED),
        ).pack(side="left", padx=12)

        # KPI cards row
        kpi_frame = ctk.CTkFrame(frame, fg_color="transparent")
        kpi_frame.pack(fill="x", padx=16, pady=(0, 8))

        self._kpi = {}
        kpi_defs = [
            ("파일", colors.PRIMARY_LIGHT, "0", "개"),
            ("측선", colors.ACCENT, "0", "개"),
            ("포인트", colors.ACCENT_WARM, "0", "개"),
            ("커버리지", "#805AD5", "—", "%"),
        ]
        for name, color, initial, unit in kpi_defs:
            card = KPICard(kpi_frame, title=name, accent_color=color,
                           initial_value=initial, unit=unit)
            card.pack(side="left", expand=True, fill="x", padx=4)
            self._kpi[name] = card

        # File table
        ctk.CTkLabel(
            frame, text="  로드된 파일", font=(BASE, 12, "bold"),
            text_color=(colors.TEXT_SECONDARY, colors.DARK_TEXT_SECONDARY),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 4))

        self._file_table = DataTable(
            frame,
            columns=["파일명", "유형", "크기", "상태"],
            column_widths=[350, 100, 120, 100],
            on_row_click=self._on_file_select,
        )
        self._file_table.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ─── FILE QC PAGE ────────────────────────────────────────────

    def _build_file_qc(self, frame):
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header, text="파일 무결성 검사",
            font=(BASE, 18, "bold"),
            text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
        ).pack(side="left")

        ctk.CTkButton(
            header, text="파일 QC 실행", width=120, height=30,
            font=(BASE, 11), fg_color=colors.PRIMARY_LIGHT,
            hover_color="#3B6FA0", corner_radius=15,
            command=self._run_file_qc_only,
        ).pack(side="right", padx=5)

        # Results table
        self._fileqc_table = DataTable(
            frame,
            columns=["항목", "상태", "상세"],
            column_widths=[200, 100, 500],
        )
        self._fileqc_table.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        # Log
        ctk.CTkLabel(
            frame, text="  검사 로그", font=(BASE, 11, "bold"),
            text_color=(colors.TEXT_SECONDARY, colors.DARK_TEXT_SECONDARY),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 4))

        self._fileqc_log = ActivityLog(frame, height=180)
        self._fileqc_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ─── SURFACE PAGE ────────────────────────────────────────────

    def _build_surface(self, frame):
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header, text="서피스 생성 / QC",
            font=(BASE, 18, "bold"),
            text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
        ).pack(side="left")

        # Controls row
        ctrl = ctk.CTkFrame(frame, fg_color=(colors.SURFACE, colors.DARK_SURFACE),
                            corner_radius=10, border_width=1,
                            border_color=(colors.TABLE_BORDER, colors.DARK_BORDER))
        ctrl.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(ctrl, text="  그리드 설정", font=(BASE, 12, "bold"),
                     text_color=(colors.TEXT_SECONDARY, colors.DARK_TEXT_SECONDARY),
                     ).pack(fill="x", padx=12, pady=(10, 6), anchor="w")

        params_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        params_row.pack(fill="x", padx=16, pady=(0, 12))

        # Cell size
        ctk.CTkLabel(params_row, text="셀 크기 (m):", font=(BASE, 12),
                     text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
                     ).pack(side="left", padx=(0, 8))
        self._cell_size_entry = ctk.CTkEntry(params_row, width=80, font=(MONO, 12))
        self._cell_size_entry.insert(0, "5.0")
        self._cell_size_entry.pack(side="left", padx=(0, 20))

        # IHO Order
        ctk.CTkLabel(params_row, text="IHO 기준:", font=(BASE, 12),
                     text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
                     ).pack(side="left", padx=(0, 8))
        self._iho_combo = ctk.CTkComboBox(
            params_row, values=["Special", "1a", "1b", "2"],
            width=100, font=(MONO, 12))
        self._iho_combo.set("1a")
        self._iho_combo.pack(side="left", padx=(0, 20))

        ctk.CTkButton(
            params_row, text="서피스 생성", width=120, height=30,
            font=(BASE, 11), fg_color=colors.ACCENT,
            hover_color="#2F855A", corner_radius=15,
            command=self._run_surface_build,
        ).pack(side="right", padx=5)

        # Surface stats
        self._surface_stats = DataTable(
            frame,
            columns=["항목", "값", "단위"],
            column_widths=[250, 200, 100],
        )
        self._surface_stats.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        # Log
        self._surface_log = ActivityLog(frame, height=180)
        self._surface_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ─── COVERAGE PAGE ───────────────────────────────────────────

    def _build_coverage(self, frame):
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header, text="커버리지 분석",
            font=(BASE, 18, "bold"),
            text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
        ).pack(side="left")

        ctk.CTkButton(
            header, text="커버리지 분석", width=130, height=30,
            font=(BASE, 11), fg_color=colors.PRIMARY_LIGHT,
            hover_color="#3B6FA0", corner_radius=15,
            command=self._run_coverage_qc_only,
        ).pack(side="right", padx=5)

        # KPI row for coverage
        cov_kpi = ctk.CTkFrame(frame, fg_color="transparent")
        cov_kpi.pack(fill="x", padx=16, pady=(0, 8))

        self._cov_kpi = {}
        cov_defs = [
            ("총 측선", colors.PRIMARY_LIGHT, "0", "개"),
            ("총 거리", colors.ACCENT, "0.0", "km"),
            ("커버리지 면적", colors.ACCENT_WARM, "0.0", "km²"),
            ("평균 중첩률", "#805AD5", "—", "%"),
        ]
        for name, color, initial, unit in cov_defs:
            card = KPICard(cov_kpi, title=name, accent_color=color,
                           initial_value=initial, unit=unit)
            card.pack(side="left", expand=True, fill="x", padx=4)
            self._cov_kpi[name] = card

        # Line details table
        ctk.CTkLabel(
            frame, text="  측선 상세", font=(BASE, 11, "bold"),
            text_color=(colors.TEXT_SECONDARY, colors.DARK_TEXT_SECONDARY),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 4))

        self._coverage_table = DataTable(
            frame,
            columns=["파일명", "핑 수", "길이 (m)", "평균 수심 (m)", "평균 스워스 (m)", "방위각 (°)"],
            column_widths=[200, 80, 100, 120, 120, 100],
        )
        self._coverage_table.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        # QC items
        self._coverage_qc_table = DataTable(
            frame,
            columns=["항목", "상태", "상세"],
            column_widths=[200, 100, 500],
        )
        self._coverage_qc_table.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ─── REPORTS PAGE ────────────────────────────────────────────

    def _build_reports(self, frame):
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            header, text="보고서 생성",
            font=(BASE, 18, "bold"),
            text_color=(colors.TEXT_PRIMARY, colors.DARK_TEXT),
        ).pack(side="left")

        # Output directory
        dir_row = ctk.CTkFrame(frame, fg_color=(colors.SURFACE, colors.DARK_SURFACE),
                               corner_radius=10, border_width=1,
                               border_color=(colors.TABLE_BORDER, colors.DARK_BORDER))
        dir_row.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(dir_row, text="  출력 폴더", font=(BASE, 12, "bold"),
                     text_color=(colors.TEXT_SECONDARY, colors.DARK_TEXT_SECONDARY),
                     ).pack(fill="x", padx=12, pady=(10, 6), anchor="w")

        inner = ctk.CTkFrame(dir_row, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(0, 12))

        self._output_label = ctk.CTkLabel(
            inner, text="(미선택)", font=(MONO, 11),
            text_color=(colors.TEXT_MUTED, colors.DARK_TEXT_MUTED),
            anchor="w",
        )
        self._output_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            inner, text="폴더 선택", width=100, height=28,
            font=(BASE, 11), fg_color=colors.PRIMARY_LIGHT,
            command=self._select_output_dir,
        ).pack(side="right", padx=5)

        # Report buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(8, 8))

        report_btns = [
            ("전체 QC 실행 + 보고서", colors.ACCENT, self._run_full_qc),
            ("Excel 보고서", colors.PRIMARY_LIGHT, self._run_excel_report),
            ("Word 보고서", colors.PRIMARY_LIGHT, self._run_word_report),
            ("DQR PPT 생성", colors.ACCENT_WARM, self._run_dqr_ppt),
        ]
        for text, color, cmd in report_btns:
            ctk.CTkButton(
                btn_frame, text=text, width=160, height=36,
                font=(BASE, 12), fg_color=color,
                corner_radius=15, command=cmd,
            ).pack(side="left", padx=6)

        # Report log
        ctk.CTkLabel(
            frame, text="  보고서 로그", font=(BASE, 11, "bold"),
            text_color=(colors.TEXT_SECONDARY, colors.DARK_TEXT_SECONDARY),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 4))

        self._report_log = ActivityLog(frame, height=300)
        self._report_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ─── ACTIONS ─────────────────────────────────────────────────

    def _open_gsf_files(self):
        files = filedialog.askopenfilenames(
            title="GSF 파일 선택",
            filetypes=[("GSF Files", "*.gsf"), ("All Files", "*.*")],
        )
        if files:
            self.gsf_files = list(files)
            self._update_file_display()

    def _open_pds_dir(self):
        d = filedialog.askdirectory(title="PDS 데이터 폴더 선택")
        if d:
            p = Path(d)
            self.pds_files = sorted(str(f) for f in p.glob("*.pds"))
            # Also look for HVF
            hvf_list = list(p.glob("*.hvf"))
            if hvf_list:
                self.hvf_path = str(hvf_list[0])
            self._update_file_display()

    def _select_output_dir(self):
        d = filedialog.askdirectory(title="출력 폴더 선택")
        if d:
            self._output_dir = d
            self._output_label.configure(text=d)

    def _update_file_display(self):
        data = []
        for f in self.gsf_files:
            p = Path(f)
            size = p.stat().st_size if p.exists() else 0
            size_str = f"{size / 1024:.1f} KB" if size < 1_048_576 else f"{size / 1_048_576:.1f} MB"
            data.append([p.name, "GSF", size_str, "대기"])
        for f in self.pds_files:
            p = Path(f)
            size = p.stat().st_size if p.exists() else 0
            size_str = f"{size / 1024:.1f} KB" if size < 1_048_576 else f"{size / 1_048_576:.1f} MB"
            data.append([p.name, "PDS", size_str, "대기"])
        if self.hvf_path:
            p = Path(self.hvf_path)
            size = p.stat().st_size if p.exists() else 0
            size_str = f"{size / 1024:.1f} KB" if size < 1_048_576 else f"{size / 1_048_576:.1f} MB"
            data.append([p.name, "HVF", size_str, "대기"])

        self._file_table.set_data(data)
        total_files = len(self.gsf_files) + len(self.pds_files)
        self._kpi["파일"].set_value(str(total_files))
        self._kpi["측선"].set_value(str(len(self.gsf_files)))
        self.set_status(f"{total_files}개 파일 로드됨")

    def _on_file_select(self, idx, row):
        self.set_status(f"선택: {row[0]}")

    # ─── File QC Only ────────────────────────────────────────────

    def _run_file_qc_only(self):
        if not self.gsf_files and not self.pds_files:
            messagebox.showinfo("MBES QC", "파일을 먼저 로드하세요.")
            return

        self._fileqc_log.clear()
        self._fileqc_log.step("파일 QC 실행 중...")
        self.set_status("파일 QC 중...")
        self.show_progress(-1)

        def worker():
            try:
                from mbes_qc.file_qc import run_file_qc
                result = run_file_qc(
                    gsf_files=self.gsf_files,
                    pds_files=self.pds_files,
                )
                rows = []
                for item in result.items:
                    rows.append([item.name, item.status, item.detail])
                self.root.after(0, lambda: self._fileqc_table.set_data(rows))
                self.root.after(0, lambda v=result.overall_verdict:
                    self._fileqc_log.success(f"파일 QC 완료: {v}"))
                self.root.after(0, lambda: self._fileqc_log.info(
                    f"GSF: {len(result.gsf_files)}개, PDS: {len(result.pds_files)}개"))
                if result.time_range:
                    self.root.after(0, lambda t=result.time_range:
                        self._fileqc_log.info(f"시간 범위: {t}"))
            except Exception as e:
                self.root.after(0, lambda: self._fileqc_log.error(f"실패: {e}"))
                logger.error("File QC failed: %s", e, exc_info=True)
            finally:
                self.root.after(0, lambda: self.set_status("준비 완료"))
                self.root.after(0, self.hide_progress)

        threading.Thread(target=worker, daemon=True).start()

    # ─── Surface Build ───────────────────────────────────────────

    def _run_surface_build(self):
        if not self.gsf_files:
            messagebox.showinfo("MBES QC", "GSF 파일을 먼저 로드하세요.")
            return

        self._surface_log.clear()
        self._surface_log.step("서피스 생성 중...")
        self.set_status("서피스 생성 중...")
        self.show_progress(-1)

        cell_size = float(self._cell_size_entry.get() or "5.0")
        out_dir = self._output_dir

        def worker():
            try:
                from pds_toolkit import read_gsf
                from mbes_qc.surface_builder import build_surfaces_from_gsf

                gsf = read_gsf(self.gsf_files[0], max_pings=None,
                               load_attitude=False, load_svp=False)
                self.root.after(0, lambda n=gsf.num_pings:
                    self._surface_log.info(f"GSF 로드: {n:,} pings"))

                surf_dir = Path(out_dir) / "surfaces" if out_dir else None
                result = build_surfaces_from_gsf(gsf, cell_size, surf_dir)

                import numpy as np
                stats = [
                    ["포인트 수", f"{result.num_points:,}", "개"],
                    ["그리드 크기", f"{result.nx} x {result.ny}", "셀"],
                    ["셀 크기", f"{result.cell_size}", "m"],
                ]
                if result.dtm is not None:
                    v = result.dtm[~np.isnan(result.dtm)]
                    if len(v) > 0:
                        stats.append(["수심 범위", f"{v.min():.2f} ~ {v.max():.2f}", "m"])
                        stats.append(["평균 수심", f"{np.mean(v):.2f}", "m"])
                if result.density is not None:
                    d = result.density[~np.isnan(result.density)]
                    if len(d) > 0:
                        stats.append(["최대 밀도", f"{d.max():.0f}", "pts/cell"])

                self.root.after(0, lambda: self._surface_stats.set_data(stats))
                self.root.after(0, lambda: self._surface_log.success(
                    f"서피스 생성 완료: {result.num_points:,} points, "
                    f"{result.nx}x{result.ny} grid"))
                self.root.after(0, lambda: self._kpi["포인트"].set_value(
                    f"{result.num_points:,}"))
            except Exception as e:
                self.root.after(0, lambda: self._surface_log.error(f"실패: {e}"))
                logger.error("Surface build failed: %s", e, exc_info=True)
            finally:
                self.root.after(0, lambda: self.set_status("준비 완료"))
                self.root.after(0, self.hide_progress)

        threading.Thread(target=worker, daemon=True).start()

    # ─── Coverage QC Only ────────────────────────────────────────

    def _run_coverage_qc_only(self):
        if len(self.gsf_files) < 2:
            messagebox.showinfo("MBES QC", "커버리지 분석에는 2개 이상의 GSF 파일이 필요합니다.")
            return

        self.set_status("커버리지 분석 중...")
        self.show_progress(-1)

        def worker():
            try:
                from pds_toolkit import read_gsf
                from mbes_qc.coverage_qc import run_coverage_qc

                gsf_objects = []
                for f in self.gsf_files:
                    gsf = read_gsf(f, max_pings=None,
                                   load_attitude=False, load_svp=False)
                    gsf_objects.append(gsf)

                result = run_coverage_qc(gsf_objects)

                # Update KPIs
                self.root.after(0, lambda: self._cov_kpi["총 측선"].set_value(
                    str(result.total_lines)))
                self.root.after(0, lambda: self._cov_kpi["총 거리"].set_value(
                    f"{result.total_length_km:.1f}"))
                self.root.after(0, lambda: self._cov_kpi["커버리지 면적"].set_value(
                    f"{result.total_area_km2:.2f}"))
                self.root.after(0, lambda: self._cov_kpi["평균 중첩률"].set_value(
                    f"{result.mean_overlap_pct:.1f}"))
                self.root.after(0, lambda: self._kpi["커버리지"].set_value(
                    f"{result.mean_overlap_pct:.1f}"))

                # Line details
                rows = []
                for line in result.lines:
                    rows.append([
                        line.filename,
                        str(line.num_pings),
                        f"{line.length_m:.1f}",
                        f"{line.mean_depth_m:.1f}",
                        f"{line.mean_swath_m:.1f}",
                        f"{line.heading_deg:.1f}",
                    ])
                self.root.after(0, lambda: self._coverage_table.set_data(rows))

                # QC items
                qc_rows = []
                for item in result.items:
                    qc_rows.append([
                        item.get("name", ""),
                        item.get("status", "N/A"),
                        item.get("detail", ""),
                    ])
                self.root.after(0, lambda: self._coverage_qc_table.set_data(qc_rows))

            except Exception as e:
                logger.error("Coverage QC failed: %s", e, exc_info=True)
                self.root.after(0, lambda: messagebox.showerror("MBES QC", f"커버리지 분석 실패: {e}"))
            finally:
                self.root.after(0, lambda: self.set_status("준비 완료"))
                self.root.after(0, self.hide_progress)

        threading.Thread(target=worker, daemon=True).start()

    # ─── Full QC ─────────────────────────────────────────────────

    def _run_qc(self):
        if not self.gsf_files:
            messagebox.showinfo("MBES QC", "GSF 파일을 먼저 로드하세요.")
            return
        self._run_full_qc()

    def _run_full_qc(self):
        if not self.gsf_files:
            messagebox.showinfo("MBES QC", "GSF 파일을 먼저 로드하세요.")
            return
        if self._analysis_running:
            messagebox.showwarning("MBES QC", "분석이 이미 진행 중입니다.")
            return
        if not self._output_dir:
            d = filedialog.askdirectory(title="출력 폴더 선택")
            if not d:
                return
            self._output_dir = d
            self._output_label.configure(text=d)

        self._analysis_running = True
        self.navigate("reports")
        self._report_log.clear()
        self._report_log.header("전체 QC 파이프라인 실행")
        self.set_status("전체 QC 실행 중...")
        self.show_progress(-1)

        def worker():
            try:
                from mbes_qc.runner import run_full_qc

                cell_size = float(self._cell_size_entry.get() or "5.0")
                iho_order = self._iho_combo.get() if hasattr(self, '_iho_combo') else "1a"

                self.root.after(0, lambda: self._report_log.step("전체 QC 파이프라인 시작..."))

                result = run_full_qc(
                    gsf_paths=self.gsf_files,
                    pds_dir=Path(self.pds_files[0]).parent if self.pds_files else None,
                    hvf_path=self.hvf_path,
                    output_dir=self._output_dir,
                    cell_size=cell_size,
                    iho_order=iho_order,
                    generate_surfaces=True,
                    generate_reports=True,
                )

                self._qc_result = result

                self.root.after(0, lambda: self._report_log.success(
                    f"완료 ({result.elapsed_sec:.1f}초)"))

                # Update KPIs
                if result.file_qc:
                    self.root.after(0, lambda: self._kpi["측선"].set_value(
                        str(result.file_qc.total_lines)))
                if result.surface:
                    self.root.after(0, lambda: self._kpi["포인트"].set_value(
                        f"{result.surface.num_points:,}"))
                if result.coverage_qc:
                    self.root.after(0, lambda: self._kpi["커버리지"].set_value(
                        f"{result.coverage_qc.mean_overlap_pct:.1f}"))

                # Log each QC section
                for section, res in result.as_dict().items():
                    verdict = getattr(res, 'overall_verdict', 'N/A')
                    self.root.after(0, lambda s=section, v=verdict:
                        self._report_log.info(f"{s}: {v}"))

                if self._output_dir:
                    self.root.after(0, lambda: self._report_log.success(
                        f"보고서 출력: {self._output_dir}"))

            except Exception as e:
                self.root.after(0, lambda: self._report_log.error(f"QC 실패: {e}"))
                logger.error("Full QC failed: %s", e, exc_info=True)
            finally:
                self._analysis_running = False
                self.root.after(0, lambda: self.set_status("준비 완료"))
                self.root.after(0, self.hide_progress)

        threading.Thread(target=worker, daemon=True).start()

    # ─── Individual Report Generators ────────────────────────────

    def _run_excel_report(self):
        if not self._qc_result:
            messagebox.showinfo("MBES QC", "먼저 전체 QC를 실행하세요.")
            return
        if not self._output_dir:
            messagebox.showinfo("MBES QC", "출력 폴더를 선택하세요.")
            return

        def worker():
            try:
                from mbes_qc.report import generate_excel_report
                out = Path(self._output_dir) / "QC_Report.xlsx"
                generate_excel_report(self._qc_result.as_dict(), out)
                self.root.after(0, lambda: self._report_log.success(f"Excel 보고서: {out}"))
            except Exception as e:
                self.root.after(0, lambda: self._report_log.error(f"Excel 실패: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _run_word_report(self):
        if not self._qc_result:
            messagebox.showinfo("MBES QC", "먼저 전체 QC를 실행하세요.")
            return
        if not self._output_dir:
            messagebox.showinfo("MBES QC", "출력 폴더를 선택하세요.")
            return

        def worker():
            try:
                from mbes_qc.report import generate_word_report
                out = Path(self._output_dir) / "QC_Report.docx"
                generate_word_report(self._qc_result.as_dict(), out)
                self.root.after(0, lambda: self._report_log.success(f"Word 보고서: {out}"))
            except Exception as e:
                self.root.after(0, lambda: self._report_log.error(f"Word 실패: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _run_dqr_ppt(self):
        if not self._qc_result:
            messagebox.showinfo("MBES QC", "먼저 전체 QC를 실행하세요.")
            return
        if not self._output_dir:
            messagebox.showinfo("MBES QC", "출력 폴더를 선택하세요.")
            return

        def worker():
            try:
                from mbes_qc.dqr_ppt import generate_dqr_ppt
                from pds_toolkit import read_pds_header, read_hvf

                out = Path(self._output_dir) / "DQR_MBES.pptx"
                pds_meta = None
                if self.pds_files:
                    pds_meta = read_pds_header(self.pds_files[0])

                hvf = None
                if self.hvf_path:
                    hvf = read_hvf(self.hvf_path)

                gsf_main = None
                if self.gsf_files:
                    from pds_toolkit import read_gsf
                    gsf_main = read_gsf(self.gsf_files[0], max_pings=100,
                                        load_attitude=False, load_svp=True)

                total_km = 0.0
                if self._qc_result and self._qc_result.coverage_qc:
                    total_km = self._qc_result.coverage_qc.total_length_km

                generate_dqr_ppt(
                    out,
                    pds_meta=pds_meta,
                    gsf_main=gsf_main,
                    hvf=hvf,
                    surface_dir=Path(self._output_dir) / "surfaces",
                    total_line_km=total_km,
                    qc_results=self._qc_result.as_dict() if self._qc_result else None,
                )
                self.root.after(0, lambda: self._report_log.success(f"DQR PPT: {out}"))
            except Exception as e:
                self.root.after(0, lambda: self._report_log.error(f"DQR PPT 실패: {e}"))

        threading.Thread(target=worker, daemon=True).start()


def main():
    logging.basicConfig(level=logging.INFO)
    app = MBESQCApp()
    app.run()


if __name__ == "__main__":
    main()

"""DQR Automation Service — End-to-end Daily QC Report generation.

Orchestrates: Precheck → CARIS Pipeline → Metadata Collection → DQR PPTX.
Follows the NOAA Charlene pattern (carisbatch Python wrapper).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from desktop.services.caris_batch_service import (
    CarisBatchConfig,
    CarisBatchRunner,
    find_carisbatch,
)

log = logging.getLogger(__name__)


@dataclass
class DqrConfig:
    """Configuration for a full DQR automation run."""

    # ── CARIS pipeline settings ──
    hips_file: str = ""
    vessel_file: str = ""            # .hvf (v11) or .vessel (v12)
    input_files: list[str] = field(default_factory=list)
    output_dir: str = ""
    grid_resolution: float = 1.0
    grid_type: str = "SWATH_ANGLE"
    crs: str = "EPSG:32652"          # UTM 52N — Grid requires projected CRS
    vessel_name: str = ""
    day_filter: str = ""

    # Tide
    tide_file: str = ""

    # RenderRaster
    colour_file: str = "Rainbow.cma"
    enable_shading: bool = True

    # ── DQR metadata ──
    project_name: str = ""
    survey_area: str = ""
    gsf_dir: str = ""                # GSF directory for metadata extraction
    total_line_km: float = 0.0

    # ── Output ──
    output_pptx: str = ""

    # ── Mode ──
    caris_only: bool = False         # True = skip PPTX generation
    skip_caris: bool = False         # True = skip CARIS pipeline (use existing surfaces)


class DqrWorker(QObject):
    """Background worker for end-to-end DQR automation.

    4 phases:
      1. Precheck — validate all inputs
      2. CARIS Pipeline — Import→Georeference→Filter→Grid→Render→Export
      3. Metadata — collect GSF/HVF data for slides
      4. PPTX — generate 11-slide DQR report
    """

    progress = Signal(int, int, str)   # current_phase, total_phases, description
    log_msg = Signal(str)              # log line for UI display
    finished = Signal(str)             # output file path
    error = Signal(str)

    TOTAL_PHASES = 4

    def __init__(self, config: DqrConfig):
        super().__init__()
        self._config = config

    @Slot()
    def run(self):
        try:
            self._execute()
        except Exception as e:
            log.exception("DQR automation failed")
            self.error.emit(str(e))

    def _execute(self):
        cfg = self._config

        if cfg.skip_caris:
            # ── Skip CARIS: use existing surfaces ──
            self.progress.emit(1, self.TOTAL_PHASES, "CARIS 스킵 — 기존 서페이스 사용")
            self.log_msg.emit("⏭ CARIS 파이프라인 스킵 (기존 이미지/GeoTIFF 사용)")
        else:
            # ── Phase 1: Precheck ──
            self.progress.emit(1, self.TOTAL_PHASES, "사전검증 중...")
            caris_config = self._build_caris_config()
            runner = CarisBatchRunner(caris_config)
            ok, errors = runner.precheck()
            if not ok:
                self.error.emit("사전검증 실패:\n" + "\n".join(f"  • {e}" for e in errors))
                return
            self.log_msg.emit("✓ 사전검증 통과")

            # ── Phase 2: CARIS Pipeline ──
            self.progress.emit(2, self.TOTAL_PHASES, "CARIS 파이프라인 실행 중...")
            runner.progress.connect(self.log_msg.emit)
            runner.step_done.connect(
                lambda name, ok: self.log_msg.emit(
                    f"  {'✓' if ok else '✗'} {name}"
                )
            )

            pipeline_ok = [True]
            pipeline_error = [""]

            def on_error(msg):
                pipeline_ok[0] = False
                pipeline_error[0] = msg

            runner.error.connect(on_error)
            runner.run()

            if not pipeline_ok[0]:
                self.error.emit(f"CARIS 파이프라인 실패: {pipeline_error[0]}")
                return

            self.log_msg.emit("✓ CARIS 파이프라인 완료")

            if cfg.caris_only:
                out_dir = Path(cfg.output_dir) if cfg.output_dir else (Path(cfg.hips_file).parent / "dqr_output" if cfg.hips_file else Path.cwd() / "dqr_output")
                self.finished.emit(str(out_dir))
                return

        # ── Phase 3: Metadata Collection ──
        self.progress.emit(3, self.TOTAL_PHASES, "메타데이터 수집 중...")
        gsf_main = self._load_gsf_metadata()
        hvf = self._load_hvf_metadata()
        pds_meta = None  # PDS metadata is optional
        self.log_msg.emit(f"  GSF: {'로드됨' if gsf_main else '없음'}")
        self.log_msg.emit(f"  HVF: {'로드됨' if hvf else '없음'}")

        # ── Phase 4: DQR PPTX Generation ──
        self.progress.emit(4, self.TOTAL_PHASES, "DQR PPTX 생성 중...")

        surface_dir = Path(cfg.output_dir) if cfg.output_dir else (Path(cfg.hips_file).parent / "dqr_output" if cfg.hips_file else Path.cwd() / "dqr_output")
        output_pptx = cfg.output_pptx or str(
            surface_dir / f"DQR_{cfg.project_name or 'Report'}_{_today()}.pptx"
        )

        from mbes_qc.dqr_ppt import generate_dqr_ppt

        generate_dqr_ppt(
            output_path=output_pptx,
            pds_meta=pds_meta,
            gsf_main=gsf_main,
            hvf=hvf,
            surface_dir=str(surface_dir),
            project_name=cfg.project_name,
            survey_area=cfg.survey_area,
            total_line_km=cfg.total_line_km,
            grid_resolution=cfg.grid_resolution,
        )

        self.log_msg.emit(f"✓ DQR 저장: {output_pptx}")
        self.finished.emit(output_pptx)

    # ── Internal helpers ──

    def _build_caris_config(self) -> CarisBatchConfig:
        cfg = self._config
        cc = CarisBatchConfig(
            hips_file=cfg.hips_file,
            vessel_file=cfg.vessel_file,
            input_files=list(cfg.input_files),
            vessel_name=cfg.vessel_name,
            day_filter=cfg.day_filter,
            tide_file=cfg.tide_file,
            crs=cfg.crs,
            grid_resolution=cfg.grid_resolution,
            gridding_method=cfg.grid_type,
            colour_file=cfg.colour_file,
            enable_shading=cfg.enable_shading,
            output_dir=cfg.output_dir,
        )
        return cc

    def _load_gsf_metadata(self):
        """Load first GSF file for metadata extraction.

        Search order:
          1. Explicitly specified gsf_dir
          2. CARIS-exported GSF_Export/ folder in output_dir
          3. Any .gsf files in output_dir recursively
        """
        search_dirs = []

        # Explicit GSF dir
        if self._config.gsf_dir:
            p = Path(self._config.gsf_dir)
            if p.is_file():
                search_dirs.append(p.parent)
            elif p.is_dir():
                search_dirs.append(p)

        # CARIS-exported GSF_Export/ in output dir
        out = Path(self._config.output_dir or ".")
        gsf_export = out / "GSF_Export"
        if gsf_export.is_dir():
            search_dirs.append(gsf_export)

        # Output dir itself
        if out.is_dir():
            search_dirs.append(out)

        for d in search_dirs:
            gsf_files = sorted(d.glob("*.gsf"))
            if not gsf_files:
                gsf_files = sorted(d.rglob("*.gsf"))
            if gsf_files:
                try:
                    from pds_toolkit.gsf_reader import read_gsf
                    self.log_msg.emit(f"  GSF 로드: {gsf_files[0].name}")
                    return read_gsf(str(gsf_files[0]), max_pings=None, load_attitude=False)
                except Exception as e:
                    log.warning("GSF metadata load failed: %s", e)

        return None

    def _load_hvf_metadata(self):
        """Load HVF/vessel file for offset metadata."""
        vessel_file = self._config.vessel_file
        if not vessel_file or not Path(vessel_file).is_file():
            return None

        try:
            from pds_toolkit.hvf_reader import read_hvf
            return read_hvf(vessel_file)
        except Exception as e:
            log.warning("HVF metadata load failed: %s", e)
            return None


def _today() -> str:
    import datetime
    return datetime.date.today().strftime("%Y%m%d")

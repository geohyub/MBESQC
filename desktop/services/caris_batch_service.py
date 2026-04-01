"""MBESQC CarisBatchService -- CARIS Batch Utility (carisbatch) CLI wrapper.

Wraps the carisbatch CLI for automatable HIPS processing steps:
  Import, Georeference (TPU), Filter, Grid creation, Raster export.

Non-automatable steps (Line QC Report wizard, Flier Finder)
are documented but not wrapped -- they require CARIS GUI or HydrOffice.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger(__name__)


def find_carisbatch() -> Optional[str]:
    """Locate carisbatch executable on the system."""
    # 1. PATH search
    found = shutil.which("carisbatch")
    if found:
        return found

    # 2. Common install locations (Windows)
    # Structure: C:\Program Files\CARIS\HIPS and SIPS\<version>\bin\carisbatch.exe
    candidates = []
    for root in [Path(r"C:\Program Files\CARIS"), Path(r"C:\Program Files (x86)\CARIS")]:
        if not root.is_dir():
            continue
        for product_dir in root.iterdir():
            if not product_dir.is_dir() or "HIPS" not in product_dir.name:
                continue
            # Direct location (legacy)
            cand = product_dir / "carisbatch.exe"
            if cand.is_file():
                candidates.append(cand)
            # Versioned subdirectories (v11+): <version>/bin/carisbatch.exe
            for ver_dir in sorted(product_dir.iterdir(), reverse=True):
                if ver_dir.is_dir():
                    cand = ver_dir / "bin" / "carisbatch.exe"
                    if cand.is_file():
                        candidates.insert(0, cand)  # newest version first

    for p in candidates:
        if p.is_file():
            return str(p)

    return None


@dataclass
class CarisBatchConfig:
    """Configuration for a carisbatch pipeline run."""
    hips_file: str = ""           # .hips project file path
    vessel_file: str = ""         # .hvf (v11) or .vessel (v12) config path
    input_files: list[str] = field(default_factory=list)  # raw data (GSF/ALL/XTF)
    vessel_name: str = ""
    day_filter: str = ""          # e.g. "2025-090" for day-of-year filtering
    tide_file: str = ""           # .tid tide file path
    crs: str = "EPSG:4326"

    # Grid parameters
    grid_resolution: float = 1.0  # meters
    gridding_method: str = "SWATH_ANGLE"  # CUBE, CUBE_v2, SHOAL_TRUE, SWATH_ANGLE, UNCERTAINTY

    # Surface bands to render as images (for DQR slides)
    surface_types: list[str] = field(default_factory=lambda: [
        "Depth", "Std_Dev", "TVU", "THU", "Density",
    ])

    # RenderRaster settings
    colour_file: str = "Rainbow.cma"
    enable_shading: bool = True
    shading_azimuth: float = 45.0
    shading_altitude: float = 45.0
    shading_exaggeration: float = 1.0

    # GSF export
    export_gsf: bool = True          # Export GSF after processing

    # Output
    output_dir: str = ""
    carisbatch_path: str = ""


class CarisBatchRunner(QObject):
    """Background worker for carisbatch CLI pipeline execution."""

    progress = Signal(str)         # step description
    step_done = Signal(str, bool)  # step name, success
    finished = Signal(str)         # output directory
    error = Signal(str)

    def __init__(self, config: CarisBatchConfig):
        super().__init__()
        self._config = config
        self._exe = config.carisbatch_path or find_carisbatch()

    def precheck(self) -> tuple[bool, list[str]]:
        """Validate configuration before running the pipeline.

        Returns (ok, errors) — all errors are collected before reporting.
        """
        errors: list[str] = []

        # carisbatch executable
        if not self._exe:
            errors.append(
                "carisbatch를 찾을 수 없습니다. "
                "CARIS HIPS and SIPS가 설치되어 있고 PATH에 등록되어 있는지 확인하세요."
            )

        # HIPS project file (will be auto-created if missing + raw files provided)
        if not self._config.hips_file:
            if self._config.input_files:
                # Will auto-create .hips from output_dir
                pass
            else:
                errors.append("HIPS 파일 또는 원시 데이터가 필요합니다.")
        elif not Path(self._config.hips_file).is_file():
            # Not an error if we'll create it
            if not self._config.input_files:
                errors.append(f"HIPS 파일 없음: {self._config.hips_file}")

        # Vessel file (.hvf for v11, .vessel for v12)
        if self._config.vessel_file:
            vf = Path(self._config.vessel_file)
            if not vf.is_file():
                errors.append(f"Vessel 파일 없음: {self._config.vessel_file}")
            elif vf.suffix.lower() not in (".hvf", ".vessel"):
                errors.append(f"Vessel 파일 형식 미지원 (hvf/vessel만 가능): {vf.suffix}")

        # Input raw data files
        for f in self._config.input_files:
            if not Path(f).is_file():
                errors.append(f"입력 파일 없음: {f}")

        # Output directory writability
        out = Path(self._config.output_dir or ".")
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            errors.append(f"출력 디렉토리 생성 실패: {e}")

        return (len(errors) == 0, errors)

    @Slot()
    def run(self):
        """Execute the full carisbatch pipeline."""
        try:
            ok, errors = self.precheck()
            if not ok:
                self.error.emit("\n".join(errors))
                return

            if self._config.output_dir:
                out = Path(self._config.output_dir)
            elif self._config.hips_file:
                out = Path(self._config.hips_file).parent / "dqr_output"
            else:
                out = Path.cwd() / "dqr_output"
            out.mkdir(parents=True, exist_ok=True)

            steps = [
                ("Create HIPS", self._step_create_hips),
                ("Import", self._step_import),
                ("Georeference", self._step_georeference),
                ("Filter", self._step_filter),
                ("Create Grid", self._step_create_grid),
                ("Render Surfaces", self._step_render_surfaces),
                ("Export Surfaces", self._step_export_surfaces),
                ("Export GSF", self._step_export_gsf),
            ]

            for name, fn in steps:
                self.progress.emit(f"{name} 실행 중...")
                ok = fn(out)
                self.step_done.emit(name, ok)
                if not ok:
                    self.error.emit(f"'{name}' 단계에서 실패했습니다. 로그를 확인하세요.")
                    return

            self.finished.emit(str(out))

        except Exception as e:
            log.exception("CarisBatch pipeline failed")
            self.error.emit(str(e))

    def _run_cmd(self, args: list[str]) -> tuple[bool, str]:
        """Execute carisbatch with args and return (success, output)."""
        cmd = [self._exe, "--run"] + args
        log.info("carisbatch: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                log.error("carisbatch failed (rc=%d): %s", result.returncode, output)
                return False, output
            return True, output
        except subprocess.TimeoutExpired:
            return False, "carisbatch timed out (10 min)"
        except FileNotFoundError:
            return False, f"carisbatch not found at: {self._exe}"

    def _hips_uri(self) -> str:
        """Build HIPS URI with optional vessel/day filter."""
        uri = f"file:///{self._config.hips_file.replace(os.sep, '/')}"
        params = []
        if self._config.vessel_name:
            params.append(f"Vessel={self._config.vessel_name}")
        if self._config.day_filter:
            params.append(f"Day={self._config.day_filter}")
        if params:
            uri += "?" + ";".join(params)
        return uri

    # ── Pipeline Steps ──

    def _step_create_hips(self, out_dir: Path) -> bool:
        """Create HIPS project file if it doesn't exist."""
        hips_path = Path(self._config.hips_file) if self._config.hips_file else None

        if hips_path and hips_path.is_file():
            log.info("HIPS file exists, skipping creation: %s", hips_path)
            return True

        if not self._config.input_files:
            log.info("No raw files and no HIPS file, skipping")
            return True

        # Auto-generate .hips path if not specified
        if not hips_path:
            project_name = Path(out_dir).name or "MBESQC_Project"
            hips_path = out_dir / f"{project_name}.hips"
            self._config.hips_file = str(hips_path)

        args = [
            "CreateHIPSFile",
            "--output-crs", self._config.crs,
            str(hips_path),
        ]
        ok, output = self._run_cmd(args)
        if ok:
            log.info("HIPS project created: %s", hips_path)
        return ok

    # Extension → carisbatch input-format mapping
    _FORMAT_MAP = {
        ".pds": "TELEDYNE_PDS",
        ".s7k": "TELEDYNE_7K",
        ".all": "KONGSBERG",
        ".kmall": "KONGSBERGKMALL",
        ".gsf": "GSF",
        ".xtf": "XTF",
        ".hsx": "HYPACK",
        ".jsf": "EDGETECH_JSF",
        ".fau": "FAU",
    }

    def _detect_input_format(self) -> str:
        """Detect input format from first file extension."""
        for f in self._config.input_files:
            ext = Path(f).suffix.lower()
            if ext in self._FORMAT_MAP:
                return self._FORMAT_MAP[ext]
        return "GSF"  # fallback

    def _step_import(self, out_dir: Path) -> bool:
        """Import raw data to HIPS project."""
        if not self._config.input_files:
            log.info("No input files to import, skipping")
            return True

        fmt = self._detect_input_format()
        args = ["ImportToHIPS", "--input-format", fmt]
        if self._config.vessel_file:
            args += ["--vessel-file", self._config.vessel_file]
        args += ["--input-crs", self._config.crs]

        for f in self._config.input_files:
            args.append(f)

        args.append(self._hips_uri())
        ok, _ = self._run_cmd(args)
        return ok

    def _step_georeference(self, out_dir: Path) -> bool:
        """Georeference bathymetry (includes TPU computation)."""
        args = ["GeoreferenceHIPSBathymetry"]
        if self._config.tide_file and Path(self._config.tide_file).is_file():
            args += ["--tide-file", self._config.tide_file]
        args.append(self._hips_uri())
        ok, _ = self._run_cmd(args)
        return ok

    def _step_filter(self, out_dir: Path) -> bool:
        """Apply depth and attitude filters."""
        uri = self._hips_uri()

        # Filter observed depths
        ok1, _ = self._run_cmd(["FilterObservedDepths", uri])
        # Filter processed depths
        ok2, _ = self._run_cmd(["FilterProcessedDepths", uri])
        # Filter attitude
        ok3, _ = self._run_cmd(["FilterHIPSAttitude", uri])

        return ok1 and ok2 and ok3

    def _step_create_grid(self, out_dir: Path) -> bool:
        """Create HIPS grid surface."""
        output_csar = out_dir / "surface.csar"
        args = [
            "CreateHIPSGrid",
            "--gridding-method", self._config.gridding_method,
            "--resolution", str(self._config.grid_resolution),
            "--output-crs", self._config.crs,
            self._hips_uri(),
            str(output_csar),
        ]
        ok, _ = self._run_cmd(args)
        return ok

    def _step_render_surfaces(self, out_dir: Path) -> bool:
        """Render grid surfaces as coloured images for DQR slides via RenderRaster."""
        all_ok = True
        for band in self._config.surface_types:
            args = [
                "RenderRaster",
                "--input-band", band,
                "--colour-file", self._config.colour_file,
            ]
            if self._config.enable_shading:
                args += [
                    "--enable-shading",
                    "--shading",
                    str(self._config.shading_azimuth),
                    str(self._config.shading_altitude),
                    str(self._config.shading_exaggeration),
                ]
            args.append(self._hips_uri())
            ok, _ = self._run_cmd(args)
            if not ok:
                log.warning("Failed to render surface band: %s", band)
                all_ok = False
        return all_ok

    def _step_export_surfaces(self, out_dir: Path) -> bool:
        """Export rendered surfaces as GeoTIFF images for DQR slides."""
        all_ok = True
        for band in self._config.surface_types:
            output_file = out_dir / f"surface_{band.lower()}.tif"
            args = [
                "ExportRaster",
                "--output-format", "GeoTIFF",
                "--output", str(output_file),
                self._hips_uri(),
            ]
            ok, _ = self._run_cmd(args)
            if not ok:
                log.warning("Failed to export surface: %s", band)
                all_ok = False
        return all_ok


    def _step_export_gsf(self, out_dir: Path) -> bool:
        """Export processed data as GSF for MBESQC QC analysis."""
        if not self._config.export_gsf:
            log.info("GSF export disabled, skipping")
            return True

        gsf_dir = out_dir / "GSF_Export"
        gsf_dir.mkdir(parents=True, exist_ok=True)

        args = [
            "ExportHIPS",
            "--output-format", "GSF",
            self._hips_uri(),
            str(gsf_dir),
        ]
        ok, _ = self._run_cmd(args)
        if ok:
            count = len(list(gsf_dir.glob("*.gsf")))
            log.info("GSF exported: %d files to %s", count, gsf_dir)
        return ok


# ── Convenience: check if CARIS is available ──

def is_caris_available() -> bool:
    """Check if carisbatch CLI is accessible."""
    return find_carisbatch() is not None

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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger(__name__)


def _redact_path_for_log(value: str | os.PathLike[str] | None) -> str:
    """Return a log-safe representation for an absolute local path or file URI."""
    if value is None:
        return ""

    text = os.fspath(value)
    if text.startswith("file:///"):
        return "file:///<redacted>"

    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"):
        return "<redacted:path>"

    try:
        path = Path(text)
    except TypeError:
        return text

    if path.is_absolute():
        return "<redacted:path>"
    return text


def _format_cmd_for_log(cmd: list[str]) -> str:
    return " ".join(_redact_path_for_log(token) for token in cmd)


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
    svp_dir: str = ""             # SVP file or directory path
    crs: str = "EPSG:32652"          # UTM 52N (default for South Korea)

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
                ("Filter", self._step_filter),
                ("Georeference", self._step_georeference),
                ("Create Depth Grid", self._step_create_grid),
                ("Create TPU Grid", self._step_create_tpu_grid),
                ("Render Surfaces", self._step_render_surfaces),
                ("Export Surfaces", self._step_export_surfaces),
                ("Export GSF", self._step_export_gsf),
            ]

            for name, fn in steps:
                self.progress.emit(f"{name} 실행 중...")
                ok = fn(out)
                self.step_done.emit(name, ok)
                if not ok:
                    # Include last _run_cmd output in error message
                    last_out = getattr(self, '_last_cmd_output', '')
                    self.error.emit(
                        f"'{name}' 단계에서 실패했습니다.\n{last_out[:500]}"
                    )
                    return

            self.finished.emit(str(out))

        except Exception as e:
            import traceback
            log.exception("CarisBatch pipeline failed")
            self.error.emit(f"{e}\n{traceback.format_exc()}")

    def _run_cmd(self, args: list[str]) -> tuple[bool, str]:
        """Execute carisbatch with args and return (success, output)."""
        if not self._exe:
            self._last_cmd_output = "carisbatch executable not found"
            return False, self._last_cmd_output
        cmd = [self._exe, "--run"] + args
        log.info("carisbatch: %s", _format_cmd_for_log(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,  # 30 min — Grid creation can be slow
            )
            output = (result.stdout or "") + (result.stderr or "")
            self._last_cmd_output = output
            if result.returncode != 0:
                log.error("carisbatch failed (rc=%d): %s", result.returncode, output)
                return False, output
            return True, output
        except subprocess.TimeoutExpired:
            return False, "carisbatch timed out (30 min)"
        except FileNotFoundError:
            return False, f"carisbatch not found at: {self._exe}"

    def _hips_uri(self) -> str:
        """Build HIPS URI with optional vessel/day filter."""
        hips = self._config.hips_file or ""
        uri = f"file:///{hips.replace(os.sep, '/')}"
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
            log.info("HIPS file exists, skipping creation: %s", _redact_path_for_log(hips_path))
            return True

        if not self._config.input_files:
            log.info("No raw files and no HIPS file, skipping")
            return True

        # Auto-generate .hips path if not specified
        # CARIS requires: folder name == hips file name (e.g., ProjectName/ProjectName.hips)
        if not hips_path:
            project_name = Path(out_dir).name or "MBESQC_Project"
            hips_dir = out_dir / project_name
            hips_dir.mkdir(parents=True, exist_ok=True)
            hips_path = hips_dir / f"{project_name}.hips"
            self._config.hips_file = str(hips_path)

        args = [
            "CreateHIPSFile",
            str(hips_path),
        ]
        log.info("exe=%s, hips=%s", _redact_path_for_log(self._exe), _redact_path_for_log(hips_path))
        ok, output = self._run_cmd(args)
        if ok:
            log.info("HIPS project created: %s", _redact_path_for_log(hips_path))
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
        """Georeference bathymetry (includes SVC + TPU computation)."""
        args = ["GeoreferenceHIPSBathymetry"]

        # SVP: apply SVC if SVP files available
        svp_path = self._config.svp_dir
        if svp_path:
            p = Path(svp_path)
            if p.is_dir() or p.is_file():
                args += ["--compute-svc", "--svp", str(p)]

        # TPU: compute THU/TVU for IHO S-44 compliance
        args.append("--compute-tpu")

        if self._config.tide_file and Path(self._config.tide_file).is_file():
            args += ["--tide-file", self._config.tide_file]
        args.append(self._hips_uri())
        ok, _ = self._run_cmd(args)
        return ok

    def _step_filter(self, out_dir: Path) -> bool:
        """Apply depth and attitude filters."""
        uri = self._hips_uri()

        # Filter observed depths (--bathymetry-type required in v12)
        ok1, out1 = self._run_cmd(["FilterObservedDepths", "--bathymetry-type", "SWATH", uri])
        if not ok1:
            log.warning("FilterObservedDepths failed: %s", out1[:200])

        # Filter processed depths (optional, may fail if no surface exists yet)
        ok2, out2 = self._run_cmd(["FilterProcessedDepths", uri])
        if not ok2:
            log.warning("FilterProcessedDepths skipped: %s", out2[:200])

        # Filter attitude (--sensor-type required in v12)
        ok3, out3 = self._run_cmd(["FilterHIPSAttitude", "--sensor-type", "HEAVE", uri])
        if not ok3:
            log.warning("FilterHIPSAttitude skipped: %s", out3[:200])

        # Don't fail pipeline if only optional filters fail
        return ok1

    def _step_create_grid(self, out_dir: Path) -> bool:
        """Create Depth grid surface (Swath Angle method).

        Per GeoView MBES QC Manual v2.00:
          Gridding Method: Swath Angle
          Compute Band: Density, Std_Dev
          Maximum Footprint: 1
        """
        output_csar = out_dir / "surface.csar"
        args = [
            "CreateHIPSGrid",
            "--gridding-method", self._config.gridding_method,
            "--resolution", f"{self._config.grid_resolution} m",
            "--output-crs", self._config.crs,
            "--include-flag", "ACCEPTED",
            "--include-flag", "EXAMINED",
            "--include-flag", "OUTSTANDING",
            "--compute-band", "DENSITY",
            "--compute-band", "STD_DEV",
            self._hips_uri(),
            str(output_csar),
        ]
        ok, out = self._run_cmd(args)
        if not ok:
            log.error("CreateHIPSGrid (Depth) failed: %s", out[:500])
        return ok

    def _step_create_tpu_grid(self, out_dir: Path) -> bool:
        """Create TPU grid surface (Shoalest Depth true Position method).

        Per GeoView MBES QC Manual v2.00:
          Gridding Method: Shoalest Depth true Position
          Bands: Depth_TPU (=TVU), Position_TPU (=THU) — auto-generated
        """
        output_csar = out_dir / "tpu_surface.csar"
        args = [
            "CreateHIPSGrid",
            "--gridding-method", "SHOAL_TRUE",
            "--resolution", f"{self._config.grid_resolution} m",
            "--output-crs", self._config.crs,
            "--include-flag", "ACCEPTED",
            "--include-flag", "EXAMINED",
            "--include-flag", "OUTSTANDING",
            self._hips_uri(),
            str(output_csar),
        ]
        ok, out = self._run_cmd(args)
        if not ok:
            log.warning("CreateHIPSGrid (TPU) failed: %s", out[:500])
        return True  # TPU grid failure shouldn't block pipeline

    def _step_render_surfaces(self, out_dir: Path) -> bool:
        """Render grid surfaces as coloured images for DQR slides via RenderRaster."""
        input_csar = out_dir / "surface.csar"
        if not input_csar.is_file():
            log.warning("No surface.csar found, skipping render")
            return False

        all_ok = True
        for band in self._config.surface_types:
            rendered_csar = out_dir / f"rendered_{band.lower()}.csar"
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
            args += [str(input_csar), str(rendered_csar)]
            ok, out = self._run_cmd(args)
            if not ok:
                if "does not exist" in out:
                    log.info("Band %s not available in surface, skipping", band)
                else:
                    log.warning("Failed to render band %s: %s", band, out[:200])
                    all_ok = False
        return all_ok

    def _step_export_surfaces(self, out_dir: Path) -> bool:
        """Export surfaces as GeoTIFF images for DQR slides.

        Exports two versions per band:
          - surface_<band>.tif  — rendered RGBA image (for PPT display)
          - raw_<band>.tif      — raw values (for colorbar range)
        """
        input_csar = out_dir / "surface.csar"
        all_ok = True

        for band in self._config.surface_types:
            # 1. Export rendered RGBA image
            rendered = out_dir / f"rendered_{band.lower()}.csar"
            if rendered.is_file():
                output_img = out_dir / f"surface_{band.lower()}.tif"
                args = [
                    "ExportRaster", "--output-format", "GeoTIFF",
                    str(rendered), str(output_img),
                ]
                ok, out = self._run_cmd(args)
                if not ok:
                    log.warning("Failed to export rendered %s: %s", band, out[:200])

        # 2. Export raw depth surface (1-band float GeoTIFF)
        # Named surface_depth.tif so dqr_ppt.py finds it for hybrid rendering
        if input_csar.is_file():
            depth_tif = out_dir / "surface_depth.tif"
            args = [
                "ExportRaster", "--output-format", "GeoTIFF",
                str(input_csar), str(depth_tif),
            ]
            ok, out = self._run_cmd(args)
            if ok:
                log.info("Depth surface exported: %s", _redact_path_for_log(depth_tif))
            else:
                log.warning("Failed to export depth surface: %s", out[:200])

        # 3. Export Depth band statistics (for DQR colorbar)
        if input_csar.is_file():
            bands_txt = out_dir / "all_bands.txt"
            band_names = ["Depth", "Density"]
            include_args = []
            for b in band_names:
                unit = "DEFAULT" if b == "Density" else "m"
                include_args += ["--include", "BAND", b, "3", unit]
            args = ["ExportCoverageToASCII"] + include_args + [
                str(input_csar), str(bands_txt),
            ]
            self._run_cmd(args)

        # 4. Export TPU surface bands (TVU=Depth_TPU, THU=Position_TPU)
        tpu_csar = out_dir / "tpu_surface.csar"
        if tpu_csar.is_file():
            # Export TPU as GeoTIFF (default band = Depth from SHOAL_TRUE)
            tpu_tif = out_dir / "surface_tpu.tif"
            args = [
                "ExportRaster", "--output-format", "GeoTIFF",
                str(tpu_csar), str(tpu_tif),
            ]
            ok, out = self._run_cmd(args)
            if ok:
                log.info("TPU surface exported: %s", _redact_path_for_log(tpu_tif))

            # Export TPU band values (Depth_TPU=TVU, Position_TPU=THU)
            tpu_txt = out_dir / "tpu_bands.txt"
            args = [
                "ExportCoverageToASCII",
                "--include", "BAND", "Depth", "3", "m",
                "--include", "BAND", "Depth_TPU", "3", "m",
                "--include", "BAND", "Position_TPU", "3", "m",
                str(tpu_csar), str(tpu_txt),
            ]
            ok_tpu, _ = self._run_cmd(args)
            if ok_tpu:
                log.info("TPU bands exported: %s", _redact_path_for_log(tpu_txt))

        return True


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
            log.info("GSF exported: %d files to %s", count, _redact_path_for_log(gsf_dir))
        return ok


# ── Convenience: check if CARIS is available ──

def is_caris_available() -> bool:
    """Check if carisbatch CLI is accessible."""
    return find_carisbatch() is not None

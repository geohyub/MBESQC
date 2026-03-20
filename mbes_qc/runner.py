"""MBES QC Runner - Execute all QC checks and generate reports.

Orchestrates: File QC, Vessel QC, Offset QC, Motion QC, SVP QC,
Coverage QC, Cross-line QC, Surface Generation, and Report output.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit import read_gsf, read_hvf, read_pds_header

from .coverage_qc import CoverageQcResult, run_coverage_qc
from .crossline_qc import CrosslineResult, run_crossline_qc
from .file_qc import FileQcResult, run_file_qc
from .motion_qc import MotionQcResult, run_motion_qc
from .offset_qc import OffsetQcResult, run_offset_qc
from .report import generate_excel_report, generate_word_report, print_terminal_report
from .surface_builder import SurfaceResult, build_surfaces_from_gsf
from .svp_qc import SvpQcResult, run_svp_qc
from .vessel_qc import VesselQcResult, run_vessel_qc


@dataclass
class FullQcResult:
    """Aggregated results from all QC modules."""

    file_qc: FileQcResult | None = None
    vessel_qc: VesselQcResult | None = None
    offset_qc: OffsetQcResult | None = None
    motion_qc: MotionQcResult | None = None
    svp_qc: SvpQcResult | None = None
    coverage_qc: CoverageQcResult | None = None
    crossline_qc: CrosslineResult | None = None
    surface: SurfaceResult | None = None
    elapsed_sec: float = 0.0

    def as_dict(self) -> dict:
        """Convert to ordered dict for report generation."""
        d = {}
        if self.file_qc: d["A. File Integrity"] = self.file_qc
        if self.vessel_qc: d["B. Vessel/Offset Config"] = self.vessel_qc
        if self.offset_qc: d["C. Offset Verification"] = self.offset_qc
        if self.motion_qc: d["D. Motion Verification"] = self.motion_qc
        if self.svp_qc: d["E. SVP Verification"] = self.svp_qc
        if self.coverage_qc: d["F. Coverage Analysis"] = self.coverage_qc
        if self.crossline_qc: d["G. Cross-line Analysis"] = self.crossline_qc
        return d


def run_full_qc(
    gsf_paths: list[str | Path] | str | Path | None = None,
    gsf_dir: str | Path | None = None,
    pds_path: str | Path | None = None,
    pds_dir: str | Path | None = None,
    hvf_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    max_pings: int | None = None,
    cell_size: float = 5.0,
    iho_order: str = "1a",
    generate_surfaces: bool = True,
    generate_reports: bool = True,
) -> FullQcResult:
    """Execute complete MBES QC pipeline.

    Args:
        gsf_paths: Single GSF file or list of GSF files.
        gsf_dir: Directory containing GSF files (alternative to gsf_paths).
        pds_path: Single PDS file for vessel config check.
        pds_dir: Directory containing PDS files.
        hvf_path: HVF vessel config file.
        output_dir: Output directory for surfaces and reports.
        max_pings: Limit pings per file.
        cell_size: Grid cell size in metres.
        iho_order: IHO S-44 order for TPU check.
        generate_surfaces: Generate GeoTIFF surfaces.
        generate_reports: Generate Excel/Word reports.
    """
    t0 = time.time()
    result = FullQcResult()
    out = Path(output_dir) if output_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    # Resolve GSF file list
    gsf_file_list = []
    if gsf_dir:
        gsf_file_list = sorted(Path(gsf_dir).glob("*.gsf"))
    elif gsf_paths:
        if isinstance(gsf_paths, (str, Path)):
            gsf_file_list = [Path(gsf_paths)]
        else:
            gsf_file_list = [Path(p) for p in gsf_paths]

    pds_file_list = []
    if pds_dir:
        pds_file_list = sorted(Path(pds_dir).glob("*.pds"))
    elif pds_path:
        pds_file_list = [Path(pds_path)]

    # ── A. File QC ──────────────────────────────────────────
    _header("A. File Integrity QC")
    result.file_qc = run_file_qc(
        gsf_files=[str(f) for f in gsf_file_list],
        pds_files=[str(f) for f in pds_file_list],
    )
    _print_items(result.file_qc.items)

    # ── B. Vessel QC ────────────────────────────────────────
    if pds_file_list:
        _header("B. Vessel/Offset Config QC")
        result.vessel_qc = run_vessel_qc(pds_file_list[0], hvf_path)
        _print_items(result.vessel_qc.items)

    # ── Load GSF data ───────────────────────────────────────
    _header("Loading GSF data")
    gsf_objects = []
    for f in gsf_file_list:
        gsf = read_gsf(f, max_pings=max_pings, load_attitude=True, load_svp=True)
        gsf_objects.append(gsf)
        print(f"  {f.name}: {gsf.num_pings} pings, {gsf.num_attitude} att")

    if not gsf_objects:
        print("  No GSF data loaded.")
        result.elapsed_sec = time.time() - t0
        return result

    # Use first file for single-file analyses
    gsf_main = gsf_objects[0]

    # ── C. Offset QC ────────────────────────────────────────
    _header("C. Offset Verification")
    hvf = read_hvf(hvf_path) if hvf_path else None
    result.offset_qc = run_offset_qc(gsf_main, hvf)
    _print_offset(result.offset_qc)

    # ── D. Motion QC ────────────────────────────────────────
    _header("D. Motion Verification")
    result.motion_qc = run_motion_qc(gsf_main)
    _print_motion(result.motion_qc)

    # ── E. SVP QC ───────────────────────────────────────────
    _header("E. SVP Verification")
    pds_svp = False
    if result.vessel_qc:
        pds_svp = result.vessel_qc.pds_apply_svp
    result.svp_qc = run_svp_qc(gsf_main, pds_svp)
    _print_items_dict(result.svp_qc.items)

    # ── F. Coverage QC ──────────────────────────────────────
    if len(gsf_objects) >= 2:
        _header("F. Coverage Analysis")
        result.coverage_qc = run_coverage_qc(gsf_objects)
        print(f"  Lines: {result.coverage_qc.total_lines}, "
              f"Length: {result.coverage_qc.total_length_km:.1f} km")
        _print_items_dict(result.coverage_qc.items)

    # ── G. Cross-line QC ────────────────────────────────────
    if len(gsf_objects) >= 2:
        _header("G. Cross-line Analysis")
        result.crossline_qc = run_crossline_qc(gsf_objects, cell_size, iho_order)
        _print_items_dict(result.crossline_qc.items)

    # ── Surface Generation ──────────────────────────────────
    if generate_surfaces and gsf_main.num_pings > 0:
        _header("Surface Generation")
        surf_dir = out / "surfaces" if out else None
        result.surface = build_surfaces_from_gsf(gsf_main, cell_size, surf_dir)
        _print_surface(result.surface)

    # ── Exports (Contour, Trackline, Allsounding, TFW) ────
    if generate_reports and out:
        from .export import (
            export_contour_dxf, export_contour_csv,
            export_tracklines_csv, export_tracklines_dxf,
            export_allsoundings, generate_all_tfw,
        )
        from .dqr_ppt import generate_dqr_ppt

        _header("Exports")
        export_dir = out / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        # Contour
        if result.surface and result.surface.dtm is not None:
            n = export_contour_dxf(result.surface, export_dir / "Contour.dxf", interval=1.0)
            print(f"  Contour DXF: {n} segments")
            n = export_contour_csv(result.surface, export_dir / "Contour.csv", interval=1.0)
            print(f"  Contour CSV: {n} segments")

        # Tracklines
        if gsf_objects:
            n = export_tracklines_csv(gsf_objects, export_dir / "Tracklines.csv")
            print(f"  Tracklines CSV: {n} points")
            n = export_tracklines_dxf(gsf_objects, export_dir / "Tracklines.dxf")
            print(f"  Tracklines DXF: {n} lines")

        # Allsoundings (first file only for speed)
        if gsf_objects:
            n = export_allsoundings(gsf_objects[:1], export_dir / "Allsoundings.csv")
            print(f"  Allsoundings: {n} points")

        # TFW files
        if result.surface:
            surf_dir = out / "surfaces"
            if surf_dir.exists():
                n = generate_all_tfw(result.surface, surf_dir)
                print(f"  TFW files: {n}")

    # ── Reports ─────────────────────────────────────────────
    result.elapsed_sec = time.time() - t0
    qc_dict = result.as_dict()

    # Terminal
    print_terminal_report(qc_dict)

    if generate_reports and out:
        # Excel
        excel_path = out / "QC_Report.xlsx"
        generate_excel_report(qc_dict, excel_path)
        print(f"  Excel: {excel_path}")

        # Word
        word_path = out / "QC_Report.docx"
        pds_meta = read_pds_header(pds_file_list[0]) if pds_file_list else None
        project = pds_meta.project_name if pds_meta else ""
        vessel = pds_meta.vessel_name if pds_meta else ""
        generate_word_report(qc_dict, word_path, project, vessel)
        print(f"  Word:  {word_path}")

        # DQR PPT
        ppt_path = out / "DQR_MBES.pptx"
        total_km = result.coverage_qc.total_length_km if result.coverage_qc else 0.0
        generate_dqr_ppt(
            ppt_path,
            pds_meta=pds_meta,
            gsf_main=gsf_main,
            hvf=read_hvf(hvf_path) if hvf_path else None,
            surface_dir=out / "surfaces" if out else None,
            total_line_km=total_km,
            qc_results=qc_dict,
        )
        print(f"  DQR PPT: {ppt_path}")

    print(f"\n  Elapsed: {result.elapsed_sec:.1f}s")
    return result


# ── Output helpers ──────────────────────────────────────────────


def _header(title: str) -> None:
    print(f"\n{'~'*60}\n  {title}\n{'~'*60}")


def _vc(v: str) -> str:
    c = {"PASS": "\033[92m", "WARNING": "\033[93m", "FAIL": "\033[91m"}.get(v, "")
    return f"{c}{v}\033[0m"


def _print_items(items) -> None:
    for i in items:
        s = getattr(i, "status", "N/A")
        n = getattr(i, "name", "")
        d = getattr(i, "detail", "")
        print(f"  [{_vc(s)}] {n}: {d}")


def _print_items_dict(items: list[dict]) -> None:
    for i in items:
        print(f"  [{_vc(i.get('status', 'N/A'))}] {i.get('name', '')}: {i.get('detail', '')}")


def _print_offset(r: OffsetQcResult) -> None:
    print(f"  Roll Bias:  {r.roll_bias_deg:+.4f} +/- {r.roll_bias_std:.4f} deg "
          f"({r.roll_num_pings} pings) [{_vc(r.roll_verdict)}]")
    print(f"  Pitch Bias: {r.pitch_bias_deg:+.4f} +/- {r.pitch_bias_std:.4f} deg "
          f"({r.pitch_num_pairs} pairs)  [{_vc(r.pitch_verdict)}]")
    if r.hvf_offsets:
        for off in r.hvf_offsets:
            print(f"  HVF: {off['name']}: R={off['roll']:+.3f} P={off['pitch']:+.3f} H={off['heading']:+.3f}")


def _print_motion(r: MotionQcResult) -> None:
    print(f"  Samples: {r.total_samples:,} ({r.time_span_sec:.0f}s, {r.sample_rate_hz:.1f}Hz)")
    for ax in [r.roll, r.pitch, r.heave, r.heading]:
        print(f"  {ax.name:>8s}: mean={ax.mean:+8.4f}{ax.unit} std={ax.std:7.4f} "
              f"spk={ax.num_spikes}({ax.spike_rate_pct:.2f}%) [{_vc(ax.verdict)}]")
    print(f"  Gaps: {r.num_gaps} (max {r.max_gap_sec:.3f}s) [{_vc(r.gap_verdict)}]")


def _print_surface(r: SurfaceResult) -> None:
    print(f"  Points: {r.num_points:,}")
    print(f"  Grid: {r.nx}x{r.ny} @ {r.cell_size}m")
    if r.dtm is not None:
        v = r.dtm[~np.isnan(r.dtm)]
        if len(v) > 0:
            print(f"  DTM: {v.min():.2f}~{v.max():.2f}m")

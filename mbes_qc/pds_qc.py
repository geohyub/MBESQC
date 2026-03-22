"""PDS-first QC — Run complete QC from a single PDS file.

Usage:
    from mbes_qc.pds_qc import run_pds_qc
    result = run_pds_qc("path/to/file.pds")

Automatically:
  1. Reads PDS text header (vessel config, offsets, SVP, calibration)
  2. Reads PDS binary data (pings, navigation, attitude, tide)
  3. Looks for matching GSF/HVF/S7K files in the same directory
  4. Runs pre-processing validation
  5. Runs post-processing QC (motion, offset, coverage)
  6. Generates reports and surfaces
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit import read_pds_header, read_pds_binary, read_gsf, read_hvf
from pds_toolkit.pds_binary import PdsBinaryData
from pds_toolkit.swath import SwathLine, pds_to_swath, gsf_to_swath, load_swath

from .preprocess_validator import PreProcessResult, validate_preprocess, print_validation_report


@dataclass
class PdsQcResult:
    """Complete QC result from PDS-first workflow."""
    filepath: str = ""
    vessel_name: str = ""
    preprocess: PreProcessResult | None = None
    pds_data: PdsBinaryData | None = None
    swath_lines: list[SwathLine] = field(default_factory=list)
    elapsed_sec: float = 0.0

    # Summary
    total_pings: int = 0
    total_beams: int = 0
    depth_range: tuple[float, float] = (0.0, 0.0)
    lat_range: tuple[float, float] = (0.0, 0.0)
    lon_range: tuple[float, float] = (0.0, 0.0)
    nav_records: int = 0
    attitude_records: int = 0

    @property
    def overall(self) -> str:
        if self.preprocess:
            return self.preprocess.overall
        return "N/A"


def _find_companion_files(pds_path: Path) -> dict:
    """Find related GSF, HVF, S7K files near the PDS file."""
    result = {'gsf': [], 'hvf': [], 's7k': [], 'pds': []}

    # Same directory
    pds_dir = pds_path.parent
    stem = pds_path.stem

    # Extract timestamp from PDS filename (e.g., EDFR-20251003-220225)
    # Common pattern: *-YYYYMMDD-HHMMSS.pds
    parts = stem.split('-')
    date_part = None
    for p in parts:
        if len(p) == 8 and p.isdigit():
            date_part = p
            break

    # Search same directory and parent
    search_dirs = [pds_dir]
    if pds_dir.parent != pds_dir:
        search_dirs.append(pds_dir.parent)
        # Common project structures
        for subdir in ['GSF', 'MBES', 'HVF', 'Vessels', 'S7K']:
            candidate = pds_dir.parent / subdir
            if candidate.exists():
                search_dirs.append(candidate)

    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext == '.gsf':
                # Match by date if available
                if date_part and date_part in f.stem:
                    result['gsf'].insert(0, f)  # prioritize matching date
                else:
                    result['gsf'].append(f)
            elif ext == '.hvf':
                result['hvf'].append(f)
            elif ext == '.s7k':
                if date_part and date_part in f.stem:
                    result['s7k'].insert(0, f)
                else:
                    result['s7k'].append(f)
            elif ext == '.pds' and f != pds_path:
                result['pds'].append(f)

    return result


def run_pds_qc(
    pds_path: str | Path,
    output_dir: str | Path | None = None,
    max_pings: int | None = None,
    offsetmanager_db: str | Path | None = None,
    lat_range: tuple[float, float] = (-90.0, 90.0),
    lon_range: tuple[float, float] = (-180.0, 180.0),
    use_companion_gsf: bool = True,
    cell_size: float = 5.0,
    generate_reports: bool = True,
) -> PdsQcResult:
    """Run complete QC from a single PDS file.

    Automatically finds companion GSF/HVF files in the same directory.
    Falls back to PDS-only analysis if no GSF is available.

    Args:
        pds_path: Path to any .pds file.
        output_dir: Output directory (default: creates next to PDS file).
        max_pings: Limit pings per file (None = all).
        offsetmanager_db: Path to OffsetManager SQLite DB.
        lat_range: Latitude range for navigation search.
        lon_range: Longitude range for navigation search.
        use_companion_gsf: If True, look for matching GSF files.
        cell_size: Grid cell size in metres.
        generate_reports: Generate Excel/Word/PPT reports.
    """
    t0 = time.time()
    pds_path = Path(pds_path)
    result = PdsQcResult(filepath=str(pds_path))

    if output_dir is None:
        output_dir = pds_path.parent / f"QC_{pds_path.stem}"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── 1. Pre-Processing Validation ──────────────────────
    _header("1. Pre-Processing Validation")
    result.preprocess = validate_preprocess(
        pds_path,
        offsetmanager_db=str(offsetmanager_db) if offsetmanager_db else None,
        check_navigation=True,
        lat_range=lat_range,
        lon_range=lon_range,
    )
    result.vessel_name = result.preprocess.vessel_name
    print_validation_report(result.preprocess)

    # ── 2. Read PDS Binary Data ───────────────────────────
    _header("2. PDS Binary Data")
    result.pds_data = read_pds_binary(
        pds_path,
        max_pings=max_pings,
        lat_range=lat_range,
        lon_range=lon_range,
    )
    pds = result.pds_data

    print(f"  Pings: {pds.num_pings}")
    print(f"  Navigation: {pds.num_nav_records} records")
    print(f"  Attitude: {len(pds.attitude)} records")
    print(f"  Tide: {pds.num_tide_records} records")

    if pds.pings:
        all_depths = []
        for p in pds.pings:
            if len(p.depth) > 0:
                d = np.abs(p.depth[p.depth != 0])
                if len(d) > 0:
                    all_depths.extend([float(d.min()), float(d.max())])
        if all_depths:
            result.depth_range = (min(all_depths), max(all_depths))
            print(f"  Depth: {result.depth_range[0]:.1f} ~ {result.depth_range[1]:.1f} m")

    result.total_pings = pds.num_pings
    result.nav_records = pds.num_nav_records
    result.attitude_records = len(pds.attitude)
    result.lat_range = pds.lat_range
    result.lon_range = pds.lon_range

    # ── 3. Find Companion Files ───────────────────────────
    companions = _find_companion_files(pds_path)
    _header("3. Companion Files")
    print(f"  GSF: {len(companions['gsf'])} files")
    print(f"  HVF: {len(companions['hvf'])} files")
    print(f"  S7K: {len(companions['s7k'])} files")
    print(f"  PDS: {len(companions['pds'])} other PDS files")

    # ── 4. Build SwathLines ───────────────────────────────
    _header("4. Loading Swath Data")

    # Primary: use GSF if available (more complete data)
    if use_companion_gsf and companions['gsf']:
        for gsf_path in companions['gsf'][:10]:  # max 10 lines
            try:
                gsf = read_gsf(str(gsf_path), max_pings=max_pings,
                               load_attitude=True, load_svp=True)
                swath = gsf_to_swath(gsf)
                result.swath_lines.append(swath)
                print(f"  GSF: {gsf_path.name} ({swath.num_pings} pings)")
            except Exception as e:
                print(f"  GSF SKIP: {gsf_path.name} ({e})")

    # Fallback: use PDS binary pings
    if not result.swath_lines and pds.pings:
        swath = pds_to_swath(pds)
        result.swath_lines.append(swath)
        print(f"  PDS: {pds_path.name} ({swath.num_pings} pings)")

    total_beams = 0
    for sw in result.swath_lines:
        for p in sw.pings:
            total_beams += p.num_beams
    result.total_beams = total_beams
    print(f"  Total: {len(result.swath_lines)} lines, {sum(s.num_pings for s in result.swath_lines)} pings, {total_beams:,} beams")

    # ── 5. Post-Processing QC (if GSF available) ─────────
    if result.swath_lines and companions['gsf']:
        gsf_objects = []
        for gsf_path in companions['gsf'][:10]:
            try:
                gsf = read_gsf(str(gsf_path), max_pings=max_pings,
                               load_attitude=True, load_svp=True)
                gsf_objects.append(gsf)
            except:
                pass

        if gsf_objects:
            gsf_main = gsf_objects[0]

            # Motion QC
            _header("5a. Motion QC")
            from .motion_qc import run_motion_qc
            motion = run_motion_qc(gsf_main)
            print(f"  Samples: {motion.total_samples:,} ({motion.time_span_sec:.0f}s)")
            for ax in [motion.roll, motion.pitch, motion.heave, motion.heading]:
                print(f"  {ax.name:>8s}: mean={ax.mean:+8.4f}{ax.unit} std={ax.std:7.4f} [{ax.verdict}]")

            # Offset QC
            _header("5b. Offset QC")
            from .offset_qc import run_offset_qc
            hvf = read_hvf(str(companions['hvf'][0])) if companions['hvf'] else None
            offset = run_offset_qc(gsf_main, hvf)
            print(f"  Roll Bias: {offset.roll_bias_deg:+.4f} +/- {offset.roll_bias_std:.4f} deg [{offset.roll_verdict}]")

            # Coverage
            if len(gsf_objects) >= 2:
                _header("5c. Coverage")
                from .coverage_qc import run_coverage_qc
                cov = run_coverage_qc(gsf_objects)
                print(f"  Lines: {cov.total_lines}, Length: {cov.total_length_km:.1f} km")

            # Surface generation
            if generate_reports:
                _header("5d. Surface Generation")
                from .surface_builder import build_surfaces_from_gsf
                surf_dir = out / "surfaces"
                surface = build_surfaces_from_gsf(gsf_main, cell_size, surf_dir)
                if surface.dtm is not None:
                    v = surface.dtm[~np.isnan(surface.dtm)]
                    if len(v) > 0:
                        print(f"  DTM: {v.min():.2f} ~ {v.max():.2f} m ({surface.nx}x{surface.ny} grid)")

    # ── Summary ───────────────────────────────────────────
    result.elapsed_sec = time.time() - t0
    _header("SUMMARY")
    print(f"  File: {pds_path.name}")
    print(f"  Vessel: {result.vessel_name}")
    print(f"  Pre-Processing: {result.preprocess.overall if result.preprocess else 'N/A'}")
    print(f"  Pings: {result.total_pings}, Beams: {result.total_beams:,}")
    print(f"  Depth: {result.depth_range[0]:.1f} ~ {result.depth_range[1]:.1f} m")
    print(f"  Nav: {result.nav_records}, Att: {result.attitude_records}")
    print(f"  Time: {result.elapsed_sec:.1f}s")

    return result


def _header(title: str) -> None:
    print(f"\n{'~' * 60}\n  {title}\n{'~' * 60}")

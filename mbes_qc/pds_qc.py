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


def _extract_timestamp_key(filename: str) -> str | None:
    """Extract YYYYMMDD-HHMMSS or similar from PDS/GSF filename for matching."""
    stem = Path(filename).stem
    parts = stem.replace('_', '-').split('-')
    # Find 8-digit date part
    for i, p in enumerate(parts):
        if len(p) == 8 and p.isdigit():
            # Also grab the next part if it looks like time (6 digits)
            if i + 1 < len(parts) and len(parts[i + 1]) == 6 and parts[i + 1].isdigit():
                return f"{p}-{parts[i + 1]}"
            return p
    return None


def _find_companion_files(
    pds_path: Path,
    gsf_dir: Path | None = None,
    hvf_dir: Path | None = None,
    s7k_dir: Path | None = None,
) -> dict:
    """Find related GSF, HVF, S7K files by searching specified directories.

    Matching priority:
    1. Same timestamp (YYYYMMDD-HHMMSS) → exact line match
    2. Same date (YYYYMMDD) → same survey day
    3. Same directory → any file of that type
    """
    result = {'gsf': [], 'hvf': [], 's7k': [], 'pds': []}

    pds_key = _extract_timestamp_key(pds_path.name)
    pds_date = pds_key[:8] if pds_key else None

    # Build search directories
    search_dirs = {
        'gsf': [],
        'hvf': [],
        's7k': [],
        'pds': [pds_path.parent],
    }

    # User-specified directories take priority
    if gsf_dir:
        search_dirs['gsf'].insert(0, Path(gsf_dir))
    if hvf_dir:
        search_dirs['hvf'].insert(0, Path(hvf_dir))
    if s7k_dir:
        search_dirs['s7k'].insert(0, Path(s7k_dir))

    # Auto-discover: same directory + parent + common subdirectories
    auto_dirs = [pds_path.parent]
    if pds_path.parent.parent != pds_path.parent:
        auto_dirs.append(pds_path.parent.parent)
        for subdir_name in ['GSF', 'MBES', 'HVF', 'Vessels', 'S7K', 'RAW',
                            'EDF_GSF', 'EDF_VESSELS', 'EDF_RAW']:
            candidate = pds_path.parent.parent / subdir_name
            if candidate.exists():
                auto_dirs.append(candidate)

    for fmt in ['gsf', 'hvf', 's7k']:
        for d in auto_dirs:
            if d not in search_dirs[fmt]:
                search_dirs[fmt].append(d)

    # Scan each directory
    ext_map = {
        '.gsf': 'gsf', '.hvf': 'hvf', '.s7k': 's7k', '.pds': 'pds',
        '.xtf': 'xtf', '.fau': 'fau', '.gpt': 'gpt',
        '.csv': 'csv', '.tiff': 'tiff', '.tif': 'tiff',
        '.dxf': 'dxf', '.nsf': 'nsf',
    }

    # Extend result dict for all formats
    for fmt in ext_map.values():
        result.setdefault(fmt, [])

    for fmt, dirs in search_dirs.items():
        for d in dirs:
            if not d.exists():
                continue
            try:
                for f in sorted(d.iterdir()):
                    if not f.is_file():
                        continue
                    ext = f.suffix.lower()
                    target_fmt = ext_map.get(ext)
                    if target_fmt != fmt:
                        continue
                    if fmt == 'pds' and f == pds_path:
                        continue

                    file_key = _extract_timestamp_key(f.name)

                    # Priority scoring
                    if pds_key and file_key == pds_key:
                        result[fmt].insert(0, f)  # exact timestamp match
                    elif pds_date and file_key and file_key.startswith(pds_date):
                        # Same date — insert after exact matches but before others
                        exact_count = sum(1 for x in result[fmt]
                                          if _extract_timestamp_key(x.name) == pds_key)
                        result[fmt].insert(exact_count, f)
                    else:
                        result[fmt].append(f)
            except PermissionError:
                continue

    # Deduplicate
    for fmt in result:
        seen = set()
        unique = []
        for f in result[fmt]:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        result[fmt] = unique

    return result


def run_pds_qc(
    pds_path: str | Path,
    gsf_dir: str | Path | None = None,
    hvf_dir: str | Path | None = None,
    s7k_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    max_pings: int | None = None,
    offsetmanager_db: str | Path | None = None,
    lat_range: tuple[float, float] = (-90.0, 90.0),
    lon_range: tuple[float, float] = (-180.0, 180.0),
    cell_size: float = 5.0,
    generate_reports: bool = True,
) -> PdsQcResult:
    """Run complete QC from a single PDS file.

    Specify data directories and the tool auto-matches files by timestamp.
    Falls back to PDS-only analysis if no GSF is available.

    Args:
        pds_path: Path to any .pds file.
        gsf_dir: Directory containing GSF files (auto-matched by timestamp).
        hvf_dir: Directory containing HVF vessel files.
        s7k_dir: Directory containing S7K raw files.
        output_dir: Output directory (default: QC_{pds_stem}/ next to PDS).
        max_pings: Limit pings per file (None = all).
        offsetmanager_db: Path to OffsetManager SQLite DB.
        lat_range: Latitude range for navigation search.
        lon_range: Longitude range for navigation search.
        cell_size: Grid cell size in metres.
        generate_reports: Generate Excel/Word/PPT reports.

    Example:
        run_pds_qc(
            "EDFR-20251003-220225.pds",
            gsf_dir="E:/project/GSF",
            hvf_dir="E:/project/Vessels",
        )
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
    companions = _find_companion_files(
        pds_path,
        gsf_dir=Path(gsf_dir) if gsf_dir else None,
        hvf_dir=Path(hvf_dir) if hvf_dir else None,
        s7k_dir=Path(s7k_dir) if s7k_dir else None,
    )
    _header("3. Companion Files")
    print(f"  GSF: {len(companions['gsf'])} files")
    print(f"  HVF: {len(companions['hvf'])} files")
    print(f"  S7K: {len(companions['s7k'])} files")
    print(f"  PDS: {len(companions['pds'])} other PDS files")

    # ── 4. Build SwathLines ───────────────────────────────
    _header("4. Loading Swath Data")

    # Primary: use GSF if available (more complete data)
    if companions['gsf']:
        for gsf_path in companions['gsf'][:10]:  # max 10 lines
            try:
                gsf = read_gsf(str(gsf_path), max_pings=max_pings,
                               load_attitude=True, load_svp=True)
                swath = gsf_to_swath(gsf)
                result.swath_lines.append(swath)
                print(f"  GSF: {gsf_path.name} ({swath.num_pings} pings)")
            except Exception as e:
                print(f"  GSF SKIP: {gsf_path.name} ({e})")

    # Fallback: use PDS binary pings directly
    if not result.swath_lines and pds.pings:
        swath = pds_to_swath(pds)
        result.swath_lines.append(swath)
        print(f"  PDS: {pds_path.name} ({swath.num_pings} pings, PDS-only mode)")

    # Load additional companion formats for enrichment
    for fmt, label in [('fau', 'FAU'), ('xtf', 'XTF'), ('gpt', 'GPT')]:
        if companions.get(fmt):
            print(f"  {label}: {len(companions[fmt])} files available")

    total_beams = 0
    for sw in result.swath_lines:
        for p in sw.pings:
            total_beams += p.num_beams
    result.total_beams = total_beams
    print(f"  Total: {len(result.swath_lines)} lines, {sum(s.num_pings for s in result.swath_lines)} pings, {total_beams:,} beams")

    # ── 5. Post-Processing QC ────────────────────────────
    gsf_objects = []
    if companions.get('gsf'):
        for gsf_path in companions['gsf'][:10]:
            try:
                gsf = read_gsf(str(gsf_path), max_pings=max_pings,
                               load_attitude=True, load_svp=True)
                gsf_objects.append(gsf)
            except:
                pass

    if gsf_objects:
        gsf_main = gsf_objects[0]

        # Motion QC (from GSF attitude)
        _header("5a. Motion QC (GSF)")
        from .motion_qc import run_motion_qc
        motion = run_motion_qc(gsf_main)
        print(f"  Samples: {motion.total_samples:,} ({motion.time_span_sec:.0f}s)")
        for ax in [motion.roll, motion.pitch, motion.heave, motion.heading]:
            print(f"  {ax.name:>8s}: mean={ax.mean:+8.4f}{ax.unit} std={ax.std:7.4f} [{ax.verdict}]")

        # Offset QC
        _header("5b. Offset QC")
        from .offset_qc import run_offset_qc
        hvf = read_hvf(str(companions['hvf'][0])) if companions.get('hvf') else None
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

    elif pds.attitude:
        # PDS-only Motion QC (from Type 8 attitude records)
        _header("5a. Motion QC (PDS attitude, limited)")
        att = pds.attitude
        pitches = np.array([a.pitch for a in att])
        rolls = np.array([a.roll for a in att])
        heaves = np.array([a.heave for a in att])
        headings = np.array([a.heading for a in att])

        print(f"  Attitude records: {len(att)} (PDS Type 8, ~1Hz)")
        print(f"  Pitch:   mean={pitches.mean():+.4f} std={pitches.std():.4f} deg")
        print(f"  Roll:    mean={rolls.mean():+.4f} std={rolls.std():.4f} deg")
        print(f"  Heading: mean={headings.mean():.1f} std={headings.std():.1f} deg")
        print(f"  Heave:   mean={heaves.mean():+.4f} std={heaves.std():.4f} m")
        print(f"  Note: PDS attitude uses MRU device frame (axes may differ from GSF)")

    else:
        _header("5. Post-Processing QC")
        print(f"  No GSF or PDS attitude data available for motion QC.")

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

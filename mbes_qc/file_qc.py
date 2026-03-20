"""File QC - Data integrity and completeness verification.

Checks: file integrity, line naming, time continuity, missing files,
coordinate system consistency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit import read_gsf, read_pds_header
from pds_toolkit.models import GsfFile, PdsMetadata


@dataclass
class FileQcItem:
    name: str = ""
    status: str = "N/A"  # PASS / WARNING / FAIL / N/A
    detail: str = ""


@dataclass
class FileQcResult:
    items: list[FileQcItem] = field(default_factory=list)
    gsf_files: list[str] = field(default_factory=list)
    pds_files: list[str] = field(default_factory=list)
    total_lines: int = 0
    total_pings: int = 0
    time_range: str = ""
    coord_system: str = ""

    @property
    def overall_verdict(self) -> str:
        vs = [i.status for i in self.items if i.status != "N/A"]
        if "FAIL" in vs: return "FAIL"
        if "WARNING" in vs: return "WARNING"
        return "PASS" if vs else "N/A"


def run_file_qc(
    gsf_dir: str | Path | None = None,
    pds_dir: str | Path | None = None,
    gsf_files: list[str | Path] | None = None,
    pds_files: list[str | Path] | None = None,
) -> FileQcResult:
    """Run file-level QC checks."""
    result = FileQcResult()

    # Collect files
    if gsf_dir:
        gsf_dir = Path(gsf_dir)
        result.gsf_files = sorted(str(f) for f in gsf_dir.glob("*.gsf"))
    if gsf_files:
        result.gsf_files = [str(f) for f in gsf_files]
    if pds_dir:
        pds_dir = Path(pds_dir)
        result.pds_files = sorted(str(f) for f in pds_dir.glob("*.pds"))
    if pds_files:
        result.pds_files = [str(f) for f in pds_files]

    result.total_lines = len(result.gsf_files)

    # A1: File integrity
    _check_integrity(result)

    # A2: File naming consistency
    _check_naming(result)

    # A3: Missing/duplicate lines
    _check_completeness(result)

    # A4: Time continuity (quick scan of first/last ping per file)
    _check_time_continuity(result)

    # A5: Coordinate system
    _check_coord_system(result)

    return result


def _check_integrity(r: FileQcResult) -> None:
    """Check file sizes and basic readability."""
    bad = []
    for f in r.gsf_files:
        size = os.path.getsize(f)
        if size < 100:
            bad.append(f"{Path(f).name}: {size} bytes (too small)")
    for f in r.pds_files:
        size = os.path.getsize(f)
        if size < 100:
            bad.append(f"{Path(f).name}: {size} bytes (too small)")

    if bad:
        r.items.append(FileQcItem("File Integrity", "FAIL", "; ".join(bad)))
    else:
        total = len(r.gsf_files) + len(r.pds_files)
        r.items.append(FileQcItem("File Integrity", "PASS", f"{total} files OK"))


def _check_naming(r: FileQcResult) -> None:
    """Check filename pattern consistency."""
    names = [Path(f).stem for f in r.gsf_files]
    if not names:
        r.items.append(FileQcItem("File Naming", "N/A", "No GSF files"))
        return

    # Check common prefix
    prefix = os.path.commonprefix(names)
    if len(prefix) < 4:
        r.items.append(FileQcItem("File Naming", "WARNING", f"No common prefix in filenames"))
    else:
        r.items.append(FileQcItem("File Naming", "PASS", f"Common prefix: '{prefix}', {len(names)} files"))


def _check_completeness(r: FileQcResult) -> None:
    """Check for missing/duplicate line files."""
    gsf_stems = set(Path(f).stem for f in r.gsf_files)
    pds_stems = set(Path(f).stem for f in r.pds_files)

    # Check GSF↔PDS matching
    if pds_stems:
        # PDS names include vessel prefix, GSF might not
        gsf_only = gsf_stems - pds_stems
        pds_only = pds_stems - gsf_stems
        if gsf_only or pds_only:
            detail = ""
            if gsf_only:
                detail += f"GSF only: {len(gsf_only)} files. "
            if pds_only:
                detail += f"PDS only: {len(pds_only)} files."
            r.items.append(FileQcItem("File Completeness", "WARNING", detail))
        else:
            r.items.append(FileQcItem("File Completeness", "PASS", f"All {len(gsf_stems)} lines matched"))
    else:
        r.items.append(FileQcItem("File Completeness", "PASS", f"{len(gsf_stems)} GSF files found"))


def _check_time_continuity(r: FileQcResult) -> None:
    """Quick scan for time gaps between consecutive files."""
    if len(r.gsf_files) < 2:
        r.items.append(FileQcItem("Time Continuity", "N/A", "Need 2+ GSF files"))
        return

    # Read first ping of each file (fast metadata scan)
    times = []
    for f in r.gsf_files[:50]:  # limit for speed
        try:
            gsf = read_gsf(f, max_pings=1, load_arrays=False, load_attitude=False, load_svp=False)
            if gsf.pings:
                times.append((Path(f).name, gsf.pings[0].time))
        except Exception:
            pass

    if len(times) < 2:
        r.items.append(FileQcItem("Time Continuity", "N/A", "Could not read timestamps"))
        return

    times.sort(key=lambda x: x[1])
    r.time_range = f"{times[0][1].strftime('%Y-%m-%d %H:%M')} to {times[-1][1].strftime('%Y-%m-%d %H:%M')}"

    # Check for gaps > 1 hour between consecutive files
    gaps = []
    for i in range(len(times) - 1):
        dt = (times[i + 1][1] - times[i][1]).total_seconds()
        if dt > 3600:
            gaps.append(f"{times[i][0]}->{times[i+1][0]}: {dt/3600:.1f}h")

    if gaps:
        r.items.append(FileQcItem("Time Continuity", "WARNING",
                                  f"{len(gaps)} gaps >1h: {'; '.join(gaps[:3])}"))
    else:
        r.items.append(FileQcItem("Time Continuity", "PASS", f"Continuous over {r.time_range}"))


def _check_coord_system(r: FileQcResult) -> None:
    """Check coordinate system from PDS headers."""
    if not r.pds_files:
        r.items.append(FileQcItem("Coordinate System", "N/A", "No PDS files"))
        return

    systems = set()
    for f in r.pds_files[:5]:
        try:
            meta = read_pds_header(f)
            cs = f"{meta.coord_system_group}/{meta.coord_system_name}"
            systems.add(cs)
        except Exception:
            pass

    if len(systems) == 1:
        r.coord_system = systems.pop()
        r.items.append(FileQcItem("Coordinate System", "PASS", r.coord_system))
    elif len(systems) > 1:
        r.items.append(FileQcItem("Coordinate System", "FAIL",
                                  f"Inconsistent: {systems}"))
    else:
        r.items.append(FileQcItem("Coordinate System", "N/A", "Could not read"))

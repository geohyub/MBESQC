"""Coverage QC - Swath coverage, gap detection, overlap analysis.

Analyzes survey line coverage, identifies gaps, computes overlap
percentages, and generates trackline data for export.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit.models import GsfFile, GsfPing


@dataclass
class LineInfo:
    filename: str = ""
    start_time: str = ""
    end_time: str = ""
    heading_deg: float = 0.0
    num_pings: int = 0
    length_m: float = 0.0
    mean_depth_m: float = 0.0
    mean_swath_m: float = 0.0


@dataclass
class CoverageQcResult:
    lines: list[LineInfo] = field(default_factory=list)
    total_lines: int = 0
    total_length_km: float = 0.0
    total_area_km2: float = 0.0
    num_gaps: int = 0
    gap_area_m2: float = 0.0
    mean_overlap_pct: float = 0.0
    items: list[dict] = field(default_factory=list)

    # Trackline data for export
    track_lats: list[np.ndarray] = field(default_factory=list)
    track_lons: list[np.ndarray] = field(default_factory=list)

    @property
    def overall_verdict(self) -> str:
        vs = [i.get("status", "N/A") for i in self.items]
        if "FAIL" in vs: return "FAIL"
        if "WARNING" in vs: return "WARNING"
        return "PASS" if vs else "N/A"


def run_coverage_qc(
    gsf_files: list[GsfFile],
    min_overlap_pct: float = 10.0,
) -> CoverageQcResult:
    """Analyze coverage from multiple GSF files."""
    result = CoverageQcResult()
    result.total_lines = len(gsf_files)

    for gsf in gsf_files:
        line = _analyze_line(gsf)
        result.lines.append(line)
        result.total_length_km += line.length_m / 1000.0

        # Collect trackline data
        lats = np.array([p.latitude for p in gsf.pings])
        lons = np.array([p.longitude for p in gsf.pings])
        result.track_lats.append(lats)
        result.track_lons.append(lons)

    # Coverage statistics
    if result.lines:
        total_swath_area = sum(l.length_m * l.mean_swath_m for l in result.lines)
        result.total_area_km2 = total_swath_area / 1e6

        result.items.append({
            "name": "Total Lines", "status": "PASS",
            "detail": f"{result.total_lines} lines, {result.total_length_km:.1f} km"
        })
        result.items.append({
            "name": "Coverage Area", "status": "PASS",
            "detail": f"~{result.total_area_km2:.2f} km2 (swath-based estimate)"
        })

    # Overlap analysis (simplified: compare adjacent line swaths)
    if len(result.lines) >= 2:
        _check_overlap(result, gsf_files, min_overlap_pct)

    return result


def _analyze_line(gsf: GsfFile) -> LineInfo:
    """Extract line-level statistics from a GSF file."""
    line = LineInfo(filename=Path(gsf.filepath).name)

    if not gsf.pings:
        return line

    pings = gsf.pings
    line.num_pings = len(pings)
    line.start_time = pings[0].time.strftime("%H:%M:%S")
    line.end_time = pings[-1].time.strftime("%H:%M:%S")

    # Mean heading
    headings = np.array([p.heading for p in pings])
    line.heading_deg = float(np.mean(headings))

    # Line length (sum of inter-ping distances)
    total_dist = 0.0
    for i in range(1, len(pings)):
        dlat = (pings[i].latitude - pings[i - 1].latitude) * 111320
        dlon = (pings[i].longitude - pings[i - 1].longitude) * 111320 * math.cos(math.radians(pings[i].latitude))
        total_dist += math.sqrt(dlat ** 2 + dlon ** 2)
    line.length_m = total_dist

    # Mean depth and swath width
    depths = []
    swaths = []
    for p in pings:
        if p.depth is not None:
            depths.append(float(np.nanmean(p.depth)))
        if p.across_track is not None:
            swaths.append(float(p.across_track.max() - p.across_track.min()))

    if depths:
        line.mean_depth_m = float(np.mean(depths))
    if swaths:
        line.mean_swath_m = float(np.mean(swaths))

    return line


def _check_overlap(result: CoverageQcResult, gsf_files: list[GsfFile], min_pct: float) -> None:
    """Simplified overlap check between adjacent lines."""
    overlaps = []

    for i in range(len(result.lines) - 1):
        l1 = result.lines[i]
        l2 = result.lines[i + 1]

        # Simple overlap estimate: if line spacing < swath width
        if len(result.track_lats[i]) > 0 and len(result.track_lats[i + 1]) > 0:
            # Mid-point distance between lines
            lat1_mid = np.mean(result.track_lats[i])
            lon1_mid = np.mean(result.track_lons[i])
            lat2_mid = np.mean(result.track_lats[i + 1])
            lon2_mid = np.mean(result.track_lons[i + 1])

            dist = math.sqrt(
                ((lat2_mid - lat1_mid) * 111320) ** 2 +
                ((lon2_mid - lon1_mid) * 111320 * math.cos(math.radians(lat1_mid))) ** 2
            )

            mean_swath = (l1.mean_swath_m + l2.mean_swath_m) / 2.0
            if mean_swath > 0:
                overlap_pct = max(0, (mean_swath - dist) / mean_swath * 100)
                overlaps.append(overlap_pct)

    if overlaps:
        result.mean_overlap_pct = float(np.mean(overlaps))
        min_overlap = min(overlaps)

        if min_overlap >= min_pct:
            result.items.append({
                "name": "Line Overlap", "status": "PASS",
                "detail": f"Mean {result.mean_overlap_pct:.1f}%, min {min_overlap:.1f}%"
            })
        elif min_overlap > 0:
            result.items.append({
                "name": "Line Overlap", "status": "WARNING",
                "detail": f"Mean {result.mean_overlap_pct:.1f}%, min {min_overlap:.1f}% (below {min_pct}%)"
            })
        else:
            result.items.append({
                "name": "Line Overlap", "status": "FAIL",
                "detail": f"Gaps detected: min overlap {min_overlap:.1f}%"
            })
            result.num_gaps = sum(1 for o in overlaps if o <= 0)

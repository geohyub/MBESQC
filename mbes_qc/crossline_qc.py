"""Cross-line QC - Depth comparison at line intersections.

Computes depth differences where survey lines cross, detects
line-to-line striping artifacts, and evaluates IHO compliance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pds_toolkit.models import GsfFile
from .iho_s44 import tvu_allowable


@dataclass
class CrosslineResult:
    num_intersections: int = 0
    depth_diff_mean: float = 0.0
    depth_diff_std: float = 0.0
    depth_diff_max: float = 0.0
    depth_diff_rms: float = 0.0
    iho_order: str = "1a"
    iho_pass_pct: float = 0.0
    iho_verdict: str = "N/A"
    striping_detected: bool = False
    striping_amplitude: float = 0.0
    items: list[dict] = field(default_factory=list)
    intersection_details: list[dict] = field(default_factory=list)
    # Per-cell data for crossline map: arrays of (easting, northing, diff, mean_depth)
    cell_eastings: np.ndarray = field(default_factory=lambda: np.empty(0))
    cell_northings: np.ndarray = field(default_factory=lambda: np.empty(0))
    cell_diffs: np.ndarray = field(default_factory=lambda: np.empty(0))
    cell_mean_depths: np.ndarray = field(default_factory=lambda: np.empty(0))
    # Per-line track data for map background (list of (E, N) arrays)
    line_tracks: list[np.ndarray] = field(default_factory=list)

    @property
    def overall_verdict(self) -> str:
        vs = [i.get("status", "N/A") for i in self.items]
        if "FAIL" in vs: return "FAIL"
        if "WARNING" in vs: return "WARNING"
        return "PASS" if vs else "N/A"


def run_crossline_qc(
    gsf_files: list[GsfFile],
    cell_size: float = 5.0,
    iho_order: str = "1a",
) -> CrosslineResult:
    """Compare depths at line crossings using grid-based matching.

    Groups all beams into grid cells, then compares depths from different
    lines within the same cell.
    """
    result = CrosslineResult(iho_order=iho_order)

    if len(gsf_files) < 2:
        result.items.append({"name": "Cross-line", "status": "N/A", "detail": "Need 2+ GSF files"})
        return result

    # Build per-line grids
    line_grids = {}
    for idx, gsf in enumerate(gsf_files):
        points = _extract_beam_positions(gsf)
        if len(points) > 0:
            line_grids[idx] = points  # (E, N, D) arrays

    if len(line_grids) < 2:
        result.items.append({"name": "Cross-line", "status": "N/A", "detail": "Not enough data"})
        return result

    # Find cells where multiple lines overlap
    depth_diffs = []
    mean_depths = []
    cell_es = []
    cell_ns = []

    all_points = {idx: pts for idx, pts in line_grids.items()}

    # Store per-line track centroids for map background
    for idx, pts in all_points.items():
        # Downsample to max 500 points per line for map rendering
        step = max(1, len(pts) // 500)
        result.line_tracks.append(pts[::step, :2].copy())

    # Grid all points
    all_e = np.concatenate([p[:, 0] for p in all_points.values()])
    all_n = np.concatenate([p[:, 1] for p in all_points.values()])

    e_min, e_max = all_e.min(), all_e.max()
    n_min, n_max = all_n.min(), all_n.max()

    for idx1, pts1 in all_points.items():
        for idx2, pts2 in all_points.items():
            if idx2 <= idx1:
                continue

            # Grid both lines -- request cell centers for map
            diffs = _grid_compare(pts1, pts2, cell_size, e_min, e_max, n_min, n_max,
                                  return_centers=True)
            if diffs is not None and len(diffs) > 0:
                depth_diffs.extend(diffs[:, 0].tolist())
                mean_depths.extend(diffs[:, 1].tolist())
                cell_es.extend(diffs[:, 2].tolist())
                cell_ns.extend(diffs[:, 3].tolist())

                result.intersection_details.append({
                    "line1": idx1, "line2": idx2,
                    "n_cells": len(diffs),
                    "mean_diff": float(np.mean(np.abs(diffs[:, 0]))),
                    "std_diff": float(np.std(diffs[:, 0])),
                })

    if not depth_diffs:
        result.items.append({"name": "Cross-line", "status": "N/A", "detail": "No overlapping cells found"})
        return result

    dd = np.array(depth_diffs)
    md = np.array(mean_depths)

    # Store per-cell geographic data for crossline map
    result.cell_eastings = np.array(cell_es)
    result.cell_northings = np.array(cell_ns)
    result.cell_diffs = dd.copy()
    result.cell_mean_depths = md.copy()

    result.num_intersections = len(dd)
    result.depth_diff_mean = float(np.mean(dd))
    result.depth_diff_std = float(np.std(dd))
    result.depth_diff_max = float(np.max(np.abs(dd)))
    result.depth_diff_rms = float(np.sqrt(np.mean(dd ** 2)))

    # IHO S-44 check
    # Cross-line allowable = sqrt(TVU_line1^2 + TVU_line2^2) ~ sqrt(2) * TVU_single
    import math
    sqrt2 = math.sqrt(2.0)
    passes = sum(1 for d, m in zip(dd, md) if abs(d) <= sqrt2 * tvu_allowable(abs(m), iho_order))
    result.iho_pass_pct = 100.0 * passes / len(dd) if len(dd) > 0 else 0.0
    result.iho_verdict = "PASS" if result.iho_pass_pct >= 95.0 else "FAIL"

    result.items.append({
        "name": "Cross-line Depth Diff",
        "status": "PASS" if result.depth_diff_rms < 0.3 else "WARNING" if result.depth_diff_rms < 0.5 else "FAIL",
        "detail": f"RMS={result.depth_diff_rms:.3f}m, mean={result.depth_diff_mean:+.3f}m, max={result.depth_diff_max:.3f}m"
    })
    result.items.append({
        "name": f"IHO S-44 {iho_order.upper()}",
        "status": result.iho_verdict,
        "detail": f"{result.iho_pass_pct:.1f}% within TVU allowable ({result.num_intersections} cells)"
    })

    # Striping detection
    _check_striping(gsf_files, result)

    return result


def _extract_beam_positions(gsf: GsfFile) -> np.ndarray:
    """Extract (E, N, D) from GSF beams using approximate projection."""
    all_points = []
    for p in gsf.pings:
        if p.depth is None or p.across_track is None:
            continue

        hdg_rad = math.radians(p.heading)
        cos_h, sin_h = math.cos(hdg_rad), math.sin(hdg_rad)
        lat_m = 111320.0
        lon_m = 111320.0 * math.cos(math.radians(p.latitude))

        along = p.along_track if p.along_track is not None else np.zeros_like(p.depth)

        e = p.longitude * lon_m + p.across_track * cos_h + along * sin_h
        n = p.latitude * lat_m - p.across_track * sin_h + along * cos_h

        pts = np.column_stack([e, n, p.depth])
        # Filter valid beams
        valid = np.isfinite(pts).all(axis=1) & (pts[:, 2] > 0)
        if p.beam_flags is not None:
            valid &= (p.beam_flags == 0)
        all_points.append(pts[valid])

    if not all_points:
        return np.empty((0, 3))
    return np.vstack(all_points)


def _grid_compare(pts1: np.ndarray, pts2: np.ndarray, cell_size: float,
                  e_min: float, e_max: float, n_min: float, n_max: float,
                  return_centers: bool = False) -> np.ndarray | None:
    """Compare two point sets on a common grid, return depth differences.

    When *return_centers* is True the returned array has shape (N, 4):
        [diff, mean_depth, cell_center_E, cell_center_N]
    otherwise (N, 2): [diff, mean_depth]  (legacy behaviour).
    """
    from scipy.stats import binned_statistic_2d

    nx = max(1, int((e_max - e_min) / cell_size) + 1)
    ny = max(1, int((n_max - n_min) / cell_size) + 1)
    bins_e = np.linspace(e_min, e_max, nx + 1)
    bins_n = np.linspace(n_min, n_max, ny + 1)

    g1 = binned_statistic_2d(pts1[:, 0], pts1[:, 1], pts1[:, 2], 'mean', [bins_e, bins_n]).statistic
    g2 = binned_statistic_2d(pts2[:, 0], pts2[:, 1], pts2[:, 2], 'mean', [bins_e, bins_n]).statistic

    # Both grids must have data in the cell
    valid = np.isfinite(g1) & np.isfinite(g2)
    if not valid.any():
        return None

    diffs = g1[valid] - g2[valid]
    means = (g1[valid] + g2[valid]) / 2.0

    if not return_centers:
        return np.column_stack([diffs, means])

    # Build cell-center coordinate grids
    center_e = 0.5 * (bins_e[:-1] + bins_e[1:])
    center_n = 0.5 * (bins_n[:-1] + bins_n[1:])
    ce_grid, cn_grid = np.meshgrid(center_e, center_n, indexing="ij")

    return np.column_stack([diffs, means, ce_grid[valid], cn_grid[valid]])


def _check_striping(gsf_files: list[GsfFile], result: CrosslineResult) -> None:
    """Detect along-track striping by analyzing depth oscillations between adjacent pings."""
    for gsf in gsf_files[:5]:
        if len(gsf.pings) < 20:
            continue

        nadir_depths = []
        for p in gsf.pings:
            if p.depth is not None:
                center = p.num_beams // 2
                nadir_depths.append(p.depth[center])

        if len(nadir_depths) < 20:
            continue

        nd = np.array(nadir_depths)
        # Detrend and check for oscillation amplitude
        trend = np.convolve(nd, np.ones(10) / 10, mode='same')
        residual = nd[5:-5] - trend[5:-5]

        if len(residual) > 0:
            amplitude = float(np.std(residual))
            if amplitude > 0.2:
                result.striping_detected = True
                result.striping_amplitude = max(result.striping_amplitude, amplitude)

    if result.striping_detected:
        result.items.append({
            "name": "Striping Detection", "status": "WARNING",
            "detail": f"Amplitude={result.striping_amplitude:.3f}m (possible line striping)"
        })
    else:
        result.items.append({
            "name": "Striping Detection", "status": "PASS",
            "detail": "No significant striping detected"
        })

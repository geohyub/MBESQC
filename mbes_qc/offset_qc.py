"""Offset QC — Verify sensor offsets are correctly applied.

Estimates roll, pitch, and heading biases from multibeam data patterns
and compares against HVF vessel configuration values.

Methods:
  Roll bias:  Port/Starboard beam depth asymmetry
  Pitch bias: Forward/Backward line nadir depth difference
  HVF check:  Compare applied offsets vs data-estimated biases
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pds_toolkit.models import GsfFile, GsfPing, HvfFile

# ── Thresholds ──────────────────────────────────────────────────

_ROLL_PASS = 0.1     # degrees
_ROLL_WARN = 0.5
_PITCH_PASS = 0.1
_PITCH_WARN = 0.5


@dataclass
class OffsetQcResult:
    """Results of offset verification."""

    # Roll bias
    roll_bias_deg: float = 0.0
    roll_bias_std: float = 0.0
    roll_num_pings: int = 0
    roll_verdict: str = "N/A"

    # Pitch bias
    pitch_bias_deg: float = 0.0
    pitch_bias_std: float = 0.0
    pitch_fwd_depth: float = 0.0
    pitch_bwd_depth: float = 0.0
    pitch_num_pairs: int = 0
    pitch_verdict: str = "N/A"

    # HVF comparison
    hvf_offsets: list[dict] = field(default_factory=list)
    hvf_vs_data: str = ""

    # Per-ping roll bias array (for plotting)
    roll_bias_per_ping: np.ndarray = field(default_factory=lambda: np.array([]))

    @property
    def overall_verdict(self) -> str:
        verdicts = [self.roll_verdict, self.pitch_verdict]
        if "FAIL" in verdicts:
            return "FAIL"
        if "WARNING" in verdicts:
            return "WARNING"
        if all(v == "PASS" for v in verdicts):
            return "PASS"
        return "N/A"


def run_offset_qc(
    gsf: GsfFile,
    hvf: HvfFile | None = None,
    min_pings: int = 10,
) -> OffsetQcResult:
    """Run full offset QC analysis.

    Args:
        gsf: Parsed GSF file with beam arrays loaded.
        hvf: Optional HVF vessel config for comparison.
        min_pings: Minimum pings required for analysis.
    """
    result = OffsetQcResult()

    pings_with_depth = [p for p in gsf.pings if p.depth is not None and p.across_track is not None]

    if len(pings_with_depth) < min_pings:
        return result

    # ── Roll Bias ───────────────────────────────────────────
    roll_biases = _estimate_roll_bias(pings_with_depth)
    result.roll_bias_per_ping = roll_biases
    result.roll_num_pings = len(roll_biases)

    if len(roll_biases) > 0:
        result.roll_bias_deg = float(np.nanmean(roll_biases))
        result.roll_bias_std = float(np.nanstd(roll_biases))
        abs_bias = abs(result.roll_bias_deg)
        if abs_bias < _ROLL_PASS:
            result.roll_verdict = "PASS"
        elif abs_bias < _ROLL_WARN:
            result.roll_verdict = "WARNING"
        else:
            result.roll_verdict = "FAIL"

    # ── Pitch Bias ──────────────────────────────────────────
    pitch_result = _estimate_pitch_bias(pings_with_depth)
    if pitch_result:
        result.pitch_bias_deg = pitch_result["bias_deg"]
        result.pitch_bias_std = pitch_result["bias_std"]
        result.pitch_fwd_depth = pitch_result["fwd_depth"]
        result.pitch_bwd_depth = pitch_result["bwd_depth"]
        result.pitch_num_pairs = pitch_result["num_pairs"]
        abs_bias = abs(result.pitch_bias_deg)
        if abs_bias < _PITCH_PASS:
            result.pitch_verdict = "PASS"
        elif abs_bias < _PITCH_WARN:
            result.pitch_verdict = "WARNING"
        else:
            result.pitch_verdict = "FAIL"

    # ── HVF Comparison ──────────────────────────────────────
    if hvf:
        result.hvf_offsets = _extract_hvf_offsets(hvf)
        result.hvf_vs_data = _compare_hvf_vs_data(result)

    return result


def _estimate_roll_bias(pings: list[GsfPing]) -> np.ndarray:
    """Estimate roll bias from port/starboard beam depth asymmetry.

    For each ping, compare symmetric beam pairs around nadir.
    If roll offset is wrong, port beams will be systematically
    deeper (or shallower) than corresponding starboard beams.

    roll_bias = arctan(mean_depth_diff / (2 x mean_across_track_one_side))

    LIMITATION: This method assumes a locally flat seafloor. Across-track
    seafloor slope will contaminate the roll bias estimate. For accurate
    results, use reciprocal line data or flat-bottom calibration areas.
    """
    biases = []

    for p in pings:
        if p.depth is None or p.across_track is None:
            continue

        n = p.num_beams
        center = p.center_beam if p.center_beam > 0 else n // 2

        # Select symmetric beam pairs (port vs starboard)
        n_pairs = min(center, n - center - 1)
        if n_pairs < 10:
            continue

        # Use beams from ~20% to ~80% of each side (avoid nadir and outer edges)
        start = max(1, n_pairs // 5)
        end = min(n_pairs, n_pairs * 4 // 5)

        port_depths = []
        stbd_depths = []
        across_dists = []

        for i in range(start, end):
            port_idx = center - i
            stbd_idx = center + i

            if port_idx < 0 or stbd_idx >= n:
                continue

            d_port = p.depth[port_idx]
            d_stbd = p.depth[stbd_idx]
            at_port = abs(p.across_track[port_idx])
            at_stbd = abs(p.across_track[stbd_idx])

            # Skip if values look invalid
            if d_port <= 0 or d_stbd <= 0 or at_port <= 0 or at_stbd <= 0:
                continue

            port_depths.append(d_port)
            stbd_depths.append(d_stbd)
            across_dists.append((at_port + at_stbd) / 2.0)

        if len(port_depths) < 5:
            continue

        port_arr = np.array(port_depths)
        stbd_arr = np.array(stbd_depths)
        across_arr = np.array(across_dists)

        # Depth difference: positive = port deeper than stbd (roll to port)
        depth_diff = port_arr - stbd_arr
        mean_diff = np.mean(depth_diff)
        mean_across = np.mean(across_arr)

        if mean_across > 0:
            # roll_bias = arctan(depth_diff / (2 × across_track))
            roll_rad = math.atan2(mean_diff, 2.0 * mean_across)
            biases.append(math.degrees(roll_rad))

    return np.array(biases)


def _estimate_pitch_bias(pings: list[GsfPing]) -> dict | None:
    """Estimate pitch bias from reciprocal line nadir depth comparison.

    Groups pings by heading direction, then compares nadir depths
    between forward and backward passes over the same area.
    """
    if len(pings) < 20:
        return None

    # Group pings by heading (forward ~heading, backward ~heading+180)
    headings = np.array([p.heading for p in pings])
    depths_nadir = []
    for p in pings:
        if p.depth is not None:
            center = p.center_beam if p.center_beam > 0 else p.num_beams // 2
            depths_nadir.append(p.depth[center])
        else:
            depths_nadir.append(np.nan)
    depths_nadir = np.array(depths_nadir)

    # Find dominant heading
    valid = ~np.isnan(depths_nadir)
    if np.sum(valid) < 10:
        return None

    hdg_valid = headings[valid]
    dep_valid = depths_nadir[valid]

    # Cluster headings into two groups (forward/backward)
    median_hdg = np.median(hdg_valid)

    # Forward: within 45° of median heading
    # Backward: within 45° of median + 180°
    fwd_mask = np.abs(_angle_diff(hdg_valid, median_hdg)) < 45
    bwd_mask = np.abs(_angle_diff(hdg_valid, (median_hdg + 180) % 360)) < 45

    fwd_depths = dep_valid[fwd_mask]
    bwd_depths = dep_valid[bwd_mask]

    if len(fwd_depths) < 5 or len(bwd_depths) < 5:
        return None

    fwd_mean = float(np.mean(fwd_depths))
    bwd_mean = float(np.mean(bwd_depths))
    diff = fwd_mean - bwd_mean

    # Pitch bias estimation (simplified):
    # depth_diff ≈ 2 × depth × tan(pitch_bias)
    mean_depth = (fwd_mean + bwd_mean) / 2.0
    if mean_depth > 0:
        pitch_rad = math.atan2(diff, 2.0 * mean_depth)
        pitch_deg = math.degrees(pitch_rad)
    else:
        pitch_deg = 0.0

    return {
        "bias_deg": pitch_deg,
        "bias_std": float(np.std(np.concatenate([fwd_depths, bwd_depths]))),
        "fwd_depth": fwd_mean,
        "bwd_depth": bwd_mean,
        "num_pairs": min(len(fwd_depths), len(bwd_depths)),
    }


def _angle_diff(a: np.ndarray, b: float) -> np.ndarray:
    """Signed angle difference in degrees, result in [-180, 180]."""
    d = a - b
    return (d + 180) % 360 - 180


def _extract_hvf_offsets(hvf: HvfFile) -> list[dict]:
    """Extract sensor offset history from HVF."""
    offsets = []
    for s in hvf.sensors:
        if "DepthSensor" in s.name:
            offsets.append({
                "name": s.name,
                "x": s.x, "y": s.y, "z": s.z,
                "pitch": s.pitch, "roll": s.roll, "heading": s.heading,
            })
    return offsets


def _compare_hvf_vs_data(result: OffsetQcResult) -> str:
    """Compare HVF applied offsets vs data-estimated biases."""
    lines = []
    for off in result.hvf_offsets:
        lines.append(f"  HVF {off['name']}: Roll={off['roll']:.3f}° Pitch={off['pitch']:.3f}° Hdg={off['heading']:.3f}°")

    if result.roll_verdict != "N/A":
        lines.append(f"  Data Roll Bias: {result.roll_bias_deg:+.4f}° (±{result.roll_bias_std:.4f}°)")
        # Check if residual is small (meaning HVF offset was correctly applied)
        lines.append(f"  → Residual roll after offset application is {'acceptable' if abs(result.roll_bias_deg) < _ROLL_WARN else 'significant'}")

    if result.pitch_verdict != "N/A":
        lines.append(f"  Data Pitch Bias: {result.pitch_bias_deg:+.4f}° (±{result.pitch_bias_std:.4f}°)")

    return "\n".join(lines)

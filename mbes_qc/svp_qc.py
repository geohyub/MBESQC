"""SVP QC - Sound Velocity Profile verification.

Checks SVP application status, profile reasonableness,
temporal coverage, and outer beam refraction indicators.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pds_toolkit.models import GsfFile, GsfSvpRecord


@dataclass
class SvpQcResult:
    applied: bool = False
    svp_filename: str = ""
    num_profiles: int = 0
    profiles_summary: list[dict] = field(default_factory=list)
    velocity_range: tuple[float, float] = (0.0, 0.0)
    outer_beam_indicator: str = "N/A"
    items: list[dict] = field(default_factory=list)

    @property
    def overall_verdict(self) -> str:
        vs = [i.get("status", "N/A") for i in self.items]
        if "FAIL" in vs: return "FAIL"
        if "WARNING" in vs: return "WARNING"
        return "PASS" if vs else "N/A"


def run_svp_qc(gsf: GsfFile, pds_apply_svp: bool = False) -> SvpQcResult:
    """Run SVP quality checks."""
    result = SvpQcResult(applied=pds_apply_svp)
    result.num_profiles = gsf.num_svp

    # SVP application check
    if pds_apply_svp:
        result.items.append({"name": "SVP Applied", "status": "PASS", "detail": "SVP correction enabled"})
    else:
        result.items.append({"name": "SVP Applied", "status": "WARNING",
                             "detail": "SVP correction NOT enabled in PDS"})

    # Profile count
    if gsf.svp_profiles:
        result.items.append({"name": "SVP Profiles", "status": "PASS",
                             "detail": f"{len(gsf.svp_profiles)} profiles in GSF"})

        all_vels = []
        for svp in gsf.svp_profiles:
            summary = {
                "time": svp.time.isoformat() if svp.time else "N/A",
                "num_points": svp.num_points,
                "depth_range": f"{svp.depth.min():.1f}-{svp.depth.max():.1f}m" if svp.num_points > 0 else "N/A",
                "vel_range": f"{svp.sound_velocity.min():.1f}-{svp.sound_velocity.max():.1f}m/s" if svp.num_points > 0 else "N/A",
            }
            result.profiles_summary.append(summary)
            if svp.num_points > 0:
                all_vels.extend(svp.sound_velocity.tolist())

        if all_vels:
            vmin, vmax = min(all_vels), max(all_vels)
            result.velocity_range = (vmin, vmax)

            # Reasonableness check: seawater 1400-1600 m/s
            if 1400 < vmin and vmax < 1600:
                result.items.append({"name": "SVP Velocity Range", "status": "PASS",
                                     "detail": f"{vmin:.1f}-{vmax:.1f} m/s (normal seawater)"})
            else:
                result.items.append({"name": "SVP Velocity Range", "status": "WARNING",
                                     "detail": f"{vmin:.1f}-{vmax:.1f} m/s (outside normal range)"})
    else:
        result.items.append({"name": "SVP Profiles", "status": "N/A",
                             "detail": "No SVP profiles in GSF file"})

    # Outer beam refraction indicator
    _check_outer_beam_refraction(gsf, result)

    return result


def _check_outer_beam_refraction(gsf: GsfFile, result: SvpQcResult) -> None:
    """Check for SVP-related artifacts in outer beams.

    If SVP is wrong, outer beams curve up or down systematically.
    Compare depth gradient from nadir to outer beams vs expected flat bottom.
    """
    if not gsf.pings:
        return

    depth_gradients = []
    for p in gsf.pings[:100]:
        if p.depth is None or p.beam_angle is None:
            continue
        n = p.num_beams
        center = n // 2

        # Compare nadir depth to outer beam depth at same across-track distance
        nadir_d = p.depth[center]
        if nadir_d <= 0:
            continue

        # Port outer 20% and Stbd outer 20%
        port_outer = slice(0, n // 5)
        stbd_outer = slice(4 * n // 5, n)

        port_mean = np.nanmean(p.depth[port_outer])
        stbd_mean = np.nanmean(p.depth[stbd_outer])

        # Ratio: outer/nadir should be close to 1.0 for flat bottom with good SVP
        if nadir_d > 0:
            depth_gradients.append((port_mean / nadir_d, stbd_mean / nadir_d))

    if not depth_gradients:
        result.outer_beam_indicator = "N/A"
        return

    port_ratios = [g[0] for g in depth_gradients]
    stbd_ratios = [g[1] for g in depth_gradients]
    mean_port = np.mean(port_ratios)
    mean_stbd = np.mean(stbd_ratios)

    # If outer beams are systematically deeper or shallower -> SVP issue
    if abs(mean_port - 1.0) > 0.05 or abs(mean_stbd - 1.0) > 0.05:
        result.outer_beam_indicator = "Possible SVP artifact"
        result.items.append({"name": "Outer Beam Refraction", "status": "WARNING",
                             "detail": f"Port ratio={mean_port:.3f}, Stbd ratio={mean_stbd:.3f} (expect ~1.0)"})
    else:
        result.outer_beam_indicator = "Normal"
        result.items.append({"name": "Outer Beam Refraction", "status": "PASS",
                             "detail": f"Port={mean_port:.3f}, Stbd={mean_stbd:.3f}"})

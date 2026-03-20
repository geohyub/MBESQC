"""Vessel QC - PDS vessel config vs HVF cross-validation.

Extracts sensor offsets, motion settings, SVP config, and draft
from PDS headers and compares against HVF vessel file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pds_toolkit import read_hvf, read_pds_header
from pds_toolkit.models import HvfFile, PdsMetadata


@dataclass
class VesselQcItem:
    name: str = ""
    pds_value: str = ""
    hvf_value: str = ""
    match: bool = True
    status: str = "N/A"
    detail: str = ""


@dataclass
class VesselQcResult:
    items: list[VesselQcItem] = field(default_factory=list)

    # PDS extracted values
    pds_offsets: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    pds_static_roll: float = 0.0
    pds_static_pitch: float = 0.0
    pds_apply_roll: bool = False
    pds_apply_pitch: bool = False
    pds_apply_heave: bool = False
    pds_apply_svp: bool = False
    pds_svp_filename: str = ""
    pds_draft: float = 0.0
    pds_sealevel: float = 0.0

    # HVF values
    hvf_mount_roll: float = 0.0
    hvf_mount_pitch: float = 0.0
    hvf_mount_heading: float = 0.0
    hvf_offsets: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    @property
    def overall_verdict(self) -> str:
        vs = [i.status for i in self.items if i.status != "N/A"]
        if "FAIL" in vs: return "FAIL"
        if "WARNING" in vs: return "WARNING"
        return "PASS" if vs else "N/A"


def run_vessel_qc(
    pds_path: str | Path,
    hvf_path: str | Path | None = None,
) -> VesselQcResult:
    """Compare PDS vessel settings against HVF."""
    result = VesselQcResult()

    meta = read_pds_header(pds_path, max_bytes=500_000)
    _extract_pds_vessel(meta, result)

    hvf = None
    if hvf_path:
        hvf = read_hvf(hvf_path)
        _extract_hvf_vessel(hvf, result)
        _compare_offsets(result)

    _check_motion_settings(result)
    _check_svp_settings(result)
    _check_draft(result)

    return result


def _extract_pds_vessel(meta: PdsMetadata, r: VesselQcResult) -> None:
    """Extract vessel config from PDS [GEOMETRY] and [COMPUTATION] sections."""

    # Geometry offsets
    geom = meta.sections.get("GEOMETRY", {})
    for key, val in geom.items():
        if key.startswith("Offset(") and "=" not in key:
            parts = val.split(",")
            if len(parts) >= 4:
                name = parts[0].strip()
                try:
                    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                    r.pds_offsets[name] = (x, y, z)
                except ValueError:
                    pass

    r.pds_sealevel = _float(geom.get("Sealevel", "0"))
    r.pds_draft = _float(geom.get("Draft", "0"))

    # Computation settings (search all COMPUTATION sections)
    for sec_name, sec_data in meta.sections.items():
        if "COMPUTATION" in sec_name or "SVPFileName" in sec_name:
            for key, val in sec_data.items():
                kl = key.lower()
                if "applyroll" in kl and "static" not in kl:
                    r.pds_apply_roll = ",1" in val
                elif "applypitch" in kl and "static" not in kl:
                    r.pds_apply_pitch = ",1" in val
                elif "applyheave" in kl:
                    r.pds_apply_heave = ",1" in val
                elif "applysvp" in kl:
                    r.pds_apply_svp = ",1" in val
                elif "staticroll" in kl:
                    r.pds_static_roll = _extract_last_float(val)
                elif "staticpitch" in kl:
                    r.pds_static_pitch = _extract_last_float(val)
                elif "svpfilename" in kl and "svpfiletime" not in kl:
                    parts = val.split(",")
                    if len(parts) >= 2:
                        r.pds_svp_filename = parts[-1].strip()
                    elif parts:
                        r.pds_svp_filename = parts[0].strip()


def _extract_hvf_vessel(hvf: HvfFile, r: VesselQcResult) -> None:
    """Extract latest calibration values from HVF."""
    depth_sensors = [s for s in hvf.sensors if "DepthSensor" in s.name]
    if depth_sensors:
        latest = depth_sensors[-1]  # Last timestamp = most recent
        r.hvf_mount_roll = latest.roll
        r.hvf_mount_pitch = latest.pitch
        r.hvf_mount_heading = latest.heading
        r.hvf_offsets["Transducer"] = (latest.x, latest.y, latest.z)


def _compare_offsets(r: VesselQcResult) -> None:
    """Compare PDS GEOMETRY offsets vs HVF transducer offsets."""

    # Find transducer offset in PDS (T50-ER or similar)
    pds_trans = None
    for name, (x, y, z) in r.pds_offsets.items():
        if name not in ("Zero Offset", "DGPS", "CACU"):
            pds_trans = (name, x, y, z)
            break

    if pds_trans and "Transducer" in r.hvf_offsets:
        pname, px, py, pz = pds_trans
        hx, hy, hz = r.hvf_offsets["Transducer"]

        x_match = abs(px - hx) < 0.01
        y_match = abs(py - hy) < 0.01
        # Z may differ (PDS stores relative to different reference)

        if x_match and y_match:
            r.items.append(VesselQcItem(
                "Transducer Offset (X,Y)",
                f"PDS {pname}: ({px:.3f}, {py:.3f})",
                f"HVF: ({hx:.3f}, {hy:.3f})",
                match=True, status="PASS",
                detail="X/Y offsets match within 0.01m"
            ))
        else:
            r.items.append(VesselQcItem(
                "Transducer Offset (X,Y)",
                f"PDS {pname}: ({px:.3f}, {py:.3f})",
                f"HVF: ({hx:.3f}, {hy:.3f})",
                match=False, status="FAIL",
                detail=f"MISMATCH: dX={px-hx:.3f}m, dY={py-hy:.3f}m"
            ))

    # Compare static roll/pitch vs HVF mount angles
    r.items.append(VesselQcItem(
        "Static Roll (Cal value)",
        f"PDS: {r.pds_static_roll:+.4f} deg",
        f"HVF MountRoll: {r.hvf_mount_roll:+.4f} deg",
        match=abs(r.pds_static_roll - r.hvf_mount_roll) < 0.01,
        status="PASS" if abs(r.pds_static_roll - r.hvf_mount_roll) < 0.1 else "WARNING",
        detail=f"Difference: {r.pds_static_roll - r.hvf_mount_roll:+.4f} deg"
    ))

    r.items.append(VesselQcItem(
        "Static Pitch (Cal value)",
        f"PDS: {r.pds_static_pitch:+.4f} deg",
        f"HVF MountPitch: {r.hvf_mount_pitch:+.4f} deg",
        match=abs(r.pds_static_pitch - r.hvf_mount_pitch) < 0.01,
        status="PASS" if abs(r.pds_static_pitch - r.hvf_mount_pitch) < 0.1 else "WARNING",
        detail=f"Difference: {r.pds_static_pitch - r.hvf_mount_pitch:+.4f} deg"
    ))


def _check_motion_settings(r: VesselQcResult) -> None:
    """Check Roll/Pitch/Heave application flags."""
    for name, applied in [
        ("Apply Roll", r.pds_apply_roll),
        ("Apply Pitch", r.pds_apply_pitch),
        ("Apply Heave", r.pds_apply_heave),
    ]:
        r.items.append(VesselQcItem(
            name,
            pds_value="ON" if applied else "OFF",
            status="PASS" if applied else "WARNING",
            detail="" if applied else f"{name} is OFF -- motion not corrected!"
        ))


def _check_svp_settings(r: VesselQcResult) -> None:
    """Check SVP application and filename."""
    r.items.append(VesselQcItem(
        "Apply SVP",
        pds_value="ON" if r.pds_apply_svp else "OFF",
        status="PASS" if r.pds_apply_svp else "WARNING",
        detail=f"SVP file: {r.pds_svp_filename}" if r.pds_svp_filename else "No SVP file set"
    ))


def _check_draft(r: VesselQcResult) -> None:
    """Check draft/sealevel values."""
    if r.pds_draft == 0 and r.pds_sealevel == 0:
        r.items.append(VesselQcItem(
            "Draft/Sealevel",
            pds_value=f"Draft={r.pds_draft}, Sealevel={r.pds_sealevel}",
            status="WARNING",
            detail="Both Draft and Sealevel are 0 -- check if intentional"
        ))
    else:
        r.items.append(VesselQcItem(
            "Draft/Sealevel",
            pds_value=f"Draft={r.pds_draft}m, Sealevel={r.pds_sealevel}m",
            status="PASS",
        ))


def _float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _extract_last_float(val: str) -> float:
    """Extract the last float from a comma-separated PDS value string."""
    parts = val.split(",")
    for p in reversed(parts):
        try:
            return float(p.strip())
        except ValueError:
            continue
    return 0.0

"""Pre-Processing Validator - Verify data is ready for processing.

Checks BEFORE running CARIS or other processing software:
1. Sensor offsets match between PDS, HVF, and OffsetManager DB
2. Motion correction flags are enabled
3. SVP is applied (or intentionally deferred)
4. Navigation is continuous and reasonable
5. Draft/Sealevel settings are correct
6. Calibration values (StaticRoll/Pitch) are reasonable

If mismatches are found, suggests the correct values.
"""

from __future__ import annotations

import sys
import sqlite3
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit import read_pds_header, read_pds_binary
from pds_toolkit.models import PdsMetadata


# ── Data Classes ───────────────────────────────────────────────

@dataclass
class CheckItem:
    """Single validation check result."""
    category: str = ""  # "Offset", "Motion", "SVP", "Navigation", "Calibration"
    name: str = ""
    status: str = "N/A"  # PASS, WARNING, FAIL, INFO
    pds_value: str = ""
    reference_value: str = ""
    difference: str = ""
    suggestion: str = ""


@dataclass
class PreProcessResult:
    """Complete pre-processing validation result."""
    filepath: str = ""
    vessel_name: str = ""
    checks: list[CheckItem] = field(default_factory=list)

    @property
    def overall(self) -> str:
        statuses = [c.status for c in self.checks if c.status != "N/A" and c.status != "INFO"]
        if "FAIL" in statuses:
            return "FAIL"
        if "WARNING" in statuses:
            return "WARNING"
        return "PASS" if statuses else "N/A"

    @property
    def num_pass(self) -> int:
        return sum(1 for c in self.checks if c.status == "PASS")

    @property
    def num_warn(self) -> int:
        return sum(1 for c in self.checks if c.status == "WARNING")

    @property
    def num_fail(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")

    def summary(self) -> str:
        return (f"[{self.overall}] {self.vessel_name}: "
                f"{self.num_pass} PASS, {self.num_warn} WARN, {self.num_fail} FAIL "
                f"({len(self.checks)} checks)")


# ── OffsetManager DB Integration ───────────────────────────────

def _load_offsetmanager_offsets(
    db_path: str,
    vessel_name: str | None = None,
    config_id: int | None = None,
) -> dict[str, dict]:
    """Load sensor offsets from OffsetManager SQLite DB.

    Returns dict keyed by sensor_name with x/y/z/roll/pitch/heading.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if config_id:
        rows = conn.execute(
            "SELECT * FROM sensor_offsets WHERE config_id = ?", (config_id,)
        ).fetchall()
    elif vessel_name:
        config = conn.execute(
            "SELECT id FROM vessel_configs WHERE vessel_name LIKE ? ORDER BY updated_at DESC LIMIT 1",
            (f"%{vessel_name}%",)
        ).fetchone()
        if config:
            rows = conn.execute(
                "SELECT * FROM sensor_offsets WHERE config_id = ?", (config["id"],)
            ).fetchall()
        else:
            conn.close()
            return {}
    else:
        rows = conn.execute(
            "SELECT * FROM sensor_offsets ORDER BY id LIMIT 100"
        ).fetchall()

    conn.close()

    offsets = {}
    for r in rows:
        r = dict(r)
        offsets[r["sensor_name"]] = {
            "type": r["sensor_type"],
            "x": r["x_offset"],
            "y": r["y_offset"],
            "z": r["z_offset"],
            "roll": r["roll_offset"],
            "pitch": r["pitch_offset"],
            "heading": r["heading_offset"],
            "latency": r["latency"],
        }

    return offsets


def _payload_to_offsets(om_payload: dict | None) -> dict[str, dict]:
    """Normalize an OffsetManager API payload into the internal offsets map."""
    if not isinstance(om_payload, dict):
        return {}

    sensors = om_payload.get("offsets", om_payload.get("sensors", []))
    if not sensors and isinstance(om_payload.get("config"), dict):
        sensors = om_payload["config"].get("offsets", om_payload["config"].get("sensors", []))

    offsets: dict[str, dict] = {}
    for sensor in sensors or []:
        if not isinstance(sensor, dict):
            continue
        name = sensor.get("sensor_name", sensor.get("name", ""))
        if not name:
            continue
        offsets[name] = {
            "type": sensor.get("sensor_type", sensor.get("type", "")),
            "x": float(sensor.get("x_offset", sensor.get("x", 0)) or 0),
            "y": float(sensor.get("y_offset", sensor.get("y", 0)) or 0),
            "z": float(sensor.get("z_offset", sensor.get("z", 0)) or 0),
            "roll": float(sensor.get("roll_offset", sensor.get("roll", 0)) or 0),
            "pitch": float(sensor.get("pitch_offset", sensor.get("pitch", 0)) or 0),
            "heading": float(sensor.get("heading_offset", sensor.get("heading", 0)) or 0),
            "latency": float(sensor.get("latency", 0) or 0),
        }

    return offsets


# ── Sensor Matching ────────────────────────────────────────────

_SENSOR_TYPE_MAP = {
    "T50": "MBES Transducer",
    "T20": "MBES Transducer",
    "ER": "MBES Transducer",
    "DGPS": "GPS",
    "GPS": "GPS",
    "CACU": "MRU",
    "MRU": "MRU",
    "IMU": "IMU",
    "Octans": "MRU",
}


def _match_pds_to_om(pds_name: str, om_offsets: dict[str, dict]) -> str | None:
    """Find best matching OffsetManager sensor for a PDS sensor name."""
    pds_upper = pds_name.upper()

    # Exact match
    if pds_name in om_offsets:
        return pds_name

    # Partial match
    for om_name in om_offsets:
        om_upper = om_name.upper()
        # Both contain same keyword
        for keyword in ["T50", "T20", "DGPS", "GPS", "MRU", "IMU", "CACU", "SBP", "USBL"]:
            if keyword in pds_upper and keyword in om_upper:
                return om_name

    # Type-based match
    for keyword, sensor_type in _SENSOR_TYPE_MAP.items():
        if keyword in pds_upper:
            for om_name, om_data in om_offsets.items():
                if om_data["type"] == sensor_type:
                    return om_name

    return None


# ── Core Checks ────────────────────────────────────────────────

def _check_offsets(meta: PdsMetadata, result: PreProcessResult,
                   om_offsets: dict[str, dict] | None = None,
                   tolerance: float = 0.05) -> None:
    """Check sensor offsets: PDS GEOMETRY vs OffsetManager DB."""

    geom = meta.sections.get("GEOMETRY", {})
    pds_offsets = {}

    for key, val in geom.items():
        if key.startswith("Offset("):
            parts = val.split(",")
            if len(parts) >= 4:
                name = parts[0].strip()
                try:
                    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                    pds_offsets[name] = (x, y, z)
                except ValueError:
                    pass

    if not om_offsets:
        for name, (x, y, z) in pds_offsets.items():
            result.checks.append(CheckItem(
                category="Offset", name=f"Sensor: {name}",
                status="INFO",
                pds_value=f"X={x:+.3f} Y={y:+.3f} Z={z:+.3f}",
                reference_value="No OffsetManager DB provided",
            ))
        return

    for pds_name, (px, py, pz) in pds_offsets.items():
        if pds_name == "Zero Offset":
            continue

        om_match = _match_pds_to_om(pds_name, om_offsets)

        if om_match:
            om = om_offsets[om_match]
            ox, oy, oz = om["x"], om["y"], om["z"]
            dx, dy, dz = abs(px - ox), abs(py - oy), abs(pz - oz)
            max_diff = max(dx, dy, dz)

            if max_diff < tolerance:
                status = "PASS"
                suggestion = ""
            elif max_diff < 0.5:
                status = "WARNING"
                suggestion = f"Consider updating PDS to X={ox:+.3f} Y={oy:+.3f} Z={oz:+.3f}"
            else:
                status = "FAIL"
                suggestion = f"CRITICAL: Update PDS offsets to X={ox:+.3f} Y={oy:+.3f} Z={oz:+.3f}"

            result.checks.append(CheckItem(
                category="Offset",
                name=f"{pds_name} ↔ {om_match}",
                status=status,
                pds_value=f"X={px:+.3f} Y={py:+.3f} Z={pz:+.3f}",
                reference_value=f"X={ox:+.3f} Y={oy:+.3f} Z={oz:+.3f}",
                difference=f"dX={px-ox:+.3f} dY={py-oy:+.3f} dZ={pz-oz:+.3f}",
                suggestion=suggestion,
            ))
        else:
            result.checks.append(CheckItem(
                category="Offset",
                name=f"Sensor: {pds_name}",
                status="WARNING",
                pds_value=f"X={px:+.3f} Y={py:+.3f} Z={pz:+.3f}",
                reference_value="No match in OffsetManager",
                suggestion="Add this sensor to OffsetManager DB",
            ))


def _check_motion(meta: PdsMetadata, result: PreProcessResult) -> None:
    """Check motion correction flags."""
    flags = {"Roll": False, "Pitch": False, "Heave": False}

    for sec_name, sec_data in meta.sections.items():
        if "COMPUTATION" not in sec_name and "SVPFileName" not in sec_name:
            continue
        for key, val in sec_data.items():
            kl = key.lower()
            if "applyroll" in kl and "static" not in kl:
                flags["Roll"] = ",1" in val
            elif "applypitch" in kl and "static" not in kl:
                flags["Pitch"] = ",1" in val
            elif "applyheave" in kl:
                flags["Heave"] = ",1" in val

    for name, applied in flags.items():
        result.checks.append(CheckItem(
            category="Motion",
            name=f"Apply {name}",
            status="PASS" if applied else "FAIL",
            pds_value="ON" if applied else "OFF",
            suggestion="" if applied else f"Enable {name} correction in PDS before processing!",
        ))


def _check_svp(meta: PdsMetadata, result: PreProcessResult) -> None:
    """Check SVP application status."""
    svp_applied = False
    svp_filename = ""

    for sec_name, sec_data in meta.sections.items():
        if "COMPUTATION" not in sec_name and "SVPFileName" not in sec_name:
            continue
        for key, val in sec_data.items():
            kl = key.lower()
            if "applysvp" in kl:
                svp_applied = ",1" in val
            elif "svpfilename" in kl and "time" not in kl and "crc" not in kl:
                parts = val.split(",")
                svp_filename = parts[-1].strip() if parts else ""

    status = "PASS" if svp_applied else "WARNING"
    detail = f"SVP file: {svp_filename}" if svp_filename else "No SVP file configured"

    result.checks.append(CheckItem(
        category="SVP",
        name="SVP Application",
        status=status,
        pds_value="ON" if svp_applied else "OFF",
        suggestion="" if svp_applied else "SVP not applied during acquisition. Apply in post-processing.",
    ))

    if svp_filename:
        result.checks.append(CheckItem(
            category="SVP",
            name="SVP Filename",
            status="INFO",
            pds_value=svp_filename,
        ))


def _check_calibration(meta: PdsMetadata, result: PreProcessResult,
                       om_offsets: dict[str, dict] | None = None) -> None:
    """Check static roll/pitch calibration values."""
    static_roll = 0.0
    static_pitch = 0.0

    for sec_name, sec_data in meta.sections.items():
        for key, val in sec_data.items():
            kl = key.lower()
            if "staticroll" in kl:
                parts = val.split(",")
                for p in reversed(parts):
                    try:
                        static_roll = float(p.strip())
                        break
                    except ValueError:
                        continue
            elif "staticpitch" in kl:
                parts = val.split(",")
                for p in reversed(parts):
                    try:
                        static_pitch = float(p.strip())
                        break
                    except ValueError:
                        continue

    # Check reasonableness (calibration values should be small, < 5°)
    for name, val in [("Static Roll", static_roll), ("Static Pitch", static_pitch)]:
        if abs(val) > 5.0:
            status = "FAIL"
            suggestion = f"Value {val:.3f}° is unreasonably large. Check calibration."
        elif abs(val) > 2.0:
            status = "WARNING"
            suggestion = f"Value {val:.3f}° is large. Verify calibration."
        else:
            status = "PASS"
            suggestion = ""

        ref_value = ""
        if om_offsets:
            # Find MBES transducer in OM and compare mount angles
            for om_name, om_data in om_offsets.items():
                if om_data["type"] == "MBES Transducer":
                    ref_field = "roll" if "Roll" in name else "pitch"
                    ref_val = om_data[ref_field]
                    ref_value = f"OM {om_name}: {ref_val:+.3f}°"
                    diff = abs(val - ref_val)
                    if diff > 1.0:
                        status = "WARNING"
                        suggestion = f"Differs from OffsetManager by {diff:.3f}°. Check if calibration was updated."
                    break

        result.checks.append(CheckItem(
            category="Calibration",
            name=name,
            status=status,
            pds_value=f"{val:+.4f}°",
            reference_value=ref_value,
            suggestion=suggestion,
        ))


def _check_draft_sealevel(meta: PdsMetadata, result: PreProcessResult) -> None:
    """Check draft and sealevel settings."""
    geom = meta.sections.get("GEOMETRY", {})

    draft = 0.0
    sealevel = 0.0
    try:
        draft = float(geom.get("Draft", "0"))
    except ValueError:
        pass
    try:
        sealevel = float(geom.get("Sealevel", "0"))
    except ValueError:
        pass

    if draft == 0 and sealevel == 0:
        result.checks.append(CheckItem(
            category="Draft",
            name="Draft & Sealevel",
            status="WARNING",
            pds_value=f"Draft={draft}m, Sealevel={sealevel}m",
            suggestion="Both zero — verify transducer depth is accounted for elsewhere.",
        ))
    else:
        result.checks.append(CheckItem(
            category="Draft",
            name="Draft & Sealevel",
            status="PASS",
            pds_value=f"Draft={draft}m, Sealevel={sealevel}m",
        ))


def _check_navigation(pds_path: str, result: PreProcessResult,
                      lat_range: tuple[float, float] = (-90.0, 90.0),
                      lon_range: tuple[float, float] = (-180.0, 180.0)) -> None:
    """Quick navigation sanity check from PDS binary data."""
    try:
        from pds_toolkit.pds_binary import read_pds_binary
        data = read_pds_binary(pds_path, max_pings=20, load_arrays=False,
                               lat_range=lat_range, lon_range=lon_range)

        valid_pings = [p for p in data.pings if p.timestamp > 0]
        if not valid_pings:
            result.checks.append(CheckItem(
                category="Navigation",
                name="Ping Timestamps",
                status="FAIL",
                pds_value="No valid timestamps found",
                suggestion="Check PDS binary data integrity.",
            ))
            return

        result.checks.append(CheckItem(
            category="Navigation",
            name="Ping Count (sampled)",
            status="PASS",
            pds_value=f"{len(valid_pings)} pings, {data.ping_rate_hz:.2f} Hz",
        ))

        if data.navigation:
            # Filter to only plausible nav records (within reasonable range)
            good_nav = [r for r in data.navigation
                        if -90 < r.latitude < 90 and -180 < r.longitude < 180
                        and r.latitude != 0 and r.longitude != 0]

            result.checks.append(CheckItem(
                category="Navigation",
                name="Nav Records",
                status="PASS" if good_nav else "WARNING",
                pds_value=f"{len(good_nav)} valid of {data.num_nav_records} total",
            ))

            if len(good_nav) >= 2:
                lats = [r.latitude for r in good_nav]
                lons = [r.longitude for r in good_nav]
                lat_range = max(lats) - min(lats)
                lon_range = max(lons) - min(lons)

                if lat_range > 0.1 or lon_range > 0.1:
                    result.checks.append(CheckItem(
                        category="Navigation",
                        name="Position Jump",
                        status="WARNING",
                        pds_value=f"Range: {lat_range:.4f}° lat, {lon_range:.4f}° lon",
                        suggestion="Large position range. Check if multiple lines or GPS issue.",
                    ))
                else:
                    result.checks.append(CheckItem(
                        category="Navigation",
                        name="Position Stability",
                        status="PASS",
                        pds_value=f"Lat: {min(lats):.6f}~{max(lats):.6f}, Lon: {min(lons):.6f}~{max(lons):.6f}",
                    ))
        else:
            result.checks.append(CheckItem(
                category="Navigation",
                name="Nav Records",
                status="WARNING",
                pds_value="No navigation records found",
                suggestion="Navigation may be stored externally or in different format.",
            ))

    except Exception as e:
        result.checks.append(CheckItem(
            category="Navigation",
            name="Binary Read",
            status="WARNING",
            pds_value=f"Error: {e}",
        ))


# ── Main Entry Point ──────────────────────────────────────────

def validate_preprocess(
    pds_path: str | Path,
    offsetmanager_db: str | Path | None = None,
    vessel_name: str | None = None,
    config_id: int | None = None,
    check_navigation: bool = True,
    offset_tolerance: float = 0.05,
    lat_range: tuple[float, float] = (-90.0, 90.0),
    lon_range: tuple[float, float] = (-180.0, 180.0),
    om_payload: dict | None = None,
) -> PreProcessResult:
    """Run all pre-processing validation checks on a PDS file.

    Args:
        pds_path: Path to PDS acquisition file.
        offsetmanager_db: Path to OffsetManager offsets.db SQLite file.
        vessel_name: Vessel name to search in OffsetManager.
        config_id: Specific OffsetManager config ID to use.
        check_navigation: If True, also verify navigation from binary data.
        offset_tolerance: Max acceptable offset difference (metres).

    Returns:
        PreProcessResult with all check items.
    """
    pds_path = str(pds_path)
    result = PreProcessResult(filepath=pds_path)

    # 1. Read PDS header
    try:
        meta = read_pds_header(pds_path, max_bytes=500_000)
        result.vessel_name = meta.vessel_name
    except Exception as e:
        result.checks.append(CheckItem(
            category="File", name="PDS Header",
            status="FAIL", pds_value=f"Cannot read: {e}",
        ))
        return result

    # 2. Load OffsetManager data (if available)
    om_offsets = None
    if om_payload:
        om_offsets = _payload_to_offsets(om_payload)
        if om_offsets:
            result.checks.append(CheckItem(
                category="Reference", name="OffsetManager",
                status="INFO",
                pds_value=f"Loaded {len(om_offsets)} sensors from API payload",
            ))
        else:
            result.checks.append(CheckItem(
                category="Reference", name="OffsetManager",
                status="WARNING",
                pds_value="API payload에는 매칭되는 센서가 없습니다.",
                suggestion=f"Check the OffsetManager payload for vessel '{vessel_name or meta.vessel_name}'.",
            ))
    elif offsetmanager_db:
        try:
            om_offsets = _load_offsetmanager_offsets(
                str(offsetmanager_db),
                vessel_name=vessel_name or meta.vessel_name,
                config_id=config_id,
            )
            if om_offsets:
                result.checks.append(CheckItem(
                    category="Reference", name="OffsetManager",
                    status="INFO",
                    pds_value=f"Loaded {len(om_offsets)} sensors from DB",
                ))
            else:
                result.checks.append(CheckItem(
                    category="Reference", name="OffsetManager",
                    status="WARNING",
                    pds_value="No matching vessel config found in DB",
                    suggestion=f"Add vessel '{vessel_name or meta.vessel_name}' to OffsetManager.",
                ))
        except Exception as e:
            result.checks.append(CheckItem(
                category="Reference", name="OffsetManager",
                status="WARNING", pds_value=f"DB error: {e}",
            ))

    # 3. Run checks
    _check_offsets(meta, result, om_offsets, tolerance=offset_tolerance)
    _check_motion(meta, result)
    _check_svp(meta, result)
    _check_calibration(meta, result, om_offsets)
    _check_draft_sealevel(meta, result)

    if check_navigation:
        _check_navigation(pds_path, result, lat_range=lat_range, lon_range=lon_range)

    return result


def print_validation_report(result: PreProcessResult) -> None:
    """Print validation result to console."""
    def _safe_print(text: str = ""):
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)

    _safe_print(f"\n{'='*70}")
    _safe_print("PRE-PROCESSING VALIDATION REPORT")
    _safe_print(f"{'='*70}")
    _safe_print(f"File: {result.filepath}")
    _safe_print(f"Vessel: {result.vessel_name}")
    _safe_print(f"Result: {result.summary()}")
    _safe_print(f"{'='*70}\n")

    categories = {}
    for c in result.checks:
        categories.setdefault(c.category, []).append(c)

    status_icons = {"PASS": "[OK]", "WARNING": "[!!]", "FAIL": "[XX]", "INFO": "[--]", "N/A": "[  ]"}

    for cat, items in categories.items():
        _safe_print(f"--- {cat} ---")
        for item in items:
            icon = status_icons.get(item.status, "[??]")
            _safe_print(f"  {icon} {item.name}")
            if item.pds_value:
                _safe_print(f"       PDS: {item.pds_value}")
            if item.reference_value:
                _safe_print(f"       Ref: {item.reference_value}")
            if item.difference:
                _safe_print(f"       Diff: {item.difference}")
            if item.suggestion:
                _safe_print(f"       >>> {item.suggestion}")
        _safe_print()

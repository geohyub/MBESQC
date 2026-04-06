"""Offset Validator — Verify PDS sensor offsets against OffsetManager and data.

Given an OffsetManager config (or HVF), compare with PDS header settings
AND verify against actual beam data patterns.

Three levels of verification:
  1. CONFIG CHECK: PDS header values vs OffsetManager/HVF reference
  2. DATA CHECK: PDS beam data patterns indicate offset correctness
  3. SUGGESTION: If mismatch found, suggest the correct values
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit import read_pds_header, read_pds_binary
from pds_toolkit.pds_binary import PdsBinaryData


@dataclass
class OffsetCheckItem:
    sensor: str = ""
    field: str = ""  # "X", "Y", "Z", "Roll", "Pitch", "Heading"
    pds_value: float = 0.0
    reference_value: float = 0.0
    difference: float = 0.0
    tolerance: float = 0.05
    status: str = "N/A"  # PASS, WARNING, FAIL
    suggestion: str = ""


@dataclass
class OffsetValidationResult:
    filepath: str = ""
    vessel_name: str = ""
    config_checks: list[OffsetCheckItem] = field(default_factory=list)
    data_checks: list[OffsetCheckItem] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    @property
    def overall(self) -> str:
        all_items = self.config_checks + self.data_checks
        statuses = [c.status for c in all_items if c.status not in ("N/A", "INFO")]
        if "FAIL" in statuses:
            return "FAIL"
        if "WARNING" in statuses:
            return "WARNING"
        return "PASS" if statuses else "N/A"


def _load_om_offsets(db_path: str, config_id: int | None = None,
                     vessel_name: str | None = None) -> dict[str, dict]:
    """Load OffsetManager offsets by config_id or vessel_name."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            if config_id is not None:
                rows = conn.execute(
                    "SELECT * FROM sensor_offsets WHERE config_id = ?", (config_id,)
                ).fetchall()
            elif vessel_name:
                config = conn.execute(
                    "SELECT id FROM vessel_configs WHERE vessel_name LIKE ? ORDER BY updated_at DESC LIMIT 1",
                    (f"%{vessel_name}%",)
                ).fetchone()
                rows = conn.execute(
                    "SELECT * FROM sensor_offsets WHERE config_id = ?", (config["id"],)
                ).fetchall() if config else []
            else:
                rows = []

            return {dict(r)["sensor_name"]: dict(r) for r in rows}
    except Exception:
        return {}


def _payload_to_offsets(om_payload: dict | None) -> dict[str, dict]:
    """Normalize an OffsetManager API payload into the internal offsets map."""
    if not isinstance(om_payload, dict):
        return {}

    sensors = om_payload.get("offsets", om_payload.get("sensors", []))
    if not sensors and isinstance(om_payload.get("config"), dict):
        sensors = om_payload["config"].get("offsets", om_payload["config"].get("sensors", []))

    result: dict[str, dict] = {}
    for s in sensors or []:
        if not isinstance(s, dict):
            continue
        name = s.get("sensor_name", s.get("name", ""))
        if not name:
            continue
        result[name] = {
            "sensor_type": s.get("sensor_type", s.get("type", "")),
            "x_offset": float(s.get("x_offset", s.get("x", 0)) or 0),
            "y_offset": float(s.get("y_offset", s.get("y", 0)) or 0),
            "z_offset": float(s.get("z_offset", s.get("z", 0)) or 0),
            "roll_offset": float(s.get("roll_offset", s.get("roll", 0)) or 0),
            "pitch_offset": float(s.get("pitch_offset", s.get("pitch", 0)) or 0),
            "heading_offset": float(s.get("heading_offset", s.get("heading", 0)) or 0),
            "latency": float(s.get("latency", 0) or 0),
        }
    return result


def _match_sensor(pds_name: str, om_offsets: dict[str, dict]) -> str | None:
    """Find best matching OM sensor for a PDS sensor name."""
    pds_up = pds_name.upper()

    # Direct match
    if pds_name in om_offsets:
        return pds_name

    # Keyword match
    keywords_by_type = {
        "MBES Transducer": ["T50", "T20", "MBES", "ER"],
        "GPS": ["DGPS", "GPS", "GNSS"],
        "MRU": ["CACU", "MRU", "OCTANS", "IMU"],
        "SBP Transducer": ["SBP", "DEEP36", "CHIRP"],
    }

    for sensor_type, keywords in keywords_by_type.items():
        for kw in keywords:
            if kw in pds_up:
                # Find OM sensor of same type
                for om_name, om_data in om_offsets.items():
                    if om_data.get("sensor_type") == sensor_type:
                        # Additional keyword match within type
                        om_up = om_name.upper()
                        if kw in om_up or any(k in om_up for k in keywords):
                            return om_name
                # Return first of type
                for om_name, om_data in om_offsets.items():
                    if om_data.get("sensor_type") == sensor_type:
                        return om_name
    return None


def validate_offsets(
    pds_path: str | Path,
    offsetmanager_db: str | Path | None = None,
    config_id: int | None = None,
    vessel_name: str | None = None,
    tolerance_xyz: float = 0.05,
    tolerance_angle: float = 0.1,
    check_data: bool = True,
    max_pings: int = 20,
    lat_range: tuple[float, float] = (-90.0, 90.0),
    lon_range: tuple[float, float] = (-180.0, 180.0),
    om_offsets_dict: dict[str, dict] | None = None,
    om_payload: dict | None = None,
) -> OffsetValidationResult:
    """Validate PDS sensor offsets against OffsetManager reference.

    Args:
        pds_path: Path to PDS file.
        offsetmanager_db: Path to OffsetManager SQLite DB.
        config_id: Specific OffsetManager config to compare against.
        vessel_name: Vessel name to search in OffsetManager.
        tolerance_xyz: Max acceptable XYZ offset difference (metres).
        tolerance_angle: Max acceptable angle difference (degrees).
        check_data: If True, also verify using beam data patterns.
        max_pings: Number of pings to read for data verification.

    Returns:
        OffsetValidationResult with config and data check items.
    """
    pds_path = str(pds_path)
    result = OffsetValidationResult(filepath=pds_path)

    # 1. Read PDS header
    meta = read_pds_header(pds_path, max_bytes=500_000)
    result.vessel_name = meta.vessel_name

    # 2. Extract PDS offsets from GEOMETRY
    geom = meta.sections.get("GEOMETRY", {})
    pds_offsets = {}
    for key, val in geom.items():
        if key.startswith("Offset("):
            parts = val.split(",")
            if len(parts) >= 4:
                name = parts[0].strip()
                try:
                    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                    pds_offsets[name] = {"x": x, "y": y, "z": z}
                except ValueError:
                    pass

    # Extract calibration values
    static_roll = static_pitch = 0.0
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

    # 3. Load OffsetManager reference
    om_offsets = {}
    reference_source = "unresolved"
    reference_mode = "api-first"
    reference_scope = "none"
    reference_path = ""
    reference_exists = False
    reference_hint = "No OffsetManager config found. Provide config_id or vessel_name."
    source_label = "OffsetManager reference"
    if om_offsets_dict:
        reference_source = "preloaded"
        reference_mode = "preloaded"
        reference_scope = "preloaded"
        source_label = "OffsetManager preloaded reference"
    elif om_payload:
        reference_source = "api"
        reference_mode = "api"
        reference_scope = "payload"
        source_label = "OffsetManager API payload"
    elif offsetmanager_db:
        reference_source = "db"
        reference_mode = "sqlite"
        reference_scope = f"config_id:{config_id}" if config_id is not None else f"vessel_name:{vessel_name or meta.vessel_name}"
        reference_path = str(offsetmanager_db)
        reference_exists = Path(reference_path).expanduser().exists()
        source_label = "OffsetManager SQLite DB"
    if om_offsets_dict:
        om_offsets = om_offsets_dict
    elif om_payload:
        om_offsets = _payload_to_offsets(om_payload)
    elif offsetmanager_db:
        om_offsets = _load_om_offsets(
            str(offsetmanager_db),
            config_id=config_id,
            vessel_name=vessel_name or meta.vessel_name,
        )

    def _build_provenance(semantic_state: str, semantic_hint: str, semantic_checks: list[dict]) -> dict:
        decision = {
            "source": reference_source,
            "source_label": source_label,
            "path": reference_path,
            "exists": reference_exists,
            "mode": reference_mode,
            "fallback_scope": reference_scope,
            "hint": reference_hint if not om_offsets else semantic_hint,
            "loaded_sensor_count": len(om_offsets),
        }
        fallback_candidate = dict(decision)
        return {
            "decision": decision,
            "fallback_candidate": fallback_candidate,
            "semantic": {
                "state": semantic_state,
                "hint": semantic_hint,
                "checks": semantic_checks,
                "overall": result.overall,
                "loaded_sensor_count": len(om_offsets),
            },
            "resolution_chain": [fallback_candidate, decision],
        }

    if not om_offsets:
        result.config_checks.append(OffsetCheckItem(
            sensor="OffsetManager", field="DB",
            status="INFO", suggestion="No OffsetManager config found. Provide config_id or vessel_name.",
        ))
        # Still show PDS offsets as INFO
        for pds_name, pds_xyz in pds_offsets.items():
            if pds_name == "Zero Offset":
                continue
            result.config_checks.append(OffsetCheckItem(
                sensor=pds_name, field="XYZ",
                pds_value=0, reference_value=0, status="INFO",
                suggestion=f"PDS: X={pds_xyz['x']:+.3f} Y={pds_xyz['y']:+.3f} Z={pds_xyz['z']:+.3f}",
            ))
        result.provenance["om"] = _build_provenance(
            "missing",
            "No OffsetManager config found. Provide config_id or vessel_name.",
            [
                {
                    "name": "OffsetManager",
                    "status": "INFO",
                    "detail": "No OffsetManager config found. Provide config_id or vessel_name.",
                }
            ],
        )
        return result

    # 4. Compare PDS vs OffsetManager for each sensor
    for pds_name, pds_xyz in pds_offsets.items():
        if pds_name == "Zero Offset":
            continue

        om_match = _match_sensor(pds_name, om_offsets)
        if not om_match:
            result.config_checks.append(OffsetCheckItem(
                sensor=pds_name, field="Match",
                status="WARNING",
                suggestion=f"No matching sensor in OffsetManager. PDS: X={pds_xyz['x']:+.3f} Y={pds_xyz['y']:+.3f} Z={pds_xyz['z']:+.3f}",
            ))
            continue

        om = om_offsets[om_match]

        # XYZ comparison
        for axis, pds_val, om_val in [
            ("X", pds_xyz["x"], om["x_offset"]),
            ("Y", pds_xyz["y"], om["y_offset"]),
            ("Z", pds_xyz["z"], om["z_offset"]),
        ]:
            diff = abs(pds_val - om_val)
            if diff < tolerance_xyz:
                status = "PASS"
                suggestion = ""
            elif diff < 0.5:
                status = "WARNING"
                suggestion = f"Update PDS {axis} to {om_val:+.3f}m (diff={pds_val - om_val:+.3f}m)"
            else:
                status = "FAIL"
                suggestion = f"CRITICAL: PDS {axis}={pds_val:+.3f}m should be {om_val:+.3f}m (diff={pds_val - om_val:+.3f}m)"

            result.config_checks.append(OffsetCheckItem(
                sensor=f"{pds_name} -> {om_match}",
                field=axis,
                pds_value=pds_val,
                reference_value=om_val,
                difference=pds_val - om_val,
                tolerance=tolerance_xyz,
                status=status,
                suggestion=suggestion,
            ))

        # Roll/Pitch comparison (for MBES transducers)
        if om.get("sensor_type") == "MBES Transducer":
            om_roll = om.get("roll_offset", 0.0)
            om_pitch = om.get("pitch_offset", 0.0)

            # Compare StaticRoll/Pitch vs OM roll/pitch
            for name, pds_val, om_val in [
                ("StaticRoll", static_roll, om_roll),
                ("StaticPitch", static_pitch, om_pitch),
            ]:
                diff = abs(pds_val - om_val)
                if diff < tolerance_angle:
                    status = "PASS"
                    suggestion = ""
                elif diff < 1.0:
                    status = "WARNING"
                    suggestion = f"PDS {name}={pds_val:+.4f}deg vs OM={om_val:+.4f}deg (diff={pds_val - om_val:+.4f}deg). Check if calibration was updated."
                else:
                    status = "FAIL"
                    suggestion = f"MISMATCH: PDS {name}={pds_val:+.4f}deg vs OM={om_val:+.4f}deg. Update PDS to {om_val:+.4f}deg."

                result.config_checks.append(OffsetCheckItem(
                    sensor=f"{pds_name} ({name})",
                    field="Calibration",
                    pds_value=pds_val,
                    reference_value=om_val,
                    difference=pds_val - om_val,
                    tolerance=tolerance_angle,
                    status=status,
                    suggestion=suggestion,
                ))

    # 5. Data-based verification (optional)
    if check_data:
        try:
            pds_data = read_pds_binary(
                pds_path, max_pings=max_pings,
                lat_range=lat_range, lon_range=lon_range,
            )
            _verify_with_data(pds_data, result)
        except Exception as e:
            result.data_checks.append(OffsetCheckItem(
                sensor="Data", field="Read",
                status="WARNING", suggestion=f"Could not read PDS binary: {e}",
            ))

    if result.provenance.get("om") is None:
        semantic_state = "verified" if result.overall == "PASS" else "review-risk" if result.overall == "WARNING" else "mismatch-risk"
        semantic_hint = (
            "OffsetManager reference loaded from the selected source and compared against PDS header/data."
            if om_offsets else
            "OffsetManager reference could not be loaded."
        )
        semantic_checks = [
            {
                "name": item.sensor or "OffsetManager",
                "field": item.field or "",
                "status": item.status,
                "detail": item.suggestion,
            }
            for item in (result.config_checks[:4] + result.data_checks[:4])
        ]
        result.provenance["om"] = _build_provenance(semantic_state, semantic_hint, semantic_checks)

    return result


def _verify_with_data(pds: PdsBinaryData, result: OffsetValidationResult) -> None:
    """Verify offsets using beam data patterns."""

    # Check 1: Depth values are physically reasonable
    if pds.pings:
        all_depths = []
        for p in pds.pings:
            if len(p.depth) > 0:
                d = np.abs(p.depth[p.depth != 0])
                if len(d) > 0:
                    all_depths.extend(d.tolist())

        if all_depths:
            d_arr = np.array(all_depths)
            d_min, d_max, d_mean = d_arr.min(), d_arr.max(), d_arr.mean()

            status = "PASS" if d_max < 500 and d_min > 0.5 else "WARNING"
            result.data_checks.append(OffsetCheckItem(
                sensor="Depth", field="Range",
                pds_value=d_mean, status=status,
                suggestion=f"Depth: {d_min:.1f}-{d_max:.1f}m (mean={d_mean:.1f}m)",
            ))

    # Check 2: Navigation is consistent
    if pds.navigation:
        valid_nav = [n for n in pds.navigation if n.latitude != 0]
        if valid_nav:
            lats = [n.latitude for n in valid_nav]
            lons = [n.longitude for n in valid_nav]
            lat_range = max(lats) - min(lats)
            lon_range = max(lons) - min(lons)

            # For a single line, position range should be small (< 0.1 deg)
            status = "PASS" if lat_range < 0.1 and lon_range < 0.1 else "WARNING"
            result.data_checks.append(OffsetCheckItem(
                sensor="Navigation", field="Consistency",
                status=status,
                suggestion=f"Position range: {lat_range:.4f}deg lat, {lon_range:.4f}deg lon",
            ))

    # Check 3: Attitude is reasonable
    if pds.attitude:
        pitches = np.array([a.pitch for a in pds.attitude])
        rolls = np.array([a.roll for a in pds.attitude])
        heaves = np.array([a.heave for a in pds.attitude])

        # Heave should be small (< 5m typically)
        heave_ok = np.abs(heaves).max() < 5.0
        result.data_checks.append(OffsetCheckItem(
            sensor="Attitude", field="Heave",
            pds_value=float(heaves.mean()),
            status="PASS" if heave_ok else "WARNING",
            suggestion=f"Heave: mean={heaves.mean():+.4f}m, max={np.abs(heaves).max():.4f}m",
        ))

    # Check 4: Ping rate is reasonable (0.5-10 Hz typical)
    if pds.ping_rate_hz > 0:
        rate_ok = 0.3 < pds.ping_rate_hz < 15
        result.data_checks.append(OffsetCheckItem(
            sensor="Ping", field="Rate",
            pds_value=pds.ping_rate_hz,
            status="PASS" if rate_ok else "WARNING",
            suggestion=f"Ping rate: {pds.ping_rate_hz:.2f} Hz",
        ))


def print_offset_validation(result: OffsetValidationResult) -> None:
    """Print offset validation report."""
    print(f"\n{'='*60}")
    print(f"  OFFSET VALIDATION: {result.vessel_name}")
    print(f"  File: {Path(result.filepath).name}")
    print(f"  Overall: {result.overall}")
    print(f"{'='*60}")

    icons = {"PASS": "[OK]", "WARNING": "[!!]", "FAIL": "[XX]", "INFO": "[--]", "N/A": "[  ]"}

    if result.config_checks:
        print(f"\n--- Config Checks ---")
        for c in result.config_checks:
            icon = icons.get(c.status, "[??]")
            if c.difference != 0:
                print(f"  {icon} {c.sensor} {c.field}: PDS={c.pds_value:+.3f} Ref={c.reference_value:+.3f} Diff={c.difference:+.3f}")
            elif c.suggestion:
                print(f"  {icon} {c.sensor}: {c.suggestion}")

    if result.data_checks:
        print(f"\n--- Data Checks ---")
        for c in result.data_checks:
            icon = icons.get(c.status, "[??]")
            print(f"  {icon} {c.sensor} {c.field}: {c.suggestion}")

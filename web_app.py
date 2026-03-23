"""MBES QC — Flask Web Application

Multibeam Echosounder data quality control web interface.
PDS 파일 하나 넣으면 전체 QC 자동 실행.

Port: 5016
Copyright (c) 2025-2026 Geoview Co., Ltd.
"""

from __future__ import annotations

import os
import sys
import json
import time
import threading
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, send_file

sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get("MBESQC_SECRET", os.urandom(24).hex())


@app.template_filter('basename')
def basename_filter(path):
    return Path(path).name if path else ''

# ── In-memory storage ─────────────────────────────────────

_jobs: dict[int, dict] = {}
_projects: dict[int, dict] = {}
_job_counter = 0
_project_counter = 0
_lock = threading.Lock()

OM_DB_PATH = r"E:\Software\GeoView_Suite\OffsetManager\offsets.db"


def _next_id(counter_name: str) -> int:
    global _job_counter, _project_counter
    with _lock:
        if counter_name == 'job':
            _job_counter += 1
            return _job_counter
        else:
            _project_counter += 1
            return _project_counter


# ── Routes ─────────────────────────────────────────────────

@app.route("/")
def dashboard():
    completed = [j for j in _jobs.values() if j.get("status") == "done"]
    completed.sort(key=lambda j: j.get("finished_at", ""), reverse=True)
    return render_template("dashboard.html", jobs=completed, total_jobs=len(_jobs),
                           projects=list(_projects.values()))


@app.route("/new-project", methods=["GET", "POST"])
def new_project():
    """Create a new project and register data files."""
    if request.method == "POST":
        data = request.form
        proj_id = _next_id('project')
        project = {
            "id": proj_id,
            "name": data.get("project_name", f"Project {proj_id}"),
            "vessel": data.get("vessel_name", ""),
            "created_at": datetime.now().isoformat(),
            "pds_files": [],
            "gsf_dir": data.get("gsf_dir", ""),
            "hvf_dir": data.get("hvf_dir", ""),
            "s7k_dir": data.get("s7k_dir", ""),
            "fau_dir": data.get("fau_dir", ""),
            "om_config_id": data.get("om_config_id", ""),
            "jobs": [],
        }

        # Auto-scan PDS directory
        pds_dir = data.get("pds_dir", "")
        if pds_dir and Path(pds_dir).is_dir():
            for f in sorted(Path(pds_dir).glob("*.pds")):
                project["pds_files"].append({
                    "path": str(f),
                    "name": f.name,
                    "size_mb": f.stat().st_size / 1024 / 1024,
                })
        # Or single PDS file
        pds_file = data.get("pds_file", "")
        if pds_file and Path(pds_file).exists():
            f = Path(pds_file)
            project["pds_files"].append({
                "path": str(f),
                "name": f.name,
                "size_mb": f.stat().st_size / 1024 / 1024,
            })

        _projects[proj_id] = project
        flash(f"Project '{project['name']}' created with {len(project['pds_files'])} PDS files.", "success")
        return redirect(url_for("view_project", project_id=proj_id))

    om_configs = _load_om_configs()
    return render_template("new_project.html", om_configs=om_configs)


@app.route("/project/<int:project_id>")
def view_project(project_id):
    """View project with registered files and QC results."""
    project = _projects.get(project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("dashboard"))
    project_jobs = [_jobs[jid] for jid in project.get("jobs", []) if jid in _jobs]
    return render_template("project.html", project=project, jobs=project_jobs,
                           om_configs=_load_om_configs())


@app.route("/new-qc")
def new_qc():
    om_configs = _load_om_configs()
    return render_template("new_qc.html", om_configs=om_configs)


@app.route("/qc/<int:job_id>")
def qc_result(job_id):
    job = _jobs.get(job_id)
    if not job:
        flash("QC job not found.", "danger")
        return redirect(url_for("dashboard"))
    return render_template("qc_result.html", job=job)


# ── API Endpoints ──────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "module": "MBES QC", "port": 5016, "jobs": len(_jobs)})


def _validate_pds_path(pds_path: str) -> str | None:
    """Validate PDS path: must exist and have .pds extension."""
    if not pds_path:
        return None
    resolved = Path(pds_path).resolve()
    if not resolved.exists():
        return None
    if resolved.suffix.lower() != '.pds':
        return None
    return str(resolved)


@app.route("/api/pds-info", methods=["POST"])
def api_pds_info():
    """Quick PDS header analysis (no binary parsing)."""
    pds_path = _validate_pds_path(request.json.get("pds_path", ""))
    if not pds_path:
        return jsonify({"error": "Invalid or missing PDS file path"}), 400

    try:
        from pds_toolkit import read_pds_header
        meta = read_pds_header(pds_path, max_bytes=500_000)

        # Extract sensor offsets
        geom = meta.sections.get("GEOMETRY", {})
        sensors = {}
        for key, val in geom.items():
            if key.startswith("Offset("):
                parts = val.split(",")
                if len(parts) >= 4:
                    name = parts[0].strip()
                    try:
                        sensors[name] = {
                            "x": float(parts[1]), "y": float(parts[2]), "z": float(parts[3])
                        }
                    except ValueError:
                        pass

        # Extract calibration
        static_roll = static_pitch = 0.0
        apply_roll = apply_pitch = apply_heave = apply_svp = False
        svp_file = ""

        for sec_name, sec_data in meta.sections.items():
            for key, val in sec_data.items():
                kl = key.lower()
                if "staticroll" in kl:
                    for p in reversed(val.split(",")):
                        try: static_roll = float(p.strip()); break
                        except: continue
                elif "staticpitch" in kl:
                    for p in reversed(val.split(",")):
                        try: static_pitch = float(p.strip()); break
                        except: continue
                elif "applyroll" in kl and "static" not in kl:
                    apply_roll = ",1" in val
                elif "applypitch" in kl and "static" not in kl:
                    apply_pitch = ",1" in val
                elif "applyheave" in kl:
                    apply_heave = ",1" in val
                elif "applysvp" in kl:
                    apply_svp = ",1" in val
                elif "svpfilename" in kl and "time" not in kl:
                    parts = val.split(",")
                    svp_file = parts[-1].strip() if parts else ""

        return jsonify({
            "vessel_name": meta.vessel_name,
            "survey_type": meta.survey_type,
            "pds_version": meta.pds_version,
            "file_version": meta.file_version,
            "coord_system": meta.coord_system_name,
            "start_time": meta.start_time,
            "sensors": sensors,
            "static_roll": static_roll,
            "static_pitch": static_pitch,
            "apply_roll": apply_roll,
            "apply_pitch": apply_pitch,
            "apply_heave": apply_heave,
            "apply_svp": apply_svp,
            "svp_file": svp_file,
            "sealevel": float(geom.get("Sealevel", "0") or "0"),
            "draft": float(geom.get("Draft", "0") or "0"),
            "file_size_mb": os.path.getsize(pds_path) / 1024 / 1024,
            "_pds_path": pds_path,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-offsets", methods=["POST"])
def api_verify_offsets():
    """Cross-validate PDS offsets against HVF and/or OffsetManager."""
    try:
        data = request.json
        pds_path = data.get("pds_path", "")
        hvf_dir = data.get("hvf_dir", "")
        om_config_id = data.get("om_config_id", "")

        if not os.path.isfile(pds_path):
            return jsonify({"error": "PDS file not found"}), 400

        # Read PDS header for offsets
        from pds_toolkit import read_pds_header
        meta = read_pds_header(pds_path)
        geom = meta.sections.get("GEOMETRY", {})

        pds_offsets = {}
        for key, val in geom.items():
            if key.startswith("Offset("):
                parts = val.split(",")
                if len(parts) >= 4:
                    name = parts[0].strip()
                    if name == "Zero Offset":
                        continue
                    pds_offsets[name] = {
                        "x": float(parts[1]),
                        "y": float(parts[2]),
                        "z": float(parts[3]),
                    }

        # PDS calibration values
        static_roll = 0.0
        static_pitch = 0.0
        for sec_data in meta.sections.values():
            for k, v in sec_data.items():
                if "StaticRoll" in k and "8193," in v:
                    try:
                        static_roll = float(v.split(",", 1)[1])
                    except (ValueError, IndexError):
                        pass
                if "StaticPitch" in k and "8193," in v:
                    try:
                        static_pitch = float(v.split(",", 1)[1])
                    except (ValueError, IndexError):
                        pass

        result = {
            "pds_offsets": pds_offsets,
            "static_roll": static_roll,
            "static_pitch": static_pitch,
            "hvf_comparison": None,
            "om_comparison": None,
            "issues": [],
        }

        # Compare with HVF if available
        if hvf_dir and os.path.isdir(hvf_dir):
            from pds_toolkit import read_hvf
            hvf_files = [f for f in os.listdir(hvf_dir) if f.endswith(".hvf")]
            if hvf_files:
                hvf_path = os.path.join(hvf_dir, hvf_files[0])
                try:
                    hvf = read_hvf(hvf_path)
                    hvf_data = {
                        "file": hvf_files[0],
                        "sensors": {},
                        "mount_roll": getattr(hvf, "mount_roll", 0.0),
                        "mount_pitch": getattr(hvf, "mount_pitch", 0.0),
                        "mount_heading": getattr(hvf, "mount_heading", 0.0),
                    }

                    # Extract HVF sensor offsets
                    if hasattr(hvf, "sensors"):
                        for s in hvf.sensors:
                            name = getattr(s, "name", "")
                            hvf_data["sensors"][name] = {
                                "x": getattr(s, "x", 0.0),
                                "y": getattr(s, "y", 0.0),
                                "z": getattr(s, "z", 0.0),
                            }

                    # Compare offsets
                    for sensor_name, pds_xyz in pds_offsets.items():
                        hvf_match = hvf_data["sensors"].get(sensor_name)
                        if hvf_match:
                            dx = abs(pds_xyz["x"] - hvf_match["x"])
                            dy = abs(pds_xyz["y"] - hvf_match["y"])
                            dz = abs(pds_xyz["z"] - hvf_match["z"])
                            if dx > 0.01 or dy > 0.01 or dz > 0.01:
                                result["issues"].append({
                                    "level": "WARN",
                                    "sensor": sensor_name,
                                    "message": f"Offset mismatch: PDS({pds_xyz['x']:.3f},{pds_xyz['y']:.3f},{pds_xyz['z']:.3f}) vs HVF({hvf_match['x']:.3f},{hvf_match['y']:.3f},{hvf_match['z']:.3f})",
                                    "delta": f"Δ=({dx:.3f},{dy:.3f},{dz:.3f})",
                                })

                    # Compare calibration
                    if abs(static_roll - hvf_data["mount_roll"]) > 0.01:
                        result["issues"].append({
                            "level": "WARN",
                            "sensor": "Calibration",
                            "message": f"Roll mismatch: PDS StaticRoll={static_roll:.4f}° vs HVF MountRoll={hvf_data['mount_roll']:.4f}°",
                            "delta": f"Δ={abs(static_roll - hvf_data['mount_roll']):.4f}°",
                        })
                    if abs(static_pitch - hvf_data["mount_pitch"]) > 0.01:
                        result["issues"].append({
                            "level": "WARN",
                            "sensor": "Calibration",
                            "message": f"Pitch mismatch: PDS StaticPitch={static_pitch:.4f}° vs HVF MountPitch={hvf_data['mount_pitch']:.4f}°",
                            "delta": f"Δ={abs(static_pitch - hvf_data['mount_pitch']):.4f}°",
                        })

                    result["hvf_comparison"] = hvf_data
                except Exception as e:
                    result["issues"].append({"level": "ERROR", "sensor": "HVF", "message": str(e), "delta": ""})

        # Compare with OffsetManager if available
        if om_config_id:
            try:
                import sqlite3
                conn = sqlite3.connect(OM_DB_PATH)
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM vessel_configs WHERE id = ?", (om_config_id,)
                ).fetchone()
                if row:
                    om_data = dict(row)
                    result["om_comparison"] = {
                        "vessel": om_data.get("vessel_name", ""),
                        "project": om_data.get("project_name", ""),
                    }
                    # Compare OM offsets with PDS offsets (if stored in OM)
                conn.close()
            except Exception:
                pass

        if not result["issues"]:
            result["issues"].append({"level": "PASS", "sensor": "All", "message": "No offset discrepancies found", "delta": ""})

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-motion", methods=["POST"])
def api_verify_motion():
    """Analyze motion data from PDS for spikes, gaps, and drift."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        max_pings = data.get("max_pings", 20)

        if not os.path.isfile(pds_path):
            return jsonify({"error": "PDS file not found"}), 400

        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(pds_path, max_pings=max_pings)

        result = {
            "nav": {"total": 0, "gaps": [], "speed": {}},
            "attitude": {"total": 0, "roll": {}, "pitch": {}, "heave": {}, "heading": {}, "spikes": []},
            "tide": {"total": 0, "range": {}},
            "ping": {"total": 0, "rate_hz": 0, "depth_range": []},
            "issues": [],
        }

        # Navigation analysis
        if pds.navigation:
            nav = pds.navigation
            result["nav"]["total"] = len(nav)
            lats = [r.latitude for r in nav if r.latitude != 0]
            lons = [r.longitude for r in nav if r.longitude != 0]
            if lats:
                result["nav"]["lat_range"] = [min(lats), max(lats)]
                result["nav"]["lon_range"] = [min(lons), max(lons)]

            # Speed from consecutive positions
            speeds = []
            for i in range(1, len(nav)):
                dt = (nav[i].timestamp - nav[i-1].timestamp) / 1000.0
                if dt > 0 and dt < 10:
                    dlat = (nav[i].latitude - nav[i-1].latitude) * 111320
                    dlon = (nav[i].longitude - nav[i-1].longitude) * 111320 * np.cos(np.radians(nav[i].latitude))
                    dist = np.sqrt(dlat**2 + dlon**2)
                    speeds.append(dist / dt)
            if speeds:
                result["nav"]["speed"] = {
                    "min": round(min(speeds), 2),
                    "max": round(max(speeds), 2),
                    "mean": round(np.mean(speeds), 2),
                    "knots_mean": round(np.mean(speeds) * 1.944, 2),
                }

            # Gap detection
            for i in range(1, len(nav)):
                dt = (nav[i].timestamp - nav[i-1].timestamp) / 1000.0
                if dt > 5.0:  # > 5 seconds = gap
                    result["nav"]["gaps"].append({
                        "index": i,
                        "duration_sec": round(dt, 1),
                    })

            if result["nav"]["gaps"]:
                result["issues"].append({
                    "level": "WARN",
                    "category": "Navigation",
                    "message": f"{len(result['nav']['gaps'])} navigation gap(s) detected (>{5}s)",
                })

        # Attitude analysis
        if pds.attitude:
            att = pds.attitude
            result["attitude"]["total"] = len(att)

            rolls = [a.roll for a in att if hasattr(a, 'roll')]
            pitches = [a.pitch for a in att if hasattr(a, 'pitch')]
            heaves = [a.heave for a in att if hasattr(a, 'heave')]
            headings = [a.heading for a in att if hasattr(a, 'heading')]

            def _stats(vals, name):
                if not vals:
                    return {}
                arr = np.array(vals)
                s = {
                    "min": round(float(arr.min()), 4),
                    "max": round(float(arr.max()), 4),
                    "mean": round(float(arr.mean()), 4),
                    "std": round(float(arr.std()), 4),
                }

                # Spike detection: values > 3*std from mean
                mean, std = arr.mean(), arr.std()
                if std > 0:
                    spikes_idx = np.where(np.abs(arr - mean) > 3 * std)[0]
                    s["spike_count"] = int(len(spikes_idx))
                    if len(spikes_idx) > 0:
                        for si in spikes_idx[:5]:
                            result["attitude"]["spikes"].append({
                                "channel": name,
                                "index": int(si),
                                "value": round(float(arr[si]), 4),
                                "threshold": round(float(mean + 3 * std), 4),
                            })
                else:
                    s["spike_count"] = 0

                # Drift detection: linear trend
                if len(arr) > 10:
                    x = np.arange(len(arr))
                    coeffs = np.polyfit(x, arr, 1)
                    s["drift_rate"] = round(float(coeffs[0]) * len(arr), 4)  # total drift over period
                    if abs(s["drift_rate"]) > 0.5:  # > 0.5° or 0.5m drift
                        result["issues"].append({
                            "level": "WARN",
                            "category": "Attitude",
                            "message": f"{name} drift detected: {s['drift_rate']:.4f}° over {len(arr)} samples",
                        })
                return s

            result["attitude"]["roll"] = _stats(rolls, "Roll")
            result["attitude"]["pitch"] = _stats(pitches, "Pitch")
            result["attitude"]["heave"] = _stats(heaves, "Heave")
            result["attitude"]["heading"] = _stats(headings, "Heading")

            # Report spikes
            total_spikes = sum(result["attitude"][ch].get("spike_count", 0) for ch in ["roll", "pitch", "heave"])
            if total_spikes > 0:
                result["issues"].append({
                    "level": "WARN",
                    "category": "Attitude",
                    "message": f"{total_spikes} attitude spike(s) detected (>3σ)",
                })

        # Tide analysis
        if hasattr(pds, 'tide') and pds.tide:
            tide = pds.tide
            result["tide"]["total"] = len(tide)
            values = [t.value for t in tide if hasattr(t, 'value')]
            if values:
                result["tide"]["range"] = {
                    "min": round(min(values), 3),
                    "max": round(max(values), 3),
                    "mean": round(np.mean(values), 3),
                }
        elif hasattr(pds, 'num_tide_records'):
            result["tide"]["total"] = pds.num_tide_records

        # Ping summary
        valid_pings = [p for p in pds.pings if len(p.depth) > 0 and np.any(p.depth != 0)]
        result["ping"]["total"] = len(valid_pings)
        result["ping"]["rate_hz"] = round(pds.ping_rate_hz, 2) if pds.ping_rate_hz else 0

        if valid_pings:
            all_depths = np.concatenate([np.abs(p.depth[p.depth != 0]) for p in valid_pings])
            if len(all_depths) > 0:
                result["ping"]["depth_range"] = [round(float(all_depths.min()), 2), round(float(all_depths.max()), 2)]

        if not result["issues"]:
            result["issues"].append({
                "level": "PASS",
                "category": "All",
                "message": "No motion anomalies detected",
            })

        return jsonify(result)

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/nav-track", methods=["POST"])
def api_nav_track():
    """Get navigation track as GeoJSON for map display."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        max_pings = data.get("max_pings", 50)

        if not os.path.isfile(pds_path):
            return jsonify({"error": "PDS file not found"}), 400

        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(pds_path, max_pings=max_pings)

        coords = []
        timestamps = []
        for r in pds.navigation:
            if r.latitude != 0 and r.longitude != 0:
                coords.append([r.longitude, r.latitude])
                if r.timestamp > 0:
                    timestamps.append(r.timestamp)

        if not coords:
            return jsonify({"error": "No navigation data found"}), 404

        # Ping positions (from depth array pings with timestamps)
        ping_points = []
        for p in pds.pings:
            if p.datetime_utc and len(p.depth) > 0:
                # Find nearest nav point
                for r in pds.navigation:
                    if r.latitude != 0 and abs(r.timestamp - p.timestamp) < 2000:
                        nadir_depth = float(np.abs(p.depth[p.depth != 0]).mean()) if np.any(p.depth != 0) else 0
                        ping_points.append({
                            "lon": r.longitude,
                            "lat": r.latitude,
                            "depth": round(nadir_depth, 1),
                            "time": str(p.datetime_utc)[:19],
                        })
                        break

        # Build GeoJSON
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "name": os.path.basename(pds_path),
                        "points": len(coords),
                    },
                }
            ],
            "ping_points": ping_points,
            "bounds": {
                "south": min(c[1] for c in coords),
                "north": max(c[1] for c in coords),
                "west": min(c[0] for c in coords),
                "east": max(c[0] for c in coords),
            },
        }

        return jsonify(geojson)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/beam-profile", methods=["POST"])
def api_beam_profile():
    """Get beam depth profile (across-track vs depth) for a single ping."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        ping_index = data.get("ping_index", 0)
        source = data.get("source", "pds")  # "pds" or "gsf"

        if not os.path.isfile(pds_path):
            return jsonify({"error": "File not found"}), 400

        if source == "pds":
            from pds_toolkit import read_pds_binary
            pds = read_pds_binary(pds_path, max_pings=ping_index + 3)
            if ping_index >= len(pds.pings):
                return jsonify({"error": f"Ping {ping_index} not found"}), 404

            p = pds.pings[ping_index]
            across = p.across_track if len(p.across_track) > 0 else np.zeros(1024)
            depth = np.abs(p.depth) if len(p.depth) > 0 else np.zeros(1024)

            # Filter valid beams
            valid = (depth > 0.1) & np.isfinite(across) & np.isfinite(depth)
            result = {
                "across": [round(float(x), 2) for x in across[valid]],
                "depth": [round(float(d), 2) for d in depth[valid]],
                "num_beams": int(valid.sum()),
                "nadir_depth": round(float(depth[len(depth)//2]), 2) if len(depth) > 0 else 0,
                "swath_width": round(float(across[valid].max() - across[valid].min()), 1) if valid.sum() > 0 else 0,
                "ping_index": ping_index,
                "source": source,
                "time": str(p.datetime_utc)[:19] if p.datetime_utc else "",
            }
        else:
            return jsonify({"error": "GSF source not yet implemented"}), 501

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/attitude-timeseries", methods=["POST"])
def api_attitude_timeseries():
    """Get attitude time series data for charting."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        max_pings = data.get("max_pings", 50)

        if not os.path.isfile(pds_path):
            return jsonify({"error": "File not found"}), 400

        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(pds_path, max_pings=max_pings)

        result = {"roll": [], "pitch": [], "heave": [], "heading": [], "timestamps": []}

        for a in pds.attitude:
            t = getattr(a, 'timestamp', 0)
            if t > 0:
                result["timestamps"].append(round(t / 1000.0, 3))  # seconds since epoch
            result["roll"].append(round(getattr(a, 'roll', 0), 4))
            result["pitch"].append(round(getattr(a, 'pitch', 0), 4))
            result["heave"].append(round(getattr(a, 'heave', 0), 4))
            result["heading"].append(round(getattr(a, 'heading', 0), 2))

        result["total"] = len(pds.attitude)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/line-stats", methods=["POST"])
def api_line_stats():
    """Get per-line depth statistics from GSF files."""
    try:
        import numpy as np
        data = request.json
        gsf_dir = data.get("gsf_dir", "")
        max_pings = data.get("max_pings", 100)

        if not os.path.isdir(gsf_dir):
            return jsonify({"error": "GSF directory not found"}), 400

        from pds_toolkit import read_gsf
        gsf_files = sorted([f for f in os.listdir(gsf_dir) if f.endswith('.gsf')])

        lines = []
        for gf in gsf_files[:20]:  # max 20 lines
            try:
                gsf_path = os.path.join(gsf_dir, gf)
                gsf = read_gsf(gsf_path, max_pings=max_pings)
                if not gsf.pings:
                    continue

                all_depths = []
                for p in gsf.pings:
                    if hasattr(p, 'depth') and len(p.depth) > 0:
                        valid = p.depth[p.depth > 0.5]
                        if len(valid) > 0:
                            all_depths.extend(valid.tolist())

                if all_depths:
                    arr = np.array(all_depths)
                    lines.append({
                        "name": gf,
                        "pings": len(gsf.pings),
                        "beams": len(all_depths),
                        "depth_min": round(float(arr.min()), 2),
                        "depth_max": round(float(arr.max()), 2),
                        "depth_mean": round(float(arr.mean()), 2),
                        "depth_std": round(float(arr.std()), 2),
                    })
            except Exception:
                continue

        # Flag outlier lines (depth mean > 2σ from overall mean)
        if len(lines) > 2:
            means = np.array([l["depth_mean"] for l in lines])
            overall_mean = float(means.mean())
            overall_std = float(means.std())
            for l in lines:
                diff = abs(l["depth_mean"] - overall_mean)
                l["outlier"] = diff > 2 * overall_std if overall_std > 0 else False

        return jsonify({"lines": lines, "total": len(lines)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/om-configs")
def api_om_configs():
    """List OffsetManager vessel configs."""
    return jsonify(_load_om_configs())


@app.route("/api/om-sensors/<int:config_id>")
def api_om_sensors(config_id):
    """Get sensor offsets for a specific OffsetManager config."""
    try:
        import sqlite3
        conn = sqlite3.connect(OM_DB_PATH)
        conn.row_factory = sqlite3.Row

        # Config info
        config = conn.execute(
            "SELECT * FROM vessel_configs WHERE id = ?", (config_id,)
        ).fetchone()
        if not config:
            conn.close()
            return jsonify({"error": "Config not found"}), 404

        # All sensors for this config
        sensors = conn.execute(
            "SELECT * FROM sensor_offsets WHERE config_id = ? ORDER BY sensor_type, sensor_name",
            (config_id,)
        ).fetchall()
        conn.close()

        # Sensor type Korean labels
        type_labels = {
            "MRU": "모션센서 (MRU)",
            "IMU": "관성센서 (IMU)",
            "MBES Transducer": "멀티빔 (MBES)",
            "GPS": "GNSS 안테나",
            "SBP Transducer": "SBP 트랜스듀서",
            "Echosounder": "싱글빔",
            "Towpoint": "견인점",
            "Other": "기타",
        }

        result = {
            "config": {
                "id": config["id"],
                "vessel_name": config["vessel_name"],
                "project_name": config["project_name"],
                "config_date": config["config_date"],
                "reference_point": config["reference_point"],
                "description": config["description"],
            },
            "sensors": [
                {
                    "id": s["id"],
                    "name": s["sensor_name"],
                    "type": s["sensor_type"],
                    "type_ko": type_labels.get(s["sensor_type"], s["sensor_type"]),
                    "x": s["x_offset"],
                    "y": s["y_offset"],
                    "z": s["z_offset"],
                    "roll": s["roll_offset"],
                    "pitch": s["pitch_offset"],
                    "heading": s["heading_offset"],
                    "latency": s["latency"],
                    "notes": s["notes"] or "",
                }
                for s in sensors
            ],
            "total": len(sensors),
        }

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan-folders", methods=["POST"])
def api_scan_folders():
    """Scan companion folders for matching files by timestamp."""
    data = request.json or {}
    pds_path = _validate_pds_path(data.get("pds_path", ""))
    if not pds_path:
        return jsonify({"error": "Invalid PDS path"}), 400

    # Extract timestamp from PDS filename
    stem = Path(pds_path).stem
    import re
    ts_match = re.search(r'(\d{8})-?(\d{6})', stem)
    pds_ts = f"{ts_match.group(1)}-{ts_match.group(2)}" if ts_match else ""

    found = {}
    for folder_key in ["gsf_dir", "hvf_dir", "s7k_dir", "fau_dir"]:
        folder = data.get(folder_key, "")
        if not folder or not Path(folder).is_dir():
            continue

        ext_map = {
            "gsf_dir": [".gsf"],
            "hvf_dir": [".hvf"],
            "s7k_dir": [".s7k"],
            "fau_dir": [".fau"],
        }
        exts = ext_map.get(folder_key, [])
        matches = []
        for f in Path(folder).iterdir():
            if f.suffix.lower() in exts:
                # Check timestamp match
                f_ts = re.search(r'(\d{8})-?(\d{6})', f.stem)
                matched = False
                if pds_ts and f_ts:
                    matched = f_ts.group(1) == ts_match.group(1)  # same date
                matches.append({
                    "name": f.name,
                    "size_mb": f.stat().st_size / 1024 / 1024,
                    "matched": matched,
                })
        if matches:
            found[folder_key] = sorted(matches, key=lambda x: x["name"])

    return jsonify({"pds_timestamp": pds_ts, "found": found})


@app.route("/api/run-qc", methods=["POST"])
def api_run_qc():
    """Start QC job in background thread."""
    data = request.json or {}
    pds_path = _validate_pds_path(data.get("pds_path", ""))

    if not pds_path:
        return jsonify({"error": "Invalid or missing PDS file path"}), 400

    job_id = _next_id('job')
    job = {
        "id": job_id,
        "pds_path": pds_path,
        "gsf_dir": data.get("gsf_dir", ""),
        "hvf_dir": data.get("hvf_dir", ""),
        "om_config_id": data.get("om_config_id"),
        "lat_range": data.get("lat_range", [-90, 90]),
        "lon_range": data.get("lon_range", [-180, 180]),
        "max_pings": data.get("max_pings"),
        "cell_size": data.get("cell_size", 5.0),
        "status": "running",
        "progress": "Starting...",
        "started_at": datetime.now().isoformat(),
        "result": None,
    }
    _jobs[job_id] = job

    thread = threading.Thread(target=_run_qc_job, args=(job,), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "started"})


@app.route("/api/project-qc/<int:project_id>/<int:file_idx>", methods=["POST"])
def api_project_qc(project_id, file_idx):
    """Run QC for a specific PDS file within a project."""
    project = _projects.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    if file_idx >= len(project["pds_files"]):
        return jsonify({"error": "File index out of range"}), 400

    pds_info = project["pds_files"][file_idx]
    pds_path = pds_info["path"]
    if not Path(pds_path).exists():
        return jsonify({"error": f"File not found: {pds_path}"}), 404

    job_id = _next_id('job')
    job = {
        "id": job_id,
        "project_id": project_id,
        "pds_path": pds_path,
        "gsf_dir": project.get("gsf_dir", ""),
        "hvf_dir": project.get("hvf_dir", ""),
        "om_config_id": project.get("om_config_id"),
        "lat_range": [-90, 90],
        "lon_range": [-180, 180],
        "max_pings": None,
        "cell_size": 5.0,
        "status": "running",
        "progress": "Starting...",
        "started_at": datetime.now().isoformat(),
        "result": None,
    }
    _jobs[job_id] = job
    project.setdefault("jobs", []).append(job_id)

    thread = threading.Thread(target=_run_qc_job, args=(job,), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "started"})


@app.route("/api/status/<int:job_id>")
def api_status(job_id):
    """Poll job status."""
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "id": job_id,
        "status": job["status"],
        "progress": job.get("progress", ""),
    })


@app.route("/api/download/<int:job_id>/<fmt>")
def api_download(job_id, fmt):
    """Download generated report."""
    job = _jobs.get(job_id)
    if not job or not job.get("output_dir"):
        return jsonify({"error": "Not found"}), 404

    out = Path(job["output_dir"])
    file_map = {
        "excel": out / "QC_Report.xlsx",
        "word": out / "QC_Report.docx",
        "ppt": out / "DQR_MBES.pptx",
    }

    path = file_map.get(fmt)
    if path and path.exists():
        return send_file(str(path), as_attachment=True)
    return jsonify({"error": f"File not found: {fmt}"}), 404


# ── Background QC Job ──────────────────────────────────────

def _run_qc_job(job: dict) -> None:
    """Execute QC in background thread."""
    try:
        from mbes_qc.pds_qc import run_pds_qc

        pds_path = job["pds_path"]
        output_dir = str(Path(pds_path).parent / f"QC_{Path(pds_path).stem}")

        job["progress"] = "Running QC pipeline..."
        job["output_dir"] = output_dir

        result = run_pds_qc(
            pds_path=pds_path,
            gsf_dir=job.get("gsf_dir") or None,
            hvf_dir=job.get("hvf_dir") or None,
            output_dir=output_dir,
            max_pings=job.get("max_pings"),
            lat_range=tuple(job.get("lat_range", (-90, 90))),
            lon_range=tuple(job.get("lon_range", (-180, 180))),
            cell_size=job.get("cell_size", 5.0),
            generate_reports=False,  # reports generated on-demand via download API
        )

        # Serialize result for template
        serialized = _serialize_result(result)
        with _job_lock:
            job["result"] = serialized
            job["finished_at"] = datetime.now().isoformat()
            job["progress"] = "Complete"
            job["status"] = "done"  # set LAST so status poll sees complete state

    except Exception as e:
        job["status"] = "error"
        job["progress"] = f"Error: {e}"
        job["finished_at"] = datetime.now().isoformat()
        import traceback
        job["traceback"] = traceback.format_exc()


def _safe_float(v):
    """Convert numpy/nan/inf to JSON-safe Python float."""
    import math
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return round(f, 6)
    except (TypeError, ValueError):
        return 0.0


def _serialize_result(result) -> dict:
    """Convert PdsQcResult to JSON-safe dict."""
    d = {
        "vessel_name": result.vessel_name,
        "total_pings": int(result.total_pings),
        "total_beams": int(result.total_beams),
        "depth_range": [_safe_float(x) for x in result.depth_range],
        "lat_range": [_safe_float(x) for x in result.lat_range],
        "lon_range": [_safe_float(x) for x in result.lon_range],
        "nav_records": int(result.nav_records),
        "attitude_records": int(result.attitude_records),
        "elapsed_sec": _safe_float(result.elapsed_sec),
    }

    # Pre-processing checks
    if result.preprocess:
        d["preprocess"] = {
            "overall": result.preprocess.overall,
            "num_pass": result.preprocess.num_pass,
            "num_warn": result.preprocess.num_warn,
            "num_fail": result.preprocess.num_fail,
            "checks": [
                {
                    "category": c.category,
                    "name": c.name,
                    "status": c.status,
                    "pds_value": c.pds_value,
                    "reference_value": c.reference_value,
                    "suggestion": c.suggestion,
                }
                for c in result.preprocess.checks
            ],
        }

    # Swath lines summary
    if result.swath_lines:
        d["lines"] = {
            "num_lines": len(result.swath_lines),
            "details": [],
        }
        for sw in result.swath_lines:
            d["lines"]["details"].append({
                "name": sw.line_name,
                "format": sw.source_format,
                "pings": int(sw.num_pings),
                "heading": _safe_float(sw.mean_heading),
                "depth": _safe_float(sw.mean_depth),
                "duration": _safe_float(sw.duration_seconds),
            })

    # Motion QC summary
    if hasattr(result, 'motion_qc') and result.motion_qc:
        mq = result.motion_qc
        d["motion"] = {
            "roll_mean": _safe_float(getattr(mq, 'roll_mean', 0)),
            "roll_std": _safe_float(getattr(mq, 'roll_std', 0)),
            "roll_min": _safe_float(getattr(mq, 'roll_min', 0)),
            "roll_max": _safe_float(getattr(mq, 'roll_max', 0)),
            "pitch_mean": _safe_float(getattr(mq, 'pitch_mean', 0)),
            "pitch_std": _safe_float(getattr(mq, 'pitch_std', 0)),
            "pitch_min": _safe_float(getattr(mq, 'pitch_min', 0)),
            "pitch_max": _safe_float(getattr(mq, 'pitch_max', 0)),
            "heave_mean": _safe_float(getattr(mq, 'heave_mean', 0)),
            "heave_std": _safe_float(getattr(mq, 'heave_std', 0)),
            "heave_min": _safe_float(getattr(mq, 'heave_min', 0)),
            "heave_max": _safe_float(getattr(mq, 'heave_max', 0)),
            "heading_mean": _safe_float(getattr(mq, 'heading_mean', 0)),
            "attitude_records": int(getattr(mq, 'total_samples', 0)),
            "roll_spikes": int(getattr(mq, 'roll_spikes', 0)),
            "pitch_spikes": int(getattr(mq, 'pitch_spikes', 0)),
            "heave_spikes": int(getattr(mq, 'heave_spikes', 0)),
        }

    # Beam stats
    d["beam_stats"] = {
        "total_pings": int(result.total_pings),
        "beams_per_ping": int(getattr(result, 'beams_per_ping', 0)),
        "depth_min": _safe_float(result.depth_range[0]) if result.depth_range else 0,
        "depth_max": _safe_float(result.depth_range[1]) if len(result.depth_range) > 1 else 0,
    }

    # Offset QC
    if hasattr(result, 'offset_qc') and result.offset_qc:
        oq = result.offset_qc
        d["offset_qc"] = {
            "roll_bias": _safe_float(getattr(oq, 'roll_bias', 0)),
            "roll_bias_std": _safe_float(getattr(oq, 'roll_bias_std', 0)),
            "roll_status": getattr(oq, 'roll_status', 'N/A'),
        }

    # PDS computed records
    if hasattr(result, 'computed') and result.computed:
        d["computed_count"] = len(result.computed)

    # Tide records
    if hasattr(result, 'tide_records'):
        d["tide_records"] = int(result.tide_records)

    return d


# ── Helpers ────────────────────────────────────────────────

def _load_om_configs() -> list[dict]:
    """Load OffsetManager vessel configs."""
    if not os.path.exists(OM_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(OM_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, vessel_name, project_name, config_date FROM vessel_configs ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(port=5016, debug=os.environ.get("FLASK_DEBUG", "0") == "1")

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
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/om-configs")
def api_om_configs():
    """List OffsetManager vessel configs."""
    return jsonify(_load_om_configs())


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
            generate_reports=True,
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


def _serialize_result(result) -> dict:
    """Convert PdsQcResult to JSON-safe dict."""
    d = {
        "vessel_name": result.vessel_name,
        "total_pings": result.total_pings,
        "total_beams": result.total_beams,
        "depth_range": list(result.depth_range),
        "lat_range": list(result.lat_range),
        "lon_range": list(result.lon_range),
        "nav_records": result.nav_records,
        "attitude_records": result.attitude_records,
        "elapsed_sec": result.elapsed_sec,
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
        d["lines"] = []
        for sw in result.swath_lines:
            d["lines"].append({
                "name": sw.line_name,
                "format": sw.source_format,
                "pings": sw.num_pings,
                "heading": sw.mean_heading,
                "depth": sw.mean_depth,
                "duration": sw.duration_seconds,
            })

    # Motion QC summary
    if hasattr(result, 'motion_qc') and result.motion_qc:
        mq = result.motion_qc
        d["motion"] = {
            "roll_mean": getattr(mq, 'roll_mean', 0),
            "roll_std": getattr(mq, 'roll_std', 0),
            "pitch_mean": getattr(mq, 'pitch_mean', 0),
            "pitch_std": getattr(mq, 'pitch_std', 0),
            "heave_mean": getattr(mq, 'heave_mean', 0),
            "heave_std": getattr(mq, 'heave_std', 0),
            "heading_mean": getattr(mq, 'heading_mean', 0),
            "roll_spikes": getattr(mq, 'roll_spikes', 0),
            "pitch_spikes": getattr(mq, 'pitch_spikes', 0),
            "heave_spikes": getattr(mq, 'heave_spikes', 0),
        }

    # PDS computed records (projected coords, speed, heading)
    if hasattr(result, 'computed') and result.computed:
        d["computed_count"] = len(result.computed)

    # Tide records
    if hasattr(result, 'tide_records'):
        d["tide_records"] = result.tide_records

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

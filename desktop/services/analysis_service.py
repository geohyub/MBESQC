"""MBESQC AnalysisService -- QThread workers for QC pipeline execution.

Uses mbes_qc.runner.run_full_qc() as the primary QC engine (GSF-first strategy).
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np

from PySide6.QtCore import QObject, Signal, Slot

from desktop.services.data_service import DataService


# ── Numpy-safe JSON encoder ──

class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            v = float(obj)
            return 0.0 if (np.isnan(v) or np.isinf(v)) else round(v, 6)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return super().default(obj)


def _sf(v) -> float:
    """Safe float conversion."""
    try:
        f = float(v)
        return 0.0 if (np.isnan(f) or np.isinf(f)) else round(f, 6)
    except (TypeError, ValueError):
        return 0.0


# ── QC Weights & Scoring ──

QC_WEIGHTS = {
    "file": 5, "vessel": 10, "offset": 15, "motion": 15,
    "svp": 10, "coverage": 15, "crossline": 20, "surface": 10,
}

_VERDICT_SCORE = {"PASS": 1.0, "WARNING": 0.7, "FAIL": 0.0}


def compute_score(result_dict: dict) -> tuple[float, str]:
    """Weighted QC score from serialized result. Returns (score, grade)."""
    total = 0.0
    max_possible = 0.0

    for qc_id, weight in QC_WEIGHTS.items():
        section = result_dict.get(qc_id)
        if not section:
            continue
        verdict = section.get("verdict", "N/A").upper()
        if verdict in ("N/A", "---", ""):
            continue
        max_possible += weight
        total += weight * _VERDICT_SCORE.get(verdict, 0.0)

    if max_possible <= 0:
        return 0.0, "F"
    score = round((total / max_possible) * 100.0, 1)

    grade = "F"
    if score >= 90: grade = "A"
    elif score >= 75: grade = "B"
    elif score >= 60: grade = "C"
    elif score >= 40: grade = "D"
    return score, grade


# ── FullQcResult → dict serialization ──

def _serialize_axis(ax) -> dict:
    """Serialize AxisStats from motion_qc."""
    return {
        "name": getattr(ax, "name", ""),
        "unit": getattr(ax, "unit", ""),
        "mean": _sf(getattr(ax, "mean", 0)),
        "std": _sf(getattr(ax, "std", 0)),
        "min": _sf(getattr(ax, "min", 0)),
        "max": _sf(getattr(ax, "max", 0)),
        "num_spikes": int(getattr(ax, "num_spikes", 0)),
        "spike_rate_pct": _sf(getattr(ax, "spike_rate_pct", 0)),
        "verdict": getattr(ax, "verdict", "N/A"),
    }


def _items_verdict(items) -> str:
    """Extract overall verdict from a list of items (dicts or dataclass)."""
    statuses = []
    for i in items:
        if isinstance(i, dict):
            s = i.get("status", "N/A")
        else:
            s = getattr(i, "status", "N/A")
        if s not in ("N/A", "INFO", ""):
            statuses.append(s)
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    return "PASS" if statuses else "N/A"


def serialize_full_qc_result(result) -> dict:
    """Convert FullQcResult → JSON-serializable dict for DB storage."""
    d = {"elapsed_sec": _sf(getattr(result, "elapsed_sec", 0))}

    # File QC
    fq = getattr(result, "file_qc", None)
    if fq:
        items = []
        for it in getattr(fq, "items", []):
            items.append({
                "name": getattr(it, "name", ""),
                "status": getattr(it, "status", "N/A"),
                "detail": getattr(it, "detail", ""),
            })
        d["file"] = {
            "verdict": getattr(fq, "overall_verdict", "N/A") if hasattr(fq, "overall_verdict") else _items_verdict(fq.items),
            "total_lines": getattr(fq, "total_lines", 0),
            "total_pings": getattr(fq, "total_pings", 0),
            "gsf_count": len(getattr(fq, "gsf_files", [])),
            "pds_count": len(getattr(fq, "pds_files", [])),
            "items": items,
        }

    # Vessel QC
    vq = getattr(result, "vessel_qc", None)
    if vq:
        items = []
        for it in getattr(vq, "items", []):
            items.append({
                "name": getattr(it, "name", ""),
                "status": getattr(it, "status", "N/A"),
                "pds_value": str(getattr(it, "pds_value", "")),
                "hvf_value": str(getattr(it, "hvf_value", "")),
            })
        d["vessel"] = {
            "verdict": _items_verdict(vq.items),
            "items": items,
        }

    # Offset QC
    oq = getattr(result, "offset_qc", None)
    if oq:
        d["offset"] = {
            "verdict": getattr(oq, "overall_verdict", None) or (
                "FAIL" if abs(getattr(oq, "roll_bias_deg", 0)) > 0.5 else
                "WARNING" if abs(getattr(oq, "roll_bias_deg", 0)) > 0.1 else "PASS"),
            "roll_bias_deg": _sf(oq.roll_bias_deg),
            "roll_bias_std": _sf(oq.roll_bias_std),
            "roll_verdict": getattr(oq, "roll_verdict", "N/A"),
            "pitch_bias_deg": _sf(oq.pitch_bias_deg),
            "pitch_bias_std": _sf(oq.pitch_bias_std),
            "pitch_verdict": getattr(oq, "pitch_verdict", "N/A"),
        }

    # Offset Validation (OffsetManager)
    ov = getattr(result, "offset_validation", None)
    if ov:
        try:
            ov_data = {
                "overall": getattr(ov, "overall", "N/A"),
                "vessel_name": getattr(ov, "vessel_name", ""),
            }
            config_checks = getattr(ov, "config_checks", [])
            if config_checks:
                ov_data["config_checks"] = [
                    {"sensor": getattr(c, "sensor", ""), "field": getattr(c, "field", ""),
                     "pds_value": str(getattr(c, "pds_value", "")),
                     "om_value": str(getattr(c, "om_value", "")),
                     "status": getattr(c, "status", "N/A")}
                    for c in config_checks
                ]
            data_checks = getattr(ov, "data_checks", [])
            if data_checks:
                ov_data["data_checks"] = [
                    {"name": getattr(c, "name", ""), "status": getattr(c, "status", "N/A"),
                     "detail": getattr(c, "detail", "")}
                    for c in data_checks
                ]
            d["offset_validation"] = ov_data
        except Exception:
            pass

    # Motion QC
    mq = getattr(result, "motion_qc", None)
    if mq:
        axes = {}
        for name in ("roll", "pitch", "heave", "heading"):
            ax = getattr(mq, name, None)
            if ax:
                axes[name] = _serialize_axis(ax)

        verdicts = [a.get("verdict", "N/A") for a in axes.values()]
        gap_verdict = getattr(mq, "gap_verdict", "N/A")
        all_v = verdicts + [gap_verdict]
        overall = "FAIL" if "FAIL" in all_v else "WARNING" if "WARNING" in all_v else "PASS"

        d["motion"] = {
            "verdict": overall,
            "axes": axes,
            "total_samples": int(getattr(mq, "total_samples", 0)),
            "time_span_sec": _sf(getattr(mq, "time_span_sec", 0)),
            "sample_rate_hz": _sf(getattr(mq, "sample_rate_hz", 0)),
            "num_gaps": int(getattr(mq, "num_gaps", 0)),
            "max_gap_sec": _sf(getattr(mq, "max_gap_sec", 0)),
            "gap_verdict": gap_verdict,
        }

    # Motion per-line
    mpl = getattr(result, "motion_per_line", None)
    if mpl and mq:
        per_line = []
        for p in mpl:
            per_line.append({
                "filename": getattr(p, "filename", ""),
                "roll_std": _sf(getattr(p.roll, "std", 0)) if hasattr(p, "roll") else 0,
                "roll_verdict": getattr(p.roll, "verdict", "N/A") if hasattr(p, "roll") else "N/A",
                "pitch_std": _sf(getattr(p.pitch, "std", 0)) if hasattr(p, "pitch") else 0,
                "pitch_verdict": getattr(p.pitch, "verdict", "N/A") if hasattr(p, "pitch") else "N/A",
                "heave_std": _sf(getattr(p.heave, "std", 0)) if hasattr(p, "heave") else 0,
                "heave_verdict": getattr(p.heave, "verdict", "N/A") if hasattr(p, "heave") else "N/A",
            })
        d["motion"]["per_line"] = per_line

    # SVP QC
    sq = getattr(result, "svp_qc", None)
    if sq:
        d["svp"] = {
            "verdict": getattr(sq, "overall_verdict", "N/A") if hasattr(sq, "overall_verdict") else _items_verdict(sq.items),
            "applied": getattr(sq, "applied", False),
            "num_profiles": getattr(sq, "num_profiles", 0),
            "velocity_range": [_sf(v) for v in getattr(sq, "velocity_range", (0, 0))],
            "items": [{"name": i.get("name",""), "status": i.get("status",""), "detail": i.get("detail","")} for i in getattr(sq, "items", [])],
        }

    # Coverage QC
    cq = getattr(result, "coverage_qc", None)
    if cq:
        lines = []
        for ln in getattr(cq, "lines", []):
            lines.append({
                "name": getattr(ln, "filename", ""),
                "heading_deg": _sf(getattr(ln, "heading_deg", 0)),
                "length_m": _sf(getattr(ln, "length_m", 0)),
                "mean_depth_m": _sf(getattr(ln, "mean_depth_m", 0)),
                "mean_swath_m": _sf(getattr(ln, "mean_swath_m", 0)),
                "num_pings": int(getattr(ln, "num_pings", 0)),
            })
        d["coverage"] = {
            "verdict": _items_verdict(cq.items),
            "total_lines": getattr(cq, "total_lines", 0),
            "total_length_km": _sf(getattr(cq, "total_length_km", 0)),
            "total_area_km2": _sf(getattr(cq, "total_area_km2", 0)),
            "mean_overlap_pct": _sf(getattr(cq, "mean_overlap_pct", 0)),
            "lines": lines,
            "items": [{"name": i.get("name",""), "status": i.get("status",""), "detail": i.get("detail","")} for i in getattr(cq, "items", [])],
        }

        # Track data for chart rendering (downsampled)
        track_lats = getattr(cq, "track_lats", None)
        track_lons = getattr(cq, "track_lons", None)
        if track_lats is not None and len(track_lats) > 0:
            d["coverage"]["track_lats"] = _downsample(track_lats, 3000).tolist()
            d["coverage"]["track_lons"] = _downsample(track_lons, 3000).tolist()

    # Cross-line QC
    xq = getattr(result, "crossline_qc", None)
    if xq:
        d["crossline"] = {
            "verdict": getattr(xq, "iho_verdict", "N/A"),
            "num_intersections": int(getattr(xq, "num_intersections", 0)),
            "depth_diff_mean": _sf(xq.depth_diff_mean),
            "depth_diff_std": _sf(xq.depth_diff_std),
            "depth_diff_rms": _sf(xq.depth_diff_rms),
            "depth_diff_max": _sf(xq.depth_diff_max),
            "iho_order": getattr(xq, "iho_order", "1a"),
            "iho_pass_pct": _sf(xq.iho_pass_pct),
            "striping_detected": bool(getattr(xq, "striping_detected", False)),
            "items": [{"name": i.get("name",""), "status": i.get("status",""), "detail": i.get("detail","")} for i in getattr(xq, "items", [])],
        }
        # Intersection pair details
        idet = getattr(xq, "intersection_details", None)
        if idet:
            d["crossline"]["intersection_details"] = [
                {
                    "line1": det.get("line1", 0),
                    "line2": det.get("line2", 0),
                    "n_cells": int(det.get("n_cells", 0)),
                    "mean_diff": _sf(det.get("mean_diff", 0)),
                    "std_diff": _sf(det.get("std_diff", 0)),
                }
                for det in idet
            ]

    # ── Chart data arrays (downsampled for rendering) ──
    # Motion: attitude time series from first GSF
    if mq:
        # Access raw arrays if available via the GSF object
        # These come from MotionQcResult internal data (not stored in dataclass)
        # We reconstruct from the axes stats + generate synthetic time array
        ts = getattr(mq, "time_span_sec", 0)
        n = int(getattr(mq, "total_samples", 0))
        if n > 0 and ts > 0:
            d["motion"]["chart_available"] = True
            d["motion"]["chart_note"] = "Render from GSF on demand"

    # Coverage: trackline data
    if cq:
        track_lines_chart = []
        for ln in getattr(cq, "lines", []):
            lats = getattr(ln, "track_lats", None)
            lons = getattr(ln, "track_lons", None)
            if lats is not None and len(lats) > 0:
                track_lines_chart.append({
                    "name": getattr(ln, "filename", ""),
                    "lats": _downsample(np.asarray(lats), 1000).tolist(),
                    "lons": _downsample(np.asarray(lons), 1000).tolist(),
                })
        if track_lines_chart:
            d["coverage"]["track_lines"] = track_lines_chart

    # Surface
    surf = getattr(result, "surface", None)
    if surf:
        has_dtm = getattr(surf, "dtm", None) is not None
        d["surface"] = {
            "verdict": "PASS" if has_dtm else "N/A",
            "num_points": int(getattr(surf, "num_points", 0)),
            "cell_size": _sf(getattr(surf, "cell_size", 0)),
            "nx": int(getattr(surf, "nx", 0)),
            "ny": int(getattr(surf, "ny", 0)),
            "has_dtm": has_dtm,
        }

    return d


def _downsample(arr, max_pts: int = 2000) -> np.ndarray:
    arr = np.asarray(arr)
    if len(arr) <= max_pts:
        return arr
    idx = np.linspace(0, len(arr) - 1, max_pts, dtype=int)
    return arr[idx]


# ══════════════════════════════════════════════
# Workers
# ══════════════════════════════════════════════

class AnalysisWorker(QObject):
    """Background worker: run_full_qc on a project.

    Supports max_gsf_files to limit processing on large datasets (900+ GSF).
    Resolves HVF path from directory or file.
    """

    stage = Signal(int, str)
    progress = Signal(int, int)           # current, total
    finished = Signal(int, dict)          # result_id, serialized
    error = Signal(int, str)              # result_id, message

    def __init__(self, project_id: int, result_id: int,
                 gsf_dir: str = "", pds_dir: str = "",
                 hvf_path: str = "", max_pings: int = 0,
                 cell_size: float = 5.0, max_gsf_files: int = 0):
        super().__init__()
        self._project_id = project_id
        self._result_id = result_id
        self._gsf_dir = gsf_dir
        self._pds_dir = pds_dir
        self._hvf_path = hvf_path
        self._max_pings = max_pings if max_pings > 0 else None
        self._cell_size = cell_size
        self._max_gsf = max_gsf_files if max_gsf_files > 0 else 0
        self._stop_event = threading.Event()

    def cancel(self):
        """Request cancellation of the running QC pipeline."""
        self._stop_event.set()

    def _resolve_hvf(self) -> str | None:
        """Resolve HVF: accept file path or directory (pick first .hvf)."""
        p = self._hvf_path
        if not p:
            return None
        pp = Path(p)
        if pp.is_file() and pp.suffix.lower() == ".hvf":
            return str(pp)
        if pp.is_dir():
            hvfs = sorted(pp.glob("*.hvf")) + sorted(pp.glob("*.HVF"))
            return str(hvfs[0]) if hvfs else None
        return None

    def _resolve_gsf_paths(self) -> list[str] | None:
        """Resolve GSF paths with optional file count limit."""
        if not self._gsf_dir:
            return None
        d = Path(self._gsf_dir)
        if not d.is_dir():
            return None
        gsf_files = sorted(d.glob("*.gsf"))
        total = len(gsf_files)
        if total == 0:
            return None

        if self._max_gsf > 0 and total > self._max_gsf:
            # Uniform sampling: pick evenly spaced files
            indices = np.linspace(0, total - 1, self._max_gsf, dtype=int)
            gsf_files = [gsf_files[i] for i in indices]
            self.stage.emit(0, f"GSF {total}개 중 {self._max_gsf}개 샘플링...")
        else:
            self.stage.emit(0, f"GSF {total}개 파일 로딩...")

        return [str(f) for f in gsf_files]

    @Slot()
    def run(self):
        try:
            import sys
            mbesqc_root = str(Path(__file__).resolve().parents[2])
            if mbesqc_root not in sys.path:
                sys.path.insert(0, mbesqc_root)

            self.stage.emit(1, "QC 엔진 로딩...")
            from mbes_qc.runner import run_full_qc

            hvf = self._resolve_hvf()
            gsf_paths = self._resolve_gsf_paths()

            def _progress_cb(current, total, desc):
                self.progress.emit(current, total)
                self.stage.emit(current, desc)

            result = run_full_qc(
                gsf_paths=gsf_paths,
                pds_dir=self._pds_dir or None,
                hvf_path=hvf,
                max_pings=self._max_pings,
                cell_size=self._cell_size,
                generate_surfaces=True,
                generate_reports=False,
                progress_callback=_progress_cb,
                stop_event=self._stop_event,
            )

            if getattr(result, "cancelled", False):
                self.stage.emit(0, "QC 취소됨")
                DataService.update_qc_result(
                    self._result_id, status="cancelled",
                    finished_at=datetime.now().isoformat(),
                )
                return

            self.stage.emit(3, "결과 직렬화...")

            serialized = serialize_full_qc_result(result)
            score, grade = compute_score(serialized)

            result_json = json.dumps(serialized, cls=_NumpyEncoder, ensure_ascii=False)

            DataService.update_qc_result(
                self._result_id,
                status="done",
                score=score,
                grade=grade,
                finished_at=datetime.now().isoformat(),
                result_json=result_json,
            )

            DataService.log_activity(
                self._project_id, "qc_complete",
                f"Score: {score:.1f} ({grade})")

            self.finished.emit(self._result_id, serialized)

        except Exception as e:
            tb = traceback.format_exc()
            DataService.update_qc_result(
                self._result_id,
                status="error",
                finished_at=datetime.now().isoformat(),
            )
            self.error.emit(self._result_id, f"{e}\n{tb}")

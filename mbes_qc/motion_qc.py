"""Motion QC — Verify attitude/motion sensor data quality.

Analyzes IMU time-series from GSF attitude records for:
  - Statistical bounds (roll/pitch/heave/heading)
  - Spike detection (abnormal rate of change)
  - Data gaps (missing IMU samples)
  - Heave amplitude and frequency analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pds_toolkit.models import GsfFile

# ── Thresholds ──────────────────────────────────────────────────

_ROLL_STD_WARN = 3.0       # degrees
_ROLL_STD_FAIL = 10.0
_PITCH_STD_WARN = 2.0
_PITCH_STD_FAIL = 5.0
_HEAVE_STD_WARN = 0.5      # metres
_HEAVE_STD_FAIL = 2.0
_SPIKE_RATE_WARN = 0.5     # percent
_SPIKE_RATE_FAIL = 2.0
_ROLL_RATE_THRESHOLD = 10.0   # deg/s for spike detection
_PITCH_RATE_THRESHOLD = 5.0
_HEAVE_RATE_THRESHOLD = 1.0   # m/s
_GAP_THRESHOLD = 0.5       # seconds


@dataclass
class AxisStats:
    """Statistics for one attitude axis."""

    name: str = ""
    unit: str = ""
    mean: float = 0.0
    std: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    num_samples: int = 0
    num_spikes: int = 0
    spike_rate_pct: float = 0.0
    verdict: str = "N/A"


@dataclass
class MotionQcResult:
    """Results of motion data verification."""

    roll: AxisStats = field(default_factory=lambda: AxisStats(name="Roll", unit="°"))
    pitch: AxisStats = field(default_factory=lambda: AxisStats(name="Pitch", unit="°"))
    heave: AxisStats = field(default_factory=lambda: AxisStats(name="Heave", unit="m"))
    heading: AxisStats = field(default_factory=lambda: AxisStats(name="Heading", unit="°"))

    total_samples: int = 0
    total_records: int = 0
    time_span_sec: float = 0.0
    sample_rate_hz: float = 0.0

    # Gap analysis
    num_gaps: int = 0
    max_gap_sec: float = 0.0
    gap_verdict: str = "N/A"

    # Heave spectral
    heave_dominant_period_sec: float = 0.0
    heave_significant_amplitude_m: float = 0.0

    @property
    def overall_verdict(self) -> str:
        verdicts = [
            self.roll.verdict, self.pitch.verdict,
            self.heave.verdict, self.gap_verdict,
        ]
        if "FAIL" in verdicts:
            return "FAIL"
        if "WARNING" in verdicts:
            return "WARNING"
        if all(v == "PASS" for v in verdicts):
            return "PASS"
        return "N/A"


def run_motion_qc(gsf: GsfFile) -> MotionQcResult:
    """Run full motion data QC analysis.

    Args:
        gsf: Parsed GSF file with attitude records loaded.
    """
    result = MotionQcResult()

    if not gsf.attitude_records:
        return result

    # Concatenate all attitude data
    all_times = gsf.all_attitude_times()
    all_roll = gsf.all_attitude_roll()
    all_pitch = gsf.all_attitude_pitch()
    all_heave = gsf.all_attitude_heave()

    # Heading from attitude records
    all_heading = np.concatenate([a.heading for a in gsf.attitude_records]) if gsf.attitude_records else np.array([])

    n = len(all_times)
    result.total_samples = n
    result.total_records = len(gsf.attitude_records)

    if n < 10:
        return result

    # Sort by time
    sort_idx = np.argsort(all_times)
    all_times = all_times[sort_idx]
    all_roll = all_roll[sort_idx]
    all_pitch = all_pitch[sort_idx]
    all_heave = all_heave[sort_idx]
    if len(all_heading) == n:
        all_heading = all_heading[sort_idx]

    result.time_span_sec = float(all_times[-1] - all_times[0])
    if result.time_span_sec > 0:
        result.sample_rate_hz = n / result.time_span_sec

    # ── Per-axis statistics ─────────────────────────────────
    result.roll = _compute_axis_stats(
        all_times, all_roll, "Roll", "°",
        _ROLL_RATE_THRESHOLD, _ROLL_STD_WARN, _ROLL_STD_FAIL,
    )
    result.pitch = _compute_axis_stats(
        all_times, all_pitch, "Pitch", "°",
        _PITCH_RATE_THRESHOLD, _PITCH_STD_WARN, _PITCH_STD_FAIL,
    )
    result.heave = _compute_axis_stats(
        all_times, all_heave, "Heave", "m",
        _HEAVE_RATE_THRESHOLD, _HEAVE_STD_WARN, _HEAVE_STD_FAIL,
    )
    if len(all_heading) == n:
        # Unwrap heading to handle 0/360 boundary correctly
        all_heading_unwrap = np.degrees(np.unwrap(np.radians(all_heading)))
        result.heading = _compute_axis_stats(
            all_times, all_heading_unwrap, "Heading", "°",
            50.0, 30.0, 90.0,  # reasonable heading std thresholds
        )
        # Report mean heading in [0, 360) range
        result.heading.mean = float(result.heading.mean % 360)

    # ── Gap analysis ────────────────────────────────────────
    dt = np.diff(all_times)
    gaps = dt[dt > _GAP_THRESHOLD]
    result.num_gaps = len(gaps)
    result.max_gap_sec = float(dt.max()) if len(dt) > 0 else 0.0

    if result.num_gaps == 0:
        result.gap_verdict = "PASS"
    elif result.num_gaps < 5:
        result.gap_verdict = "WARNING"
    else:
        result.gap_verdict = "FAIL"

    # ── Heave spectral analysis ─────────────────────────────
    if len(all_heave) > 100 and result.sample_rate_hz > 0:
        try:
            heave_detrended = all_heave - np.mean(all_heave)
            fft = np.fft.rfft(heave_detrended)
            freqs = np.fft.rfftfreq(len(heave_detrended), 1.0 / result.sample_rate_hz)
            power = np.abs(fft) ** 2

            # Skip DC component
            if len(power) > 1:
                peak_idx = np.argmax(power[1:]) + 1
                if freqs[peak_idx] > 0:
                    result.heave_dominant_period_sec = 1.0 / freqs[peak_idx]

            # Significant wave height proxy: 4 × std(heave)
            result.heave_significant_amplitude_m = 4.0 * float(np.std(heave_detrended))
        except Exception:
            pass

    return result


@dataclass
class PerLineMotion:
    """Per-line (per-GSF-file) motion statistics."""
    filename: str = ""
    roll: AxisStats = field(default_factory=lambda: AxisStats(name="Roll", unit="°"))
    pitch: AxisStats = field(default_factory=lambda: AxisStats(name="Pitch", unit="°"))
    heave: AxisStats = field(default_factory=lambda: AxisStats(name="Heave", unit="m"))


def run_motion_qc_multi(gsf_files: list[GsfFile]) -> tuple[MotionQcResult, list[PerLineMotion]]:
    """Run motion QC on multiple GSF files, returning overall + per-line stats.

    Overall result uses the first GSF file (same as single-file mode).
    Per-line results compute independent statistics per file.
    """
    from pathlib import Path

    overall = run_motion_qc(gsf_files[0]) if gsf_files else MotionQcResult()
    per_line = []

    for gsf in gsf_files:
        plm = PerLineMotion(filename=Path(gsf.filepath).name if hasattr(gsf, "filepath") else "")

        if not gsf.attitude_records:
            per_line.append(plm)
            continue

        all_times = gsf.all_attitude_times()
        all_roll = gsf.all_attitude_roll()
        all_pitch = gsf.all_attitude_pitch()
        all_heave = gsf.all_attitude_heave()

        if len(all_times) < 10:
            per_line.append(plm)
            continue

        sort_idx = np.argsort(all_times)
        all_times = all_times[sort_idx]
        all_roll = all_roll[sort_idx]
        all_pitch = all_pitch[sort_idx]
        all_heave = all_heave[sort_idx]

        plm.roll = _compute_axis_stats(
            all_times, all_roll, "Roll", "°",
            _ROLL_RATE_THRESHOLD, _ROLL_STD_WARN, _ROLL_STD_FAIL)
        plm.pitch = _compute_axis_stats(
            all_times, all_pitch, "Pitch", "°",
            _PITCH_RATE_THRESHOLD, _PITCH_STD_WARN, _PITCH_STD_FAIL)
        plm.heave = _compute_axis_stats(
            all_times, all_heave, "Heave", "m",
            _HEAVE_RATE_THRESHOLD, _HEAVE_STD_WARN, _HEAVE_STD_FAIL)

        per_line.append(plm)

    return overall, per_line


def _compute_axis_stats(
    times: np.ndarray,
    values: np.ndarray,
    name: str,
    unit: str,
    rate_threshold: float,
    std_warn: float,
    std_fail: float,
) -> AxisStats:
    """Compute statistics and spike detection for one attitude axis."""
    stats = AxisStats(name=name, unit=unit, num_samples=len(values))

    if len(values) == 0:
        return stats

    valid = ~np.isnan(values)
    v = values[valid]
    t = times[valid]

    if len(v) == 0:
        return stats

    stats.mean = float(np.mean(v))
    stats.std = float(np.std(v))
    stats.min_val = float(np.min(v))
    stats.max_val = float(np.max(v))

    # Spike detection: rate of change exceeds threshold
    if len(v) > 1 and len(t) > 1:
        dt = np.diff(t)
        dt[dt == 0] = 1e-6  # avoid division by zero
        rate = np.abs(np.diff(v) / dt)
        stats.num_spikes = int(np.sum(rate > rate_threshold))
        stats.spike_rate_pct = 100.0 * stats.num_spikes / len(rate) if len(rate) > 0 else 0.0

    # Verdict
    if stats.std < std_warn and stats.spike_rate_pct < _SPIKE_RATE_WARN:
        stats.verdict = "PASS"
    elif stats.std < std_fail and stats.spike_rate_pct < _SPIKE_RATE_FAIL:
        stats.verdict = "WARNING"
    else:
        stats.verdict = "FAIL"

    return stats

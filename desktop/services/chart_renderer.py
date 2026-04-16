"""MBESQC Chart Renderer -- 7 high-quality matplotlib charts (dark theme, 200 DPI).

All render_* functions return PNG bytes.
Surface/text colors are resolved lazily via _t() so they respond to theme changes.
Accent colors are fixed by design.
"""

from __future__ import annotations

import io
import sys
import os
from typing import Optional

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.theme_aware import c


# ── Theme-aware helpers ──

def _t():
    """Current theme surface/text colors (lazy)."""
    return c()


def _style():
    """Build matplotlib rc dict from current theme."""
    t = _t()
    return {
        "figure.facecolor": t.BG,
        "axes.facecolor": t.BG_ALT,
        "axes.edgecolor": t.SLATE,
        "axes.labelcolor": t.MUTED,
        "text.color": t.TEXT,
        "xtick.color": t.MUTED,
        "ytick.color": t.MUTED,
        "grid.color": t.SLATE,
        "grid.alpha": 0.6,
        "font.family": "Pretendard",
        "font.size": 10,
        "savefig.dpi": 200,
        "savefig.facecolor": t.BG,
    }


# Accent colors (fixed, not theme-dependent)
_CYAN = "#06B6D4"
_GREEN = "#10B981"
_BLUE = "#3B82F6"
_ORANGE = "#F59E0B"
_RED = "#EF4444"
_PURPLE = "#8B5CF6"

# Depth colormap (shallow=cyan, mid=green, deep=blue)
_DEPTH_CMAP = LinearSegmentedColormap.from_list(
    "depth", ["#06B6D4", "#10B981", "#3B82F6", "#6366F1"])

# Score colormap (red→orange→green)
_SCORE_CMAP = LinearSegmentedColormap.from_list(
    "score", [_RED, _ORANGE, _GREEN])


def _fig_to_png(fig: Figure) -> bytes:
    """Render figure to PNG bytes and close."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _smooth(arr, window: int = 15) -> np.ndarray:
    """Apply moving average smoothing."""
    if len(arr) < window:
        return arr
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same")


def _downsample(arr, max_points: int = 2000) -> np.ndarray:
    """Downsample array if too large."""
    if len(arr) <= max_points:
        return arr
    indices = np.linspace(0, len(arr) - 1, max_points, dtype=int)
    return arr[indices]


# ═══════════════════════════════════════════
# 1. NAV TRACK — Survey trackline colored by depth
# ═══════════════════════════════════════════

def render_nav_track(lats: np.ndarray, lons: np.ndarray,
                     depths: Optional[np.ndarray] = None,
                     title: str = "Navigation Track") -> bytes:
    """Survey track map colored by depth."""
    with plt.rc_context(_style()):
        fig, ax = plt.subplots(figsize=(10, 8))

        lats = _downsample(np.asarray(lats))
        lons = _downsample(np.asarray(lons))

        if depths is not None and len(depths) == len(lats):
            depths = _downsample(np.asarray(depths))
            sc = ax.scatter(lons, lats, c=depths, cmap=_DEPTH_CMAP,
                           s=1, alpha=0.7, edgecolors="none")
            cb = fig.colorbar(sc, ax=ax, shrink=0.7, pad=0.02)
            cb.set_label("Depth (m)", fontsize=9)
            cb.ax.tick_params(labelsize=8)
        else:
            ax.plot(lons, lats, color=_CYAN, linewidth=0.8, alpha=0.8)

        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title(title, fontsize=12, fontweight=600, color=_t().TEXT_BRIGHT)
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.3)

        # Annotation box
        stats_text = f"Points: {len(lats):,}\nLat: {lats.min():.6f} ~ {lats.max():.6f}\nLon: {lons.min():.6f} ~ {lons.max():.6f}"
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
                fontsize=8, va="top", color=_t().MUTED,
                bbox=dict(boxstyle="round,pad=0.4", facecolor=_t().NAVY,
                         edgecolor=_t().SLATE, alpha=0.9))

        return _fig_to_png(fig)


# ═══════════════════════════════════════════
# 2. BEAM PROFILE — Across-track vs depth
# ═══════════════════════════════════════════

def render_beam_profile(across: np.ndarray, depths: np.ndarray,
                        ping_num: int = 0,
                        title: str = "Beam Profile") -> bytes:
    """Single ping beam depth profile."""
    with plt.rc_context(_style()):
        fig, ax = plt.subplots(figsize=(10, 5))

        ax.scatter(across, depths, c=_CYAN, s=2, alpha=0.6, edgecolors="none")

        # Smoothed line
        if len(across) > 20:
            sort_idx = np.argsort(across)
            ax.plot(across[sort_idx], _smooth(depths[sort_idx]),
                    color=_GREEN, linewidth=1.5, alpha=0.9)

        ax.set_xlabel("Across-Track (m)")
        ax.set_ylabel("Depth (m)")
        ax.set_title(f"{title} (Ping #{ping_num})", fontsize=12,
                     fontweight=600, color=_t().TEXT_BRIGHT)
        ax.invert_yaxis()
        ax.grid(True, alpha=0.3)

        stats = f"Beams: {len(across)}\nDepth: {depths.min():.1f} ~ {depths.max():.1f} m"
        ax.text(0.02, 0.02, stats, transform=ax.transAxes,
                fontsize=8, va="bottom", color=_t().MUTED,
                bbox=dict(boxstyle="round,pad=0.4", facecolor=_t().NAVY,
                         edgecolor=_t().SLATE, alpha=0.9))

        return _fig_to_png(fig)


# ═══════════════════════════════════════════
# 3. MOTION TIME SERIES — Roll/Pitch/Heave/Heading
# ═══════════════════════════════════════════

def render_motion_timeseries(
    times: np.ndarray,
    roll: np.ndarray, pitch: np.ndarray,
    heave: np.ndarray, heading: np.ndarray,
    title: str = "Motion Time Series"
) -> bytes:
    """4-panel attitude time series."""
    with plt.rc_context(_style()):
        fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

        data = [
            (roll, "Roll", _CYAN, "\u00b0"),
            (pitch, "Pitch", _GREEN, "\u00b0"),
            (heave, "Heave", _ORANGE, "m"),
            (heading, "Heading", _PURPLE, "\u00b0"),
        ]

        t_ds = _downsample(np.asarray(times))

        for ax, (arr, label, color, unit) in zip(axes, data):
            arr = _downsample(np.asarray(arr))
            if len(arr) > 30:
                smoothed = _smooth(arr)
                ax.plot(t_ds[:len(smoothed)], smoothed,
                        color=color, linewidth=0.8, alpha=0.9)
            else:
                ax.plot(t_ds[:len(arr)], arr,
                        color=color, linewidth=0.8, alpha=0.9)

            ax.set_ylabel(f"{label} ({unit})", fontsize=9)
            ax.grid(True, alpha=0.3)

            # Stats annotation
            stats = f"\u03bc={np.nanmean(arr):.3f}  \u03c3={np.nanstd(arr):.3f}  range={np.nanmin(arr):.3f}~{np.nanmax(arr):.3f}"
            ax.text(0.99, 0.95, stats, transform=ax.transAxes,
                    fontsize=7, ha="right", va="top", color=_t().MUTED)

        axes[0].set_title(title, fontsize=12, fontweight=600, color=_t().TEXT_BRIGHT)
        axes[-1].set_xlabel("Time (s)")

        fig.tight_layout()
        return _fig_to_png(fig)


# ═══════════════════════════════════════════
# 4. COVERAGE MAP — Line swath boundaries
# ═══════════════════════════════════════════

def render_coverage_map(lines: list[dict],
                        title: str = "Coverage Map") -> bytes:
    """Multi-line survey coverage display."""
    with plt.rc_context(_style()):
        fig, ax = plt.subplots(figsize=(10, 8))

        colors = [_CYAN, _GREEN, _BLUE, _ORANGE, _PURPLE, _RED,
                  "#EC4899", "#14B8A6", "#8B5CF6", "#F97316"]

        for i, line in enumerate(lines):
            color = colors[i % len(colors)]
            lats = np.asarray(line.get("lats", []))
            lons = np.asarray(line.get("lons", []))
            name = line.get("name", f"Line {i+1}")

            if len(lats) > 0:
                ax.plot(lons, lats, color=color, linewidth=1.2,
                        alpha=0.8, label=name)

        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title(title, fontsize=12, fontweight=600, color=_t().TEXT_BRIGHT)
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.3)

        if lines:
            ax.legend(loc="upper right", fontsize=8,
                     facecolor=_t().NAVY, edgecolor=_t().SLATE,
                     labelcolor=_t().TEXT)

        return _fig_to_png(fig)


# ═══════════════════════════════════════════
# 5. CROSS-LINE SCATTER — Depth diff vs IHO limit
# ═══════════════════════════════════════════

def render_crossline_scatter(
    depths: np.ndarray, diffs: np.ndarray,
    iho_limits: Optional[np.ndarray] = None,
    iho_order: str = "1a",
    title: str = "Cross-line Depth Comparison"
) -> bytes:
    """Scatter plot of intersection depth differences."""
    with plt.rc_context(_style()):
        fig, ax = plt.subplots(figsize=(10, 6))

        ax.scatter(depths, np.abs(diffs), c=_CYAN, s=4, alpha=0.5,
                   edgecolors="none", label="Observations")

        if iho_limits is not None and len(iho_limits) == len(depths):
            sort_idx = np.argsort(depths)
            ax.plot(depths[sort_idx], iho_limits[sort_idx],
                    color=_RED, linewidth=1.5, linestyle="--",
                    label=f"IHO Order {iho_order}", alpha=0.8)

        ax.set_xlabel("Depth (m)")
        ax.set_ylabel("|Depth Difference| (m)")
        ax.set_title(title, fontsize=12, fontweight=600, color=_t().TEXT_BRIGHT)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=9, facecolor=_t().NAVY, edgecolor=_t().SLATE,
                 labelcolor=_t().TEXT)

        # Pass rate annotation
        if iho_limits is not None:
            pass_count = np.sum(np.abs(diffs) <= iho_limits)
            total = len(diffs)
            pct = (pass_count / total * 100) if total > 0 else 0
            color = _GREEN if pct >= 95 else _ORANGE if pct >= 80 else _RED
            ax.text(0.98, 0.02, f"Pass: {pct:.1f}% ({pass_count}/{total})",
                    transform=ax.transAxes, fontsize=10, ha="right", va="bottom",
                    color=color, fontweight=600,
                    bbox=dict(boxstyle="round,pad=0.4", facecolor=_t().NAVY,
                             edgecolor=_t().SLATE, alpha=0.9))

        return _fig_to_png(fig)


# ═══════════════════════════════════════════
# 6. ATTITUDE SPECTRUM — Heave FFT
# ═══════════════════════════════════════════

def render_attitude_spectrum(heave: np.ndarray, sample_rate: float = 50.0,
                            title: str = "Heave Spectrum") -> bytes:
    """FFT power spectrum of heave signal."""
    with plt.rc_context(_style()):
        fig, ax = plt.subplots(figsize=(10, 5))

        heave = np.asarray(heave)
        n = len(heave)
        if n < 32:
            ax.text(0.5, 0.5, "Insufficient data for spectrum",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=12, color=_t().MUTED)
            return _fig_to_png(fig)

        # Detrend
        heave_detrend = heave - np.mean(heave)

        # FFT
        fft_vals = np.fft.rfft(heave_detrend)
        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        power = np.abs(fft_vals) ** 2 / n

        # Plot (skip DC)
        ax.semilogy(freqs[1:], power[1:], color=_CYAN, linewidth=0.8, alpha=0.8)

        # Peak annotation
        peak_idx = np.argmax(power[1:]) + 1
        peak_freq = freqs[peak_idx]
        peak_period = 1.0 / peak_freq if peak_freq > 0 else 0
        ax.axvline(peak_freq, color=_GREEN, linewidth=1, alpha=0.5, linestyle="--")
        ax.text(peak_freq, power[peak_idx],
                f"  Peak: {peak_freq:.2f} Hz ({peak_period:.1f} s)",
                fontsize=9, color=_GREEN, va="bottom")

        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Power")
        ax.set_title(title, fontsize=12, fontweight=600, color=_t().TEXT_BRIGHT)
        ax.grid(True, alpha=0.3)

        sig_amp = 4 * np.std(heave)
        ax.text(0.98, 0.98,
                f"Significant Amplitude: {sig_amp:.3f} m",
                transform=ax.transAxes, fontsize=9, ha="right", va="top",
                color=_t().MUTED,
                bbox=dict(boxstyle="round,pad=0.4", facecolor=_t().NAVY,
                         edgecolor=_t().SLATE, alpha=0.9))

        return _fig_to_png(fig)


# ═══════════════════════════════════════════
# 7. QC RADAR — 8-component score breakdown
# ═══════════════════════════════════════════

def render_qc_radar(scores: dict[str, float],
                    title: str = "QC Score Breakdown") -> bytes:
    """Radar/spider chart of 8 QC component scores."""
    with plt.rc_context(_style()):
        fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))

        labels = list(scores.keys())
        values = [scores.get(k, 0) for k in labels]
        n = len(labels)

        if n == 0:
            ax.text(0, 0, "No data", ha="center", fontsize=14, color=_t().MUTED)
            return _fig_to_png(fig)

        # Close the polygon
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        values_closed = values + [values[0]]
        angles_closed = angles + [angles[0]]

        # Filled area
        ax.fill(angles_closed, values_closed, color=_CYAN, alpha=0.15)
        ax.plot(angles_closed, values_closed, color=_CYAN, linewidth=2, alpha=0.8)

        # Score dots
        for angle, val in zip(angles, values):
            color = _GREEN if val >= 80 else _ORANGE if val >= 50 else _RED
            ax.scatter([angle], [val], c=color, s=40, zorder=5, edgecolors="none")

        # Labels
        ax.set_xticks(angles)
        ax.set_xticklabels(labels, fontsize=9, color=_t().TEXT)
        ax.set_ylim(0, 100)
        ax.set_yticks([25, 50, 75, 100])
        ax.set_yticklabels(["25", "50", "75", "100"], fontsize=7, color=_t().MUTED)

        ax.set_title(title, fontsize=12, fontweight=600, color=_t().TEXT_BRIGHT, pad=20)

        # Grid styling
        ax.spines["polar"].set_color(_t().SLATE)
        ax.grid(color=_t().SLATE, alpha=0.6)

        return _fig_to_png(fig)

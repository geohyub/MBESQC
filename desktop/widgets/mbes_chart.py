"""MBESQC Interactive Chart Widget -- Premium visuals + mouse interaction.

Features:
  - Scroll zoom on chart area
  - Click-drag pan
  - Hover: coordinate display in status bar
  - Home button to reset view
  - Save button for high-DPI PNG export
  - Premium dark theme with gradient fills, glow, annotations
"""

from __future__ import annotations

import io
from typing import Optional

import numpy as np

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.ticker as mticker
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c


# ══════════════════════════════════════════════
# Premium Theme -- surface/text via _t(), accents fixed
# ══════════════════════════════════════════════

def _t():
    """Current theme surface/text colors (lazy)."""
    return c()


def _BG():       return _t().BG
def _SURFACE():  return _t().BG_ALT
def _CARD():     return _t().NAVY
def _GRID():     return _t().SLATE
def _GRID_SUB(): return _t().BORDER
def _BORDER():   return _t().BORDER_H
def _TEXT():     return _t().MUTED
def _TEXT_DIM(): return _t().MUTED
def _BRIGHT():   return _t().TEXT_BRIGHT
_WHITE    = "#FFFFFF"

# Accent palette -- fixed by design, not theme-dependent
_CYAN     = "#22D3EE"
_CYAN_DIM = "#0891B2"
_EMERALD  = "#34D399"
_EMERALD_DIM = "#059669"
_BLUE     = "#60A5FA"
_BLUE_DIM = "#2563EB"
_AMBER    = "#FBBF24"
_AMBER_DIM = "#D97706"
_ROSE     = "#FB7185"
_ROSE_DIM = "#E11D48"
_PURPLE   = "#A78BFA"
_TEAL     = "#2DD4BF"
_INDIGO   = "#818CF8"

def _VERDICT_COLOR():
    return {"PASS": _EMERALD, "WARNING": _AMBER, "FAIL": _ROSE, "N/A": _TEXT_DIM()}

_CMAP_DEPTH = LinearSegmentedColormap.from_list(
    "depth", [_CYAN, _EMERALD, _BLUE, _INDIGO])
_CMAP_HEAT = LinearSegmentedColormap.from_list(
    "heat", [_ROSE_DIM, _AMBER, _EMERALD, _CYAN])


def _dark_ax(ax, title: str = ""):
    """Apply premium dark styling to axes."""
    ax.set_facecolor(_SURFACE())
    ax.tick_params(colors=_TEXT_DIM(), labelsize=9, direction="in", length=3)
    for spine in ax.spines.values():
        spine.set_color(_BORDER())
        spine.set_linewidth(0.8)
    ax.grid(True, color=_GRID(), alpha=0.4, linewidth=0.5, linestyle="-")
    ax.grid(True, which="minor", color=_GRID_SUB(), alpha=0.2, linewidth=0.3)
    if title:
        ax.set_title(title, fontsize=13, fontweight=700, color=_BRIGHT(), pad=12,
                     path_effects=[pe.withStroke(linewidth=3, foreground=_BG())])


def _stat_box(ax, text: str, loc: str = "upper right"):
    """Frosted glass stats box."""
    coords = {
        "upper right": (0.98, 0.96, "right", "top"),
        "upper left":  (0.02, 0.96, "left", "top"),
        "lower right": (0.98, 0.04, "right", "bottom"),
        "lower left":  (0.02, 0.04, "left", "bottom"),
    }
    x, y, ha, va = coords.get(loc, coords["upper right"])
    ax.text(x, y, text, transform=ax.transAxes, fontsize=8.5, color=_TEXT(),
            ha=ha, va=va, family="monospace", linespacing=1.7,
            bbox=dict(boxstyle="round,pad=0.6", facecolor=_CARD(),
                     edgecolor=_BORDER(), alpha=0.92, linewidth=0.8))


def _verdict_badge(ax, text: str, color: str, x: float = 0.98, y: float = 0.98):
    """Verdict badge with glow."""
    ax.text(x, y, text, transform=ax.transAxes,
            ha="right", va="top", fontsize=15, fontweight=800, color=color,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=_CARD(),
                     edgecolor=color, alpha=0.95, linewidth=1.5),
            path_effects=[pe.withStroke(linewidth=4, foreground=color + "30")])


# ══════════════════════════════════════════════
# Interactive Canvas with zoom/pan/hover
# ══════════════════════════════════════════════

class _InteractiveCanvas(FigureCanvas):
    """FigureCanvas with mouse scroll zoom, drag pan, and hover coordinates."""

    coord_changed = Signal(str)

    def __init__(self, fig: Figure, parent=None):
        super().__init__(fig)
        self._pan_active = False
        self._pan_start = None
        self._pan_xlim = None
        self._pan_ylim = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("motion_notify_event", self._on_motion)

    def _on_scroll(self, event):
        """Zoom in/out with scroll wheel."""
        if event.inaxes is None:
            return
        ax = event.inaxes
        if ax.name == "polar":
            return

        factor = 0.8 if event.button == "up" else 1.25

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        xdata = event.xdata
        ydata = event.ydata

        new_xlim = [xdata - (xdata - xlim[0]) * factor,
                    xdata + (xlim[1] - xdata) * factor]
        new_ylim = [ydata - (ydata - ylim[0]) * factor,
                    ydata + (ylim[1] - ydata) * factor]

        ax.set_xlim(new_xlim)
        ax.set_ylim(new_ylim)
        self.draw_idle()

    def _on_press(self, event):
        """Start pan on middle-click or right-click."""
        if event.inaxes is None:
            return
        if event.button in (2, 3):  # middle or right
            self._pan_active = True
            self._pan_start = (event.xdata, event.ydata)
            self._pan_xlim = event.inaxes.get_xlim()
            self._pan_ylim = event.inaxes.get_ylim()
            self._pan_ax = event.inaxes
            self.setCursor(QCursor(Qt.ClosedHandCursor))

    def _on_release(self, event):
        if self._pan_active:
            self._pan_active = False
            self.setCursor(QCursor(Qt.ArrowCursor))

    def _on_motion(self, event):
        if event.inaxes is None:
            self.coord_changed.emit("")
            return

        # Hover: show coordinates
        if event.xdata is not None and event.ydata is not None:
            self.coord_changed.emit(f"x={event.xdata:.6g}  y={event.ydata:.6g}")

        # Pan
        if self._pan_active and self._pan_start:
            dx = self._pan_start[0] - event.xdata
            dy = self._pan_start[1] - event.ydata
            self._pan_ax.set_xlim(self._pan_xlim[0] + dx, self._pan_xlim[1] + dx)
            self._pan_ax.set_ylim(self._pan_ylim[0] + dy, self._pan_ylim[1] + dy)
            self.draw_idle()


# ══════════════════════════════════════════════
# Main Chart Widget
# ══════════════════════════════════════════════

class MBESChartWidget(QFrame):
    """Interactive chart container: premium charts + zoom/pan/hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._apply_frame_style()
        self.setMinimumHeight(420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(0)

        # Title
        self._title = QLabel("")
        self._title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {_BRIGHT()};
            background: transparent;
            padding: 4px 8px;
        """)
        self._layout.addWidget(self._title)

        # BL-038 lazy canvas: Figure() + _InteractiveCanvas() are ~1.2 s
        # of matplotlib backend init (Agg cache + font scan + first-draw).
        # Skip it during startup — a placeholder sits in the layout and
        # _ensure_canvas() swaps the real canvas in when any plot method
        # is first called. QTimer.singleShot(0) warms the canvas in the
        # background after the first event-loop tick, so by the time
        # users navigate to the analysis tab the canvas is typically
        # ready.
        self._fig = None
        self._canvas = None
        self._canvas_init_scheduled = False
        self._placeholder = QLabel("Loading chart…")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {_TEXT_DIM()}; padding: 60px; background: {_BG()};"
        )
        self._layout.addWidget(self._placeholder, 1)
        # Canvas is built lazily in:
        #   1. showEvent() — when widget first becomes visible
        #   2. any render_*() / _save_*() call that needs it
        # Both paths call _ensure_canvas() which is idempotent.

        # Bottom bar: toolbar buttons + coordinate display
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 2, 8, 2)
        bottom.setSpacing(4)

        for label, callback in [
            ("Home", self._reset_view),
            ("Save PNG", self._save_png),
        ]:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {_TEXT_DIM()};
                    border: 1px solid {_BORDER()};
                    border-radius: 4px;
                    padding: 0 10px;
                    font-size: {Font.XS}px;
                }}
                QPushButton:hover {{
                    background: {_CARD()};
                    color: {_BRIGHT()};
                    border-color: {_CYAN};
                }}
            """)
            btn.clicked.connect(callback)
            bottom.addWidget(btn)

        bottom.addStretch()

        self._coord_label = QLabel("Scroll: zoom | Right-drag: pan")
        self._coord_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {_TEXT_DIM()};
            background: transparent;
            font-family: monospace;
        """)
        # BL-038 lazy canvas: coord_changed is wired in _ensure_canvas()
        # once the canvas actually exists.
        bottom.addWidget(self._coord_label)

        self._layout.addLayout(bottom)

        # Store original limits for home reset
        self._home_limits = {}

    def showEvent(self, event):  # noqa: N802 — Qt override
        # BL-038: build canvas the first time the widget becomes visible.
        # Deferred — startups that never open this panel skip the cost.
        super().showEvent(event)
        if self._canvas is None and not self._canvas_init_scheduled:
            self._canvas_init_scheduled = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._ensure_canvas)

    def _ensure_canvas(self):
        """BL-038 lazy canvas: build Figure/Canvas on first use.

        Returns True if the canvas was (or is now) ready. Safe to call
        repeatedly — the second call is a ~5 ns dict lookup."""
        if self._canvas is not None:
            return True
        # Swap placeholder for the real canvas.
        self._fig = Figure(figsize=(11, 5.5), dpi=110, facecolor=_BG())
        self._canvas = _InteractiveCanvas(self._fig, self)
        self._canvas.setStyleSheet(f"background: {_BG()};")
        self._canvas.coord_changed.connect(
            lambda t: self._coord_label.setText(
                t if t else "Scroll: zoom | Right-drag: pan"
            )
        )
        # Replace placeholder in layout slot 1 (after title, before bottom bar).
        if self._placeholder is not None:
            self._layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
        self._layout.insertWidget(1, self._canvas, 1)
        return True

    def _apply_frame_style(self):
        self.setStyleSheet(f"""
            MBESChartWidget {{
                background: {_BG()};
                border: 1px solid {_BORDER()};
                border-radius: {Radius.SM}px;
            }}
        """)

    def refresh_theme(self):
        """Update all visual theme colors without reloading chart data."""
        self._apply_frame_style()
        if self._canvas is None:  # BL-038: canvas not built yet — nothing to repaint
            return
        self._fig.patch.set_facecolor(_BG())
        self._canvas.setStyleSheet(f"background: {_BG()};")
        self._title.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {_BRIGHT()};
            background: transparent;
            padding: 4px 8px;
        """)
        self._coord_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {_TEXT_DIM()};
            background: transparent;
            font-family: monospace;
        """)
        # Re-render axes with new theme if data exists
        for ax in self._fig.axes:
            ax.set_facecolor(_SURFACE())
        self._canvas.draw_idle()

    def _save_home(self):
        """Save current axes limits as home."""
        self._home_limits = {}
        if self._fig is None:  # BL-038
            return
        for ax in self._fig.axes:
            self._home_limits[id(ax)] = (ax.get_xlim(), ax.get_ylim())

    def _reset_view(self):
        """Reset to original zoom/pan."""
        if self._fig is None:  # BL-038
            return
        for ax in self._fig.axes:
            lims = self._home_limits.get(id(ax))
            if lims:
                ax.set_xlim(lims[0])
                ax.set_ylim(lims[1])
        self._canvas.draw_idle()

    def _save_png(self):
        """Save chart as high-DPI PNG."""
        if self._fig is None:  # BL-038: nothing to save if never drawn
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "차트 저장", "MBESQC_Chart.png", "PNG (*.png)")
        if path:
            self._fig.savefig(path, dpi=200, facecolor=_BG(),
                             bbox_inches="tight", pad_inches=0.3)

    def clear(self):
        self._title.setText("")
        if self._fig is None:  # BL-038
            return
        self._fig.clear()
        self._canvas.draw()

    def set_title(self, text: str):
        self._title.setText(text)

    # ═══════════════════════════════════════════
    # PREMIUM CHART RENDERERS
    # ═══════════════════════════════════════════

    def render_offset(self, data: dict):
        """Roll/Pitch bias -- horizontal bars with threshold zones."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        _dark_ax(ax, "Offset QC -- Roll / Pitch Bias Estimation")

        roll = data.get("roll_bias_deg", 0)
        pitch = data.get("pitch_bias_deg", 0)
        roll_std = data.get("roll_bias_std", 0)
        pitch_std = data.get("pitch_bias_std", 0)
        rv = data.get("roll_verdict", "N/A")
        pv = data.get("pitch_verdict", "N/A")

        names = ["Pitch Bias", "Roll Bias"]
        vals = [pitch, roll]
        errs = [pitch_std, roll_std]
        verdicts = [pv, rv]
        colors = [_VERDICT_COLOR().get(v, _TEXT_DIM()) for v in verdicts]

        y_pos = np.arange(len(names))

        # Threshold zones
        ax.axvspan(-0.1, 0.1, alpha=0.06, color=_EMERALD, zorder=0)
        ax.axvspan(-0.5, -0.1, alpha=0.04, color=_AMBER, zorder=0)
        ax.axvspan(0.1, 0.5, alpha=0.04, color=_AMBER, zorder=0)

        # Bars with gradient effect
        for i, (y, val, err, color) in enumerate(zip(y_pos, vals, errs, colors)):
            ax.barh(y, val, height=0.45, color=color, alpha=0.85,
                    edgecolor=color, linewidth=1.5, zorder=3)
            # Error cap
            ax.errorbar(val, y, xerr=err, fmt="none", ecolor=_TEXT_DIM(),
                       capsize=6, capthick=1.5, elinewidth=1.5, zorder=4)
            # Glow
            ax.barh(y, val, height=0.55, color=color, alpha=0.08, zorder=2)
            # Value label
            offset = err + 0.015 if val >= 0 else -(err + 0.015)
            ax.text(val + offset, y, f"{val:+.4f}\u00b0 \u00b1 {err:.4f}",
                    ha="left" if val >= 0 else "right", va="center",
                    fontsize=10, color=_BRIGHT(), fontweight=600,
                    path_effects=[pe.withStroke(linewidth=3, foreground=_BG())])
            # Verdict
            ax.text(0.01, y + 0.22, f"[{verdicts[i]}]",
                    fontsize=8, color=colors[i], fontweight=700,
                    transform=ax.get_yaxis_transform())

        # Threshold lines
        for th in [-0.5, -0.1, 0.1, 0.5]:
            th_color = _ROSE if abs(th) == 0.5 else _AMBER
            ax.axvline(th, color=th_color, linewidth=1, linestyle="--", alpha=0.5, zorder=1)
            ax.text(th, len(names) - 0.1, f"{th}\u00b0", ha="center", va="bottom",
                    fontsize=7, color=th_color, alpha=0.7)
        ax.axvline(0, color=_TEXT_DIM(), linewidth=0.8, alpha=0.4, zorder=1)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(names, fontsize=11, color=_BRIGHT(), fontweight=500)
        ax.set_xlabel("Bias (degrees)", color=_TEXT(), fontsize=10)

        # Auto-range with padding
        max_val = max(abs(roll) + roll_std, abs(pitch) + pitch_std, 0.6) * 1.3
        ax.set_xlim(-max_val, max_val)

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("Offset QC")

    def render_motion(self, data: dict):
        """Attitude statistics -- grouped bars with stats panel."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        axes_data = data.get("axes", {})
        if not axes_data:
            return

        ax = self._fig.add_subplot(111)
        _dark_ax(ax, "Motion QC -- IMU Attitude Statistics")

        names = []
        means = []
        stds = []
        colors = []
        verdicts = []

        for key in ("roll", "pitch", "heave", "heading"):
            a = axes_data.get(key, {})
            if a:
                names.append(a.get("name", key.title()))
                means.append(abs(a.get("mean", 0)))
                stds.append(a.get("std", 0))
                v = a.get("verdict", "N/A")
                verdicts.append(v)
                colors.append(_VERDICT_COLOR().get(v, _TEXT_DIM()))

        x = np.arange(len(names))
        w = 0.32

        # Mean bars (translucent)
        bars_mean = ax.bar(x - w/2, means, w, label="|Mean|",
                           color=[clr + "50" for clr in colors],
                           edgecolor=colors, linewidth=1.5, zorder=3)
        # Std bars (solid)
        bars_std = ax.bar(x + w/2, stds, w, label="Std Dev",
                          color=colors, alpha=0.85, zorder=3)
        # Glow under std bars
        ax.bar(x + w/2, stds, w * 1.5, color=colors, alpha=0.06, zorder=2)

        # Value labels
        for bar, val in zip(bars_std, stds):
            ax.text(bar.get_x() + bar.get_width()/2, val + max(stds)*0.03,
                    f"{val:.4f}", ha="center", va="bottom",
                    fontsize=8.5, color=_BRIGHT(), fontweight=600,
                    path_effects=[pe.withStroke(linewidth=2, foreground=_BG())])

        # Verdict badges on bars
        for i, (bar, v, clr) in enumerate(zip(bars_std, verdicts, colors)):
            ax.text(bar.get_x() + bar.get_width()/2, -max(stds)*0.08,
                    v, ha="center", va="top", fontsize=8, fontweight=700,
                    color=clr, transform=ax.transData)

        ax.set_xticks(x)
        ax.set_xticklabels(names, fontsize=11, color=_BRIGHT(), fontweight=500)
        ax.set_ylabel("Value", color=_TEXT(), fontsize=10)
        ax.legend(loc="upper left", fontsize=8, facecolor=_CARD(),
                 edgecolor=_BORDER(), labelcolor=_TEXT())

        # Stats box
        stat_text = (f"Samples: {data.get('total_samples', 0):,}\n"
                     f"Duration: {data.get('time_span_sec', 0):.0f}s\n"
                     f"Rate: {data.get('sample_rate_hz', 0):.0f} Hz\n"
                     f"Gaps: {data.get('num_gaps', 0)} "
                     f"(max {data.get('max_gap_sec', 0):.3f}s)\n"
                     f"Gap: [{data.get('gap_verdict', 'N/A')}]")
        _stat_box(ax, stat_text)

        ax.set_ylim(bottom=0)
        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("Motion QC")

    def render_svp(self, data: dict):
        """SVP QC -- clean item status display."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(_SURFACE())
        ax.axis("off")

        verdict = data.get("verdict", "N/A")
        v_color = _VERDICT_COLOR().get(verdict, _TEXT_DIM())

        ax.set_title(f"SVP QC", fontsize=14, fontweight=700, color=_BRIGHT(), pad=15)
        _verdict_badge(ax, verdict, v_color)

        # Header
        vel = data.get("velocity_range", [0, 0])
        header = (f"Profiles: {data.get('num_profiles', 0)}        "
                  f"Velocity: {vel[0]:.1f} \u2013 {vel[1]:.1f} m/s        "
                  f"Applied: {'Yes' if data.get('applied') else 'No'}")
        ax.text(0.03, 0.82, header, transform=ax.transAxes,
                fontsize=11, color=_BRIGHT(), fontweight=500)

        # Items
        items = data.get("items", [])
        y = 0.68
        for it in items:
            status = it.get("status", "N/A")
            name = it.get("name", "")
            detail = it.get("detail", "")
            s_color = _VERDICT_COLOR().get(status, _TEXT_DIM())

            # Status badge
            ax.text(0.03, y, status, transform=ax.transAxes,
                    fontsize=10, color=_BG(), fontweight=700,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=s_color,
                             edgecolor=s_color, alpha=0.9))
            ax.text(0.13, y, name, transform=ax.transAxes,
                    fontsize=10, color=_BRIGHT(), fontweight=500)
            ax.text(0.13, y - 0.07, detail, transform=ax.transAxes,
                    fontsize=9, color=_TEXT())
            y -= 0.16

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("SVP QC")

    def render_coverage(self, data: dict, selected_line: str | None = None):
        """Coverage map with premium tracklines. Highlight selected_line if given."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        title = "Coverage QC -- Survey Tracklines"
        if selected_line:
            title += f" [{selected_line}]"
        _dark_ax(ax, title)

        track_lines = data.get("track_lines", [])
        palette = [_CYAN, _EMERALD, _BLUE, _AMBER, _PURPLE, _ROSE, _TEAL, _INDIGO]

        if track_lines:
            for i, line in enumerate(track_lines):
                lats = np.asarray(line.get("lats", []))
                lons = np.asarray(line.get("lons", []))
                name = line.get("name", f"Line {i+1}")
                clr = palette[i % len(palette)]
                is_selected = (selected_line is None) or (name == selected_line)
                alpha = 0.9 if is_selected else 0.15
                lw = 2.5 if (selected_line and is_selected) else 1.8
                if len(lats) > 0:
                    ax.plot(lons, lats, color=clr, linewidth=lw, alpha=alpha,
                            label=name[:25], zorder=5 if is_selected else 2)
                    if is_selected:
                        ax.plot(lons, lats, color=clr, linewidth=5, alpha=0.12, zorder=1)
                        ax.scatter(lons[0], lats[0], c=clr, s=30, marker="o",
                                  edgecolors=_BRIGHT(), linewidths=0.8, zorder=6)
                        ax.scatter(lons[-1], lats[-1], c=clr, s=30, marker="s",
                                  edgecolors=_BRIGHT(), linewidths=0.8, zorder=6)
        else:
            lats = data.get("track_lats")
            lons = data.get("track_lons")
            if lats and lons:
                ax.plot(lons, lats, color=_CYAN, linewidth=1.2, alpha=0.8)
                ax.plot(lons, lats, color=_CYAN, linewidth=3, alpha=0.1)

        ax.set_xlabel("Longitude (\u00b0)", color=_TEXT(), fontsize=10)
        ax.set_ylabel("Latitude (\u00b0)", color=_TEXT(), fontsize=10)
        ax.set_aspect("equal")

        if track_lines and len(track_lines) <= 12:
            ax.legend(loc="upper right", fontsize=7, facecolor=_CARD(),
                     edgecolor=_BORDER(), labelcolor=_TEXT(), ncol=2,
                     framealpha=0.9)

        verdict = data.get("verdict", "N/A")
        v_color = _VERDICT_COLOR().get(verdict, _TEXT_DIM())

        stat_text = (f"Lines: {data.get('total_lines', 0)}\n"
                     f"Length: {data.get('total_length_km', 0):.1f} km\n"
                     f"Area: {data.get('total_area_km2', 0):.2f} km\u00b2\n"
                     f"Overlap: {data.get('mean_overlap_pct', 0):.1f}%")
        _stat_box(ax, stat_text, "lower right")
        _verdict_badge(ax, verdict, v_color, 0.02, 0.04)

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("Coverage QC")

    def render_crossline(self, data: dict):
        """Cross-line depth differences with IHO assessment."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)

        iho = data.get("iho_order", "1a")
        pct = data.get("iho_pass_pct", 0)
        verdict = data.get("verdict", "N/A")
        v_color = _VERDICT_COLOR().get(verdict, _TEXT_DIM())

        _dark_ax(ax, f"Cross-line QC -- IHO S-44 Order {iho}")

        metrics = [
            ("Mean |dZ|", abs(data.get("depth_diff_mean", 0)), _CYAN),
            ("Std dZ", data.get("depth_diff_std", 0), _BLUE),
            ("RMS dZ", data.get("depth_diff_rms", 0), _EMERALD),
            ("Max |dZ|", data.get("depth_diff_max", 0), _AMBER),
        ]

        names = [m[0] for m in metrics]
        vals = [m[1] for m in metrics]
        colors = [m[2] for m in metrics]

        y_pos = np.arange(len(names))
        bars = ax.barh(y_pos, vals, height=0.45, color=colors, alpha=0.85,
                       edgecolor=colors, linewidth=1.5, zorder=3)
        # Glow
        ax.barh(y_pos, vals, height=0.6, color=colors, alpha=0.08, zorder=2)

        for bar, val in zip(bars, vals):
            ax.text(val + max(vals) * 0.03, bar.get_y() + bar.get_height()/2,
                    f"{val:.4f} m", ha="left", va="center",
                    fontsize=10, color=_BRIGHT(), fontweight=600,
                    path_effects=[pe.withStroke(linewidth=2, foreground=_BG())])

        ax.set_yticks(y_pos)
        ax.set_yticklabels(names, fontsize=11, color=_BRIGHT(), fontweight=500)
        ax.set_xlabel("Depth Difference (m)", color=_TEXT(), fontsize=10)

        # IHO pass rate badge
        _verdict_badge(ax, f"IHO Pass: {pct:.1f}%", v_color)

        n_int = data.get("num_intersections", 0)
        striping = "Yes" if data.get("striping_detected") else "No"
        _stat_box(ax, f"Intersections: {n_int:,}\nStriping: {striping}", "lower right")

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("Cross-line QC")

    def render_motion_perline(self, data: dict):
        """Per-line motion statistics: horizontal grouped bar chart."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        _dark_ax(ax, "Motion QC -- Per-line Statistics")

        per_line = data.get("per_line", [])
        if not per_line:
            ax.text(0.5, 0.5, "Per-line 데이터 없음", transform=ax.transAxes,
                    ha="center", va="center", color=_TEXT_DIM(), fontsize=12)
            self._canvas.draw()
            return

        names = [p.get("filename", "?")[:20] for p in per_line]
        roll_vals = [p.get("roll_std", 0) for p in per_line]
        pitch_vals = [p.get("pitch_std", 0) for p in per_line]
        heave_vals = [p.get("heave_std", 0) for p in per_line]

        y = np.arange(len(names))
        h = 0.25

        ax.barh(y - h, roll_vals, height=h, color=_CYAN, alpha=0.85, label="Roll σ (°)")
        ax.barh(y, pitch_vals, height=h, color=_EMERALD, alpha=0.85, label="Pitch σ (°)")
        ax.barh(y + h, heave_vals, height=h, color=_AMBER, alpha=0.85, label="Heave σ (m)")

        ax.set_yticks(y)
        ax.set_yticklabels(names, fontsize=7, color=_TEXT())
        ax.set_xlabel("Standard Deviation", color=_TEXT(), fontsize=10)
        ax.legend(loc="lower right", fontsize=7, facecolor=_CARD(),
                  edgecolor=_BORDER(), labelcolor=_TEXT(), framealpha=0.9)

        ax.invert_yaxis()
        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("Motion QC (Per-line)")

    def render_radar(self, scores: dict[str, float]):
        """QC score radar with premium styling."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111, polar=True)
        ax.set_facecolor(_SURFACE())

        labels = list(scores.keys())
        values = [scores[k] for k in labels]
        n = len(labels)
        if n == 0:
            return

        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        values_c = values + [values[0]]
        angles_c = angles + [angles[0]]

        # Gradient fill
        ax.fill(angles_c, values_c, color=_CYAN, alpha=0.10)
        # Inner glow
        inner = [v * 0.85 for v in values_c]
        ax.fill(angles_c, inner, color=_CYAN, alpha=0.06)
        # Main line
        ax.plot(angles_c, values_c, color=_CYAN, linewidth=2.5, alpha=0.9,
                path_effects=[pe.withStroke(linewidth=5, foreground=_CYAN + "20")])

        # Score dots with color + value
        for angle, val in zip(angles, values):
            dot_clr = _EMERALD if val >= 80 else _AMBER if val >= 50 else _ROSE
            ax.scatter([angle], [val], c=dot_clr, s=80, zorder=5,
                      edgecolors=_BRIGHT(), linewidths=1.2)
            ax.text(angle, val + 10, f"{val:.0f}", ha="center", va="bottom",
                    fontsize=9, color=_BRIGHT(), fontweight=700,
                    path_effects=[pe.withStroke(linewidth=2, foreground=_BG())])

        ax.set_xticks(angles)
        ax.set_xticklabels(labels, fontsize=9.5, color=_BRIGHT(), fontweight=500)
        ax.set_ylim(0, 110)
        ax.set_yticks([25, 50, 75, 100])
        ax.set_yticklabels(["25", "50", "75", "100"], fontsize=7, color=_TEXT_DIM())
        ax.spines["polar"].set_color(_BORDER())
        ax.grid(color=_GRID(), alpha=0.4, linewidth=0.5)

        ax.set_title("QC Score Breakdown", fontsize=14, fontweight=700,
                     color=_BRIGHT(), pad=25,
                     path_effects=[pe.withStroke(linewidth=3, foreground=_BG())])

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("QC Score")

    def render_file_summary(self, data: dict):
        """File QC items with status badges."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(_SURFACE())
        ax.axis("off")

        verdict = data.get("verdict", "N/A")
        v_color = _VERDICT_COLOR().get(verdict, _TEXT_DIM())

        ax.set_title("File QC", fontsize=14, fontweight=700, color=_BRIGHT(), pad=15)
        _verdict_badge(ax, verdict, v_color)

        items = data.get("items", [])
        y = 0.78
        for it in items:
            status = it.get("status", "N/A")
            name = it.get("name", "")
            detail = it.get("detail", "")
            s_color = _VERDICT_COLOR().get(status, _TEXT_DIM())

            ax.text(0.03, y, status, transform=ax.transAxes,
                    fontsize=9, color=_BG(), fontweight=700,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=s_color,
                             edgecolor=s_color, alpha=0.9))
            ax.text(0.14, y, name, transform=ax.transAxes,
                    fontsize=10, color=_BRIGHT(), fontweight=500)
            ax.text(0.14, y - 0.065, detail, transform=ax.transAxes,
                    fontsize=9, color=_TEXT())
            y -= 0.14

        # File stats
        ax.text(0.03, y - 0.05,
                f"GSF: {data.get('gsf_count', 0)}    PDS: {data.get('pds_count', 0)}    "
                f"Lines: {data.get('total_lines', 0)}    Pings: {data.get('total_pings', 0):,}",
                transform=ax.transAxes, fontsize=9, color=_TEXT_DIM(), fontweight=500)

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("File QC")

    def render_vessel(self, data: dict):
        """Vessel QC -- PDS vs HVF comparison."""
        self._ensure_canvas()  # BL-038
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(_SURFACE())
        ax.axis("off")

        verdict = data.get("verdict", "N/A")
        v_color = _VERDICT_COLOR().get(verdict, _TEXT_DIM())

        ax.set_title("Vessel QC -- PDS vs HVF Comparison", fontsize=14,
                     fontweight=700, color=_BRIGHT(), pad=15)
        _verdict_badge(ax, verdict, v_color)

        items = data.get("items", [])
        y = 0.80
        for it in items:
            status = it.get("status", "N/A")
            name = it.get("name", "")
            pds_val = it.get("pds_value", "")
            hvf_val = it.get("hvf_value", "")
            s_color = _VERDICT_COLOR().get(status, _TEXT_DIM())

            ax.text(0.02, y, status, transform=ax.transAxes,
                    fontsize=9, color=_BG(), fontweight=700,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=s_color,
                             edgecolor=s_color, alpha=0.9))
            ax.text(0.12, y, name, transform=ax.transAxes,
                    fontsize=10, color=_BRIGHT(), fontweight=500)
            if pds_val or hvf_val:
                ax.text(0.45, y, f"PDS: {pds_val}", transform=ax.transAxes,
                        fontsize=9, color=_CYAN)
                ax.text(0.72, y, f"HVF: {hvf_val}", transform=ax.transAxes,
                        fontsize=9, color=_EMERALD)
            y -= 0.10

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()
        self.set_title("Vessel QC")

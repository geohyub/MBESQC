"""MBESQC CrosslineMap -- Interactive intersection map with IHO compliance.

Displays a 2D scatter map of crossline intersection cells colored by
depth-difference status against IHO S-44 TVU allowable.

Features:
  - Scroll zoom / right-drag pan (reuses _InteractiveCanvas pattern)
  - Hover: coordinate + depth diff + status tooltip in status bar
  - IHO Order combo (Special / Exclusive / 1a / 1b / 2)
  - Line tracks as background
  - Per-cell scatter: green/yellow/red by compliance ratio
  - Colorbar + legend
  - Home reset / PNG export
  - c() theme + refresh_theme()
  - Bilingual (i18n keys registered at module scope)
"""

from __future__ import annotations

import io
import math
from typing import Optional

import numpy as np

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.colors import LinearSegmentedColormap, BoundaryNorm
from matplotlib.cm import ScalarMappable
import matplotlib.ticker as mticker
import matplotlib.patheffects as pe

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QSizePolicy, QComboBox, QFileDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c
from geoview_pyside6 import i18n as _i18n


# ── IHO S-44 TVU parameters ──

_IHO_TVU = {
    "exclusive": (0.15, 0.0075),
    "special":   (0.25, 0.0075),
    "1a":        (0.50, 0.0130),
    "1b":        (0.50, 0.0130),
    "2":         (1.00, 0.0230),
}

_IHO_ORDERS = ["exclusive", "special", "1a", "1b", "2"]

_IHO_LABELS = {
    "exclusive": "Exclusive",
    "special":   "Special",
    "1a":        "Order 1a",
    "1b":        "Order 1b",
    "2":         "Order 2",
}


def _tvu_allowable(depth: float, order: str = "1a") -> float:
    a, b = _IHO_TVU.get(order.lower(), _IHO_TVU["1a"])
    return math.sqrt(a ** 2 + (b * depth) ** 2)


# ── i18n strings ──

_TRANSLATIONS = {
    "ko": {
        "crossline_map.title": "교차점 수심 차이 맵",
        "crossline_map.iho_order": "IHO 기준:",
        "crossline_map.no_data": "교차점 데이터가 없습니다",
        "crossline_map.easting": "Easting (m)",
        "crossline_map.northing": "Northing (m)",
        "crossline_map.hover_default": "스크롤: 확대/축소  |  우클릭 드래그: 이동",
        "crossline_map.colorbar_label": "|dZ| (m)",
        "crossline_map.pass": "PASS",
        "crossline_map.warning": "WARNING",
        "crossline_map.fail": "FAIL",
        "crossline_map.pass_rate": "통과율: {pct:.1f}% ({n}/{total})",
        "crossline_map.intersections": "교차점: {n}개",
        "crossline_map.btn_home": "Home",
        "crossline_map.btn_save": "PNG 저장",
        "crossline_map.save_title": "교차점 맵 저장",
        "crossline_map.legend_pass": "허용 범위 이내",
        "crossline_map.legend_warning": "허용 범위 근접",
        "crossline_map.legend_fail": "허용 범위 초과",
        "crossline_map.tooltip": "E={e:.1f}  N={n:.1f}\ndZ={dz:+.3f}m  depth={d:.1f}m\nTVU={tvu:.3f}m  {status}",
    },
    "en": {
        "crossline_map.title": "Crossline Intersection Map",
        "crossline_map.iho_order": "IHO Order:",
        "crossline_map.no_data": "No crossline data available",
        "crossline_map.easting": "Easting (m)",
        "crossline_map.northing": "Northing (m)",
        "crossline_map.hover_default": "Scroll: zoom  |  Right-drag: pan",
        "crossline_map.colorbar_label": "|dZ| (m)",
        "crossline_map.pass": "PASS",
        "crossline_map.warning": "WARNING",
        "crossline_map.fail": "FAIL",
        "crossline_map.pass_rate": "Pass: {pct:.1f}% ({n}/{total})",
        "crossline_map.intersections": "Intersections: {n}",
        "crossline_map.btn_home": "Home",
        "crossline_map.btn_save": "Save PNG",
        "crossline_map.save_title": "Save Crossline Map",
        "crossline_map.legend_pass": "Within tolerance",
        "crossline_map.legend_warning": "Near tolerance",
        "crossline_map.legend_fail": "Exceeds tolerance",
        "crossline_map.tooltip": "E={e:.1f}  N={n:.1f}\ndZ={dz:+.3f}m  depth={d:.1f}m\nTVU={tvu:.3f}m  {status}",
    },
}

_i18n.register_translations(_TRANSLATIONS)


def _t(key: str) -> str:
    return _i18n.t(key, key)


# ── Theme helpers (mirrors mbes_chart.py pattern) ──

def _BG():       return c().BG
def _SURFACE():  return c().BG_ALT
def _CARD():     return c().NAVY
def _GRID():     return c().SLATE
def _BORDER():   return c().BORDER_H
def _TEXT():     return c().MUTED
def _TEXT_DIM(): return c().MUTED
def _BRIGHT():   return c().TEXT_BRIGHT

_GREEN    = "#34D399"
_AMBER    = "#FBBF24"
_RED      = "#FB7185"
_CYAN     = "#22D3EE"
_BLUE     = "#60A5FA"
_PURPLE   = "#A78BFA"
_TEAL     = "#2DD4BF"
_INDIGO   = "#818CF8"
_WHITE    = "#FFFFFF"

# Palette for background line tracks
_LINE_PALETTE = [_CYAN, _BLUE, _PURPLE, _TEAL, _INDIGO, _AMBER]

# Diverging colormap for depth differences: green -> yellow -> red
_CMAP_IHO = LinearSegmentedColormap.from_list(
    "iho_status", [_GREEN, _AMBER, _RED], N=256)


def _dark_ax(ax, title: str = ""):
    ax.set_facecolor(_SURFACE())
    ax.tick_params(colors=_TEXT_DIM(), labelsize=9, direction="in", length=3)
    for spine in ax.spines.values():
        spine.set_color(_BORDER())
        spine.set_linewidth(0.8)
    ax.grid(True, color=_GRID(), alpha=0.3, linewidth=0.5, linestyle="-")
    if title:
        ax.set_title(title, fontsize=13, fontweight=700, color=_BRIGHT(), pad=12,
                     path_effects=[pe.withStroke(linewidth=3, foreground=_BG())])


# ── Interactive Canvas (zoom / pan / hover with pick) ──

class _MapCanvas(FigureCanvas):
    """FigureCanvas with scroll-zoom, right-drag-pan, and hover pick for annotations."""

    coord_changed = Signal(str)

    def __init__(self, fig: Figure, parent=None):
        super().__init__(fig)
        self._pan_active = False
        self._pan_start = None
        self._pan_xlim = None
        self._pan_ylim = None
        self._pan_ax = None
        self._annotation = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("motion_notify_event", self._on_motion)

    def set_annotation(self, ann):
        self._annotation = ann

    def _on_scroll(self, event):
        if event.inaxes is None:
            return
        ax = event.inaxes
        factor = 0.8 if event.button == "up" else 1.25
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        xd, yd = event.xdata, event.ydata
        ax.set_xlim([xd - (xd - xlim[0]) * factor, xd + (xlim[1] - xd) * factor])
        ax.set_ylim([yd - (yd - ylim[0]) * factor, yd + (ylim[1] - yd) * factor])
        self.draw_idle()

    def _on_press(self, event):
        if event.inaxes is None:
            return
        if event.button in (2, 3):
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
            # Hide annotation
            if self._annotation and self._annotation.get_visible():
                self._annotation.set_visible(False)
                self.draw_idle()
            return

        if event.xdata is not None and event.ydata is not None:
            self.coord_changed.emit(
                f"E={event.xdata:.1f}  N={event.ydata:.1f}")

        if self._pan_active and self._pan_start:
            dx = self._pan_start[0] - event.xdata
            dy = self._pan_start[1] - event.ydata
            self._pan_ax.set_xlim(self._pan_xlim[0] + dx, self._pan_xlim[1] + dx)
            self._pan_ax.set_ylim(self._pan_ylim[0] + dy, self._pan_ylim[1] + dy)
            self.draw_idle()


# ══════════════════════════════════════════════
# CrosslineMap Widget
# ══════════════════════════════════════════════

class CrosslineMap(QFrame):
    """Interactive crossline intersection map with IHO S-44 compliance coloring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}
        self._iho_order: str = "1a"
        self._scatter = None
        self._home_limits: dict = {}
        self._cell_data: Optional[dict] = None  # cached parsed arrays

        self._apply_frame_style()
        self.setMinimumHeight(480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(0)

        # ── Top bar: title + IHO order combo ──
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 4, 8, 2)
        top_bar.setSpacing(8)

        self._title_label = QLabel(_t("crossline_map.title"))
        self._title_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {_BRIGHT()};
            background: transparent;
        """)
        top_bar.addWidget(self._title_label)

        top_bar.addStretch()

        iho_label = QLabel(_t("crossline_map.iho_order"))
        iho_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {_TEXT()};
            background: transparent;
        """)
        top_bar.addWidget(iho_label)

        self._iho_combo = QComboBox()
        self._iho_combo.setFixedHeight(26)
        self._iho_combo.setFixedWidth(120)
        for order in _IHO_ORDERS:
            self._iho_combo.addItem(_IHO_LABELS[order], order)
        self._iho_combo.setCurrentIndex(2)  # "1a" default
        self._iho_combo.currentIndexChanged.connect(self._on_iho_changed)
        self._iho_combo.setStyleSheet(f"""
            QComboBox {{
                background: {c().DARK};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: {Font.XS}px;
            }}
            QComboBox:hover {{ border-color: {c().CYAN}; }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: {c().NAVY};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                selection-background-color: {c().SLATE};
            }}
        """)
        top_bar.addWidget(self._iho_combo)

        self._layout.addLayout(top_bar)

        # ── Canvas ──
        self._fig = Figure(figsize=(11, 6), dpi=110, facecolor=_BG())
        self._canvas = _MapCanvas(self._fig, self)
        self._canvas.setStyleSheet(f"background: {_BG()};")
        self._layout.addWidget(self._canvas, 1)

        # ── Bottom bar: buttons + coordinate display ──
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 2, 8, 2)
        bottom.setSpacing(4)

        for label_key, callback in [
            ("crossline_map.btn_home", self._reset_view),
            ("crossline_map.btn_save", self._save_png),
        ]:
            btn = QPushButton(_t(label_key))
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

        self._coord_label = QLabel(_t("crossline_map.hover_default"))
        self._coord_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {_TEXT_DIM()};
            background: transparent;
            font-family: monospace;
        """)
        self._canvas.coord_changed.connect(
            lambda t: self._coord_label.setText(
                t if t else _t("crossline_map.hover_default")))
        bottom.addWidget(self._coord_label)

        self._layout.addLayout(bottom)

    # ── Public API ──

    def set_data(self, crossline_data: dict):
        """Set crossline QC result data and render the map."""
        self._data = crossline_data or {}
        # Sync IHO order from data
        order = self._data.get("iho_order", "1a")
        if order in _IHO_ORDERS:
            idx = _IHO_ORDERS.index(order)
            self._iho_combo.blockSignals(True)
            self._iho_combo.setCurrentIndex(idx)
            self._iho_combo.blockSignals(False)
            self._iho_order = order
        self._parse_cell_data()
        self._render()

    def clear(self):
        self._data = {}
        self._cell_data = None
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        _dark_ax(ax)
        ax.text(0.5, 0.5, _t("crossline_map.no_data"),
                transform=ax.transAxes, ha="center", va="center",
                fontsize=14, color=_TEXT_DIM())
        self._canvas.draw()

    def refresh_theme(self):
        self._apply_frame_style()
        self._fig.patch.set_facecolor(_BG())
        self._canvas.setStyleSheet(f"background: {_BG()};")
        self._title_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {_BRIGHT()};
            background: transparent;
        """)
        self._coord_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {_TEXT_DIM()};
            background: transparent;
            font-family: monospace;
        """)
        if self._data:
            self._render()

    # ── Internal ──

    def _apply_frame_style(self):
        self.setStyleSheet(f"""
            CrosslineMap {{
                background: {_BG()};
                border: 1px solid {_BORDER()};
                border-radius: {Radius.SM}px;
            }}
        """)

    def _parse_cell_data(self):
        """Parse and cache cell arrays from data dict."""
        cell_e = self._data.get("cell_eastings")
        cell_n = self._data.get("cell_northings")
        cell_d = self._data.get("cell_diffs")
        cell_m = self._data.get("cell_mean_depths")

        if not cell_e or not cell_n:
            self._cell_data = None
            return

        self._cell_data = {
            "e": np.asarray(cell_e, dtype=float),
            "n": np.asarray(cell_n, dtype=float),
            "diffs": np.asarray(cell_d, dtype=float),
            "depths": np.asarray(cell_m, dtype=float),
        }

    def _compute_compliance(self) -> tuple[np.ndarray, np.ndarray, float, int, int]:
        """Compute per-cell IHO compliance.

        Returns:
            abs_diffs: absolute depth differences
            tvu_ratios: |dZ| / TVU_allowable (0 = perfect, 1 = at limit)
            pass_pct: percentage passing
            n_pass: count passing
            n_total: total count
        """
        cd = self._cell_data
        if cd is None:
            return np.empty(0), np.empty(0), 0.0, 0, 0

        diffs = cd["diffs"]
        depths = cd["depths"]
        order = self._iho_order
        sqrt2 = math.sqrt(2.0)

        abs_diffs = np.abs(diffs)
        tvu_limits = np.array([sqrt2 * _tvu_allowable(abs(d), order) for d in depths])
        tvu_ratios = np.where(tvu_limits > 0, abs_diffs / tvu_limits, 1.0)

        n_pass = int(np.sum(abs_diffs <= tvu_limits))
        n_total = len(diffs)
        pass_pct = 100.0 * n_pass / n_total if n_total > 0 else 0.0

        return abs_diffs, tvu_ratios, pass_pct, n_pass, n_total

    def _render(self):
        """Full render of the crossline intersection map."""
        self._fig.clear()

        if self._cell_data is None or len(self._cell_data["e"]) == 0:
            ax = self._fig.add_subplot(111)
            _dark_ax(ax)
            ax.text(0.5, 0.5, _t("crossline_map.no_data"),
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=14, color=_TEXT_DIM())
            self._canvas.draw()
            return

        ax = self._fig.add_subplot(111)
        _dark_ax(ax, _t("crossline_map.title")
                 + f" -- IHO S-44 {_IHO_LABELS.get(self._iho_order, self._iho_order)}")

        cd = self._cell_data

        # ── Background: line tracks ──
        line_tracks = self._data.get("line_tracks", [])
        for i, trk in enumerate(line_tracks):
            e_arr = trk.get("eastings", [])
            n_arr = trk.get("northings", [])
            if len(e_arr) > 1:
                clr = _LINE_PALETTE[i % len(_LINE_PALETTE)]
                ax.plot(e_arr, n_arr, color=clr, linewidth=1.0, alpha=0.25, zorder=1)

        # ── Compute compliance ──
        abs_diffs, tvu_ratios, pass_pct, n_pass, n_total = self._compute_compliance()

        # ── Scatter: color by TVU ratio ──
        # Clamp ratio to [0, 2] for coloring (0=perfect, 1=at limit, 2=2x limit)
        clamp_ratios = np.clip(tvu_ratios, 0, 2.0)

        # Size: proportional to |dZ|, clamped
        max_diff = max(abs_diffs.max(), 0.01)
        sizes = 8 + 80 * (abs_diffs / max_diff)

        self._scatter = ax.scatter(
            cd["e"], cd["n"],
            c=clamp_ratios,
            cmap=_CMAP_IHO,
            vmin=0, vmax=2.0,
            s=sizes,
            alpha=0.85,
            edgecolors="none",
            zorder=3,
            picker=True,
        )

        # Glow effect for fail points
        fail_mask = tvu_ratios > 1.0
        if fail_mask.any():
            ax.scatter(
                cd["e"][fail_mask], cd["n"][fail_mask],
                c=_RED, s=sizes[fail_mask] * 1.8, alpha=0.10,
                edgecolors="none", zorder=2,
            )

        # ── Colorbar ──
        sm = ScalarMappable(cmap=_CMAP_IHO,
                            norm=plt.Normalize(vmin=0, vmax=2.0))
        sm.set_array([])
        cbar = self._fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02, aspect=30)
        cbar.set_label(_t("crossline_map.colorbar_label") + " / TVU",
                       color=_TEXT(), fontsize=9)
        cbar.ax.tick_params(colors=_TEXT_DIM(), labelsize=8)
        cbar.ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"{v:.1f}x"))

        # ── Axes labels ──
        ax.set_xlabel(_t("crossline_map.easting"), color=_TEXT(), fontsize=10)
        ax.set_ylabel(_t("crossline_map.northing"), color=_TEXT(), fontsize=10)
        ax.set_aspect("equal")

        # ── Stats box ──
        order_label = _IHO_LABELS.get(self._iho_order, self._iho_order)
        pass_text = _t("crossline_map.pass_rate").format(
            pct=pass_pct, n=n_pass, total=n_total)
        int_text = _t("crossline_map.intersections").format(n=n_total)

        rms = self._data.get("depth_diff_rms", 0)
        max_d = self._data.get("depth_diff_max", 0)

        stats = f"IHO {order_label}\n{pass_text}\n{int_text}\nRMS={rms:.3f}m  Max={max_d:.3f}m"
        ax.text(0.02, 0.02, stats, transform=ax.transAxes,
                fontsize=8, color=_TEXT(), ha="left", va="bottom",
                family="monospace", linespacing=1.6,
                bbox=dict(boxstyle="round,pad=0.6", facecolor=_CARD(),
                         edgecolor=_BORDER(), alpha=0.92, linewidth=0.8))

        # ── Verdict badge ──
        verdict = "PASS" if pass_pct >= 95.0 else "WARNING" if pass_pct >= 80.0 else "FAIL"
        v_color = {
            "PASS": _GREEN, "WARNING": _AMBER, "FAIL": _RED,
        }.get(verdict, _TEXT_DIM())
        ax.text(0.98, 0.98, verdict, transform=ax.transAxes,
                ha="right", va="top", fontsize=15, fontweight=800, color=v_color,
                bbox=dict(boxstyle="round,pad=0.5", facecolor=_CARD(),
                         edgecolor=v_color, alpha=0.95, linewidth=1.5),
                path_effects=[pe.withStroke(linewidth=4, foreground=v_color + "30")])

        # ── Custom legend (manual patches) ──
        from matplotlib.lines import Line2D
        legend_items = [
            Line2D([0], [0], marker="o", color="none", markerfacecolor=_GREEN,
                   markersize=7, label=_t("crossline_map.legend_pass")),
            Line2D([0], [0], marker="o", color="none", markerfacecolor=_AMBER,
                   markersize=7, label=_t("crossline_map.legend_warning")),
            Line2D([0], [0], marker="o", color="none", markerfacecolor=_RED,
                   markersize=7, label=_t("crossline_map.legend_fail")),
        ]
        ax.legend(handles=legend_items, loc="upper left", fontsize=7,
                  facecolor=_CARD(), edgecolor=_BORDER(),
                  labelcolor=_TEXT(), framealpha=0.9)

        # ── Hover annotation (hidden initially) ──
        ann = ax.annotate(
            "", xy=(0, 0), xytext=(15, 15),
            textcoords="offset points",
            fontsize=8, color=_BRIGHT(), family="monospace",
            bbox=dict(boxstyle="round,pad=0.5", facecolor=_CARD(),
                     edgecolor=_BORDER(), alpha=0.95),
            arrowprops=dict(arrowstyle="->", color=_TEXT_DIM(), lw=0.8),
            zorder=10,
        )
        ann.set_visible(False)
        self._canvas.set_annotation(ann)

        # Connect pick events for hover detail
        self._fig.canvas.mpl_connect("motion_notify_event", self._on_hover_pick)

        self._fig.tight_layout()
        self._save_home()
        self._canvas.draw()

    def _on_hover_pick(self, event):
        """Show annotation on nearest scatter point when hovering."""
        if event.inaxes is None or self._scatter is None or self._cell_data is None:
            return
        ann = self._canvas._annotation
        if ann is None:
            return

        cd = self._cell_data
        if event.xdata is None or event.ydata is None:
            return

        # Find nearest point within a reasonable radius
        dx = cd["e"] - event.xdata
        dy = cd["n"] - event.ydata

        # Scale tolerance by current view extent
        ax = event.inaxes
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        view_scale = max(abs(xlim[1] - xlim[0]), abs(ylim[1] - ylim[0]))
        tol = view_scale * 0.015  # 1.5% of view extent

        dists = np.sqrt(dx**2 + dy**2)
        idx = np.argmin(dists)

        if dists[idx] > tol:
            if ann.get_visible():
                ann.set_visible(False)
                self._canvas.draw_idle()
            return

        e_val = float(cd["e"][idx])
        n_val = float(cd["n"][idx])
        dz = float(cd["diffs"][idx])
        depth = float(cd["depths"][idx])
        sqrt2 = math.sqrt(2.0)
        tvu = sqrt2 * _tvu_allowable(abs(depth), self._iho_order)
        status = _t("crossline_map.pass") if abs(dz) <= tvu else _t("crossline_map.fail")

        tooltip = _t("crossline_map.tooltip").format(
            e=e_val, n=n_val, dz=dz, d=depth, tvu=tvu, status=status)
        ann.set_text(tooltip)
        ann.xy = (e_val, n_val)
        ann.set_visible(True)
        self._canvas.draw_idle()

    def _on_iho_changed(self, index: int):
        """Re-render with new IHO order."""
        order = self._iho_combo.itemData(index)
        if order and order != self._iho_order:
            self._iho_order = order
            if self._cell_data is not None:
                self._render()

    def _save_home(self):
        """Store current axis limits for home-reset."""
        for ax in self._fig.axes:
            if hasattr(ax, "get_xlim"):
                self._home_limits[id(ax)] = (ax.get_xlim(), ax.get_ylim())

    def _reset_view(self):
        """Reset zoom/pan to original view."""
        for ax in self._fig.axes:
            stored = self._home_limits.get(id(ax))
            if stored:
                ax.set_xlim(stored[0])
                ax.set_ylim(stored[1])
        self._canvas.draw()

    def _save_png(self):
        """Export map as high-DPI PNG."""
        path, _ = QFileDialog.getSaveFileName(
            self, _t("crossline_map.save_title"), "crossline_map.png",
            "PNG (*.png)")
        if path:
            self._fig.savefig(path, dpi=200, facecolor=_BG(),
                              bbox_inches="tight", pad_inches=0.3)

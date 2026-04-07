"""MBESQC 3D Bathymetry Map -- pyqtgraph GLViewWidget surface plot.

Displays multibeam bathymetry data as an interactive 3D surface:
  X: Easting
  Y: Northing
  Z: Depth (inverted, deeper = lower)
  Color: depth-based gradient (shallow=blue -> deep=red)

Features:
  - Mouse rotation / zoom
  - Depth colormap (blue-cyan-green-yellow-red)
  - Grid resolution control
  - c() theme-aware + refresh_theme()
  - Bilingual (ui_text via i18n)
"""

from __future__ import annotations

import sys
import os
from typing import Optional

import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QSizePolicy, QSlider,
)
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c
from geoview_pyside6 import i18n as _i18n

# Register i18n keys
_TRANSLATIONS = {
    "ko": {
        "mbes.bathy3d.title": "3D Bathymetry Map",
        "mbes.bathy3d.resolution": "Resolution",
        "mbes.bathy3d.reset": "Reset View",
        "mbes.bathy3d.exag": "Vertical Exag.",
        "mbes.bathy3d.nodata": "No bathymetry data",
        "mbes.bathy3d.points": "points",
        "mbes.bathy3d.depth_range": "Depth range",
    },
    "en": {
        "mbes.bathy3d.title": "3D Bathymetry Map",
        "mbes.bathy3d.resolution": "Resolution",
        "mbes.bathy3d.reset": "Reset View",
        "mbes.bathy3d.exag": "Vertical Exag.",
        "mbes.bathy3d.nodata": "No bathymetry data",
        "mbes.bathy3d.points": "points",
        "mbes.bathy3d.depth_range": "Depth range",
    },
}

_i18n.register_translations(_TRANSLATIONS)


def _tr(key: str) -> str:
    return _i18n.t(key, key)


def _depth_colormap(n: int = 256) -> np.ndarray:
    """Build depth-based RGBA colormap: blue(shallow) -> cyan -> green -> yellow -> red(deep)."""
    colors = np.zeros((n, 4), dtype=np.float32)
    # Key stops: blue, cyan, green, yellow, red
    stops = [
        (0.0,  (0.2, 0.4, 1.0, 1.0)),    # blue (shallow)
        (0.25, (0.0, 0.8, 0.9, 1.0)),     # cyan
        (0.5,  (0.1, 0.8, 0.3, 1.0)),     # green
        (0.75, (0.9, 0.8, 0.1, 1.0)),     # yellow
        (1.0,  (0.9, 0.2, 0.1, 1.0)),     # red (deep)
    ]
    for i in range(n):
        t = i / max(n - 1, 1)
        # Find surrounding stops
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]
            t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                frac = (t - t0) / max(t1 - t0, 1e-9)
                colors[i] = [
                    c0[k] + frac * (c1[k] - c0[k]) for k in range(4)
                ]
                break
    return colors


class Bathymetry3D(QWidget):
    """Interactive 3D bathymetry surface viewer using pyqtgraph OpenGL."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._eastings: Optional[np.ndarray] = None
        self._northings: Optional[np.ndarray] = None
        self._depths: Optional[np.ndarray] = None
        self._grid_size: int = 50
        self._exaggeration: float = 2.0
        self._gl_widget = None
        self._surface_item = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(Space.SM)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(Space.SM)

        self._title_lbl = QLabel(_tr("mbes.bathy3d.title"))
        toolbar.addWidget(self._title_lbl)
        toolbar.addStretch()

        # Grid resolution
        self._res_label = QLabel(_tr("mbes.bathy3d.resolution"))
        toolbar.addWidget(self._res_label)

        self._res_combo = QComboBox()
        for label, val in [("30x30", 30), ("50x50", 50), ("80x80", 80), ("120x120", 120)]:
            self._res_combo.addItem(label, val)
        self._res_combo.setCurrentIndex(1)
        self._res_combo.setFixedWidth(90)
        self._res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        toolbar.addWidget(self._res_combo)

        # Vertical exaggeration slider
        self._exag_label = QLabel(_tr("mbes.bathy3d.exag"))
        toolbar.addWidget(self._exag_label)

        self._exag_slider = QSlider(Qt.Horizontal)
        self._exag_slider.setRange(10, 100)
        self._exag_slider.setValue(20)
        self._exag_slider.setFixedWidth(100)
        self._exag_slider.valueChanged.connect(self._on_exag_changed)
        toolbar.addWidget(self._exag_slider)

        self._exag_value_lbl = QLabel("2.0x")
        self._exag_value_lbl.setFixedWidth(40)
        toolbar.addWidget(self._exag_value_lbl)

        # Reset button
        self._reset_btn = QPushButton(_tr("mbes.bathy3d.reset"))
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setFixedHeight(28)
        self._reset_btn.clicked.connect(self._reset_view)
        toolbar.addWidget(self._reset_btn)

        root.addLayout(toolbar)

        # OpenGL 3D view
        try:
            import pyqtgraph.opengl as gl
            self._gl_widget = gl.GLViewWidget()
            self._gl_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._gl_widget.setCameraPosition(distance=200, elevation=30, azimuth=45)
            self._gl_widget.setBackgroundColor(c().BG)

            # Add coordinate grid
            grid = gl.GLGridItem()
            grid.setSize(100, 100, 0)
            grid.setSpacing(10, 10, 0)
            self._grid_item = grid
            self._gl_widget.addItem(grid)

            root.addWidget(self._gl_widget, 1)
        except Exception:
            # Fallback if OpenGL unavailable
            placeholder = QLabel(_tr("mbes.bathy3d.nodata") + " (OpenGL unavailable)")
            placeholder.setAlignment(Qt.AlignCenter)
            root.addWidget(placeholder, 1)
            self._gl_widget = None

        # Info bar
        self._info_lbl = QLabel("")
        root.addWidget(self._info_lbl)

        self._apply_styles()

    def _apply_styles(self):
        self._title_lbl.setStyleSheet(
            f"color: {c().TEXT_BRIGHT}; font-size: {Font.SM}px; "
            f"font-weight: 600; background: transparent;")
        self._res_label.setStyleSheet(
            f"color: {c().MUTED}; font-size: {Font.XS}px; background: transparent;")
        self._exag_label.setStyleSheet(
            f"color: {c().MUTED}; font-size: {Font.XS}px; background: transparent;")
        self._exag_value_lbl.setStyleSheet(
            f"color: {c().TEXT}; font-size: {Font.XS}px; background: transparent;")
        self._info_lbl.setStyleSheet(
            f"color: {c().MUTED}; font-size: {Font.XS}px; background: transparent;")

        combo_qss = f"""
            QComboBox {{
                background: {c().DARK};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                padding: 3px 8px;
                font-size: {Font.XS}px;
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: {c().DARK};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                selection-background-color: {c().NAVY};
            }}
        """
        self._res_combo.setStyleSheet(combo_qss)

        self._exag_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {c().DARK};
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {c().CYAN};
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }}
        """)

        self._reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c().NAVY};
                color: {c().TEXT};
                border: 1px solid {c().BORDER};
                border-radius: {Radius.SM}px;
                padding: 0 12px;
                font-size: {Font.XS}px;
            }}
            QPushButton:hover {{ background: {c().SLATE}; }}
        """)

        if self._gl_widget is not None:
            self._gl_widget.setBackgroundColor(c().BG)

    def refresh_theme(self):
        self._apply_styles()

    # ── Data Loading ──

    def set_data(self, eastings: list | np.ndarray,
                 northings: list | np.ndarray,
                 depths: list | np.ndarray):
        """Set bathymetry scatter data and render 3D surface."""
        self._eastings = np.asarray(eastings, dtype=np.float64)
        self._northings = np.asarray(northings, dtype=np.float64)
        self._depths = np.asarray(depths, dtype=np.float64)
        self._render()

    def set_data_from_result(self, result_data: dict):
        """Extract easting/northing/depth from MBESQC analysis result."""
        cov = result_data.get("coverage", {})
        xline = result_data.get("crossline", {})

        # Try coverage data first (line-level ping data)
        lines = cov.get("lines", [])
        all_e, all_n, all_d = [], [], []
        for ln in lines:
            pings = ln.get("pings", [])
            for ping in pings:
                e = ping.get("easting") or ping.get("center_easting")
                n = ping.get("northing") or ping.get("center_northing")
                d = ping.get("depth") or ping.get("mean_depth_m")
                if e is not None and n is not None and d is not None:
                    all_e.append(float(e))
                    all_n.append(float(n))
                    all_d.append(float(d))

        # Fallback to crossline cell data
        if not all_e and xline:
            cell_e = xline.get("cell_eastings", [])
            cell_n = xline.get("cell_northings", [])
            cell_d = xline.get("cell_mean_depths", [])
            if cell_e and cell_n and cell_d:
                all_e = [float(v) for v in cell_e]
                all_n = [float(v) for v in cell_n]
                all_d = [float(v) for v in cell_d]

        # Also try line track eastings/northings with mean depth
        if not all_e:
            tracks = xline.get("line_tracks", [])
            for trk in tracks:
                es = trk.get("eastings", [])
                ns = trk.get("northings", [])
                depth = trk.get("mean_depth_m", 0)
                for e, n in zip(es, ns):
                    all_e.append(float(e))
                    all_n.append(float(n))
                    all_d.append(float(depth))

        if all_e:
            self.set_data(all_e, all_n, all_d)
        else:
            self._info_lbl.setText(_tr("mbes.bathy3d.nodata"))

    # ── Rendering ──

    def _render(self):
        if self._gl_widget is None:
            return
        if self._eastings is None or len(self._eastings) < 3:
            self._info_lbl.setText(_tr("mbes.bathy3d.nodata"))
            return

        # Remove old surface
        if self._surface_item is not None:
            self._gl_widget.removeItem(self._surface_item)
            self._surface_item = None

        try:
            import pyqtgraph.opengl as gl
        except Exception:
            return

        e = self._eastings
        n = self._northings
        d = self._depths

        # Normalize coordinates to centered grid
        e_min, e_max = e.min(), e.max()
        n_min, n_max = n.min(), n.max()
        d_min, d_max = d.min(), d.max()

        e_norm = e - e_min
        n_norm = n - n_min

        e_range = max(e_max - e_min, 1.0)
        n_range = max(n_max - n_min, 1.0)
        d_range = max(d_max - d_min, 1.0)

        # Create gridded surface using scipy if available, else simple binning
        grid_n = self._grid_size
        try:
            from scipy.interpolate import griddata
            xi = np.linspace(0, e_range, grid_n)
            yi = np.linspace(0, n_range, grid_n)
            xi_grid, yi_grid = np.meshgrid(xi, yi)
            zi_grid = griddata(
                (e_norm, n_norm), d,
                (xi_grid, yi_grid),
                method='linear',
                fill_value=np.nan,
            )
            # Fill NaN with nearest
            mask = np.isnan(zi_grid)
            if mask.any():
                zi_nearest = griddata(
                    (e_norm, n_norm), d,
                    (xi_grid, yi_grid),
                    method='nearest',
                )
                zi_grid[mask] = zi_nearest[mask]
        except Exception:
            # Fall back when scipy is unavailable or triangulation fails
            # on nearly collinear / sparse point clouds.
            xi = np.linspace(0, e_range, grid_n)
            yi = np.linspace(0, n_range, grid_n)
            zi_grid = np.full((grid_n, grid_n), d.mean())
            counts = np.zeros((grid_n, grid_n))
            for ei, ni, di in zip(e_norm, n_norm, d):
                ix = min(int(ei / e_range * (grid_n - 1)), grid_n - 1)
                iy = min(int(ni / n_range * (grid_n - 1)), grid_n - 1)
                zi_grid[iy, ix] += di
                counts[iy, ix] += 1
            valid = counts > 0
            zi_grid[valid] /= counts[valid]

        # Depth values: invert so deeper is lower (negative Z)
        z_surface = -zi_grid * self._exaggeration

        # Color by depth
        d_norm_grid = (zi_grid - d_min) / max(d_range, 1e-9)
        d_norm_grid = np.clip(d_norm_grid, 0, 1)
        cmap = _depth_colormap(256)
        color_indices = (d_norm_grid * 255).astype(int)
        colors = cmap[color_indices]  # (grid_n, grid_n, 4)

        # Create surface mesh
        surface = gl.GLSurfacePlotItem(
            x=np.linspace(-e_range / 2, e_range / 2, grid_n),
            y=np.linspace(-n_range / 2, n_range / 2, grid_n),
            z=z_surface,
            colors=colors,
            shader='shaded',
            smooth=True,
        )
        self._surface_item = surface
        self._gl_widget.addItem(surface)

        # Adjust camera
        max_range = max(e_range, n_range)
        self._gl_widget.setCameraPosition(
            distance=max_range * 1.5,
            elevation=30,
            azimuth=45,
        )

        # Update grid
        self._grid_item.setSize(e_range, n_range, 0)
        self._grid_item.setSpacing(e_range / 10, n_range / 10, 0)

        # Info
        self._info_lbl.setText(
            f"{len(self._eastings):,} {_tr('mbes.bathy3d.points')}  |  "
            f"{_tr('mbes.bathy3d.depth_range')}: {d_min:.1f} - {d_max:.1f} m  |  "
            f"Grid: {grid_n}x{grid_n}"
        )

    def _reset_view(self):
        if self._gl_widget is not None:
            e_range = 100
            if self._eastings is not None:
                e_range = max(self._eastings.max() - self._eastings.min(), 100)
            n_range = e_range
            if self._northings is not None:
                n_range = max(self._northings.max() - self._northings.min(), 100)
            max_range = max(e_range, n_range)
            self._gl_widget.setCameraPosition(
                distance=max_range * 1.5,
                elevation=30,
                azimuth=45,
            )

    def _on_resolution_changed(self, _idx: int):
        val = self._res_combo.currentData()
        if val:
            self._grid_size = val
            if self._eastings is not None:
                self._render()

    def _on_exag_changed(self, value: int):
        self._exaggeration = value / 10.0
        self._exag_value_lbl.setText(f"{self._exaggeration:.1f}x")
        if self._eastings is not None:
            self._render()

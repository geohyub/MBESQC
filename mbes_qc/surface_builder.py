"""Surface Builder — Generate gridded surfaces from point cloud data.

Produces GeoTIFF outputs for:
  DTM (Bathymetric Average), Density, Std, Slope, Contour
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pds_toolkit.models import FauFile, GsfFile, GsfPing

try:
    import rasterio
    from rasterio.transform import from_bounds
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

try:
    from scipy.stats import binned_statistic_2d
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


@dataclass
class SurfaceResult:
    """Generated surface grids."""

    dtm: np.ndarray | None = None           # mean depth per cell
    density: np.ndarray | None = None        # point count per cell
    std: np.ndarray | None = None            # depth std per cell
    slope: np.ndarray | None = None          # slope degrees per cell
    tvu: np.ndarray | None = None            # mean TVU per cell
    thu: np.ndarray | None = None            # mean THU per cell

    cell_size: float = 1.0
    x_min: float = 0.0
    x_max: float = 0.0
    y_min: float = 0.0
    y_max: float = 0.0
    nx: int = 0
    ny: int = 0
    crs: str = ""
    num_points: int = 0


def build_surfaces_from_fau(
    fau: FauFile,
    cell_size: float = 1.0,
    output_dir: str | Path | None = None,
) -> SurfaceResult:
    """Build surfaces from FAU point cloud (already in projected coords)."""
    return _build_surfaces(
        fau.easting, fau.northing, fau.depth,
        cell_size=cell_size, output_dir=output_dir,
    )


def build_surfaces_from_gsf(
    gsf: GsfFile,
    cell_size: float = 1.0,
    output_dir: str | Path | None = None,
) -> SurfaceResult:
    """Build surfaces from GSF beam data.

    Converts per-ping beam arrays to georeferenced point cloud,
    then grids into surfaces.
    """
    all_e, all_n, all_d, all_tvu, all_thu = [], [], [], [], []

    for p in gsf.pings:
        if p.depth is None or p.across_track is None:
            continue

        hdg_rad = math.radians(p.heading)
        sin_h = math.sin(hdg_rad)
        cos_h = math.cos(hdg_rad)

        # Convert lat/lon to approximate local metres
        # (simple cylindrical projection, adequate for swath-scale distances)
        lat_m_per_deg = 111320.0
        lon_m_per_deg = 111320.0 * math.cos(math.radians(p.latitude))

        along = p.along_track if p.along_track is not None else np.zeros_like(p.depth)

        # Beam positions in projected metres
        # Body frame to geographic: across-track is perpendicular to heading
        # For heading h (bearing from north CW):
        #   starboard (+across) -> +E when heading N (sin=0, cos=1) -> use cos_h
        beam_e = p.longitude * lon_m_per_deg + p.across_track * cos_h + along * sin_h
        beam_n = p.latitude * lat_m_per_deg - p.across_track * sin_h + along * cos_h

        # Filter: only accepted beams (flag == 0) with valid depth
        valid_mask = (p.depth > 0) & np.isfinite(p.depth)
        if p.beam_flags is not None:
            valid_mask &= (p.beam_flags == 0)

        all_e.append(beam_e[valid_mask])
        all_n.append(beam_n[valid_mask])
        all_d.append(p.depth[valid_mask])

        if p.vert_error is not None:
            all_tvu.append(p.vert_error[valid_mask])
        if p.horiz_error is not None:
            all_thu.append(p.horiz_error[valid_mask])

    if not all_e:
        return SurfaceResult()

    e = np.concatenate(all_e)
    n = np.concatenate(all_n)
    d = np.concatenate(all_d)
    tvu = np.concatenate(all_tvu) if all_tvu else None
    thu = np.concatenate(all_thu) if all_thu else None

    return _build_surfaces(e, n, d, tvu=tvu, thu=thu,
                           cell_size=cell_size, output_dir=output_dir)


def _build_surfaces(
    e: np.ndarray, n: np.ndarray, d: np.ndarray,
    tvu: np.ndarray | None = None,
    thu: np.ndarray | None = None,
    cell_size: float = 1.0,
    output_dir: str | Path | None = None,
) -> SurfaceResult:
    """Core gridding engine."""
    if not HAS_SCIPY:
        raise ImportError("scipy required for surface generation: pip install scipy")

    # Filter invalid values
    valid = np.isfinite(e) & np.isfinite(n) & np.isfinite(d) & (d > 0)
    e, n, d = e[valid], n[valid], d[valid]
    if tvu is not None:
        tvu = tvu[valid]
    if thu is not None:
        thu = thu[valid]

    x_min, x_max = float(e.min()), float(e.max())
    y_min, y_max = float(n.min()), float(n.max())

    # Grid bins
    nx = max(1, int((x_max - x_min) / cell_size) + 1)
    ny = max(1, int((y_max - y_min) / cell_size) + 1)
    bins_x = np.linspace(x_min, x_max, nx + 1)
    bins_y = np.linspace(y_min, y_max, ny + 1)

    # DTM (mean depth)
    dtm_stat = binned_statistic_2d(e, n, d, statistic="mean", bins=[bins_x, bins_y])
    dtm = dtm_stat.statistic.T  # transpose to (ny, nx) for raster convention

    # Density (count)
    den_stat = binned_statistic_2d(e, n, d, statistic="count", bins=[bins_x, bins_y])
    density = den_stat.statistic.T

    # Std
    std_stat = binned_statistic_2d(e, n, d, statistic="std", bins=[bins_x, bins_y])
    std = std_stat.statistic.T

    # Slope (degrees from DTM gradient)
    slope = _compute_slope(dtm, cell_size)

    result = SurfaceResult(
        dtm=dtm, density=density, std=std, slope=slope,
        cell_size=cell_size,
        x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
        nx=nx, ny=ny, num_points=len(d),
    )

    # TPU surfaces
    if tvu is not None:
        tvu_stat = binned_statistic_2d(e, n, tvu, statistic="mean", bins=[bins_x, bins_y])
        result.tvu = tvu_stat.statistic.T
    if thu is not None:
        thu_stat = binned_statistic_2d(e, n, thu, statistic="mean", bins=[bins_x, bins_y])
        result.thu = thu_stat.statistic.T

    # Export GeoTIFFs
    if output_dir:
        _export_geotiffs(result, Path(output_dir))

    return result


def _compute_slope(dtm: np.ndarray, cell_size: float) -> np.ndarray:
    """Compute slope in degrees from DTM grid."""
    # Replace NaN with neighbor mean for gradient calculation
    dtm_filled = dtm.copy()
    nan_mask = np.isnan(dtm_filled)
    if nan_mask.any():
        dtm_filled[nan_mask] = np.nanmean(dtm_filled)

    dy, dx = np.gradient(dtm_filled, cell_size)
    slope_rad = np.arctan(np.sqrt(dx ** 2 + dy ** 2))
    slope_deg = np.degrees(slope_rad)
    slope_deg[nan_mask] = np.nan
    return slope_deg


def _export_geotiffs(result: SurfaceResult, output_dir: Path) -> None:
    """Export all surfaces as GeoTIFF files."""
    if not HAS_RASTERIO:
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    transform = from_bounds(
        result.x_min, result.y_min, result.x_max, result.y_max,
        result.nx, result.ny,
    )

    surfaces = {
        "DTM": result.dtm,
        "Density": result.density,
        "Std": result.std,
        "Slope": result.slope,
        "TVU": result.tvu,
        "THU": result.thu,
    }

    for name, grid in surfaces.items():
        if grid is None:
            continue

        # Flip vertically (rasterio expects top-left origin)
        grid_out = np.flipud(grid).astype(np.float32)
        grid_out = np.where(np.isnan(grid_out), -9999.0, grid_out)

        path = output_dir / f"{name}.tiff"
        with rasterio.open(
            str(path), "w",
            driver="GTiff",
            height=result.ny,
            width=result.nx,
            count=1,
            dtype="float32",
            nodata=-9999.0,
            transform=transform,
        ) as dst:
            dst.write(grid_out, 1)

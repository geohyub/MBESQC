"""Export utilities - Contour, Trackline, Allsounding, TFW.

Generates additional deliverable outputs from QC/surface data:
  - Contour lines from DTM (DXF format)
  - Tracklines from GSF ping positions (CSV/DXF)
  - Allsounding point cloud (CSV with E,N,D per beam)
  - TFW world files for GeoTIFFs
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from pds_toolkit.models import GsfFile
from .surface_builder import SurfaceResult


# ── Contour ─────────────────────────────────────────────────────


def export_contour_csv(
    surface: SurfaceResult,
    output_path: str | Path,
    interval: float = 1.0,
    min_depth: float | None = None,
    max_depth: float | None = None,
) -> int:
    """Export contour lines as CSV (X, Y, Z, contour_level).

    Uses matplotlib contour extraction. Returns number of contour segments.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if surface.dtm is None:
        return 0

    dtm = surface.dtm.copy()
    valid = dtm[~np.isnan(dtm)]
    if len(valid) == 0:
        return 0

    d_min = min_depth if min_depth is not None else float(np.floor(valid.min()))
    d_max = max_depth if max_depth is not None else float(np.ceil(valid.max()))

    levels = np.arange(d_min, d_max + interval, interval)
    if len(levels) < 2:
        return 0

    # Create grid coordinates
    x = np.linspace(surface.x_min, surface.x_max, surface.nx)
    y = np.linspace(surface.y_min, surface.y_max, surface.ny)
    X, Y = np.meshgrid(x, y)

    # Extract contours
    fig, ax = plt.subplots()
    cs = ax.contour(X, Y, dtm, levels=levels)
    plt.close(fig)

    # Write CSV
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_segments = 0
    with open(output_path, "w") as f:
        f.write("X,Y,Z,Level\n")
        for level_idx, level in enumerate(cs.levels):
            for path in cs.allsegs[level_idx]:
                for x_val, y_val in path:
                    f.write(f"{x_val:.3f},{y_val:.3f},{level:.2f},{level:.2f}\n")
                n_segments += 1

    return n_segments


def export_contour_dxf(
    surface: SurfaceResult,
    output_path: str | Path,
    interval: float = 1.0,
) -> int:
    """Export contour lines as DXF.

    Minimal DXF writer - no external dependency required.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if surface.dtm is None:
        return 0

    dtm = surface.dtm.copy()
    valid = dtm[~np.isnan(dtm)]
    if len(valid) == 0:
        return 0

    d_min = float(np.floor(valid.min()))
    d_max = float(np.ceil(valid.max()))
    levels = np.arange(d_min, d_max + interval, interval)

    x = np.linspace(surface.x_min, surface.x_max, surface.nx)
    y = np.linspace(surface.y_min, surface.y_max, surface.ny)
    X, Y = np.meshgrid(x, y)

    fig, ax = plt.subplots()
    cs = ax.contour(X, Y, dtm, levels=levels)
    plt.close(fig)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_entities = 0
    with open(output_path, "w") as f:
        # DXF header (R12 minimum)
        f.write("0\nSECTION\n2\nHEADER\n")
        f.write("9\n$ACADVER\n1\nAC1009\n")
        f.write("0\nENDSEC\n")
        f.write("0\nSECTION\n2\nENTITIES\n")

        for level_idx, level in enumerate(cs.levels):
            for path in cs.allsegs[level_idx]:
                if len(path) < 2:
                    continue
                # LWPOLYLINE entity
                f.write("0\nLWPOLYLINE\n")
                f.write("8\n0\n")  # layer
                f.write(f"38\n{level:.2f}\n")  # elevation
                f.write(f"90\n{len(path)}\n")  # vertex count
                f.write("70\n0\n")  # open polyline
                for pt in path:
                    f.write(f"10\n{pt[0]:.3f}\n20\n{pt[1]:.3f}\n")
                n_entities += 1

        f.write("0\nENDSEC\n0\nEOF\n")

    return n_entities


# ── Trackline ───────────────────────────────────────────────────


def export_tracklines_csv(
    gsf_files: list[GsfFile],
    output_path: str | Path,
) -> int:
    """Export tracklines as CSV (Lat, Lon, Heading, Time, LineFile)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_points = 0
    with open(output_path, "w") as f:
        f.write("Latitude,Longitude,Heading,Time,Line\n")
        for gsf in gsf_files:
            line_name = Path(gsf.filepath).stem
            for p in gsf.pings:
                f.write(f"{p.latitude:.8f},{p.longitude:.8f},"
                        f"{p.heading:.2f},"
                        f"{p.time.strftime('%Y-%m-%d %H:%M:%S')}.{p.time_nsec // 1000000:03d},"
                        f"{line_name}\n")
                n_points += 1

    return n_points


def export_tracklines_dxf(
    gsf_files: list[GsfFile],
    output_path: str | Path,
) -> int:
    """Export tracklines as DXF polylines."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_lines = 0
    with open(output_path, "w") as f:
        f.write("0\nSECTION\n2\nHEADER\n")
        f.write("9\n$ACADVER\n1\nAC1009\n")
        f.write("0\nENDSEC\n")
        f.write("0\nSECTION\n2\nENTITIES\n")

        for gsf in gsf_files:
            if not gsf.pings:
                continue
            f.write("0\nLWPOLYLINE\n8\nTracklines\n")
            f.write(f"90\n{len(gsf.pings)}\n70\n0\n")
            for p in gsf.pings:
                f.write(f"10\n{p.longitude:.8f}\n20\n{p.latitude:.8f}\n")
            n_lines += 1

        f.write("0\nENDSEC\n0\nEOF\n")

    return n_lines


# ── Allsounding ─────────────────────────────────────────────────


def export_allsoundings(
    gsf_files: list[GsfFile],
    output_path: str | Path,
    coord_type: str = "geographic",  # "geographic" or "projected"
) -> int:
    """Export all accepted beam soundings as CSV.

    Args:
        gsf_files: List of parsed GSF files.
        output_path: Output CSV file path.
        coord_type: "geographic" for lat/lon, "projected" for local E/N.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_points = 0
    with open(output_path, "w") as f:
        if coord_type == "geographic":
            f.write("Latitude,Longitude,Depth,Time,Line,Beam\n")
        else:
            f.write("Easting,Northing,Depth,Time,Line,Beam\n")

        for gsf in gsf_files:
            line_name = Path(gsf.filepath).stem
            for p in gsf.pings:
                if p.depth is None or p.across_track is None:
                    continue

                ts = f"{p.time.strftime('%Y-%m-%d %H:%M:%S')}.{p.time_nsec // 1000000:03d}"
                hdg_rad = math.radians(p.heading)
                cos_h, sin_h = math.cos(hdg_rad), math.sin(hdg_rad)

                along = p.along_track if p.along_track is not None else np.zeros_like(p.depth)

                for bi in range(p.num_beams):
                    # Skip flagged beams
                    if p.beam_flags is not None and p.beam_flags[bi] != 0:
                        continue
                    if p.depth[bi] <= 0:
                        continue

                    if coord_type == "geographic":
                        # Approximate geographic offset
                        lat_m = 111320.0
                        lon_m = 111320.0 * math.cos(math.radians(p.latitude))
                        de = p.across_track[bi] * cos_h + along[bi] * sin_h
                        dn = -p.across_track[bi] * sin_h + along[bi] * cos_h
                        lat = p.latitude + dn / lat_m
                        lon = p.longitude + de / lon_m
                        f.write(f"{lat:.8f},{lon:.8f},{p.depth[bi]:.3f},{ts},{line_name},{bi}\n")
                    else:
                        lat_m = 111320.0
                        lon_m = 111320.0 * math.cos(math.radians(p.latitude))
                        e = p.longitude * lon_m + p.across_track[bi] * cos_h + along[bi] * sin_h
                        n = p.latitude * lat_m - p.across_track[bi] * sin_h + along[bi] * cos_h
                        f.write(f"{e:.3f},{n:.3f},{p.depth[bi]:.3f},{ts},{line_name},{bi}\n")

                    n_points += 1

    return n_points


# ── TFW World File ──────────────────────────────────────────────


def generate_tfw(surface: SurfaceResult, tiff_path: str | Path) -> None:
    """Generate TFW world file for a GeoTIFF.

    TFW format:
      Line 1: pixel size in x (cell_size)
      Line 2: rotation about y axis (0)
      Line 3: rotation about x axis (0)
      Line 4: pixel size in y (-cell_size, negative for top-down)
      Line 5: x coordinate of center of upper-left pixel
      Line 6: y coordinate of center of upper-left pixel
    """
    tiff_path = Path(tiff_path)
    tfw_path = tiff_path.with_suffix(".tfw")

    x_center = surface.x_min + surface.cell_size / 2.0
    y_center = surface.y_max - surface.cell_size / 2.0

    with open(tfw_path, "w") as f:
        f.write(f"{surface.cell_size:.6f}\n")
        f.write("0.000000\n")
        f.write("0.000000\n")
        f.write(f"{-surface.cell_size:.6f}\n")
        f.write(f"{x_center:.6f}\n")
        f.write(f"{y_center:.6f}\n")


def generate_all_tfw(surface: SurfaceResult, surface_dir: str | Path) -> int:
    """Generate TFW files for all GeoTIFFs in a directory."""
    surface_dir = Path(surface_dir)
    count = 0
    for tiff in surface_dir.glob("*.tiff"):
        generate_tfw(surface, tiff)
        count += 1
    for tiff in surface_dir.glob("*.tif"):
        generate_tfw(surface, tiff)
        count += 1
    return count

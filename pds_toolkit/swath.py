"""Unified Swath Model - Format-agnostic multibeam ping interface.

Converts GSF, PDS binary, and FAU data into a common SwathLine
so that QC modules can operate on any source format.
"""

from __future__ import annotations

import datetime
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class SwathPing:
    """Format-agnostic single ping."""
    ping_number: int = 0
    timestamp: float = 0.0  # Unix seconds (float64)
    datetime_utc: datetime.datetime | None = None
    latitude: float = 0.0
    longitude: float = 0.0
    heading: float = 0.0   # degrees
    pitch: float = 0.0     # degrees
    roll: float = 0.0      # degrees
    heave: float = 0.0     # metres
    speed: float = 0.0     # m/s
    num_beams: int = 0
    source_format: str = ""  # "GSF", "PDS", "FAU"

    # Beam arrays (positive-down depth, port-negative across)
    depth: np.ndarray = field(default_factory=lambda: np.zeros(0))
    across_track: np.ndarray = field(default_factory=lambda: np.zeros(0))
    along_track: np.ndarray = field(default_factory=lambda: np.zeros(0))
    travel_time: np.ndarray = field(default_factory=lambda: np.zeros(0))
    beam_angle: np.ndarray = field(default_factory=lambda: np.zeros(0))
    beam_flags: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    quality: np.ndarray = field(default_factory=lambda: np.zeros(0))


@dataclass
class SwathLine:
    """Collection of pings from a single survey line."""
    filepath: str = ""
    line_name: str = ""
    source_format: str = ""
    pings: list[SwathPing] = field(default_factory=list)

    # Summary (computed after loading)
    start_time: datetime.datetime | None = None
    end_time: datetime.datetime | None = None
    duration_seconds: float = 0.0
    mean_heading: float = 0.0
    mean_depth: float = 0.0
    mean_speed: float = 0.0
    lat_range: tuple[float, float] = (0.0, 0.0)
    lon_range: tuple[float, float] = (0.0, 0.0)
    depth_range: tuple[float, float] = (0.0, 0.0)

    @property
    def num_pings(self) -> int:
        return len(self.pings)

    def compute_summary(self) -> None:
        if not self.pings:
            return
        valid = [p for p in self.pings if p.timestamp > 0]
        if not valid:
            return

        self.start_time = valid[0].datetime_utc
        self.end_time = valid[-1].datetime_utc
        self.duration_seconds = valid[-1].timestamp - valid[0].timestamp

        headings = [p.heading for p in valid if p.heading > 0]
        if headings:
            self.mean_heading = float(np.mean(headings))

        depths = []
        for p in valid:
            if len(p.depth) > 0:
                valid_d = p.depth[(p.depth != 0) & np.isfinite(p.depth)]
                if len(valid_d) > 0:
                    depths.append(float(np.mean(np.abs(valid_d))))
        if depths:
            self.mean_depth = float(np.mean(depths))

        speeds = [p.speed for p in valid if p.speed > 0]
        if speeds:
            self.mean_speed = float(np.mean(speeds))

        lats = [p.latitude for p in valid if p.latitude != 0]
        lons = [p.longitude for p in valid if p.longitude != 0]
        if lats:
            self.lat_range = (min(lats), max(lats))
        if lons:
            self.lon_range = (min(lons), max(lons))

        all_depths = []
        for p in valid:
            if len(p.depth) > 0:
                d = np.abs(p.depth[(p.depth != 0) & np.isfinite(p.depth)])
                if len(d) > 0:
                    all_depths.extend([float(d.min()), float(d.max())])
        if all_depths:
            self.depth_range = (min(all_depths), max(all_depths))


# ── Converters ─────────────────────────────────────────────────

def gsf_to_swath(gsf_file) -> SwathLine:
    """Convert GsfFile to SwathLine."""
    from pds_toolkit.models import GsfFile
    line = SwathLine(
        filepath=gsf_file.filepath,
        line_name=Path(gsf_file.filepath).stem,
        source_format="GSF",
    )

    for i, gp in enumerate(gsf_file.pings):
        sp = SwathPing(
            ping_number=i,
            timestamp=gp.time.timestamp() if gp.time else 0.0,
            datetime_utc=gp.time,
            latitude=gp.latitude,
            longitude=gp.longitude,
            heading=gp.heading,
            pitch=gp.pitch,
            roll=gp.roll,
            heave=gp.heave,
            speed=gp.speed,
            num_beams=gp.num_beams,
            source_format="GSF",
        )
        if gp.depth is not None:
            sp.depth = np.abs(gp.depth)
        if gp.across_track is not None:
            sp.across_track = gp.across_track
        if gp.along_track is not None:
            sp.along_track = gp.along_track
        if gp.travel_time is not None:
            sp.travel_time = gp.travel_time
        if gp.beam_angle is not None:
            sp.beam_angle = gp.beam_angle
        if gp.beam_flags is not None:
            sp.beam_flags = gp.beam_flags
        if gp.quality_flags is not None:
            sp.quality = gp.quality_flags.astype(np.float32)

        line.pings.append(sp)

    line.compute_summary()
    return line


def pds_to_swath(pds_data) -> SwathLine:
    """Convert PdsBinaryData to SwathLine."""
    from pds_toolkit.pds_binary import PdsBinaryData
    line = SwathLine(
        filepath=pds_data.filepath,
        line_name=Path(pds_data.filepath).stem,
        source_format="PDS",
    )

    # Build nav interpolation arrays
    nav_times = np.array([r.timestamp for r in pds_data.navigation]) if pds_data.navigation else np.array([])
    nav_lats = np.array([r.latitude for r in pds_data.navigation]) if pds_data.navigation else np.array([])
    nav_lons = np.array([r.longitude for r in pds_data.navigation]) if pds_data.navigation else np.array([])

    for pp in pds_data.pings:
        sp = SwathPing(
            ping_number=pp.ping_number,
            timestamp=pp.timestamp / 1000.0 if pp.timestamp > 0 else 0.0,
            datetime_utc=pp.datetime_utc,
            num_beams=pp.num_beams,
            source_format="PDS",
        )

        # Interpolate navigation
        if len(nav_times) > 0 and pp.timestamp > 0:
            sp.latitude = float(np.interp(pp.timestamp, nav_times, nav_lats))
            sp.longitude = float(np.interp(pp.timestamp, nav_times, nav_lons))

        # Heading from azimuth center
        if len(pp.azimuth) > 0:
            sp.heading = float(np.median(pp.azimuth))

        # Depth: convert to positive-down
        if len(pp.depth) > 0:
            sp.depth = np.abs(pp.depth)
        if len(pp.across_track) > 0:
            sp.across_track = pp.across_track
        if len(pp.along_track) > 0:
            sp.along_track = pp.along_track
        if len(pp.travel_time) > 0:
            sp.travel_time = pp.travel_time
        if len(pp.rx_angle) > 0:
            sp.beam_angle = np.degrees(pp.rx_angle)
        if len(pp.beam_flags) > 0:
            sp.beam_flags = pp.beam_flags
        if len(pp.quality) > 0:
            sp.quality = pp.quality

        line.pings.append(sp)

    line.compute_summary()
    return line


def load_swath(filepath: str | Path, max_pings: int | None = None, **kwargs) -> SwathLine:
    """Auto-detect format and load as SwathLine.

    Supports: .gsf, .pds, .fau
    """
    filepath = Path(filepath)
    ext = filepath.suffix.lower()

    if ext == ".gsf":
        from pds_toolkit import read_gsf
        gsf = read_gsf(str(filepath), max_pings=max_pings)
        return gsf_to_swath(gsf)

    elif ext == ".pds":
        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(str(filepath), max_pings=max_pings, **kwargs)
        return pds_to_swath(pds)

    else:
        raise ValueError(f"Unsupported format: {ext}. Use .gsf or .pds")

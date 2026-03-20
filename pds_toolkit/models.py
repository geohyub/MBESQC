"""Common data models for PDS Toolkit.

All parsed data from GSF/FAU/GPT/PDS/S7K/XTF/HVF formats are returned as
these dataclasses or numpy arrays, providing a unified interface.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import numpy as np


# ── PDS Header ──────────────────────────────────────────────────


@dataclass
class PdsMetadata:
    """Metadata extracted from PDS file text header."""

    filepath: str = ""
    pds_version: str = ""
    file_version: str = ""
    vessel_name: str = ""
    survey_type: str = ""
    start_time: str = ""
    project_name: str = ""
    project_number: str = ""
    client_name: str = ""
    contractor_name: str = ""
    operator_name: str = ""
    coord_system_group: str = ""
    coord_system_name: str = ""
    system_units: str = ""
    depth_units: str = ""
    sections: dict[str, dict[str, str]] = field(default_factory=dict)

    def get(self, section: str, key: str, default: str = "") -> str:
        return self.sections.get(section, {}).get(key, default)


# ── GSF ─────────────────────────────────────────────────────────


@dataclass
class GsfScaleFactors:
    """Scale factors for GSF beam arrays.

    Formula: actual_value = stored_value / multiplier + dc_offset
    """

    multiplier: float = 1.0
    dc_offset: float = 0.0

    def apply(self, raw: np.ndarray) -> np.ndarray:
        if self.multiplier == 0:
            return raw.astype(np.float64)
        return raw.astype(np.float64) / self.multiplier + self.dc_offset


@dataclass
class GsfAttitudeSample:
    """Single attitude measurement at IMU rate."""

    time: datetime.datetime
    time_nsec: int = 0
    pitch: float = 0.0   # degrees
    roll: float = 0.0    # degrees
    heave: float = 0.0   # metres
    heading: float = 0.0  # degrees


@dataclass
class GsfAttitudeRecord:
    """GSF Attitude record (type 12) containing IMU time-series."""

    num_measurements: int = 0
    times: np.ndarray = field(default_factory=lambda: np.array([]))      # Unix seconds (float64)
    pitch: np.ndarray = field(default_factory=lambda: np.array([]))      # degrees
    roll: np.ndarray = field(default_factory=lambda: np.array([]))       # degrees
    heave: np.ndarray = field(default_factory=lambda: np.array([]))      # metres
    heading: np.ndarray = field(default_factory=lambda: np.array([]))    # degrees


@dataclass
class GsfSvpRecord:
    """GSF Sound Velocity Profile record (type 3)."""

    time: datetime.datetime | None = None
    latitude: float = 0.0
    longitude: float = 0.0
    num_points: int = 0
    depth: np.ndarray = field(default_factory=lambda: np.array([]))      # metres
    sound_velocity: np.ndarray = field(default_factory=lambda: np.array([]))  # m/s


@dataclass
class GsfPing:
    """One multibeam ping from a GSF file."""

    time: datetime.datetime
    time_nsec: int
    longitude: float    # decimal degrees
    latitude: float     # decimal degrees
    num_beams: int
    center_beam: int
    heading: float      # degrees
    pitch: float        # degrees
    roll: float         # degrees
    heave: float        # metres
    tide_corrector: float    # metres
    depth_corrector: float   # metres
    course: float = 0.0     # degrees
    speed: float = 0.0      # knots
    ping_flags: int = 0

    # Beam arrays (actual physical values after scale-factor application)
    depth: np.ndarray | None = None           # metres (positive down)
    across_track: np.ndarray | None = None    # metres (port negative, stbd positive)
    along_track: np.ndarray | None = None     # metres
    travel_time: np.ndarray | None = None     # seconds
    beam_angle: np.ndarray | None = None      # degrees
    mean_rel_amp: np.ndarray | None = None    # dB or raw
    beam_flags: np.ndarray | None = None      # uint8 per beam
    quality_flags: np.ndarray | None = None   # uint8 per beam
    vert_error: np.ndarray | None = None      # metres
    horiz_error: np.ndarray | None = None     # metres


@dataclass
class GsfSummary:
    """Swath bathymetry summary from a GSF file."""

    start_time: datetime.datetime | None = None
    end_time: datetime.datetime | None = None
    min_latitude: float = 0.0
    max_latitude: float = 0.0
    min_longitude: float = 0.0
    max_longitude: float = 0.0
    min_depth: float = 0.0   # metres
    max_depth: float = 0.0   # metres


@dataclass
class GsfFile:
    """Complete parsed contents of a GSF file."""

    filepath: str = ""
    version: str = ""
    comments: list[str] = field(default_factory=list)
    summary: GsfSummary | None = None
    pings: list[GsfPing] = field(default_factory=list)
    scale_factors: dict[int, GsfScaleFactors] = field(default_factory=dict)
    processing_params: dict[str, str] = field(default_factory=dict)
    svp_profiles: list[GsfSvpRecord] = field(default_factory=list)
    attitude_records: list[GsfAttitudeRecord] = field(default_factory=list)

    @property
    def num_pings(self) -> int:
        return len(self.pings)

    @property
    def num_svp(self) -> int:
        return len(self.svp_profiles)

    @property
    def num_attitude(self) -> int:
        return len(self.attitude_records)

    def all_attitude_times(self) -> np.ndarray:
        """Concatenate all attitude record times into one array."""
        if not self.attitude_records:
            return np.array([])
        return np.concatenate([a.times for a in self.attitude_records])

    def all_attitude_roll(self) -> np.ndarray:
        if not self.attitude_records:
            return np.array([])
        return np.concatenate([a.roll for a in self.attitude_records])

    def all_attitude_pitch(self) -> np.ndarray:
        if not self.attitude_records:
            return np.array([])
        return np.concatenate([a.pitch for a in self.attitude_records])

    def all_attitude_heave(self) -> np.ndarray:
        if not self.attitude_records:
            return np.array([])
        return np.concatenate([a.heave for a in self.attitude_records])


# ── GPS Track ───────────────────────────────────────────────────


@dataclass
class GpsTrack:
    """GPS track from a GPT file."""

    filepath: str = ""
    latitudes: np.ndarray = field(default_factory=lambda: np.array([]))
    longitudes: np.ndarray = field(default_factory=lambda: np.array([]))

    @property
    def num_points(self) -> int:
        return len(self.latitudes)


# ── FAU ─────────────────────────────────────────────────────────


@dataclass
class FauFile:
    """Parsed FAU (Fledermaus) point cloud file."""

    filepath: str = ""
    num_points: int = 0
    easting: np.ndarray = field(default_factory=lambda: np.array([]))
    northing: np.ndarray = field(default_factory=lambda: np.array([]))
    depth: np.ndarray = field(default_factory=lambda: np.array([]))
    timestamps: np.ndarray = field(default_factory=lambda: np.array([]))
    field5: np.ndarray = field(default_factory=lambda: np.array([]))
    flags: np.ndarray = field(default_factory=lambda: np.array([]))

    def time_range(self) -> tuple[datetime.datetime, datetime.datetime] | None:
        if len(self.timestamps) == 0:
            return None
        ts_min, ts_max = int(self.timestamps.min()), int(self.timestamps.max())
        return (
            datetime.datetime.fromtimestamp(ts_min, tz=datetime.timezone.utc),
            datetime.datetime.fromtimestamp(ts_max, tz=datetime.timezone.utc),
        )


# ── CSV/ASCII Grid ──────────────────────────────────────────────


@dataclass
class DepthGrid:
    """Depth grid from CSV/ASCII export."""

    filepath: str = ""
    easting: np.ndarray = field(default_factory=lambda: np.array([]))
    northing: np.ndarray = field(default_factory=lambda: np.array([]))
    depth: np.ndarray = field(default_factory=lambda: np.array([]))

    @property
    def num_points(self) -> int:
        return len(self.depth)

    def extent(self) -> dict[str, float]:
        if self.num_points == 0:
            raise ValueError("Cannot compute extent of empty DepthGrid")
        return {
            "e_min": float(self.easting.min()),
            "e_max": float(self.easting.max()),
            "n_min": float(self.northing.min()),
            "n_max": float(self.northing.max()),
            "d_min": float(self.depth.min()),
            "d_max": float(self.depth.max()),
        }


# ── S7K ─────────────────────────────────────────────────────────


@dataclass
class S7kFileHeader:
    """S7K file header (record 7200)."""

    filepath: str = ""
    protocol_version: int = 0
    file_identifier: bytes = b""
    version: int = 0
    session_identifier: bytes = b""
    record_data_size: int = 0
    num_devices: int = 0
    recording_name: str = ""
    recording_program_version: str = ""
    user_defined_name: str = ""
    notes: str = ""


@dataclass
class S7kRecord:
    """Generic S7K data record."""

    record_type: int = 0
    device_id: int = 0
    system_enumerator: int = 0
    time: datetime.datetime | None = None
    size: int = 0
    data: bytes = b""


@dataclass
class S7kBathymetricData:
    """S7K Record 7006 - Bathymetric Data."""

    time: datetime.datetime | None = None
    serial_number: int = 0
    ping_number: int = 0
    num_beams: int = 0
    depth: np.ndarray = field(default_factory=lambda: np.array([]))
    across_track: np.ndarray = field(default_factory=lambda: np.array([]))
    along_track: np.ndarray = field(default_factory=lambda: np.array([]))
    pointing_angle: np.ndarray = field(default_factory=lambda: np.array([]))
    azimuth_angle: np.ndarray = field(default_factory=lambda: np.array([]))


@dataclass
class S7kRawDetection:
    """S7K Record 7027 - Raw Detection Data."""

    time: datetime.datetime | None = None
    serial_number: int = 0
    ping_number: int = 0
    num_detections: int = 0
    data_field_size: int = 0
    detection_algorithm: int = 0
    sampling_rate: float = 0.0
    tx_angle: float = 0.0  # degrees
    detection_point: np.ndarray = field(default_factory=lambda: np.array([]))
    rx_angle: np.ndarray = field(default_factory=lambda: np.array([]))
    flags: np.ndarray = field(default_factory=lambda: np.array([]))
    quality: np.ndarray = field(default_factory=lambda: np.array([]))


@dataclass
class S7kAttitude:
    """S7K Record 1016 - Attitude (IMU time-series)."""

    time: datetime.datetime | None = None
    num_entries: int = 0
    delta_time_ms: np.ndarray = field(default_factory=lambda: np.array([]))
    pitch: np.ndarray = field(default_factory=lambda: np.array([]))      # degrees
    roll: np.ndarray = field(default_factory=lambda: np.array([]))       # degrees
    heave: np.ndarray = field(default_factory=lambda: np.array([]))      # metres
    heading: np.ndarray = field(default_factory=lambda: np.array([]))    # degrees


@dataclass
class S7kSonarSettings:
    """S7K Record 7000 - Sonar Settings."""

    time: datetime.datetime | None = None
    serial_number: int = 0
    ping_number: int = 0
    frequency: float = 0.0       # Hz
    sample_rate: float = 0.0     # Hz
    receiver_bandwidth: float = 0.0
    tx_pulse_width: float = 0.0  # seconds
    tx_beamwidth_along: float = 0.0   # degrees
    tx_beamwidth_across: float = 0.0  # degrees
    tx_power: float = 0.0       # dB
    gain: float = 0.0           # dB
    spreading: float = 0.0
    absorption: float = 0.0     # dB/km


@dataclass
class S7kPosition:
    """S7K Record 1003 - Position."""

    time: datetime.datetime | None = None
    datum: int = 0
    latency: float = 0.0
    latitude: float = 0.0   # degrees
    longitude: float = 0.0  # degrees
    height: float = 0.0     # metres


@dataclass
class S7kFile:
    """Parsed S7K file contents."""

    filepath: str = ""
    file_header: S7kFileHeader | None = None
    records: list[S7kRecord] = field(default_factory=list)
    bathymetry: list[S7kBathymetricData] = field(default_factory=list)
    raw_detections: list[S7kRawDetection] = field(default_factory=list)
    positions: list[S7kPosition] = field(default_factory=list)
    attitudes: list[S7kAttitude] = field(default_factory=list)
    sonar_settings: list[S7kSonarSettings] = field(default_factory=list)
    record_type_counts: dict[int, int] = field(default_factory=dict)


# ── XTF ─────────────────────────────────────────────────────────


@dataclass
class XtfFileHeader:
    """XTF file header."""

    filepath: str = ""
    system_type: int = 0
    recording_program_name: str = ""
    recording_program_version: str = ""
    sonar_name: str = ""
    sonar_type: int = 0
    navigation_system: str = ""
    num_channels: int = 0
    num_bytes_header: int = 0


@dataclass
class XtfPingHeader:
    """XTF ping/packet header."""

    time: datetime.datetime | None = None
    event_number: int = 0
    ping_number: int = 0
    sound_velocity: float = 0.0
    latitude: float = 0.0
    longitude: float = 0.0
    heading: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    heave: float = 0.0
    num_samples: int = 0


@dataclass
class XtfFile:
    """Parsed XTF file contents."""

    filepath: str = ""
    file_header: XtfFileHeader | None = None
    ping_headers: list[XtfPingHeader] = field(default_factory=list)
    record_type_counts: dict[int, int] = field(default_factory=dict)


# ── HVF (Vessel Configuration) ──────────────────────────────────


@dataclass
class HvfSensorOffset:
    """Sensor offset/lever arm from vessel reference point."""

    name: str = ""
    x: float = 0.0   # forward (metres)
    y: float = 0.0   # starboard (metres)
    z: float = 0.0   # down (metres)
    roll: float = 0.0    # degrees
    pitch: float = 0.0   # degrees
    heading: float = 0.0  # degrees


@dataclass
class HvfFile:
    """Parsed HVF (CARIS HVF / PDS vessel config) file."""

    filepath: str = ""
    vessel_name: str = ""
    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    sensors: list[HvfSensorOffset] = field(default_factory=list)

    def get(self, section: str, key: str, default: str = "") -> str:
        return self.sections.get(section, {}).get(key, default)

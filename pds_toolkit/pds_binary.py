"""PDS Binary Data Reader - Teledyne PDS native acquisition format.

Reverse-engineered from PDS FileVersion 2.2 / PdsVersion 4.4.11.2.
Reads multibeam ping records, navigation, attitude, and tide data
directly from PDS binary files WITHOUT requiring CARIS or PDS software.

Supported hardware: Reson T50/T20 series at 1024 beams.
Other beam counts may require offset recalibration.

0xFF08 Record System:
  All sensor data uses [prefix(1)][FF 08 00(3)][u16 data_size][u16 type] headers.
  Record total = data_size + 5 bytes.

  Type  1 (81B): Navigation (lat, lon, heading, speed)
  Type  2 (39B): GNSS raw (50Hz)
  Type  4 (59B): MRU raw (50Hz)
  Type  8 (119B): Attitude (pitch, roll, heading, heave, rates)
  Type  9 (31B): Clock/sync
  Type 10 (59B): Sensor status (50Hz)
  Type 12 (59B): Tide/Sealevel
  Type 13 (155B): Computed ping parameters

Ping Record Structure (variable size, ~140KB):
  +0:       1024 x f32  Two-Way Travel Time (ms)
  +8204:    1024 x f32  Quality / SNR
  +12300:   1024 x f32  RX Beam Angles (radians)
  +61532:   1024 x f32  Along-Track distance (m)
  +115004:  1024 x f32  Across-Track distance (m)
  +118400:  ~177 x f32  Beam Azimuths (degrees)
  +123204:  1024 x f32  Depth (m, negative = below surface)
  +127300:  1024 x u8   Beam Flags (0=good, 11=flagged)
  +131402:  f64          Ping Timestamp (ms since Unix epoch)
"""

from __future__ import annotations

import struct
import datetime
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


# ── Constants ──────────────────────────────────────────────────

# Ping record field offsets (bytes from TT array start)
_TT_BEAMS_START = 8      # first beam TT at byte 8 (after 2 header floats)
_SAMPLING_RATE_OFFSET = 4104
_QUALITY_OFFSET = 8204
_RX_ANGLE_OFFSET = 12300
_ALONG_TRACK_OFFSET = 61532
_ACROSS_TRACK_OFFSET = 115004
_AZIMUTH_OFFSET = 118400
_DEPTH_OFFSET = 123204
_BEAM_FLAGS_OFFSET = 127300
_TIMESTAMP_OFFSET = 131402
_NUM_BEAMS = 1024

# TT array signature: [f32 ~0.21] [f32 0.0] before beam data
_TT_SIGNATURE_TOLERANCE = 0.05

# Minimum spacing between two legitimate ping TT arrays (bytes).
# At 1024 beams the smallest observed record is ~8KB; use conservative minimum.
_MIN_PING_SPACING = 6000

# Navigation record structure
_NAV_RECORD_SIZE = 648
_NAV_LAT_OFFSET = 24
_NAV_LON_OFFSET = 32
_NAV_ALT_OFFSET = 40
_NAV_HDG_OFFSET = 64
_NAV_SPEED_OFFSET = 480


# ── Data Classes ───────────────────────────────────────────────

@dataclass
class PdsPing:
    """Single multibeam ping from PDS binary data."""
    ping_number: int = 0
    timestamp: float = 0.0  # ms since epoch
    datetime_utc: datetime.datetime | None = None

    travel_time: np.ndarray = field(default_factory=lambda: np.zeros(0))
    depth: np.ndarray = field(default_factory=lambda: np.zeros(0))
    across_track: np.ndarray = field(default_factory=lambda: np.zeros(0))
    along_track: np.ndarray = field(default_factory=lambda: np.zeros(0))
    quality: np.ndarray = field(default_factory=lambda: np.zeros(0))
    rx_angle: np.ndarray = field(default_factory=lambda: np.zeros(0))
    beam_flags: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    azimuth: np.ndarray = field(default_factory=lambda: np.zeros(0))

    backscatter: np.ndarray = field(default_factory=lambda: np.zeros(0))

    sampling_rate: float = 0.0
    num_beams: int = 0  # actual valid beams (updated after parsing)
    file_offset: int = 0


@dataclass
class PdsNavRecord:
    """Single navigation fix from PDS binary data."""
    timestamp: float = 0.0
    datetime_utc: datetime.datetime | None = None
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    heading: float = 0.0
    speed: float = 0.0


@dataclass
class PdsAttitudeRecord:
    """Single attitude measurement from PDS Type 8 record."""
    timestamp: float = 0.0
    datetime_utc: datetime.datetime | None = None
    pitch: float = 0.0     # degrees (field +32)
    roll: float = 0.0      # degrees (field +40)
    heading: float = 0.0   # degrees (field +56)
    course: float = 0.0    # degrees (field +64)
    heave: float = 0.0     # metres  (field +72)


@dataclass
class PdsTideRecord:
    """Single tide/sealevel record."""
    timestamp: float = 0.0
    datetime_utc: datetime.datetime | None = None
    sealevel: float = 0.0


@dataclass
class PdsComputedRecord:
    """Computed ping parameters (Type 13/15) — per-ping solved position."""
    timestamp: float = 0.0
    datetime_utc: datetime.datetime | None = None
    easting: float = 0.0      # projected X (meters, EPSG from header)
    northing: float = 0.0     # projected Y (meters)
    speed: float = 0.0        # m/s
    heading: float = 0.0      # degrees
    along_dist: float = 0.0   # cumulative along-track distance (meters)
    draft: float = 0.0        # applied draft/sealevel (meters)
    heave: float = 0.0        # applied heave (meters)


@dataclass
class PdsSensorStatus:
    """Sensor status record (Type 10) — raw IMU + heading angles."""
    timestamp: float = 0.0
    datetime_utc: datetime.datetime | None = None
    raw_roll: float = 0.0     # raw IMU roll (degrees, may include mounting)
    raw_pitch: float = 0.0    # raw IMU pitch
    raw_heading: float = 0.0  # raw heading (degrees)
    raw_course: float = 0.0   # raw course (degrees)


@dataclass
class PdsBinaryData:
    """Complete parsed PDS binary acquisition data."""
    filepath: str = ""
    file_size: int = 0
    pings: list[PdsPing] = field(default_factory=list)
    navigation: list[PdsNavRecord] = field(default_factory=list)
    attitude: list[PdsAttitudeRecord] = field(default_factory=list)
    tide: list[PdsTideRecord] = field(default_factory=list)
    computed: list[PdsComputedRecord] = field(default_factory=list)
    sensor_status: list[PdsSensorStatus] = field(default_factory=list)

    # Derived summary (prefer @property but keep fields for serialisation)
    first_ping_time: datetime.datetime | None = None
    last_ping_time: datetime.datetime | None = None
    duration_seconds: float = 0.0
    ping_rate_hz: float = 0.0
    lat_range: tuple[float, float] = (0.0, 0.0)
    lon_range: tuple[float, float] = (0.0, 0.0)
    depth_range: tuple[float, float] = (0.0, 0.0)

    @property
    def num_pings(self) -> int:
        return len(self.pings)

    @property
    def num_nav_records(self) -> int:
        return len(self.navigation)

    @property
    def num_tide_records(self) -> int:
        return len(self.tide)


# ── Helper Functions ───────────────────────────────────────────

def _ms_to_datetime(ms: float) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def _is_valid_timestamp(val: float) -> bool:
    return 1.577e12 < val < 1.893e12


def _read_f32_array(data: bytes, offset: int, count: int) -> np.ndarray:
    end = offset + count * 4
    if end > len(data):
        available = (len(data) - offset) // 4
        if available <= 0:
            return np.zeros(0, dtype=np.float32)
        return np.frombuffer(data[offset:offset + available * 4], dtype='<f4').copy()
    return np.frombuffer(data[offset:end], dtype='<f4').copy()


def _read_f64(data: bytes, offset: int) -> float:
    if offset + 8 > len(data):
        return 0.0
    return struct.unpack_from('<d', data, offset)[0]


def _read_u8_array(data: bytes, offset: int, count: int) -> np.ndarray:
    end = offset + count
    if end > len(data):
        available = len(data) - offset
        if available <= 0:
            return np.zeros(0, dtype=np.uint8)
        return np.frombuffer(data[offset:offset + available], dtype=np.uint8).copy()
    return np.frombuffer(data[offset:end], dtype=np.uint8).copy()


# ── Ping Record Scanner ───────────────────────────────────────

def _find_text_header_end(filepath: str) -> int:
    """Find where the text configuration header ends."""
    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        file_size = f.tell()
        f.seek(0)
        data = f.read(min(600000, file_size))

    for i in range(10000, len(data) - 64):
        window = data[i:i + 64]
        printable = sum(1 for b in window if 32 <= b <= 126 or b in (10, 13, 9))
        if printable < 20:
            return i
    return 0


def _find_tt_arrays(filepath: str, max_pings: int | None = None) -> list[int]:
    """Find all TT array offsets by V-shape detection.

    Searches for 1024-element float32 arrays with characteristic V-shape:
    port/stbd edges > nadir center, monotonically decreasing toward center.
    No header signature assumed (varies across vessels).
    Skips text header area automatically.
    """
    offsets: list[int] = []
    chunk_size = 20 * 1024 * 1024
    overlap = 4200

    # Skip past text header
    search_start = _find_text_header_end(filepath)

    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        file_size = f.tell()

        for chunk_start in range(search_start, file_size, chunk_size - overlap):
            f.seek(chunk_start)
            read_size = min(chunk_size, file_size - chunk_start)
            if read_size < 4200:
                break

            data = f.read(read_size)
            n = len(data) // 4
            if n < 1030:
                continue

            floats = np.frombuffer(data[:n * 4], dtype='<f4')

            i = 0
            while i < n - 1026:
                # Quick check: center beam (nadir) should be 10-300ms
                center = float(floats[i + 512])
                if 10 < center < 300:
                    edge_port = float(floats[i + 2])
                    edge_stbd = float(floats[i + 1023])

                    # V-shape: both edges larger than center
                    if (edge_port > center * 1.2 and
                            edge_stbd > center * 1.2 and
                            edge_port < 1000 and edge_stbd < 1000):
                        # Verify monotonic decrease port→nadir
                        if float(floats[i + 100]) > float(floats[i + 300]) > float(floats[i + 500]):
                            abs_off = chunk_start + i * 4
                            # Verify all 1024 values are finite and reasonable
                            window = floats[i:i + 1024]
                            finite_count = int(np.sum(np.isfinite(window) & (np.abs(window) < 1000)))
                            if finite_count < 800:
                                i += 1
                                continue
                            if not offsets or abs_off - offsets[-1] > _MIN_PING_SPACING:
                                offsets.append(abs_off)
                                if max_pings and len(offsets) >= max_pings:
                                    return offsets
                            i += 1024
                            continue
                i += 1

    return offsets


def _parse_ping(data: bytes, file_offset: int, ping_number: int) -> PdsPing:
    """Parse a single ping record from raw bytes.

    Uses a two-pass approach:
    1. Try fixed offsets (fast, works for EDF-type files)
    2. Fall back to dynamic array detection (slower, works for all files)
    """
    ping = PdsPing(ping_number=ping_number, file_offset=file_offset)
    data_len = len(data)
    if data_len < _NUM_BEAMS * 4:
        return ping

    # Travel Time: always at the start (possibly with 2 header floats)
    tt_start = 0
    if data_len >= 8:
        hdr0 = struct.unpack_from('<f', data, 0)[0]
        hdr1 = struct.unpack_from('<f', data, 4)[0]
        if 0.1 < hdr0 < 0.5 and abs(hdr1) < 0.01:
            tt_start = _TT_BEAMS_START
    if data_len >= tt_start + _NUM_BEAMS * 4:
        ping.travel_time = _read_f32_array(data, tt_start, _NUM_BEAMS)

    # Try fixed offsets first
    _try_fixed_offsets(data, ping)

    # If depth not found via fixed offsets, use dynamic detection
    if len(ping.depth) == 0 or not np.any(ping.depth != 0):
        _try_dynamic_detection(data, ping)

    # Search for timestamp: try known offsets first, then full scan
    _TS_OFFSETS = [_TIMESTAMP_OFFSET, 61528, 44968, 2744, 1308]
    for ts_off in _TS_OFFSETS:
        if 0 <= ts_off and ts_off + 8 <= data_len:
            ts = _read_f64(data, ts_off)
            if _is_valid_timestamp(ts):
                ping.timestamp = ts
                ping.datetime_utc = _ms_to_datetime(ts)
                break

    # Fallback: scan entire ping for valid timestamps
    if ping.timestamp == 0:
        for off in range(0, min(data_len - 8, 150000), 4):
            ts = _read_f64(data, off)
            if _is_valid_timestamp(ts):
                ping.timestamp = ts
                ping.datetime_utc = _ms_to_datetime(ts)
                break

    # Fallback: compute depth from TT if no depth array found
    if (len(ping.depth) == 0 or not np.any(ping.depth != 0)) and len(ping.travel_time) > 0:
        tt = ping.travel_time.copy()
        valid = np.isfinite(tt) & (tt > 0) & (tt < 1e6)
        if np.sum(valid) > 100:
            _DEFAULT_SV = 1500.0
            depth_approx = np.zeros_like(tt)
            depth_approx[valid] = tt[valid] * _DEFAULT_SV / 2.0 / 1000.0
            ping.depth = -depth_approx
            ping._depth_source = 'computed_from_tt'

    # Fallback: compute across-track from depth + TT (no beam angle needed)
    has_good_across = (len(ping.across_track) > 0 and
                       np.sum(np.abs(ping.across_track) > 1.0) > _NUM_BEAMS * 0.15)
    if not has_good_across:
        if len(ping.depth) > 0 and len(ping.travel_time) > 0:
            tt = ping.travel_time
            d = np.abs(ping.depth)
            valid = np.isfinite(tt) & np.isfinite(d) & (tt > 0) & (d > 0) & (tt < 1e6)
            if np.sum(valid) > 100:
                _DEFAULT_SV = 1500.0
                across = np.zeros_like(tt)
                slant = tt[valid] * _DEFAULT_SV / 2.0 / 1000.0
                sq_diff = np.maximum(slant ** 2 - d[valid] ** 2, 0)
                across[valid] = np.sqrt(sq_diff)
                # Port side (first half) = negative, stbd = positive
                across[:_NUM_BEAMS // 2] *= -1
                # Sanity check: across should be < 500m for realistic swath
                if np.all(np.abs(across[across != 0]) < 500):
                    ping.across_track = across
                    ping._across_source = 'computed_from_tt_depth'

    # Set actual valid beam count
    if len(ping.depth) > 0:
        ping.num_beams = int(np.sum(ping.depth != 0))
    elif len(ping.travel_time) > 0:
        ping.num_beams = int(np.sum(ping.travel_time > 0))

    return ping


def _try_fixed_offsets(data: bytes, ping: PdsPing) -> None:
    """Try reading beam arrays at known fixed offsets (EDF calibrated)."""
    data_len = len(data)

    if data_len >= _QUALITY_OFFSET + _NUM_BEAMS * 4:
        arr = _read_f32_array(data, _QUALITY_OFFSET, _NUM_BEAMS)
        if np.all(np.isfinite(arr)) and np.all(np.abs(arr) < 200):
            ping.quality = arr

    if data_len >= _RX_ANGLE_OFFSET + _NUM_BEAMS * 4:
        arr = _read_f32_array(data, _RX_ANGLE_OFFSET, _NUM_BEAMS)
        if np.all(np.isfinite(arr)) and np.all(arr >= 0) and np.all(arr < 2.5):
            # Verify it's actually angles: should have range > 0.5 rad
            nz = arr[arr > 0.01]
            if len(nz) > 100 and nz.max() - nz.min() > 0.3:
                ping.rx_angle = arr

    # Backscatter at +65628 (confirmed for EDF/JAKO/Sinan)
    _BACKSCATTER_OFFSET = 65628
    if data_len >= _BACKSCATTER_OFFSET + _NUM_BEAMS * 4:
        arr = _read_f32_array(data, _BACKSCATTER_OFFSET, _NUM_BEAMS)
        if np.all(np.isfinite(arr)) and np.all(arr >= 0) and np.all(arr < 100000):
            ping.backscatter = arr

    # Across-track: try multiple known offsets
    _ACROSS_OFFSETS = [_ACROSS_TRACK_OFFSET, 61440, 135168, 170632]
    for a_off in _ACROSS_OFFSETS:
        if len(ping.across_track) > 0 and np.any(ping.across_track != 0):
            break
        if data_len >= a_off + _NUM_BEAMS * 4:
            arr = _read_f32_array(data, a_off, _NUM_BEAMS)
            finite = arr[np.isfinite(arr)]
            # Real across-track: has both negative and positive values
            if not np.all(np.abs(finite) < 500):
                continue
            has_neg = np.any(finite < -1.0)
            has_pos = np.any(finite > 1.0)
            nz_sig = finite[np.abs(finite) > 0.5]
            if len(nz_sig) > _NUM_BEAMS * 0.15 and has_neg and has_pos:
                ping.across_track = arr
                break

    # Depth: try multiple known offsets (varies by ping size and snippet count)
    # EDF/Bada 139K pings: 122880 (block 30)
    # Sinan 174K pings: 167936 (block 41) — 11 extra snippet blocks
    # Depth offsets vary by ping type:
    # Big pings (139K): 122880 (block 30)
    # Extra-snippet (174K): 167936 (block 41)
    # Medium pings (72K): 53248 (block 13)
    # Small pings (67K): via dynamic detection
    _DEPTH_OFFSETS = [_DEPTH_OFFSET, 122880, 126976, 167936, 172032, 53248, 57344]
    for d_off in _DEPTH_OFFSETS:
        if len(ping.depth) > 0 and np.any(ping.depth != 0):
            break
        if data_len >= d_off + _NUM_BEAMS * 4:
            arr = _read_f32_array(data, d_off, _NUM_BEAMS)
            finite = arr[np.isfinite(arr)]
            nz = finite[finite != 0]
            if len(nz) > _NUM_BEAMS * 0.3 and np.all(nz < 0) and np.all(nz > -500):
                ping.depth = arr
                break

    if data_len >= _TIMESTAMP_OFFSET + 8:
        ts = _read_f64(data, _TIMESTAMP_OFFSET)
        if _is_valid_timestamp(ts):
            ping.timestamp = ts
            ping.datetime_utc = _ms_to_datetime(ts)


def _try_dynamic_detection(data: bytes, ping: PdsPing) -> None:
    """Dynamically find beam arrays by value patterns."""
    data_len = len(data)
    n = data_len // 4
    if n < _NUM_BEAMS:
        return

    floats = np.frombuffer(data[:n * 4], dtype='<f4')

    i = _NUM_BEAMS + 10  # skip past TT array
    while i < n - _NUM_BEAMS:
        window = floats[i:i + _NUM_BEAMS]

        if not np.all(np.isfinite(window)):
            i += 1
            continue
        if np.any(np.abs(window) > 1e6):
            i += 1
            continue

        nonzero = window[window != 0]
        if len(nonzero) < _NUM_BEAMS * 0.3:
            i += 1
            continue

        mn = float(np.min(nonzero))
        mx = float(np.max(nonzero))
        std = float(np.std(nonzero))
        center = float(window[_NUM_BEAMS // 2])

        # Depth (negative): all < 0, range 1-500m
        if len(ping.depth) == 0 and mn < -0.5 and mx < 0 and abs(mn) < 500 and std < 30:
            ping.depth = window.copy()
            i += _NUM_BEAMS
            continue

        # Depth (positive): all > 0, range 1-500m
        # May be U-shape (edges > center for corrected depth)
        # or flat-ish (similar values across all beams)
        if len(ping.depth) == 0 and mn > 0.5 and mx < 500 and std > 1 and std < 50:
            # Verify it's not just TT values by checking range differs from TT
            if len(ping.travel_time) > 0:
                tt_center = float(ping.travel_time[_NUM_BEAMS // 2])
                if abs(center - tt_center) > 2:  # different from TT nadir
                    ping.depth = -window.copy()
                    i += _NUM_BEAMS
                    continue
            else:
                ping.depth = -window.copy()
                i += _NUM_BEAMS
                continue

        # Across-track: sign change, realistic range (max ~500m swath per side)
        if (len(ping.across_track) == 0 and mn < -5 and mx > 5
                and mn > -500 and mx < 500 and std < 200):
            # Use non-zero portions for sign check (handles partial swath)
            left_nz = window[:_NUM_BEAMS // 2]
            left_nz = left_nz[left_nz != 0]
            right_nz = window[_NUM_BEAMS // 2:]
            right_nz = right_nz[right_nz != 0]
            if len(left_nz) > 50 and len(right_nz) > 50:
                left_m = float(np.mean(left_nz))
                right_m = float(np.mean(right_nz))
                if (left_m < 0 and right_m > 0) or (left_m > 0 and right_m < 0):
                    ping.across_track = window.copy()
                    i += _NUM_BEAMS
                    continue

        i += 1


# ── Navigation Scanner ─────────────────────────────────────────

def _find_navigation(
    filepath: str,
    lat_range: tuple[float, float] = (-90.0, 90.0),
    lon_range: tuple[float, float] = (-180.0, 180.0),
    search_mb: int = 20,
) -> list[PdsNavRecord]:
    """Find and parse navigation records by searching for lat/lon patterns.

    Args:
        lat_range: Valid latitude range for the survey area.
        lon_range: Valid longitude range for the survey area.
        search_mb: How many MB from file start to search.
    """
    records: list[PdsNavRecord] = []

    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        file_size = f.tell()

        search_end = min(file_size, search_mb * 1024 * 1024)
        f.seek(0)
        data = f.read(search_end)

        lat_min, lat_max = lat_range
        lon_min, lon_max = lon_range

        for i in range(0, len(data) - 48, 8):
            lat = struct.unpack_from('<d', data, i)[0]
            if lat_min < lat < lat_max:
                lon = struct.unpack_from('<d', data, i + 8)[0]
                if lon_min < lon < lon_max:
                    ts_off = i - 24
                    if ts_off >= 0:
                        ts = struct.unpack_from('<d', data, ts_off)[0]
                        if _is_valid_timestamp(ts):
                            rec = PdsNavRecord(
                                timestamp=ts,
                                datetime_utc=_ms_to_datetime(ts),
                                latitude=lat,
                                longitude=lon,
                            )
                            if i + 16 < len(data):
                                alt = struct.unpack_from('<d', data, i + 16)[0]
                                if 0 < alt < 500:
                                    rec.altitude = alt
                            if i + 40 < len(data):
                                hdg = struct.unpack_from('<d', data, i + 40)[0]
                                if 0 < hdg < 360:
                                    rec.heading = hdg
                            records.append(rec)

    # De-duplicate (same timestamp within 1ms)
    if records:
        unique = [records[0]]
        for r in records[1:]:
            if abs(r.timestamp - unique[-1].timestamp) > 1:
                unique.append(r)
        records = unique

    if not records:
        warnings.warn(f"No navigation records found in {filepath}. "
                      f"Check lat_range={lat_range}, lon_range={lon_range}.")

    return records


# ── 0xFF08 Record Scanner ──────────────────────────────────

# Record sizes (INVARIANT across files - type numbers change per vessel config!)
_RS_NAV = 81        # Navigation: lat, lon, heading
_RS_GNSS = 39       # GNSS raw (50Hz): heading from GPS
_RS_MRU = 59        # MRU raw (50Hz): raw sensor values
_RS_ATTITUDE = 119  # Attitude: pitch, roll, heading, heave
_RS_CLOCK = 29      # Clock/sync
_RS_SENSOR = 59     # Sensor status (50Hz)
_RS_COMPUTED = 155  # Computed ping parameters

_FF08_MARKER = bytes([0xFF, 0x08, 0x00])


def _scan_ff08_records(filepath: str, search_start: int = 0,
                       search_size: int = 20 * 1024 * 1024) -> dict[str, list[tuple[int, int, int]]]:
    """Scan for all 0xFF08 records and return offsets grouped by record SIZE.

    Type numbers vary per vessel config, but record sizes are invariant:
      81B = Navigation, 39B = GNSS, 59B = MRU/status/tide,
      119B = Attitude, 29B = Clock, 155B = Computed

    Returns: {record_size_category: [(file_offset, data_size, type_number), ...]}
    Categories: 'nav', 'gnss', 'attitude', 'clock', 'mru_59', 'computed', 'other'
    """
    _SIZE_TO_CAT = {
        _RS_NAV: 'nav',
        _RS_GNSS: 'gnss',
        _RS_ATTITUDE: 'attitude',
        _RS_CLOCK: 'clock',
        _RS_COMPUTED: 'computed',
    }

    result: dict[str, list[tuple[int, int, int]]] = {}

    with open(filepath, 'rb') as f:
        f.seek(search_start)
        data = f.read(search_size)

    pos = 0
    while pos < len(data) - 10:
        idx = data.find(_FF08_MARKER, pos)
        if idx == -1:
            break

        if idx + 7 <= len(data):
            data_size = data[idx + 3] | (data[idx + 4] << 8)
            rec_type = data[idx + 5] | (data[idx + 6] << 8)

            if 4 < data_size < 50000 and rec_type < 1000:
                abs_off = search_start + idx
                total_size = data_size + 5
                category = _SIZE_TO_CAT.get(total_size, f'size_{total_size}')
                result.setdefault(category, []).append((abs_off, data_size, rec_type))

        pos = idx + 3

    return result


def _parse_ff08_nav(filepath: str, offsets: list[tuple[int, int]]) -> list[PdsNavRecord]:
    """Parse navigation records (81B, any type number).

    Payload layout (74 bytes):
      +0:  u16 echo (data size)
      +2:  f64 timestamp (ms epoch) — non-standard offset!
      +10: zeros (16 bytes padding)
      +26: f64 latitude (degrees)
      +34: f64 longitude (degrees)
      +42: remaining fields (altitude, heading, speed)
    """
    records = []
    with open(filepath, 'rb') as f:
        for file_off, data_size in offsets:
            f.seek(file_off + 7)
            payload = f.read(data_size - 2)
            if len(payload) < 42:
                continue

            # Timestamp at +2 (same non-standard offset as Type 8)
            ts = struct.unpack_from('<d', payload, 2)[0]
            if not _is_valid_timestamp(ts):
                continue

            rec = PdsNavRecord(timestamp=ts, datetime_utc=_ms_to_datetime(ts))

            # Lat at +26, Lon at +34
            if len(payload) >= 42:
                lat = struct.unpack_from('<d', payload, 26)[0]
                lon = struct.unpack_from('<d', payload, 34)[0]
                if -90 < lat < 90 and -180 < lon < 180:
                    rec.latitude = lat
                    rec.longitude = lon

            records.append(rec)

    return records


def _parse_ff08_attitude(filepath: str, offsets: list[tuple[int, int]]) -> list[PdsAttitudeRecord]:
    """Parse Type 8 attitude records.

    Type 8 payload layout (112 bytes):
      +0:  u16 data echo (112)
      +2:  f64 timestamp (ms epoch) — hidden at non-standard offset!
      +10: zeros (padding, 22 bytes)
      +32: f64 pitch (degrees)
      +40: f64 roll (degrees)
      +48: f64 -roll (mirror, ignored)
      +56: f64 heading (degrees)
      +64: f64 course/CoG (degrees)
      +72: f64 heave (metres)
      +80: f64 roll_rate, pitch_rate, heading_rate, heave_rate
    """
    records = []
    with open(filepath, 'rb') as f:
        for file_off, data_size in offsets:
            f.seek(file_off + 7)
            payload = f.read(data_size - 2)
            if len(payload) < 80:
                continue

            rec = PdsAttitudeRecord()

            # Timestamp at payload[2:10] (non-standard position)
            if len(payload) >= 10:
                ts = struct.unpack_from('<d', payload, 2)[0]
                if _is_valid_timestamp(ts):
                    rec.timestamp = ts
                    rec.datetime_utc = _ms_to_datetime(ts)

            if len(payload) >= 40:
                rec.pitch = struct.unpack_from('<d', payload, 32)[0]
            if len(payload) >= 48:
                rec.roll = struct.unpack_from('<d', payload, 40)[0]
            if len(payload) >= 64:
                rec.heading = struct.unpack_from('<d', payload, 56)[0]
            if len(payload) >= 72:
                rec.course = struct.unpack_from('<d', payload, 64)[0]
            if len(payload) >= 80:
                rec.heave = struct.unpack_from('<d', payload, 72)[0]

            records.append(rec)

    return records


def _parse_ff08_tide(filepath: str, offsets: list[tuple[int, int]]) -> list[PdsTideRecord]:
    """Parse Type 12 tide records."""
    records = []
    with open(filepath, 'rb') as f:
        for file_off, data_size in offsets:
            f.seek(file_off + 7)
            payload = f.read(data_size - 2)
            if len(payload) < 60:
                continue

            ts = struct.unpack_from('<d', payload, 0)[0]
            if not _is_valid_timestamp(ts):
                continue

            # Sealevel at payload +52
            sealevel = struct.unpack_from('<f', payload, 52)[0]
            if abs(sealevel) < 50:
                records.append(PdsTideRecord(
                    timestamp=ts,
                    datetime_utc=_ms_to_datetime(ts),
                    sealevel=sealevel,
                ))

    return records


# ── Tide Scanner ───────────────────────────────────────────────

def _find_tide_records(
    filepath: str,
    sealevel_value: float | None = None,
) -> list[PdsTideRecord]:
    """Find tide/sealevel records.

    Args:
        sealevel_value: If provided, search for this exact f64 value.
                        If None, search for any f64 in [-5.0, +5.0] with
                        a valid timestamp 18 bytes before it.
    """
    records: list[PdsTideRecord] = []

    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        file_size = f.tell()
        search_end = min(file_size, 5 * 1024 * 1024)
        f.seek(0)
        data = f.read(search_end)

    if sealevel_value is not None:
        # Exact value search
        sealevel_bytes = struct.pack('<d', sealevel_value)
        pos = 0
        while pos < len(data) - 8:
            idx = data.find(sealevel_bytes, pos)
            if idx == -1:
                break
            ts_off = idx - 18
            if ts_off >= 0:
                ts = struct.unpack_from('<d', data, ts_off)[0]
                if _is_valid_timestamp(ts):
                    records.append(PdsTideRecord(
                        timestamp=ts,
                        datetime_utc=_ms_to_datetime(ts),
                        sealevel=sealevel_value,
                    ))
            pos = idx + 8
    else:
        # Pattern search: look for timestamps with nearby small f64 values
        for i in range(0, len(data) - 48, 2):
            ts = struct.unpack_from('<d', data, i)[0]
            if _is_valid_timestamp(ts):
                sl_off = i + 18
                if sl_off + 8 <= len(data):
                    sl = struct.unpack_from('<d', data, sl_off)[0]
                    if -5.0 <= sl <= 5.0 and sl != 0.0:
                        if not records or abs(ts - records[-1].timestamp) > 500:
                            records.append(PdsTideRecord(
                                timestamp=ts,
                                datetime_utc=_ms_to_datetime(ts),
                                sealevel=sl,
                            ))

    return records


# ── Main Reader ────────────────────────────────────────────────

def read_pds_binary(
    filepath: str | Path,
    max_pings: int | None = None,
    load_arrays: bool = True,
    load_navigation: bool = True,
    load_tide: bool = True,
    min_record_bytes: int = 132000,
    lat_range: tuple[float, float] = (-90.0, 90.0),
    lon_range: tuple[float, float] = (-180.0, 180.0),
    tide_sealevel_hint: float | None = None,
) -> PdsBinaryData:
    """Parse PDS binary acquisition data.

    Args:
        filepath: Path to .pds file.
        max_pings: Maximum number of pings to read (None = all).
        load_arrays: If True, load all beam arrays (TT, depth, across, etc.).
        load_navigation: If True, parse navigation records.
        load_tide: If True, parse tide/sealevel records.
        min_record_bytes: Minimum bytes to read per ping for full arrays.
        lat_range: Valid latitude range for navigation search.
        lon_range: Valid longitude range for navigation search.
        tide_sealevel_hint: Known sealevel value to search for (from PDS header).

    Returns:
        PdsBinaryData with all parsed records.

    Note:
        Offsets are calibrated for Reson T50/T20 at 1024 beams.
        Other beam configurations may produce incorrect results.
    """
    filepath = str(filepath)
    result = PdsBinaryData(filepath=filepath)

    with open(filepath, 'rb') as f:
        f.seek(0, 2)
        result.file_size = f.tell()

    # 1. Find all ping TT arrays
    try:
        tt_offsets = _find_tt_arrays(filepath, max_pings=max_pings)
    except (IOError, OSError) as e:
        warnings.warn(f"Failed to scan pings in {filepath}: {e}")
        return result

    # 2. Parse each ping
    try:
        with open(filepath, 'rb') as f:
            for idx, tt_off in enumerate(tt_offsets):
                if load_arrays:
                    f.seek(tt_off)
                    data = f.read(min_record_bytes)
                    ping = _parse_ping(data, tt_off, idx)
                else:
                    ts_off = tt_off + _TIMESTAMP_OFFSET
                    if ts_off + 8 <= result.file_size:
                        f.seek(ts_off)
                        ts = struct.unpack('<d', f.read(8))[0]
                        ping = PdsPing(
                            ping_number=idx,
                            file_offset=tt_off,
                            timestamp=ts if _is_valid_timestamp(ts) else 0.0,
                            datetime_utc=_ms_to_datetime(ts) if _is_valid_timestamp(ts) else None,
                        )
                    else:
                        ping = PdsPing(ping_number=idx, file_offset=tt_off)
                result.pings.append(ping)
    except (IOError, OSError, struct.error) as e:
        warnings.warn(f"Error reading pings from {filepath}: {e}")

    # Summary statistics
    valid_pings = [p for p in result.pings if p.timestamp > 0]
    if valid_pings:
        result.first_ping_time = valid_pings[0].datetime_utc
        result.last_ping_time = valid_pings[-1].datetime_utc
        result.duration_seconds = (valid_pings[-1].timestamp - valid_pings[0].timestamp) / 1000.0
        if result.duration_seconds > 0:
            result.ping_rate_hz = len(valid_pings) / result.duration_seconds

        if load_arrays:
            depth_arrays = [p.depth[p.depth != 0] for p in valid_pings if len(p.depth) > 0]
            if depth_arrays:
                all_depths = np.concatenate(depth_arrays)
                if len(all_depths) > 0:
                    result.depth_range = (float(np.nanmin(all_depths)), float(np.nanmax(all_depths)))

    # 3. Scan 0xFF08 records for nav, attitude, tide
    try:
        ff08_records = _scan_ff08_records(filepath, search_start=0,
                                          search_size=min(result.file_size, 20 * 1024 * 1024))
    except (IOError, OSError) as e:
        warnings.warn(f"Failed to scan 0xFF08 records in {filepath}: {e}")
        ff08_records = {}

    # 3a. Navigation (size 81B)
    if load_navigation:
        nav_entries = ff08_records.get('nav', [])
        if nav_entries:
            try:
                nav_offsets = [(off, sz) for off, sz, _ in nav_entries]
                nav_recs = _parse_ff08_nav(filepath, nav_offsets)
                result.navigation = [r for r in nav_recs
                                     if lat_range[0] < r.latitude < lat_range[1]
                                     and lon_range[0] < r.longitude < lon_range[1]]
                if not result.navigation and nav_recs:
                    result.navigation = nav_recs
            except (IOError, OSError) as e:
                warnings.warn(f"Failed to parse navigation: {e}")
        if not result.navigation:
            try:
                result.navigation = _find_navigation(filepath, lat_range=lat_range, lon_range=lon_range)
            except (IOError, OSError) as e:
                warnings.warn(f"Failed to read navigation from {filepath}: {e}")

        if result.navigation:
            lats = [r.latitude for r in result.navigation if r.latitude != 0]
            lons = [r.longitude for r in result.navigation if r.longitude != 0]
            if lats:
                result.lat_range = (min(lats), max(lats))
            if lons:
                result.lon_range = (min(lons), max(lons))

    # 3b. Attitude (size 119B)
    att_entries = ff08_records.get('attitude', [])
    if att_entries:
        try:
            att_offsets = [(off, sz) for off, sz, _ in att_entries]
            result.attitude = _parse_ff08_attitude(filepath, att_offsets)
        except (IOError, OSError) as e:
            warnings.warn(f"Failed to parse attitude: {e}")

    # 3c. Tide - try size_59 records (multiple types share this size)
    if load_tide:
        # Tide is one of the 59B record types; try all of them
        for cat, entries in ff08_records.items():
            if cat.startswith('size_59') or cat == 'mru_59':
                try:
                    tide_offsets = [(off, sz) for off, sz, _ in entries[:50]]
                    tide_recs = _parse_ff08_tide(filepath, tide_offsets)
                    if tide_recs:
                        result.tide = tide_recs
                        break
                except (IOError, OSError):
                    continue
        if not result.tide:
            try:
                result.tide = _find_tide_records(filepath, sealevel_value=tide_sealevel_hint)
            except (IOError, OSError) as e:
                warnings.warn(f"Failed to read tide from {filepath}: {e}")

    # 3d. Computed ping parameters (Type 13/15, 155B)
    computed_entries = ff08_records.get('computed', [])
    if computed_entries:
        try:
            result.computed = _parse_ff08_computed(filepath, computed_entries)
        except (IOError, OSError) as e:
            warnings.warn(f"Failed to parse computed records: {e}")

    # 3e. Sensor status (Type 10, variable size ~111B)
    for cat, entries in ff08_records.items():
        if cat.startswith('size_111') or cat.startswith('size_119'):
            # Skip attitude which is already parsed
            if cat == 'attitude':
                continue
            try:
                result.sensor_status = _parse_ff08_sensor_status(filepath, entries)
                if result.sensor_status:
                    break
            except (IOError, OSError):
                continue

    return result


def _parse_ff08_computed(filepath: str,
                         entries: list[tuple[int, int, int]]) -> list[PdsComputedRecord]:
    """Parse Type 13/15 computed ping parameters.

    Payload layout (148 bytes):
      +2:  f64 timestamp (ms epoch) — at standard offset
      +40: f64 easting (projected meters)
      +48: f64 northing (projected meters)
      +56: f64 speed (m/s)
      +64: f64 heading (degrees)
      +72: f64 along-track distance (meters, cumulative)
      +80: f64 draft/sealevel (meters)
      +88: f64 heave (meters)
    """
    records = []
    with open(filepath, 'rb') as f:
        for file_off, data_size, rec_type in entries:
            f.seek(file_off + 7)
            payload = f.read(data_size - 2)
            if len(payload) < 96:
                continue

            ts = struct.unpack_from('<d', payload, 2)[0]
            if not _is_valid_timestamp(ts):
                continue

            rec = PdsComputedRecord(
                timestamp=ts,
                datetime_utc=_ms_to_datetime(ts),
                easting=struct.unpack_from('<d', payload, 40)[0],
                northing=struct.unpack_from('<d', payload, 48)[0],
                speed=struct.unpack_from('<d', payload, 56)[0],
                heading=struct.unpack_from('<d', payload, 64)[0],
                along_dist=struct.unpack_from('<d', payload, 72)[0],
                draft=struct.unpack_from('<d', payload, 80)[0],
                heave=struct.unpack_from('<d', payload, 88)[0],
            )
            # Sanity check: easting/northing should be reasonable projected coords
            if abs(rec.easting) < 1e8 and abs(rec.northing) < 1e8:
                records.append(rec)

    return records


def _parse_ff08_sensor_status(filepath: str,
                               entries: list[tuple[int, int, int]]) -> list[PdsSensorStatus]:
    """Parse Type 10 sensor status records.

    Payload layout (~104 bytes):
      +2:  f64 timestamp (ms epoch)
      +32: f64 raw roll (degrees, with mounting offset)
      +40: f64 raw pitch
      +48: f64 raw heading (degrees)
      +64: f64 raw course (degrees)
    """
    records = []
    with open(filepath, 'rb') as f:
        for file_off, data_size, rec_type in entries:
            f.seek(file_off + 7)
            payload = f.read(data_size - 2)
            if len(payload) < 72:
                continue

            ts = struct.unpack_from('<d', payload, 2)[0]
            if not _is_valid_timestamp(ts):
                continue

            rec = PdsSensorStatus(
                timestamp=ts,
                datetime_utc=_ms_to_datetime(ts),
            )

            # Roll at +32, Pitch at +40
            if len(payload) >= 48:
                v32 = struct.unpack_from('<d', payload, 32)[0]
                v40 = struct.unpack_from('<d', payload, 40)[0]
                if abs(v32) < 360 and abs(v40) < 360:
                    rec.raw_roll = v32
                    rec.raw_pitch = v40

            # Heading at +48
            if len(payload) >= 56:
                v48 = struct.unpack_from('<d', payload, 48)[0]
                if 0 <= v48 < 360:
                    rec.raw_heading = v48

            # Course at +64
            if len(payload) >= 72:
                v64 = struct.unpack_from('<d', payload, 64)[0]
                if 0 <= v64 < 360:
                    rec.raw_course = v64

            records.append(rec)

    return records


# ── Convenience Functions ──────────────────────────────────────

def pds_binary_info(filepath: str | Path) -> dict:
    """Quick summary of PDS binary file without loading all arrays."""
    result = read_pds_binary(filepath, load_arrays=False, max_pings=100)
    return {
        "filepath": result.filepath,
        "file_size_mb": result.file_size / 1024 / 1024,
        "num_pings_sampled": result.num_pings,
        "first_ping": str(result.first_ping_time) if result.first_ping_time else None,
        "last_ping": str(result.last_ping_time) if result.last_ping_time else None,
        "duration_seconds": result.duration_seconds,
        "ping_rate_hz": result.ping_rate_hz,
        "num_nav_records": result.num_nav_records,
        "lat_range": result.lat_range,
        "lon_range": result.lon_range,
    }


def pds_binary_to_xyz(pds_data: PdsBinaryData) -> np.ndarray:
    """Convert PDS binary pings to XYZ point cloud (Nx3 array).

    Depth is returned as positive-down.
    """
    if not pds_data.pings or not pds_data.navigation:
        return np.zeros((0, 3))

    nav_times = np.array([r.timestamp for r in pds_data.navigation])
    nav_lats = np.array([r.latitude for r in pds_data.navigation])
    nav_lons = np.array([r.longitude for r in pds_data.navigation])

    chunks: list[np.ndarray] = []
    for ping in pds_data.pings:
        if ping.timestamp <= 0 or len(ping.depth) == 0:
            continue

        lat = float(np.interp(ping.timestamp, nav_times, nav_lats))
        lon = float(np.interp(ping.timestamp, nav_times, nav_lons))

        heading_deg = float(np.median(ping.azimuth)) if len(ping.azimuth) > 0 else 0.0
        heading_rad = np.radians(heading_deg)

        valid = (ping.depth != 0)
        if len(ping.beam_flags) > 0:
            valid &= (ping.beam_flags == 0)
        if not np.any(valid):
            continue

        across = ping.across_track[valid] if len(ping.across_track) > 0 else np.zeros(int(np.sum(valid)))
        along = ping.along_track[valid] if len(ping.along_track) > 0 else np.zeros(int(np.sum(valid)))
        depth = np.abs(ping.depth[valid])

        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * np.cos(np.radians(lat))

        dx = across * np.sin(heading_rad) + along * np.cos(heading_rad)
        dy = across * np.cos(heading_rad) - along * np.sin(heading_rad)

        beam_lons = lon + dx / m_per_deg_lon
        beam_lats = lat + dy / m_per_deg_lat

        chunks.append(np.column_stack([beam_lons, beam_lats, depth]))

    return np.vstack(chunks) if chunks else np.zeros((0, 3))

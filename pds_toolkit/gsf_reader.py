"""GSF (Generic Sensor Format) v3.09 reader.

Parses GSF files exported from Teledyne PDS / CARIS HIPS containing
multibeam bathymetry, attitude time-series, and SVP profiles.
All multi-byte integers are Big Endian.

Record types:
  1  HEADER                  2  SWATH_BATHYMETRY_PING
  3  SOUND_VELOCITY_PROFILE  4  PROCESSING_PARAMETERS
  6  COMMENT                 7  HISTORY
  9  SWATH_BATHY_SUMMARY    12  ATTITUDE
"""

from __future__ import annotations

import datetime
import struct
import warnings
from pathlib import Path

import numpy as np

from .models import (
    GsfAttitudeRecord,
    GsfFile,
    GsfPing,
    GsfScaleFactors,
    GsfSummary,
    GsfSvpRecord,
)

# ── Record type IDs ─────────────────────────────────────────────

_HEADER = 1
_SWATH_BATHY_PING = 2
_SVP = 3
_PROCESSING_PARAMS = 4
_COMMENT = 6
_HISTORY = 7
_SWATH_BATHY_SUMMARY = 9
_ATTITUDE = 12

# ── Beam array subrecord IDs ───────────────────────────────────

_SR_DEPTH = 1
_SR_ACROSS_TRACK = 2
_SR_ALONG_TRACK = 3
_SR_TRAVEL_TIME = 4
_SR_BEAM_ANGLE = 5
_SR_MEAN_CAL_AMP = 6
_SR_MEAN_REL_AMP = 7
_SR_ECHO_WIDTH = 8
_SR_QUALITY = 9
_SR_BEAM_FLAGS = 16
_SR_VERT_ERROR = 19
_SR_HORIZ_ERROR = 20
_SR_QUALITY_FLAGS = 22
_SR_SCALE_FACTORS = 100


def _ts(sec: int) -> datetime.datetime:
    try:
        return datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
    except (OSError, ValueError, OverflowError):
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


# ── Public API ──────────────────────────────────────────────────


def read_gsf(
    filepath: str | Path,
    *,
    max_pings: int | None = None,
    load_arrays: bool = True,
    load_attitude: bool = True,
    load_svp: bool = True,
) -> GsfFile:
    """Read a GSF file and return parsed data.

    Args:
        filepath: Path to .gsf file.
        max_pings: Stop after this many pings (None = all).
        load_arrays: Load beam arrays (depth, across_track, etc.).
        load_attitude: Parse attitude time-series records.
        load_svp: Parse sound velocity profile records.
    """
    filepath = Path(filepath)
    result = GsfFile(filepath=str(filepath))
    file_size = filepath.stat().st_size

    with open(filepath, "rb") as f:
        ping_count = 0

        while f.tell() < file_size:
            pos = f.tell()
            hdr = f.read(8)
            if len(hdr) < 8:
                break

            raw_size = struct.unpack(">I", hdr[0:4])[0]
            data_size = raw_size & 0x7FFFFFFF
            record_type = struct.unpack(">I", hdr[4:8])[0] & 0xFF

            if data_size == 0 or pos + 8 + data_size > file_size:
                break

            # ── HEADER ──────────────────────────────────────
            if record_type == _HEADER:
                data = f.read(data_size)
                result.version = data.decode("ascii", errors="replace").strip("\x00")

            # ── SWATH BATHY PING ────────────────────────────
            elif record_type == _SWATH_BATHY_PING:
                data = f.read(data_size)
                if max_pings is not None and ping_count >= max_pings:
                    ping_count += 1
                    continue
                ping = _parse_ping(data, result.scale_factors, load_arrays)
                if ping:
                    result.pings.append(ping)
                ping_count += 1

            # ── SVP ─────────────────────────────────────────
            elif record_type == _SVP:
                data = f.read(data_size)
                if load_svp:
                    svp = _parse_svp(data)
                    if svp:
                        result.svp_profiles.append(svp)

            # ── PROCESSING PARAMS ───────────────────────────
            elif record_type == _PROCESSING_PARAMS:
                data = f.read(data_size)
                result.processing_params = _parse_processing_params(data)

            # ── COMMENT ─────────────────────────────────────
            elif record_type == _COMMENT:
                data = f.read(data_size)
                if len(data) >= 12:
                    clen = struct.unpack(">i", data[8:12])[0]
                    result.comments.append(
                        data[12:12 + clen].decode("ascii", errors="replace")
                    )

            # ── SUMMARY ─────────────────────────────────────
            elif record_type == _SWATH_BATHY_SUMMARY:
                data = f.read(data_size)
                # Store raw summary data; depth conversion deferred until after
                # scale factors are known (summary often precedes first ping)
                result._raw_summary_data = data

            # ── ATTITUDE ────────────────────────────────────
            elif record_type == _ATTITUDE:
                data = f.read(data_size)
                if load_attitude:
                    att = _parse_attitude(data)
                    if att:
                        result.attitude_records.append(att)

            # ── SKIP ────────────────────────────────────────
            else:
                f.seek(pos + 8 + data_size)

    # Deferred summary depth conversion (scale factors now known)
    if hasattr(result, "_raw_summary_data") and result._raw_summary_data:
        result.summary = _parse_summary(result._raw_summary_data, result.scale_factors)
        del result._raw_summary_data

    return result


# ── Ping Parsing ────────────────────────────────────────────────


def _parse_ping(
    data: bytes,
    shared_sf: dict[int, GsfScaleFactors],
    load_arrays: bool,
) -> GsfPing | None:
    if len(data) < 36:
        return None

    time_sec = struct.unpack(">i", data[0:4])[0]
    time_nsec = struct.unpack(">i", data[4:8])[0]
    lon_raw = struct.unpack(">i", data[8:12])[0]
    lat_raw = struct.unpack(">i", data[12:16])[0]
    num_beams = struct.unpack(">H", data[16:18])[0]
    center_beam = struct.unpack(">H", data[18:20])[0]
    ping_flags = struct.unpack(">H", data[20:22])[0]
    # bytes 22-23: reserved
    tide_corr = struct.unpack(">h", data[24:26])[0]
    depth_corr = struct.unpack(">h", data[26:28])[0]
    heading = struct.unpack(">H", data[28:30])[0]
    pitch = struct.unpack(">h", data[30:32])[0]
    roll = struct.unpack(">h", data[32:34])[0]
    heave = struct.unpack(">h", data[34:36])[0]

    course = 0.0
    speed = 0.0
    if ping_flags & 0x0001 and len(data) >= 40:
        course = struct.unpack(">H", data[36:38])[0] / 100.0
        speed = struct.unpack(">H", data[38:40])[0] / 100.0

    ping = GsfPing(
        time=_ts(time_sec),
        time_nsec=time_nsec,
        longitude=lon_raw / 1e7,
        latitude=lat_raw / 1e7,
        num_beams=num_beams,
        center_beam=center_beam,
        heading=heading / 100.0,
        pitch=pitch / 100.0,
        roll=roll / 100.0,
        heave=heave / 100.0,
        tide_corrector=tide_corr / 100.0,   # cm → m
        depth_corrector=depth_corr / 1000.0,  # mm → m (GSF spec)
        course=course,
        speed=speed,
        ping_flags=ping_flags,
    )

    if not load_arrays:
        return ping

    # Find subrecord start by scanning for SCALE_FACTORS or DEPTH
    sr_start = _find_subrecord_start(data)

    # Pass 1: extract scale factors
    local_sf: dict[int, GsfScaleFactors] = {}
    offset = sr_start
    while offset + 4 <= len(data):
        sr_id = data[offset]
        sr_size = int.from_bytes(data[offset + 1:offset + 4], "big")
        if sr_size == 0 or offset + 4 + sr_size > len(data):
            break
        if sr_id == _SR_SCALE_FACTORS:
            local_sf = _parse_scale_factors(data[offset + 4:offset + 4 + sr_size])
            shared_sf.update(local_sf)
        offset += 4 + sr_size

    sf = {**shared_sf, **local_sf}

    # Pass 2: extract beam arrays
    offset = sr_start
    while offset + 4 <= len(data):
        sr_id = data[offset]
        sr_size = int.from_bytes(data[offset + 1:offset + 4], "big")
        if sr_size == 0 or offset + 4 + sr_size > len(data):
            break
        sr_data = data[offset + 4:offset + 4 + sr_size]

        if sr_id in (_SR_DEPTH, _SR_ACROSS_TRACK, _SR_ALONG_TRACK,
                     _SR_TRAVEL_TIME, _SR_BEAM_ANGLE,
                     _SR_VERT_ERROR, _SR_HORIZ_ERROR):
            signed = sr_id != _SR_DEPTH
            arr = _decode_beam_array(sr_data, num_beams, sr_size, signed=signed)
            if sr_id in sf:
                arr = sf[sr_id].apply(arr)
                if sr_id == _SR_DEPTH:
                    arr = np.abs(arr)
            elif sr_id == _SR_DEPTH:
                # No scale factor: assume raw values are in cm, convert to m
                arr = np.abs(arr.astype(np.float64) / 100.0)
            _assign_beam_array(ping, sr_id, arr)

        elif sr_id == _SR_MEAN_REL_AMP:
            arr = _decode_beam_array(sr_data, num_beams, sr_size, signed=False)
            if sr_id in sf and sf[sr_id].multiplier != 1:
                arr = sf[sr_id].apply(arr)
            ping.mean_rel_amp = arr

        elif sr_id == _SR_BEAM_FLAGS:
            if sr_size >= num_beams:
                ping.beam_flags = np.frombuffer(sr_data[:num_beams], dtype=np.uint8).copy()

        elif sr_id == _SR_QUALITY_FLAGS:
            if sr_size >= num_beams:
                ping.quality_flags = np.frombuffer(sr_data[:num_beams], dtype=np.uint8).copy()

        offset += 4 + sr_size

    return ping


def _find_subrecord_start(data: bytes) -> int:
    """Find byte offset where subrecords begin within a ping record.

    Scans from byte 36 forward looking for SCALE_FACTORS (id=100) or
    DEPTH (id=1) subrecord headers with valid sizes.
    """
    for offset in range(36, min(128, len(data) - 4)):
        sr_id = data[offset]
        sr_size = int.from_bytes(data[offset + 1:offset + 4], "big")
        if sr_id == _SR_SCALE_FACTORS and 12 < sr_size < 2000:
            return offset
        if sr_id == _SR_DEPTH and 100 < sr_size < 100000:
            return offset
    return 56  # safe default for CARIS-exported GSF


def _assign_beam_array(ping: GsfPing, sr_id: int, arr: np.ndarray) -> None:
    mapping = {
        _SR_DEPTH: "depth",
        _SR_ACROSS_TRACK: "across_track",
        _SR_ALONG_TRACK: "along_track",
        _SR_TRAVEL_TIME: "travel_time",
        _SR_BEAM_ANGLE: "beam_angle",
        _SR_VERT_ERROR: "vert_error",
        _SR_HORIZ_ERROR: "horiz_error",
    }
    attr = mapping.get(sr_id)
    if attr:
        setattr(ping, attr, arr)


# ── Scale Factors ───────────────────────────────────────────────


def _parse_scale_factors(sr_data: bytes) -> dict[int, GsfScaleFactors]:
    """Parse SCALE_FACTORS subrecord.

    Format: [u32 num_entries] then N × 12 bytes:
      [id(1) compression_flags(3)] [multiplier(u32)] [dc_offset(i32)]

    Note: multiplier is stored as uint32 in GSF v3.09. The reviewer
    suggested it might be float, but empirical analysis of CARIS-exported
    GSF files confirms integer encoding (values like 10000, 200, 500).
    """
    result = {}
    if len(sr_data) < 4:
        return result

    num_entries = struct.unpack(">I", sr_data[0:4])[0]

    for i in range(num_entries):
        base = 4 + i * 12
        if base + 12 > len(sr_data):
            break
        array_id = sr_data[base]
        mult_raw = struct.unpack(">I", sr_data[base + 4:base + 8])[0]
        dc_raw = struct.unpack(">i", sr_data[base + 8:base + 12])[0]

        # Multiplier sanity check: if it looks like an IEEE float (exponent bits
        # are non-zero but mantissa suggests float), interpret as float.
        # Otherwise keep as integer. Real GSF integer multipliers are < 1,000,000.
        if mult_raw > 1_000_000:
            mult = struct.unpack(">f", sr_data[base + 4:base + 8])[0]
        else:
            mult = float(mult_raw)

        if mult > 0:
            result[array_id] = GsfScaleFactors(multiplier=mult, dc_offset=float(dc_raw))

    return result


# ── Beam Array Decoding ─────────────────────────────────────────


def _decode_beam_array(
    sr_data: bytes, num_beams: int, sr_size: int, signed: bool = True,
) -> np.ndarray:
    bytes_per_beam = sr_size // max(num_beams, 1)
    if bytes_per_beam == 2:
        dtype = ">i2" if signed else ">u2"
    elif bytes_per_beam == 4:
        dtype = ">i4" if signed else ">u4"
    elif bytes_per_beam == 1:
        dtype = ">i1" if signed else ">u1"
    else:
        # Fallback: try 2-byte elements
        dtype = ">i2" if signed else ">u2"
        bytes_per_beam = 2
        num_beams = sr_size // 2

    actual_bytes = num_beams * bytes_per_beam
    if actual_bytes > len(sr_data):
        actual_bytes = (len(sr_data) // bytes_per_beam) * bytes_per_beam
    if actual_bytes == 0:
        return np.array([], dtype=np.float64)
    return np.frombuffer(sr_data[:actual_bytes], dtype=dtype).copy()


# ── Attitude ────────────────────────────────────────────────────


def _parse_attitude(data: bytes) -> GsfAttitudeRecord | None:
    """Parse ATTITUDE record (type 12).

    Format:
      [4] base_time_sec  [4] base_time_nsec
      [2] num_measurements
      Then per measurement (N times):
        [2] time_offset_ms (signed, relative to base_time)
        [2] pitch (signed, hundredths of degree)
        [2] roll  (signed, hundredths of degree)
        [2] heave (signed, centimetres)
        [2] heading (unsigned, hundredths of degree)
    """
    if len(data) < 10:
        return None

    base_sec = struct.unpack(">i", data[0:4])[0]
    base_nsec = struct.unpack(">i", data[4:8])[0]
    num_meas = struct.unpack(">H", data[8:10])[0]

    if num_meas == 0:
        return None

    record_size = 10  # bytes per measurement
    expected = 10 + num_meas * record_size
    actual_meas = min(num_meas, (len(data) - 10) // record_size)

    if actual_meas == 0:
        return None

    # Parse all measurements at once using numpy for speed
    raw = np.frombuffer(data[10:10 + actual_meas * record_size],
                        dtype=np.dtype([
                            ("time_offset_ms", ">i2"),
                            ("pitch", ">i2"),
                            ("roll", ">i2"),
                            ("heave", ">i2"),
                            ("heading", ">u2"),
                        ]))

    base_time = float(base_sec) + float(base_nsec) / 1e9
    times = base_time + raw["time_offset_ms"].astype(np.float64) / 1000.0

    return GsfAttitudeRecord(
        num_measurements=actual_meas,
        times=times,
        pitch=raw["pitch"].astype(np.float64) / 100.0,
        roll=raw["roll"].astype(np.float64) / 100.0,
        heave=raw["heave"].astype(np.float64) / 100.0,
        heading=raw["heading"].astype(np.float64) / 100.0,
    )


# ── SVP ─────────────────────────────────────────────────────────


def _parse_svp(data: bytes) -> GsfSvpRecord | None:
    """Parse SOUND_VELOCITY_PROFILE record (type 3).

    Format:
      [4] obs_time_sec  [4] obs_time_nsec
      [4] app_time_sec  [4] app_time_nsec
      [4] longitude (1e-7 degrees)  [4] latitude (1e-7 degrees)
      [4] num_points
      Then per point:
        [4] depth_cm (signed)  [4] sound_velocity (cm/s, unsigned)
    """
    if len(data) < 28:
        return None

    obs_sec = struct.unpack(">i", data[0:4])[0]
    lon = struct.unpack(">i", data[16:20])[0] / 1e7
    lat = struct.unpack(">i", data[20:24])[0] / 1e7
    num_points = struct.unpack(">I", data[24:28])[0]

    actual_points = min(num_points, (len(data) - 28) // 8)

    if actual_points == 0:
        return GsfSvpRecord(time=_ts(obs_sec), latitude=lat, longitude=lon)

    raw = np.frombuffer(data[28:28 + actual_points * 8],
                        dtype=np.dtype([("depth_cm", ">i4"), ("velocity_cms", ">u4")]))

    return GsfSvpRecord(
        time=_ts(obs_sec),
        latitude=lat,
        longitude=lon,
        num_points=actual_points,
        depth=raw["depth_cm"].astype(np.float64) / 100.0,
        sound_velocity=raw["velocity_cms"].astype(np.float64) / 100.0,
    )


# ── Summary ─────────────────────────────────────────────────────


def _parse_summary(data: bytes, scale_factors: dict) -> GsfSummary:
    s = GsfSummary()
    if len(data) < 40:
        return s

    s.start_time = _ts(struct.unpack(">i", data[0:4])[0])
    s.end_time = _ts(struct.unpack(">i", data[8:12])[0])
    s.min_latitude = struct.unpack(">i", data[16:20])[0] / 1e7
    s.min_longitude = struct.unpack(">i", data[20:24])[0] / 1e7
    s.max_latitude = struct.unpack(">i", data[24:28])[0] / 1e7
    s.max_longitude = struct.unpack(">i", data[28:32])[0] / 1e7

    # Summary depths: apply scale factors if available, else assume cm
    raw_min = struct.unpack(">i", data[32:36])[0]
    raw_max = struct.unpack(">i", data[36:40])[0]
    if _SR_DEPTH in scale_factors:
        sf = scale_factors[_SR_DEPTH]
        s.min_depth = abs(raw_min / sf.multiplier + sf.dc_offset)
        s.max_depth = abs(raw_max / sf.multiplier + sf.dc_offset)
    else:
        s.min_depth = abs(raw_min / 100.0)
        s.max_depth = abs(raw_max / 100.0)

    return s


# ── Processing Parameters ──────────────────────────────────────


def _parse_processing_params(data: bytes) -> dict[str, str]:
    params: dict[str, str] = {}
    if len(data) < 10:
        return params

    num = struct.unpack(">H", data[8:10])[0]
    offset = 10

    for _ in range(num):
        if offset + 2 > len(data):
            break
        nlen = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        name = data[offset:offset + nlen].decode("ascii", errors="replace").strip("\x00")
        offset += nlen
        if offset + 2 > len(data):
            break
        vlen = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        value = data[offset:offset + vlen].decode("ascii", errors="replace").strip("\x00")
        offset += vlen
        if name:
            params[name] = value

    return params

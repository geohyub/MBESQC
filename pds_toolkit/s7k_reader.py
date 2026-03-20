"""S7K (Reson 7k) raw sonar data reader.

Parses Reson/Teledyne S7K files. All multi-byte values are Little Endian.

DRF (Data Record Frame) v5: 60 bytes, sync=0x0000FFFF.
Total record = DRF(60) + data(size-60) + checksum(4).

Supported record types:
  1003  Position             1008  Depth
  1015  Navigation           1016  Attitude (IMU time-series)
  7000  Sonar Settings       7004  Beam Geometry
  7006  Bathymetric Data     7007  Side Scan Data
  7027  Raw Detection        7028  Snippet
  7030  Sonar Install Params 7200  File Header
"""

from __future__ import annotations

import datetime
import math
import struct
from pathlib import Path

import numpy as np

from .models import (
    S7kAttitude,
    S7kBathymetricData,
    S7kFile,
    S7kFileHeader,
    S7kPosition,
    S7kRawDetection,
    S7kRecord,
    S7kSonarSettings,
)

_SYNC = 0x0000FFFF
_DRF_SIZE = 60


def read_s7k(
    filepath: str | Path,
    *,
    max_records: int | None = None,
    record_types: set[int] | None = None,
) -> S7kFile:
    """Read an S7K file.

    Args:
        filepath: Path to .s7k file.
        max_records: Stop after N records total.
        record_types: If set, only fully parse these types (others counted only).
            Default = all key types.
    """
    filepath = Path(filepath)
    result = S7kFile(filepath=str(filepath))
    file_size = filepath.stat().st_size

    if record_types is None:
        record_types = {1003, 1016, 7000, 7006, 7027, 7200}

    with open(filepath, "rb") as f:
        count = 0
        pos = 0

        while pos < file_size:
            f.seek(pos)
            drf = f.read(_DRF_SIZE)
            if len(drf) < _DRF_SIZE:
                break

            sync = struct.unpack("<I", drf[4:8])[0]
            if sync != _SYNC:
                pos += 1
                continue

            rec_size = struct.unpack("<I", drf[8:12])[0]
            rec_type = struct.unpack("<H", drf[32:34])[0]
            device_id = struct.unpack("<I", drf[34:38])[0]
            dt = _parse_s7k_time(drf)

            result.record_type_counts[rec_type] = result.record_type_counts.get(rec_type, 0) + 1

            data_size = rec_size - _DRF_SIZE
            next_pos = pos + rec_size + 4  # skip checksum

            if rec_type in record_types and data_size > 0:
                data = f.read(data_size)

                if rec_type == 7200:
                    result.file_header = _parse_file_header(data, filepath)
                elif rec_type == 1003:
                    p = _parse_position(data, dt)
                    if p:
                        result.positions.append(p)
                elif rec_type == 1016:
                    a = _parse_attitude(data, dt)
                    if a:
                        result.attitudes.append(a)
                elif rec_type == 7000:
                    ss = _parse_sonar_settings(data, dt)
                    if ss:
                        result.sonar_settings.append(ss)
                elif rec_type == 7006:
                    b = _parse_bathy(data, dt)
                    if b:
                        result.bathymetry.append(b)
                elif rec_type == 7027:
                    rd = _parse_raw_detection(data, dt)
                    if rd:
                        result.raw_detections.append(rd)
                else:
                    result.records.append(S7kRecord(
                        record_type=rec_type, device_id=device_id,
                        time=dt, size=data_size, data=data[:min(data_size, 1024)],
                    ))

            pos = next_pos
            count += 1
            if max_records and count >= max_records:
                break

    return result


def _parse_s7k_time(drf: bytes) -> datetime.datetime:
    year = struct.unpack("<H", drf[20:22])[0]
    day = struct.unpack("<H", drf[22:24])[0]
    seconds = struct.unpack("<f", drf[24:28])[0]
    hours = drf[28]
    minutes = drf[29]
    try:
        base = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
        return base + datetime.timedelta(
            days=day - 1, hours=hours, minutes=minutes, seconds=seconds
        )
    except (ValueError, OverflowError):
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _parse_file_header(data: bytes, filepath: Path) -> S7kFileHeader:
    hdr = S7kFileHeader(filepath=str(filepath))
    if len(data) >= 56:
        hdr.file_identifier = data[0:16]
        hdr.version = struct.unpack("<H", data[16:18])[0]
        hdr.session_identifier = data[32:48]
        hdr.record_data_size = struct.unpack("<I", data[48:52])[0]
        hdr.num_devices = struct.unpack("<I", data[52:56])[0]
    if len(data) > 120:
        hdr.recording_name = data[56:120].decode("ascii", errors="replace").rstrip("\x00")
    return hdr


def _parse_position(data: bytes, dt: datetime.datetime) -> S7kPosition | None:
    """Record 1003. Standard ICD: [u32 datum][f32 latency][f64 lat][f64 lon][f64 height] (32 bytes).
    PDS variant: 4-byte pad after latency, so lat starts at byte 12 (36 bytes).
    We try both: check if offset-8 lat is in valid radian range; if not, try offset 12.
    """
    if len(data) < 32:
        return None
    datum = struct.unpack("<I", data[0:4])[0]
    latency = struct.unpack("<f", data[4:8])[0]

    # Try standard layout (lat at byte 8)
    lat_rad = struct.unpack("<d", data[8:16])[0]
    lon_rad = struct.unpack("<d", data[16:24])[0]
    height = struct.unpack("<d", data[24:32])[0]

    # If lat is not in valid radian range [-pi/2, pi/2], try PDS variant (lat at byte 12)
    if not (-math.pi / 2 <= lat_rad <= math.pi / 2) and len(data) >= 36:
        lat_rad = struct.unpack("<d", data[12:20])[0]
        lon_rad = struct.unpack("<d", data[20:28])[0]
        height = struct.unpack("<d", data[28:36])[0]
    return S7kPosition(
        time=dt, datum=datum, latency=latency,
        latitude=math.degrees(lat_rad), longitude=math.degrees(lon_rad), height=height,
    )


def _parse_attitude(data: bytes, dt: datetime.datetime) -> S7kAttitude | None:
    """Record 1016. PDS-exported variant layout: [4 reserved][u8 n][4 reserved]
    then n × 18 bytes: [u16 dt_ms][f32 pitch_rad][f32 roll_rad][f32 heave_m][f32 heading_rad].

    NOTE: Standard Reson ICD has [u32 n] at byte 0, but PDS-exported S7K files
    have 4 zero bytes followed by n as a u8 at byte 4. We try both layouts:
    first the 4-byte header (standard), then the 9-byte header (PDS variant).
    """
    if len(data) < 4:
        return None

    entry_size = 18

    # Try standard ICD layout first: [u32 n_entries] at byte 0, data at byte 4
    n_std = struct.unpack("<I", data[0:4])[0]
    hdr_size_std = 4

    # Try PDS variant: [4 reserved][u8 n_entries] at byte 4, data at byte 9
    n_pds = data[4] if len(data) > 4 else 0
    hdr_size_pds = 9

    # Pick the layout that fits: data_size == header + n * 18
    if n_std > 0 and len(data) >= hdr_size_std + n_std * entry_size:
        n = n_std
        hdr_size = hdr_size_std
    elif n_pds > 0 and len(data) >= hdr_size_pds + n_pds * entry_size:
        n = n_pds
        hdr_size = hdr_size_pds
    else:
        return None

    if n == 0 or len(data) < hdr_size + n * entry_size:
        return None

    dt_arr = np.zeros(n, dtype=np.float64)
    pitch_arr = np.zeros(n, dtype=np.float64)
    roll_arr = np.zeros(n, dtype=np.float64)
    heave_arr = np.zeros(n, dtype=np.float64)
    heading_arr = np.zeros(n, dtype=np.float64)

    for i in range(n):
        off = hdr_size + i * entry_size
        dt_arr[i] = struct.unpack("<H", data[off:off + 2])[0]
        pitch_arr[i] = math.degrees(struct.unpack("<f", data[off + 2:off + 6])[0])
        roll_arr[i] = math.degrees(struct.unpack("<f", data[off + 6:off + 10])[0])
        heave_arr[i] = struct.unpack("<f", data[off + 10:off + 14])[0]
        heading_arr[i] = math.degrees(struct.unpack("<f", data[off + 14:off + 18])[0]) % 360

    return S7kAttitude(
        time=dt, num_entries=n,
        delta_time_ms=dt_arr, pitch=pitch_arr, roll=roll_arr,
        heave=heave_arr, heading=heading_arr,
    )


def _parse_sonar_settings(data: bytes, dt: datetime.datetime) -> S7kSonarSettings | None:
    """Record 7000. Key fields from the sonar configuration."""
    if len(data) < 60:
        return None

    serial = struct.unpack("<Q", data[0:8])[0]
    ping_num = struct.unpack("<I", data[8:12])[0]
    freq = struct.unpack("<f", data[12:16])[0]
    sample_rate = struct.unpack("<f", data[16:20])[0]
    rx_bw = struct.unpack("<f", data[20:24])[0]
    tx_pw = struct.unpack("<f", data[24:28])[0]
    tx_bw_along = struct.unpack("<f", data[28:32])[0]
    tx_bw_across = struct.unpack("<f", data[32:36])[0]
    tx_power = struct.unpack("<f", data[44:48])[0]
    gain = struct.unpack("<f", data[52:56])[0]
    spread = struct.unpack("<f", data[56:60])[0]
    absorb = struct.unpack("<f", data[60:64])[0] if len(data) >= 64 else 0.0

    return S7kSonarSettings(
        time=dt, serial_number=serial, ping_number=ping_num,
        frequency=freq, sample_rate=sample_rate, receiver_bandwidth=rx_bw,
        tx_pulse_width=tx_pw,
        tx_beamwidth_along=math.degrees(tx_bw_along),
        tx_beamwidth_across=math.degrees(tx_bw_across),
        tx_power=tx_power, gain=gain, spreading=spread, absorption=absorb,
    )


def _parse_bathy(data: bytes, dt: datetime.datetime) -> S7kBathymetricData | None:
    """Record 7006."""
    if len(data) < 24:
        return None
    serial = struct.unpack("<Q", data[0:8])[0]
    ping_num = struct.unpack("<I", data[8:12])[0]
    num_beams = struct.unpack("<I", data[14:18])[0]

    beam_start = 24
    beam_size = 20
    n = min(num_beams, (len(data) - beam_start) // beam_size)

    if n == 0:
        return S7kBathymetricData(time=dt, serial_number=serial, ping_number=ping_num)

    raw = np.frombuffer(data[beam_start:beam_start + n * beam_size],
                        dtype=np.dtype([("d", "<f4"), ("a", "<f4"), ("l", "<f4"),
                                        ("pa", "<f4"), ("az", "<f4")]))
    return S7kBathymetricData(
        time=dt, serial_number=serial, ping_number=ping_num, num_beams=n,
        depth=raw["d"].copy(), across_track=raw["a"].copy(), along_track=raw["l"].copy(),
        pointing_angle=np.degrees(raw["pa"]), azimuth_angle=np.degrees(raw["az"]),
    )


def _parse_raw_detection(data: bytes, dt: datetime.datetime) -> S7kRawDetection | None:
    """Record 7027. RTH: [8 serial][4 ping][2 multi_ping][4 n_det][4 field_size]
    [1 algo][4 flags][4 sample_rate][4 tx_angle][...reserved...]
    Then n_det × field_size bytes of detection data.
    """
    if len(data) < 40:
        return None

    serial = struct.unpack("<Q", data[0:8])[0]
    ping_num = struct.unpack("<I", data[8:12])[0]
    n_det = struct.unpack("<I", data[14:18])[0]
    field_size = struct.unpack("<I", data[18:22])[0]
    det_algo = data[22]
    flags_raw = struct.unpack("<I", data[23:27])[0]
    samp_rate = struct.unpack("<f", data[27:31])[0]
    tx_angle = struct.unpack("<f", data[31:35])[0]

    if n_det == 0 or field_size == 0:
        return S7kRawDetection(time=dt, serial_number=serial, ping_number=ping_num)

    # RTH ends after reserved fields, detect data starts at ~offset 68 or later
    # We search for the start by looking for reasonable field_size blocks
    rth_end = 68  # typical RTH size for 7027
    # Adjust if data is larger
    while rth_end < min(200, len(data)) and rth_end + n_det * field_size > len(data):
        rth_end += 4

    actual_n = min(n_det, (len(data) - rth_end) // max(field_size, 1))

    if actual_n == 0 or field_size < 6:
        return S7kRawDetection(
            time=dt, serial_number=serial, ping_number=ping_num,
            num_detections=n_det, data_field_size=field_size,
            detection_algorithm=det_algo, sampling_rate=samp_rate,
            tx_angle=math.degrees(tx_angle),
        )

    det_point = np.zeros(actual_n, dtype=np.float32)
    rx_angle = np.zeros(actual_n, dtype=np.float32)
    det_flags = np.zeros(actual_n, dtype=np.uint32)
    det_quality = np.zeros(actual_n, dtype=np.uint32)

    for i in range(actual_n):
        off = rth_end + i * field_size
        if off + 18 > len(data):
            break
        det_point[i] = struct.unpack("<f", data[off + 2:off + 6])[0]
        rx_angle[i] = struct.unpack("<f", data[off + 6:off + 10])[0]
        det_flags[i] = struct.unpack("<I", data[off + 10:off + 14])[0]
        det_quality[i] = struct.unpack("<I", data[off + 14:off + 18])[0]

    return S7kRawDetection(
        time=dt, serial_number=serial, ping_number=ping_num,
        num_detections=actual_n, data_field_size=field_size,
        detection_algorithm=det_algo, sampling_rate=samp_rate,
        tx_angle=math.degrees(tx_angle),
        detection_point=det_point, rx_angle=np.degrees(rx_angle),
        flags=det_flags, quality=det_quality,
    )

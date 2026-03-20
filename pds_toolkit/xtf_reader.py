"""XTF (eXtended Triton Format) reader.

Parses XTF files exported from Teledyne PDS containing multibeam and
sidescan sonar data. Little Endian throughout.

XTF File structure:
  - 1024-byte file header (magic byte 0x7B = 123)
  - Channel info headers (64 bytes each, up to 6 channels)
  - Packet records (variable size, type-tagged)
"""

from __future__ import annotations

import datetime
import struct
from pathlib import Path

from .models import XtfFile, XtfFileHeader, XtfPingHeader

_XTF_MAGIC = 0x7B  # 123

# XTF packet/header types
_XTF_HEADER_SONAR = 0       # Sidescan or subbottom
_XTF_HEADER_NOTES = 1       # Notes (text annotation)
_XTF_HEADER_BATHY = 2       # Bathymetry (multibeam)
_XTF_HEADER_ATTITUDE = 3    # Attitude data
_XTF_HEADER_FORWARD = 4     # Forward-look sonar
_XTF_HEADER_ELAC = 5        # Elac multibeam
_XTF_HEADER_RAW_SERIAL = 6  # Raw serial
_XTF_HEADER_EMBED_HEAD = 7  # Embedded file header
_XTF_HEADER_HIDDEN = 8      # Hidden (vendor specific)
_XTF_HEADER_SEGY = 10       # SEG-Y data
_XTF_HEADER_RAW_CUSTOM = 199


def read_xtf(
    filepath: str | Path,
    *,
    max_packets: int | None = None,
) -> XtfFile:
    """Read an XTF file.

    Args:
        filepath: Path to .xtf file.
        max_packets: Stop after this many packets.
    """
    filepath = Path(filepath)
    result = XtfFile(filepath=str(filepath))
    file_size = filepath.stat().st_size

    with open(filepath, "rb") as f:
        # ── File Header (1024 bytes) ────────────────────
        fhdr = f.read(1024)
        if len(fhdr) < 1024 or fhdr[0] != _XTF_MAGIC:
            return result

        result.file_header = _parse_file_header(fhdr, filepath)

        # Channel info is embedded within the 1024-byte file header (not appended)
        # Packet data starts immediately at byte 1024
        f.seek(1024)

        # ── Packet Records ──────────────────────────────
        count = 0
        while f.tell() < file_size:
            pos = f.tell()

            # XTF packet header: first 14 bytes are common
            # [0:2]   u16  MagicNumber (0xFACE)
            # [2]     u8   HeaderType
            # [3]     u8   SubChannelNumber
            # [4:6]   u16  NumChansToFollow
            # [6:8]   u16  Reserved[0]
            # [8:12]  u32  NumBytesThisRecord (total including this header)
            # [12:14] u16  Reserved[1]

            pkt_hdr = f.read(14)
            if len(pkt_hdr) < 14:
                break

            magic = struct.unpack("<H", pkt_hdr[0:2])[0]
            if magic != 0xFACE:
                # Try to resync
                f.seek(pos + 1)
                continue

            hdr_type = pkt_hdr[2]
            num_bytes = struct.unpack("<I", pkt_hdr[8:12])[0]

            result.record_type_counts[hdr_type] = result.record_type_counts.get(hdr_type, 0) + 1

            if num_bytes < 14 or pos + num_bytes > file_size:
                break

            if hdr_type == _XTF_HEADER_BATHY:
                # Read the rest of the bathy header (256 bytes total)
                remaining = f.read(min(num_bytes - 14, 242))
                ping = _parse_bathy_header(pkt_hdr + remaining)
                if ping:
                    result.ping_headers.append(ping)
                # Seek to end of record
                f.seek(pos + num_bytes)

            elif hdr_type == _XTF_HEADER_ATTITUDE:
                # Attitude record - read but just count for now
                f.seek(pos + num_bytes)

            else:
                f.seek(pos + num_bytes)

            count += 1
            if max_packets and count >= max_packets:
                break

    return result


def _parse_file_header(data: bytes, filepath: Path) -> XtfFileHeader:
    """Parse 1024-byte XTF file header."""
    hdr = XtfFileHeader(filepath=str(filepath))
    hdr.system_type = data[1]
    hdr.recording_program_name = data[2:10].decode("ascii", errors="replace").rstrip("\x00")
    hdr.recording_program_version = data[10:16].decode("ascii", errors="replace").rstrip("\x00")
    hdr.sonar_name = data[16:32].decode("ascii", errors="replace").rstrip("\x00")
    hdr.sonar_type = struct.unpack("<H", data[32:34])[0]
    hdr.navigation_system = data[36:52].decode("ascii", errors="replace").rstrip("\x00")
    hdr.num_channels = struct.unpack("<H", data[70:72])[0]
    hdr.num_bytes_header = struct.unpack("<H", data[72:74])[0]
    return hdr


def _parse_bathy_header(data: bytes) -> XtfPingHeader | None:
    """Parse XTF bathymetry packet header.

    After the 14-byte common header, the bathy ping header contains
    navigation, attitude, and beam count info.
    """
    if len(data) < 64:
        return None

    # Time fields start at offset 14 in the combined header
    # XTF bathy header layout (after 14 common bytes):
    # [14:16] u16 Year
    # [16]    u8  Month
    # [17]    u8  Day
    # [18]    u8  Hour
    # [19]    u8  Minute
    # [20]    u8  Second
    # [21]    u8  HSeconds (hundredths)
    # [22:24] u16 JulianDay

    if len(data) < 100:
        return None

    year = struct.unpack("<H", data[14:16])[0]
    month = data[16]
    day = data[17]
    hour = data[18]
    minute = data[19]
    second = data[20]
    hseconds = data[21]

    try:
        dt = datetime.datetime(year, max(month, 1), max(day, 1),
                               hour, minute, second,
                               hseconds * 10000,
                               tzinfo=datetime.timezone.utc)
    except (ValueError, OverflowError):
        dt = None

    # Navigation at various offsets (XTF spec varies by version)
    # Typically: EventNumber(u32), PingNumber(u32), SoundVelocity(f32),
    # then lat/lon/heading etc. Offsets depend on XTF sub-version.
    # We'll try standard positions:

    ping = XtfPingHeader(time=dt)

    # Search for plausible lat/lon values in the header
    # Standard XTF bathy: SensorXcoordinate at offset ~64, SensorYcoordinate at offset ~72
    if len(data) >= 92:
        ping.event_number = struct.unpack("<I", data[24:28])[0]
        ping.ping_number = struct.unpack("<I", data[28:32])[0]
        ping.sound_velocity = struct.unpack("<f", data[32:36])[0]
        # Lat/Lon often at offsets 64-80 as float64
        try:
            lat = struct.unpack("<d", data[64:72])[0]
            lon = struct.unpack("<d", data[72:80])[0]
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                ping.latitude = lat
                ping.longitude = lon
        except struct.error:
            pass

        # Attitude fields
        if len(data) >= 100:
            ping.heading = struct.unpack("<f", data[80:84])[0]
            ping.pitch = struct.unpack("<f", data[84:88])[0]
            ping.roll = struct.unpack("<f", data[88:92])[0]

    return ping

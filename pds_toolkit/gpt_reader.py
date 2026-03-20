"""GPT (GPS Track) reader for Teledyne PDS.

GPT files contain GPS track points as geographic coordinates (lat/lon).

File format (Little Endian, no text header):
  [0:4]   uint32  Point count (N)
  [4:]    N pairs of float64: [latitude, longitude] in decimal degrees
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from .models import GpsTrack


def read_gpt(filepath: str | Path) -> GpsTrack:
    """Read a GPT GPS track file.

    Args:
        filepath: Path to .gpt file.

    Returns:
        GpsTrack with latitude/longitude arrays.
    """
    filepath = Path(filepath)

    with open(filepath, "rb") as f:
        header = f.read(4)
        if len(header) < 4:
            return GpsTrack(filepath=str(filepath))

        count = struct.unpack("<I", header)[0]
        remaining = f.read()

    expected_bytes = count * 16  # 2 x float64 per point
    actual_points = min(count, len(remaining) // 16)

    if actual_points == 0:
        return GpsTrack(filepath=str(filepath))

    coords = np.frombuffer(remaining[:actual_points * 16], dtype="<f8").reshape(-1, 2)

    return GpsTrack(
        filepath=str(filepath),
        latitudes=coords[:, 0].copy(),
        longitudes=coords[:, 1].copy(),
    )

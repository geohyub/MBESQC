"""FAU (Fledermaus / Teledyne) point cloud reader.

Record layout (24 bytes, Little Endian):
  [0:4]   int32   Easting  (millimeters)
  [4:8]   int32   Northing (millimeters)
  [8:12]  int32   Depth    (millimeters)
  [12:16] uint32  Unix timestamp (seconds since 1970-01-01 UTC)
  [16:20] int32   field5   (sub-second time or auxiliary data)
  [20:24] uint32  flags    (quality / beam info)
"""

from __future__ import annotations

import datetime
import warnings
from pathlib import Path

import numpy as np

from .models import FauFile

_RECORD_SIZE = 24
_RECORD_DTYPE = np.dtype([
    ("easting_mm", "<i4"),
    ("northing_mm", "<i4"),
    ("depth_mm", "<i4"),
    ("timestamp", "<u4"),
    ("field5", "<i4"),
    ("flags", "<u4"),
])


def read_fau(filepath: str | Path, max_records: int | None = None) -> FauFile:
    """Read an FAU point cloud file."""
    filepath = Path(filepath)
    file_size = filepath.stat().st_size
    total_records = file_size // _RECORD_SIZE

    if file_size % _RECORD_SIZE != 0:
        warnings.warn(
            f"{filepath.name}: file size {file_size} is not a multiple of "
            f"{_RECORD_SIZE}. Trailing {file_size % _RECORD_SIZE} bytes discarded.",
            stacklevel=2,
        )

    if total_records == 0:
        return FauFile(filepath=str(filepath))

    n = total_records if max_records is None else min(max_records, total_records)
    raw = np.fromfile(str(filepath), dtype=_RECORD_DTYPE, count=n)

    return FauFile(
        filepath=str(filepath),
        num_points=len(raw),
        easting=raw["easting_mm"].astype(np.float64) / 1000.0,
        northing=raw["northing_mm"].astype(np.float64) / 1000.0,
        depth=raw["depth_mm"].astype(np.float64) / 1000.0,
        timestamps=raw["timestamp"].copy(),
        field5=raw["field5"].copy(),
        flags=raw["flags"].copy(),
    )


def fau_info(filepath: str | Path) -> dict:
    """Quick summary of an FAU file without loading all data."""
    filepath = Path(filepath)
    file_size = filepath.stat().st_size
    total_records = file_size // _RECORD_SIZE

    if total_records == 0:
        return {
            "filepath": str(filepath),
            "file_size_mb": round(file_size / 1024 / 1024, 1),
            "num_records": 0,
            "time_start": "N/A",
            "time_end": "N/A",
        }

    raw_first = np.fromfile(str(filepath), dtype=_RECORD_DTYPE, count=1)
    raw_last = np.fromfile(str(filepath), dtype=_RECORD_DTYPE, count=1,
                           offset=(total_records - 1) * _RECORD_SIZE)

    def _fmt(val: int) -> str:
        try:
            return datetime.datetime.fromtimestamp(
                int(val), tz=datetime.timezone.utc
            ).isoformat()
        except (OSError, ValueError):
            return "N/A"

    return {
        "filepath": str(filepath),
        "file_size_mb": round(file_size / 1024 / 1024, 1),
        "num_records": total_records,
        "time_start": _fmt(raw_first["timestamp"][0]),
        "time_end": _fmt(raw_last["timestamp"][0]),
    }

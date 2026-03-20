"""CSV/ASCII depth grid reader.

Reads space- or comma-delimited depth files exported from Teledyne PDS
Grid Model export. Files contain Easting, Northing, Depth columns
with no header row.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .models import DepthGrid


def read_depth_csv(
    filepath: str | Path,
    delimiter: str | None = None,
    max_rows: int | None = None,
) -> DepthGrid:
    """Read a CSV or ASCII depth grid file.

    Args:
        filepath: Path to .csv or .txt file.
        delimiter: Column separator. None = auto-detect (comma or whitespace).
        max_rows: If set, read only the first N rows.
    """
    filepath = Path(filepath)

    if delimiter is None:
        with open(filepath, "r") as f:
            first_line = f.readline()
        delimiter = "," if "," in first_line else None

    data = np.loadtxt(str(filepath), delimiter=delimiter, max_rows=max_rows, dtype=np.float64)

    if data.ndim == 1:
        data = data.reshape(1, -1)

    if data.shape[1] < 3:
        raise ValueError(f"{filepath.name}: expected at least 3 columns (E, N, D), got {data.shape[1]}")

    return DepthGrid(
        filepath=str(filepath),
        easting=data[:, 0],
        northing=data[:, 1],
        depth=data[:, 2],
    )

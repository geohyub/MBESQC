"""Snippet/Backscatter Parser for PDS binary data.

Extracts amplitude snippet data from PDS ping records.
The snippet region contains per-beam amplitude samples around
the detection point.

PDS snippet structure (within ping record, Gap 1: ~45KB):
  Each 4-byte entry: [u16 value_a][u16 value_b]
  - value_a in first entries slowly decreases (detection sample indices)
  - value_b varies rapidly (amplitude data)
  - ~11 entries per beam for 1024 beams

This parser provides raw snippet data for backscatter analysis.
Full interpretation requires knowledge of the sonar's detection parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SnippetData:
    """Raw snippet data from a single ping."""
    ping_number: int = 0
    raw_entries: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.uint16))
    total_entries: int = 0
    nonzero_entries: int = 0
    gap_offset: int = 0  # byte offset within ping record
    gap_size: int = 0     # bytes


def extract_snippet(ping_data: bytes, tt_end_offset: int = 16396,
                    depth_start_offset: int = 61532) -> SnippetData:
    """Extract snippet data from raw ping record bytes.

    Args:
        ping_data: Raw bytes of the ping record (from TT array start).
        tt_end_offset: Byte offset where snippet region starts
                       (after TT + sampling_rate + quality + rx_angles).
        depth_start_offset: Byte offset where snippet region ends
                            (before along-track array).

    Returns:
        SnippetData with raw u16 pairs.
    """
    snippet = SnippetData()

    gap_size = depth_start_offset - tt_end_offset
    if gap_size <= 0 or tt_end_offset + gap_size > len(ping_data):
        return snippet

    snippet.gap_offset = tt_end_offset
    snippet.gap_size = gap_size

    gap_data = ping_data[tt_end_offset:depth_start_offset]

    # Parse as u32 LE, then split into two u16 channels
    n_entries = len(gap_data) // 4
    if n_entries == 0:
        return snippet

    u32s = np.frombuffer(gap_data[:n_entries * 4], dtype='<u4')
    low = (u32s & 0xFFFF).astype(np.uint16)
    high = ((u32s >> 16) & 0xFFFF).astype(np.uint16)

    snippet.raw_entries = np.column_stack([low, high])
    snippet.total_entries = n_entries
    snippet.nonzero_entries = int(np.sum(u32s > 0))

    return snippet


def snippet_statistics(snippet: SnippetData) -> dict:
    """Compute basic statistics from snippet data.

    Returns dict with:
      - total_entries, nonzero_entries
      - channel_a: min, max, mean, std (typically sample indices)
      - channel_b: min, max, mean, std (typically amplitude)
    """
    if snippet.total_entries == 0:
        return {"total_entries": 0, "nonzero_entries": 0}

    a = snippet.raw_entries[:, 0].astype(np.float64)
    b = snippet.raw_entries[:, 1].astype(np.float64)

    # Filter non-zero
    a_nz = a[a > 0]
    b_nz = b[b > 0]

    return {
        "total_entries": snippet.total_entries,
        "nonzero_entries": snippet.nonzero_entries,
        "gap_offset": snippet.gap_offset,
        "gap_size": snippet.gap_size,
        "channel_a": {
            "min": float(a_nz.min()) if len(a_nz) > 0 else 0,
            "max": float(a_nz.max()) if len(a_nz) > 0 else 0,
            "mean": float(a_nz.mean()) if len(a_nz) > 0 else 0,
            "std": float(a_nz.std()) if len(a_nz) > 0 else 0,
            "nonzero": int(len(a_nz)),
        },
        "channel_b": {
            "min": float(b_nz.min()) if len(b_nz) > 0 else 0,
            "max": float(b_nz.max()) if len(b_nz) > 0 else 0,
            "mean": float(b_nz.mean()) if len(b_nz) > 0 else 0,
            "std": float(b_nz.std()) if len(b_nz) > 0 else 0,
            "nonzero": int(len(b_nz)),
        },
    }

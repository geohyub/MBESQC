"""PDS file header parser.

Reads the INI-style text header embedded at the start of Teledyne PDS (.pds)
acquisition log files. The binary measurement data is NOT parsed here.

File structure (PDS v2.2 / PdsVersion 4.x):
  [16-byte binary file header]
  [HEADER] section (file-level metadata, padded to block boundary)
  [Header] section + 120+ INI sections (project/vessel/device config)
  ... binary measurement data ...
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from .models import PdsMetadata


def read_pds_header(filepath: str | Path, max_bytes: int = 1_000_000) -> PdsMetadata:
    """Parse PDS text header and return structured metadata.

    Args:
        filepath: Path to a .pds file.
        max_bytes: Maximum bytes to read for header extraction.
            Default 1 MB covers even large config blocks.

    Returns:
        PdsMetadata with all INI sections parsed.
    """
    filepath = Path(filepath)
    meta = PdsMetadata(filepath=str(filepath))

    with open(filepath, "rb") as f:
        # --- Binary file header (16 bytes) ---
        bin_header = f.read(16)
        if len(bin_header) < 16:
            return meta

        rec_type = struct.unpack("<H", bin_header[0:2])[0]
        rec_sub = struct.unpack("<H", bin_header[2:4])[0]
        _timestamp = struct.unpack("<I", bin_header[4:8])[0]
        _reserved = struct.unpack("<I", bin_header[8:12])[0]
        text_len = struct.unpack("<I", bin_header[12:16])[0]

        # --- Read text area ---
        # The first text block starts right after the 16-byte header
        # with a 1-byte prefix (0x04) before [HEADER].
        # We skip any non-printable prefix bytes to find the text.
        raw = f.read(max_bytes)

    # Extract all printable text (the config area is UTF-8 text with padding)
    # Skip any leading binary bytes before the first [HEADER] section
    text_start = 0
    header_marker = raw.find(b"[HEADER]")
    if header_marker >= 0:
        text_start = header_marker

    text = raw[text_start:].decode("utf-8", errors="replace")

    # Parse all INI-style [Section] blocks
    sections = _parse_ini_sections(text)
    meta.sections = sections

    # Map well-known fields
    hdr = sections.get("HEADER", {})
    meta.pds_version = hdr.get("PdsVersion", "")
    meta.file_version = hdr.get("FileVersion", "")
    meta.vessel_name = hdr.get("VesselName", "")
    meta.survey_type = hdr.get("SurveyType", "")
    meta.start_time = hdr.get("AcquisLogStartTime", "")

    hdr2 = sections.get("Header", {})
    meta.project_name = hdr2.get("ProjectName", "")
    meta.project_number = hdr2.get("ProjectNumber", "")
    meta.client_name = hdr2.get("ClientName", "")
    meta.contractor_name = hdr2.get("ContractorName", "")
    meta.operator_name = hdr2.get("OperatorName", "")

    coord = sections.get("CoordSystem", {})
    meta.coord_system_group = coord.get("System group name", "")
    meta.coord_system_name = coord.get("System name", "")

    units = sections.get("Units", {})
    meta.system_units = units.get("System Units", "")
    meta.depth_units = units.get("Depth Units", "")

    return meta


def _parse_ini_sections(text: str) -> dict[str, dict[str, str]]:
    """Parse INI-style text into nested dict of sections."""
    sections: dict[str, dict[str, str]] = {}
    current_section: str | None = None

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Section header: [SectionName] or [SECTION(123)]
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            section_name = m.group(1)
            # Handle duplicate section names by appending index
            if section_name in sections:
                i = 2
                while f"{section_name}_{i}" in sections:
                    i += 1
                section_name = f"{section_name}_{i}"
            sections[section_name] = {}
            current_section = section_name
            continue

        # Key = Value pair
        if current_section and "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                sections[current_section][key] = value

    return sections


def list_pds_sections(filepath: str | Path) -> list[str]:
    """Return list of all INI section names found in a PDS file."""
    meta = read_pds_header(filepath)
    return list(meta.sections.keys())

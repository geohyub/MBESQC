"""PDS Toolkit - Teledyne PDS / CARIS survey data format parsers.

Unified Python readers for multibeam survey data formats:
  GSF  - Generic Sensor Format (bathymetry + attitude + SVP)
  FAU  - Fledermaus point cloud
  GPT  - GPS track
  S7K  - Reson 7k raw sonar data
  XTF  - eXtended Triton Format
  HVF  - HIPS Vessel File (sensor offsets & calibration)
  PDS  - Teledyne PDS header metadata
  CSV  - Depth grid export
"""

from .csv_reader import read_depth_csv
from .fau_reader import fau_info, read_fau
from .gpt_reader import read_gpt
from .gsf_reader import read_gsf
from .hvf_reader import read_hvf
from .models import (
    DepthGrid,
    FauFile,
    GpsTrack,
    GsfAttitudeRecord,
    GsfFile,
    GsfPing,
    GsfScaleFactors,
    GsfSummary,
    GsfSvpRecord,
    HvfFile,
    HvfSensorOffset,
    PdsMetadata,
    S7kAttitude,
    S7kBathymetricData,
    S7kFile,
    S7kPosition,
    S7kRawDetection,
    S7kSonarSettings,
    XtfFile,
    XtfFileHeader,
    XtfPingHeader,
)
from .pds_binary import PdsAttitudeRecord, PdsBinaryData, PdsNavRecord, PdsPing, PdsTideRecord, pds_binary_info, pds_binary_to_xyz, read_pds_binary
from .pds_header import list_pds_sections, read_pds_header
from .swath import SwathLine, SwathPing, gsf_to_swath, load_swath, pds_to_swath
from .s7k_reader import read_s7k
from .xtf_reader import read_xtf

__all__ = [
    # Readers
    "read_gsf", "read_fau", "read_gpt", "read_depth_csv",
    "read_pds_header", "read_s7k", "read_xtf", "read_hvf",
    "fau_info", "list_pds_sections",
    # GSF models
    "GsfFile", "GsfPing", "GsfScaleFactors", "GsfSummary",
    "GsfAttitudeRecord", "GsfSvpRecord",
    # Other models
    "FauFile", "GpsTrack", "DepthGrid", "PdsMetadata",
    "S7kFile", "S7kBathymetricData", "S7kPosition",
    "S7kAttitude", "S7kSonarSettings", "S7kRawDetection",
    "XtfFile", "XtfFileHeader", "XtfPingHeader",
    "HvfFile", "HvfSensorOffset",
    # PDS Binary
    "read_pds_binary", "pds_binary_info", "pds_binary_to_xyz",
    "PdsBinaryData", "PdsPing", "PdsNavRecord", "PdsTideRecord",
    # Swath (unified model)
    "SwathLine", "SwathPing", "load_swath", "gsf_to_swath", "pds_to_swath",
]

__version__ = "0.2.0"

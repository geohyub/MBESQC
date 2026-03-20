"""Run full MBES QC on complete EDF dataset (922 GSF files)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mbes_qc.runner import run_full_qc

REF = Path(r"E:/Software/MBESQC/reference")
RAW = REF / "EDF_RAW" / "1003+svp"
OUTPUT = Path(r"E:/Software/MBESQC/qc_output_full")

# Full dataset: all 922 GSF files, 100 pings each for reasonable speed
result = run_full_qc(
    gsf_dir=REF / "EDF_GSF",
    pds_path=RAW / "EDFR-20251003-220225.pds",
    hvf_path=REF / "EDF_VESSELS" / "DP-1.hvf",
    output_dir=OUTPUT,
    max_pings=100,     # 100 pings per file (922 files x 100 = ~92K pings)
    cell_size=5.0,
    iho_order="1a",
    generate_surfaces=True,
    generate_reports=True,
)

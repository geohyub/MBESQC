"""Run full MBES QC on EDF reference dataset (quick test: 5 GSF files)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mbes_qc.runner import run_full_qc

REF = Path(r"E:/Software/MBESQC/reference")
RAW = REF / "EDF_RAW" / "1003+svp"
OUTPUT = Path(r"E:/Software/MBESQC/qc_output")

# Use 5 GSF files for quick test (mix of dates for cross-line)
gsf_files = sorted(REF.joinpath("EDF_GSF").glob("*.gsf"))[:5]

result = run_full_qc(
    gsf_paths=gsf_files,
    pds_path=RAW / "EDFR-20251003-220225.pds",
    hvf_path=REF / "EDF_VESSELS" / "DP-1.hvf",
    output_dir=OUTPUT,
    max_pings=50,
    cell_size=5.0,
    iho_order="1a",
    generate_surfaces=True,
    generate_reports=True,
)

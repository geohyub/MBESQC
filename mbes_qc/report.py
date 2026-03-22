"""Report Generator - Terminal, Excel, Word, and DQR PPT output.

Generates QC reports in multiple formats from QC analysis results.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from dataclasses import dataclass

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False

try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    HAS_WORD = True
except ImportError:
    HAS_WORD = False

try:
    import sys as _sys
    from pathlib import Path as _Path
    _shared = _Path(__file__).resolve().parents[2] / "_shared"
    if _shared.exists() and str(_shared) not in _sys.path:
        _sys.path.insert(0, str(_shared))
    from geoview_common.reporting.design_system import WordBuilder, WORD_STYLES
    HAS_WORDBUILDER = True
except ImportError:
    HAS_WORDBUILDER = False


# ── Color definitions ───────────────────────────────────────────

_GREEN = "00B050"
_RED = "FF0000"
_YELLOW = "FFC000"
_GRAY = "808080"


def _status_color(status: str) -> str:
    return {"PASS": _GREEN, "FAIL": _RED, "WARNING": _YELLOW}.get(status, _GRAY)


# ── Excel Report ────────────────────────────────────────────────


def generate_excel_report(
    qc_results: dict,
    output_path: str | Path,
    project_name: str = "",
    vessel_name: str = "",
) -> None:
    """Generate Excel QC report with professional styling."""
    if not HAS_EXCEL:
        print("  [SKIP] openpyxl not installed")
        return

    wb = openpyxl.Workbook()

    # ── Style constants ──
    NAVY = "1E3A5F"
    navy_fill = PatternFill("solid", fgColor=NAVY)
    hdr_font = Font(bold=True, size=11, color="FFFFFF")
    hdr_align = Alignment(horizontal="center", vertical="center")
    title_font = Font(bold=True, size=16, color="FFFFFF")
    title_align = Alignment(horizontal="center", vertical="center")
    info_label_font = Font(bold=True, size=10, color=NAVY)
    info_value_font = Font(size=10)
    data_font = Font(size=10)
    alt_fill = PatternFill("solid", fgColor="F2F6FA")
    pass_fill = PatternFill("solid", fgColor="C6EFCE")
    fail_fill = PatternFill("solid", fgColor="FFC7CE")
    warn_fill = PatternFill("solid", fgColor="FFEB9C")
    pass_font = Font(bold=True, size=10, color="006100")
    fail_font = Font(bold=True, size=10, color="9C0006")
    warn_font = Font(bold=True, size=10, color="9C6500")
    thin_border = Border(
        left=Side(style="thin", color="D0D0D0"),
        right=Side(style="thin", color="D0D0D0"),
        top=Side(style="thin", color="D0D0D0"),
        bottom=Side(style="thin", color="D0D0D0"),
    )

    def _status_style(val: str):
        """Return (font, fill) for a status value."""
        if val == "PASS":
            return pass_font, pass_fill
        elif val == "FAIL":
            return fail_font, fail_fill
        elif val == "WARNING":
            return warn_font, warn_fill
        return data_font, None

    def _add_sheet(name: str, headers: list[str], rows: list[list],
                   start_row: int = 1) -> None:
        ws = wb.create_sheet(name)

        # Header row
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=start_row, column=c, value=h)
            cell.font = hdr_font
            cell.fill = navy_fill
            cell.alignment = hdr_align
            cell.border = thin_border

        # Data rows with alternating fills & status cell coloring
        for r, row_data in enumerate(rows, start_row + 1):
            is_alt = (r - start_row) % 2 == 0
            for c, val in enumerate(row_data, 1):
                cell = ws.cell(row=r, column=c, value=str(val))
                cell.border = thin_border
                cell.font = data_font
                # Alternating row background
                if is_alt:
                    cell.fill = alt_fill

                # Status cells: colored fill + bold font
                if isinstance(val, str) and val in ("PASS", "FAIL", "WARNING"):
                    sfont, sfill = _status_style(val)
                    cell.font = sfont
                    if sfill:
                        cell.fill = sfill

        # Auto-width
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(50, max(12, max_len + 2))

        return ws

    # ── Summary sheet ──
    ws_sum = wb.create_sheet("Summary", 0)

    # Navy title banner (merged A1:F1)
    ws_sum.merge_cells("A1:F1")
    title_cell = ws_sum["A1"]
    title_cell.value = "MBES QC Report"
    title_cell.font = title_font
    title_cell.fill = navy_fill
    title_cell.alignment = title_align
    ws_sum.row_dimensions[1].height = 36

    # Project info block (rows 3-6)
    info_items = [
        ("Project", project_name or "N/A"),
        ("Vessel", vessel_name or "N/A"),
        ("Report Date", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    for idx, (label, value) in enumerate(info_items):
        r = idx + 3
        lbl_cell = ws_sum.cell(row=r, column=1, value=label)
        lbl_cell.font = info_label_font
        val_cell = ws_sum.cell(row=r, column=2, value=value)
        val_cell.font = info_value_font

    # QC Results header
    qc_start_row = len(info_items) + 4
    summary_rows = []
    overall = "PASS"
    for category, result in qc_results.items():
        verdict = "N/A"
        if hasattr(result, "overall_verdict"):
            verdict = result.overall_verdict
        elif hasattr(result, "items"):
            vs = []
            for i in (result.items if isinstance(result.items, list) else []):
                s = i.get("status", "N/A") if isinstance(i, dict) else getattr(i, "status", "N/A")
                vs.append(s)
            if "FAIL" in vs:
                verdict = "FAIL"
            elif "WARNING" in vs:
                verdict = "WARNING"
            elif vs:
                verdict = "PASS"

        summary_rows.append([category, verdict])
        if verdict == "FAIL":
            overall = "FAIL"
        elif verdict == "WARNING" and overall == "PASS":
            overall = "WARNING"

    summary_rows.insert(0, ["OVERALL", overall])

    # Write summary table headers
    for c, h in enumerate(["Category", "Verdict"], 1):
        cell = ws_sum.cell(row=qc_start_row, column=c, value=h)
        cell.font = hdr_font
        cell.fill = navy_fill
        cell.alignment = hdr_align
        cell.border = thin_border

    # Write summary data
    for r_idx, row_data in enumerate(summary_rows, qc_start_row + 1):
        is_alt = (r_idx - qc_start_row) % 2 == 0
        for c, val in enumerate(row_data, 1):
            cell = ws_sum.cell(row=r_idx, column=c, value=str(val))
            cell.border = thin_border
            cell.font = data_font
            if is_alt:
                cell.fill = alt_fill
            if isinstance(val, str) and val in ("PASS", "FAIL", "WARNING"):
                sfont, sfill = _status_style(val)
                cell.font = sfont
                if sfill:
                    cell.fill = sfill

    ws_sum.column_dimensions["A"].width = 25
    ws_sum.column_dimensions["B"].width = 20

    # ── Detail sheets per category ──
    for category, result in qc_results.items():
        items = []
        if hasattr(result, "items"):
            raw_items = result.items
            for i in raw_items:
                if isinstance(i, dict):
                    items.append([i.get("name", ""), i.get("status", ""), i.get("detail", "")])
                elif hasattr(i, "name"):
                    items.append([i.name, i.status, i.detail])

        if items:
            safe_name = category[:31].replace("/", "-").replace("\\", "-")
            _add_sheet(safe_name, ["Check", "Status", "Detail"], items)

    # Remove default sheet
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    wb.save(str(output_path))


# ── Word Report ─────────────────────────────────────────────────


def generate_word_report(
    qc_results: dict,
    output_path: str | Path,
    project_name: str = "",
    vessel_name: str = "",
) -> None:
    """Generate Word QC report using GeoView design system."""
    if not HAS_WORDBUILDER:
        if not HAS_WORD:
            print("  [SKIP] python-docx not installed")
            return
        # Fallback: minimal raw-docx report
        _generate_word_report_fallback(qc_results, output_path, project_name, vessel_name)
        return

    wb = WordBuilder(WORD_STYLES["geoview_report"])

    # ── Cover Page ──
    wb.cover(
        title="MBES QC Report",
        subtitle="Multi-Beam Echo Sounder Quality Control",
        meta=f"Project: {project_name or 'N/A'}  |  "
             f"Vessel: {vessel_name or 'N/A'}  |  "
             f"Date: {datetime.datetime.now():%Y-%m-%d}",
    )
    wb.setup_page_footer("MBES QC Report")
    wb.page_break()

    # ── 1. Project Information ──
    wb.reset_numbering()
    wb.heading("Project Information", level=1)
    wb.kv_table([
        ("Project", project_name or "N/A"),
        ("Vessel", vessel_name or "N/A"),
        ("Report Date", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
    ])

    # ── 2. QC Summary ──
    wb.heading("QC Summary", level=1)

    summary_rows = []
    overall = "PASS"
    for category, result in qc_results.items():
        verdict = "N/A"
        if hasattr(result, "overall_verdict"):
            verdict = result.overall_verdict
        elif hasattr(result, "items"):
            vs = []
            for i in (result.items if isinstance(result.items, list) else []):
                s = i.get("status", "N/A") if isinstance(i, dict) else getattr(i, "status", "N/A")
                vs.append(s)
            if "FAIL" in vs:
                verdict = "FAIL"
            elif "WARNING" in vs:
                verdict = "WARNING"
            elif vs:
                verdict = "PASS"

        summary_rows.append([category, verdict])
        if verdict == "FAIL":
            overall = "FAIL"
        elif verdict == "WARNING" and overall == "PASS":
            overall = "WARNING"

    summary_rows.insert(0, ["OVERALL", overall])
    wb.table(
        headers=["Category", "Verdict"],
        rows=summary_rows,
    )

    # ── 3. Detailed Results ──
    wb.heading("Detailed Results", level=1)

    for category, result in qc_results.items():
        wb.heading(category, level=2)

        items = []
        if hasattr(result, "items"):
            for i in result.items:
                if isinstance(i, dict):
                    items.append(i)
                elif hasattr(i, "name"):
                    items.append({"name": i.name, "status": i.status, "detail": i.detail})

        if items:
            detail_rows = []
            for item in items:
                detail_rows.append([
                    item.get("name", ""),
                    item.get("status", ""),
                    item.get("detail", ""),
                ])
            wb.table(
                headers=["Check", "Status", "Detail"],
                rows=detail_rows,
            )
        else:
            wb.body_text("No detailed check items available for this category.")

    wb.doc.save(str(output_path))


def _generate_word_report_fallback(
    qc_results: dict, output_path: str | Path,
    project_name: str, vessel_name: str,
) -> None:
    """Minimal fallback Word report when WordBuilder is unavailable."""
    doc = Document()
    title = doc.add_heading("MBES QC Report", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run("Project: ").bold = True
    p.add_run(project_name or "N/A")
    p = doc.add_paragraph()
    p.add_run("Vessel: ").bold = True
    p.add_run(vessel_name or "N/A")
    p = doc.add_paragraph()
    p.add_run("Date: ").bold = True
    p.add_run(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    for category, result in qc_results.items():
        doc.add_heading(category, level=2)
    doc.save(str(output_path))


# ── Terminal Report ─────────────────────────────────────────────


def print_terminal_report(qc_results: dict) -> None:
    """Print colored terminal QC report."""

    def _vc(v: str) -> str:
        c = {"PASS": "\033[92m", "WARNING": "\033[93m", "FAIL": "\033[91m"}.get(v, "")
        return f"{c}{v}\033[0m"

    print("\n" + "=" * 70)
    print("  MBES QC REPORT")
    print("=" * 70)

    overall = "PASS"
    for category, result in qc_results.items():
        verdict = "N/A"
        if hasattr(result, "overall_verdict"):
            verdict = result.overall_verdict

        print(f"\n  [{_vc(verdict)}] {category}")

        items = []
        if hasattr(result, "items"):
            for i in result.items:
                if isinstance(i, dict):
                    items.append(i)
                elif hasattr(i, "name"):
                    items.append({"name": i.name, "status": i.status, "detail": i.detail})

        for item in items:
            s = item.get("status", "N/A")
            print(f"      {_vc(s):>20s}  {item.get('name', '')}: {item.get('detail', '')}")

        if verdict == "FAIL":
            overall = "FAIL"
        elif verdict == "WARNING" and overall == "PASS":
            overall = "WARNING"

    print("\n" + "=" * 70)
    print(f"  OVERALL: {_vc(overall)}")
    print("=" * 70 + "\n")

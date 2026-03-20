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


# ── Color definitions ───────────────────────────────────────────

_GREEN = "00B050"
_RED = "FF0000"
_YELLOW = "FFC000"
_GRAY = "808080"


def _status_color(status: str) -> str:
    return {"PASS": _GREEN, "FAIL": _RED, "WARNING": _YELLOW}.get(status, _GRAY)


# ── Excel Report ────────────────────────────────────────────────


def generate_excel_report(qc_results: dict, output_path: str | Path) -> None:
    """Generate Excel QC report with one sheet per QC category."""
    if not HAS_EXCEL:
        print("  [SKIP] openpyxl not installed")
        return

    wb = openpyxl.Workbook()

    # Header style
    hdr_font = Font(bold=True, size=11, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="2F5496")
    hdr_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    def _add_sheet(name: str, headers: list[str], rows: list[list]) -> None:
        ws = wb.create_sheet(name)
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = hdr_align
            cell.border = thin_border

        for r, row_data in enumerate(rows, 2):
            for c, val in enumerate(row_data, 1):
                cell = ws.cell(row=r, column=c, value=str(val))
                cell.border = thin_border
                # Color status cells
                if isinstance(val, str) and val in ("PASS", "FAIL", "WARNING"):
                    cell.font = Font(bold=True, color=_status_color(val))

        # Auto-width
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(50, max(12, max_len + 2))

    # Summary sheet
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
            if "FAIL" in vs: verdict = "FAIL"
            elif "WARNING" in vs: verdict = "WARNING"
            elif vs: verdict = "PASS"

        summary_rows.append([category, verdict])
        if verdict == "FAIL":
            overall = "FAIL"
        elif verdict == "WARNING" and overall == "PASS":
            overall = "WARNING"

    summary_rows.insert(0, ["OVERALL", overall])
    _add_sheet("Summary", ["Category", "Verdict"], summary_rows)

    # Detail sheets per category
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
    """Generate Word QC report."""
    if not HAS_WORD:
        print("  [SKIP] python-docx not installed")
        return

    doc = Document()

    # Title
    title = doc.add_heading("MBES QC Report", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Info table
    p = doc.add_paragraph()
    p.add_run(f"Project: ").bold = True
    p.add_run(project_name or "N/A")
    p = doc.add_paragraph()
    p.add_run(f"Vessel: ").bold = True
    p.add_run(vessel_name or "N/A")
    p = doc.add_paragraph()
    p.add_run(f"Date: ").bold = True
    p.add_run(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    doc.add_paragraph()

    # Summary table
    doc.add_heading("QC Summary", level=1)
    table = doc.add_table(rows=1, cols=2, style="Table Grid")
    table.rows[0].cells[0].text = "Category"
    table.rows[0].cells[1].text = "Verdict"

    for cell in table.rows[0].cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.font.bold = True

    for category, result in qc_results.items():
        verdict = "N/A"
        if hasattr(result, "overall_verdict"):
            verdict = result.overall_verdict

        row = table.add_row()
        row.cells[0].text = category
        run = row.cells[1].paragraphs[0].add_run(verdict)
        run.font.bold = True
        color_map = {"PASS": RGBColor(0, 176, 80), "FAIL": RGBColor(255, 0, 0),
                     "WARNING": RGBColor(255, 192, 0)}
        run.font.color.rgb = color_map.get(verdict, RGBColor(128, 128, 128))

    # Detail sections
    for category, result in qc_results.items():
        doc.add_heading(category, level=2)

        items = []
        if hasattr(result, "items"):
            for i in result.items:
                if isinstance(i, dict):
                    items.append(i)
                elif hasattr(i, "name"):
                    items.append({"name": i.name, "status": i.status, "detail": i.detail})

        if items:
            table = doc.add_table(rows=1, cols=3, style="Table Grid")
            for j, h in enumerate(["Check", "Status", "Detail"]):
                table.rows[0].cells[j].text = h
                for paragraph in table.rows[0].cells[j].paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True

            for item in items:
                row = table.add_row()
                row.cells[0].text = item.get("name", "")
                row.cells[1].text = item.get("status", "")
                row.cells[2].text = item.get("detail", "")

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

"""PDF Report Generator — ReportLab-based MBES QC Report.

Generates a professional multi-page PDF from FullQcResult:
  Cover, Project Summary, File QC, Surface Statistics,
  Coverage Analysis, Cross-line QC, IHO Compliance, SVP Summary.

Copyright (c) 2025-2026 Geoview Co., Ltd.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

try:
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm, cm, inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, KeepTogether, HRFlowable,
    )
    from reportlab.platypus.flowables import Flowable
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


# ── Color Palette ──────────────────────────────────────────────

NAVY = rl_colors.HexColor("#0F172A") if HAS_REPORTLAB else None
NAVY_LIGHT = rl_colors.HexColor("#1E293B") if HAS_REPORTLAB else None
BLUE = rl_colors.HexColor("#2D5F8A") if HAS_REPORTLAB else None
CYAN = rl_colors.HexColor("#06B6D4") if HAS_REPORTLAB else None
GREEN = rl_colors.HexColor("#38A169") if HAS_REPORTLAB else None
RED = rl_colors.HexColor("#E53E3E") if HAS_REPORTLAB else None
YELLOW = rl_colors.HexColor("#D69E2E") if HAS_REPORTLAB else None
GRAY = rl_colors.HexColor("#718096") if HAS_REPORTLAB else None
WHITE = rl_colors.HexColor("#FFFFFF") if HAS_REPORTLAB else None
LIGHT_BG = rl_colors.HexColor("#F7FAFC") if HAS_REPORTLAB else None
TABLE_BORDER = rl_colors.HexColor("#CBD5E0") if HAS_REPORTLAB else None

STATUS_COLORS = {
    "PASS": GREEN,
    "FAIL": RED,
    "WARNING": YELLOW,
    "N/A": GRAY,
}


# ── Page Template (header/footer) ────────────────────────────

def _header_footer(canvas, doc):
    """Draw navy header bar, page number, and GeoView branding on each page."""
    canvas.saveState()
    width, height = A4

    # Navy header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 28 * mm, width, 28 * mm, fill=True, stroke=False)

    # Header text
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(15 * mm, height - 12 * mm, "MBES QC Report")

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(rl_colors.HexColor("#94A3B8"))
    canvas.drawString(15 * mm, height - 19 * mm, "Multibeam Echosounder Quality Control")

    # GeoView branding (right side of header)
    canvas.setFillColor(CYAN)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawRightString(width - 15 * mm, height - 12 * mm, "GeoView")
    canvas.setFillColor(rl_colors.HexColor("#64748B"))
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(width - 15 * mm, height - 19 * mm, "Geoview Co., Ltd.")

    # Footer — page number
    canvas.setFillColor(GRAY)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(width / 2, 12 * mm, f"Page {doc.page}")

    # Footer line
    canvas.setStrokeColor(TABLE_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(15 * mm, 18 * mm, width - 15 * mm, 18 * mm)

    # Footer branding
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRAY)
    canvas.drawString(15 * mm, 10 * mm, f"Generated: {datetime.datetime.now():%Y-%m-%d %H:%M}")
    canvas.drawRightString(width - 15 * mm, 10 * mm, "GeoView MBES QC")

    canvas.restoreState()


# ── Style helpers ──────────────────────────────────────────────

def _get_styles():
    """Create custom paragraph styles for the report."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "SectionTitle", parent=styles["Heading1"],
        fontSize=14, textColor=NAVY, spaceAfter=8, spaceBefore=16,
        fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "SubTitle", parent=styles["Heading2"],
        fontSize=11, textColor=BLUE, spaceAfter=6, spaceBefore=10,
        fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "BodyText2", parent=styles["BodyText"],
        fontSize=9, textColor=rl_colors.HexColor("#2D3748"),
        spaceAfter=4, leading=13,
    ))
    styles.add(ParagraphStyle(
        "CellText", fontSize=8, textColor=rl_colors.HexColor("#2D3748"),
        leading=10, fontName="Helvetica",
    ))
    styles.add(ParagraphStyle(
        "CellBold", fontSize=8, textColor=NAVY,
        leading=10, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "CoverTitle", fontSize=28, textColor=WHITE,
        fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "CoverSub", fontSize=14, textColor=rl_colors.HexColor("#94A3B8"),
        fontName="Helvetica", alignment=TA_CENTER, spaceAfter=4,
    ))
    return styles


# ── Table helpers ──────────────────────────────────────────────

def _status_para(status: str, styles) -> Paragraph:
    """Return a colored Paragraph for PASS/FAIL/WARNING."""
    color = {"PASS": "#38A169", "FAIL": "#E53E3E", "WARNING": "#D69E2E"}.get(status, "#718096")
    return Paragraph(f'<font color="{color}"><b>{status}</b></font>', styles["CellText"])


def _make_table(headers: list[str], rows: list[list], col_widths=None, styles=None):
    """Build a formatted table with navy header row."""
    sty = styles or _get_styles()

    header_row = [Paragraph(f'<b>{h}</b>', sty["CellBold"]) for h in headers]
    data = [header_row] + rows

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        # Body
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        # Alternating rows
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_BG]),
        # Grid
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _verdict_text(result) -> str:
    """Extract overall verdict from a QC result object."""
    if hasattr(result, "overall_verdict"):
        return result.overall_verdict
    return "N/A"


def _get_items(result) -> list[dict]:
    """Extract items list from a QC result (handles both dict and object items)."""
    items = []
    if hasattr(result, "items"):
        for i in result.items:
            if isinstance(i, dict):
                items.append(i)
            elif hasattr(i, "name"):
                items.append({"name": i.name, "status": i.status, "detail": i.detail})
    return items


# ── Cover Page ─────────────────────────────────────────────────

class _CoverBackground(Flowable):
    """Draw a full-page navy cover background."""

    def __init__(self, width, height):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        self.canv.setFillColor(NAVY)
        self.canv.rect(0, -self.height + 100, self.width, self.height + 200, fill=True, stroke=False)
        # Accent line
        self.canv.setFillColor(CYAN)
        self.canv.rect(0, -40, self.width, 3, fill=True, stroke=False)


def _build_cover(elements, styles, qc_result, project_name: str = "", vessel_name: str = ""):
    """Build the cover page."""
    w, h = A4

    elements.append(_CoverBackground(w, h))
    elements.append(Spacer(1, 80 * mm))

    elements.append(Paragraph("MBES QC Report", styles["CoverTitle"]))
    elements.append(Paragraph("Multibeam Echosounder Quality Control", styles["CoverSub"]))
    elements.append(Spacer(1, 20 * mm))

    # Info lines on cover
    info_style = ParagraphStyle(
        "CoverInfo", fontSize=11, textColor=rl_colors.HexColor("#94A3B8"),
        fontName="Helvetica", alignment=TA_CENTER, spaceAfter=6,
    )

    if project_name:
        elements.append(Paragraph(f"Project: {project_name}", info_style))
    if vessel_name:
        elements.append(Paragraph(f"Vessel: {vessel_name}", info_style))
    elements.append(Paragraph(
        f"Date: {datetime.datetime.now():%Y-%m-%d %H:%M}", info_style
    ))

    # Overall verdict
    overall = "PASS"
    qc_dict = qc_result.as_dict() if hasattr(qc_result, "as_dict") else qc_result
    for cat, result in qc_dict.items():
        v = _verdict_text(result)
        if v == "FAIL":
            overall = "FAIL"
        elif v == "WARNING" and overall == "PASS":
            overall = "WARNING"

    color = {"PASS": "#38A169", "FAIL": "#E53E3E", "WARNING": "#D69E2E"}.get(overall, "#718096")
    verdict_style = ParagraphStyle(
        "CoverVerdict", fontSize=20, textColor=rl_colors.HexColor(color),
        fontName="Helvetica-Bold", alignment=TA_CENTER, spaceBefore=20,
    )
    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph(f"Overall: {overall}", verdict_style))

    # GeoView branding
    brand_style = ParagraphStyle(
        "CoverBrand", fontSize=9, textColor=rl_colors.HexColor("#475569"),
        fontName="Helvetica", alignment=TA_CENTER, spaceBefore=40,
    )
    elements.append(Spacer(1, 30 * mm))
    elements.append(Paragraph("GeoView MBES QC  |  Geoview Co., Ltd.", brand_style))

    elements.append(PageBreak())


# ── Section Builders ───────────────────────────────────────────

def _build_project_summary(elements, styles, qc_result):
    """Section: Project Summary with per-category verdicts."""
    elements.append(Paragraph("1. Project Summary", styles["SectionTitle"]))

    qc_dict = qc_result.as_dict() if hasattr(qc_result, "as_dict") else qc_result

    rows = []
    for cat, result in qc_dict.items():
        v = _verdict_text(result)
        rows.append([
            Paragraph(cat, styles["CellText"]),
            _status_para(v, styles),
        ])

    if rows:
        tbl = _make_table(["QC Category", "Verdict"], rows,
                          col_widths=[120 * mm, 40 * mm], styles=styles)
        elements.append(tbl)

    # Timing info
    if hasattr(qc_result, "elapsed_sec") and qc_result.elapsed_sec > 0:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph(
            f"Total processing time: {qc_result.elapsed_sec:.1f} seconds",
            styles["BodyText2"],
        ))

    elements.append(Spacer(1, 6 * mm))


def _build_file_qc(elements, styles, file_qc):
    """Section: File QC Results."""
    if file_qc is None:
        return

    elements.append(Paragraph("2. File QC Results", styles["SectionTitle"]))

    # Summary info
    info_lines = []
    if hasattr(file_qc, "total_lines"):
        info_lines.append(f"Survey lines: {file_qc.total_lines}")
    if hasattr(file_qc, "total_pings"):
        info_lines.append(f"Total pings: {file_qc.total_pings:,}")
    if hasattr(file_qc, "time_range") and file_qc.time_range:
        info_lines.append(f"Time range: {file_qc.time_range}")
    if hasattr(file_qc, "coord_system") and file_qc.coord_system:
        info_lines.append(f"Coordinate system: {file_qc.coord_system}")

    if info_lines:
        for line in info_lines:
            elements.append(Paragraph(line, styles["BodyText2"]))
        elements.append(Spacer(1, 4 * mm))

    # File list
    if hasattr(file_qc, "gsf_files") and file_qc.gsf_files:
        elements.append(Paragraph("GSF Files", styles["SubTitle"]))
        file_rows = []
        for i, f in enumerate(file_qc.gsf_files, 1):
            file_rows.append([
                Paragraph(str(i), styles["CellText"]),
                Paragraph(str(Path(f).name), styles["CellText"]),
            ])
        if file_rows:
            tbl = _make_table(["#", "Filename"], file_rows,
                              col_widths=[15 * mm, 145 * mm], styles=styles)
            elements.append(tbl)
            elements.append(Spacer(1, 4 * mm))

    # Check items
    items = _get_items(file_qc)
    if items:
        elements.append(Paragraph("Check Results", styles["SubTitle"]))
        rows = []
        for item in items:
            rows.append([
                Paragraph(item.get("name", ""), styles["CellText"]),
                _status_para(item.get("status", "N/A"), styles),
                Paragraph(item.get("detail", ""), styles["CellText"]),
            ])
        tbl = _make_table(["Check", "Status", "Detail"], rows,
                          col_widths=[45 * mm, 25 * mm, 90 * mm], styles=styles)
        elements.append(tbl)

    elements.append(Spacer(1, 6 * mm))


def _build_surface_stats(elements, styles, surface):
    """Section: Surface Statistics."""
    if surface is None:
        return

    elements.append(Paragraph("3. Surface Statistics", styles["SectionTitle"]))

    rows = []
    if hasattr(surface, "dtm") and surface.dtm is not None:
        import numpy as np
        valid = surface.dtm[~np.isnan(surface.dtm)] if hasattr(np, "isnan") else surface.dtm.ravel()
        if len(valid) > 0:
            rows.append(["DTM (Depth)", f"{np.nanmin(valid):.2f}", f"{np.nanmax(valid):.2f}",
                          f"{np.nanmean(valid):.2f}", f"{np.nanstd(valid):.2f}"])

    if hasattr(surface, "density") and surface.density is not None:
        import numpy as np
        valid = surface.density[surface.density > 0]
        if len(valid) > 0:
            rows.append(["Density (pts/cell)", f"{np.min(valid):.0f}", f"{np.max(valid):.0f}",
                          f"{np.mean(valid):.1f}", f"{np.std(valid):.1f}"])

    if hasattr(surface, "std") and surface.std is not None:
        import numpy as np
        valid = surface.std[~np.isnan(surface.std)]
        if len(valid) > 0:
            rows.append(["Depth Std", f"{np.nanmin(valid):.3f}", f"{np.nanmax(valid):.3f}",
                          f"{np.nanmean(valid):.3f}", f"{np.nanstd(valid):.3f}"])

    if hasattr(surface, "tvu") and surface.tvu is not None:
        import numpy as np
        valid = surface.tvu[~np.isnan(surface.tvu)]
        if len(valid) > 0:
            rows.append(["TVU", f"{np.nanmin(valid):.3f}", f"{np.nanmax(valid):.3f}",
                          f"{np.nanmean(valid):.3f}", f"{np.nanstd(valid):.3f}"])

    if hasattr(surface, "thu") and surface.thu is not None:
        import numpy as np
        valid = surface.thu[~np.isnan(surface.thu)]
        if len(valid) > 0:
            rows.append(["THU", f"{np.nanmin(valid):.3f}", f"{np.nanmax(valid):.3f}",
                          f"{np.nanmean(valid):.3f}", f"{np.nanstd(valid):.3f}"])

    if rows:
        fmt_rows = [
            [Paragraph(r[0], styles["CellBold"])] + [Paragraph(v, styles["CellText"]) for v in r[1:]]
            for r in rows
        ]
        tbl = _make_table(["Surface", "Min", "Max", "Mean", "Std"], fmt_rows,
                          col_widths=[40 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm],
                          styles=styles)
        elements.append(tbl)
    else:
        elements.append(Paragraph("No surface data available.", styles["BodyText2"]))

    elements.append(Spacer(1, 6 * mm))


def _build_coverage(elements, styles, coverage_qc):
    """Section: Coverage Analysis."""
    if coverage_qc is None:
        return

    elements.append(Paragraph("4. Coverage Analysis", styles["SectionTitle"]))

    # Summary metrics
    metrics = []
    if hasattr(coverage_qc, "total_lines"):
        metrics.append(("Total Lines", str(coverage_qc.total_lines)))
    if hasattr(coverage_qc, "total_length_km"):
        metrics.append(("Total Length", f"{coverage_qc.total_length_km:.2f} km"))
    if hasattr(coverage_qc, "total_area_km2"):
        metrics.append(("Coverage Area", f"{coverage_qc.total_area_km2:.4f} km\u00b2"))
    if hasattr(coverage_qc, "num_gaps"):
        metrics.append(("Gaps Detected", str(coverage_qc.num_gaps)))
    if hasattr(coverage_qc, "gap_area_m2"):
        metrics.append(("Gap Area", f"{coverage_qc.gap_area_m2:.1f} m\u00b2"))
    if hasattr(coverage_qc, "mean_overlap_pct"):
        metrics.append(("Mean Overlap", f"{coverage_qc.mean_overlap_pct:.1f}%"))

    if metrics:
        rows = [
            [Paragraph(m[0], styles["CellBold"]), Paragraph(m[1], styles["CellText"])]
            for m in metrics
        ]
        tbl = _make_table(["Metric", "Value"], rows,
                          col_widths=[60 * mm, 100 * mm], styles=styles)
        elements.append(tbl)
        elements.append(Spacer(1, 4 * mm))

    # Line details
    if hasattr(coverage_qc, "lines") and coverage_qc.lines:
        elements.append(Paragraph("Line Details", styles["SubTitle"]))
        rows = []
        for line in coverage_qc.lines:
            rows.append([
                Paragraph(getattr(line, "filename", ""), styles["CellText"]),
                Paragraph(f"{getattr(line, 'num_pings', 0):,}", styles["CellText"]),
                Paragraph(f"{getattr(line, 'length_m', 0):.0f}", styles["CellText"]),
                Paragraph(f"{getattr(line, 'mean_depth_m', 0):.1f}", styles["CellText"]),
                Paragraph(f"{getattr(line, 'heading_deg', 0):.1f}", styles["CellText"]),
            ])
        if rows:
            tbl = _make_table(
                ["Filename", "Pings", "Length (m)", "Mean Depth (m)", "Heading (\u00b0)"],
                rows, col_widths=[55 * mm, 25 * mm, 25 * mm, 30 * mm, 25 * mm],
                styles=styles,
            )
            elements.append(tbl)

    # Check items
    items = _get_items(coverage_qc)
    if items:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph("Coverage Checks", styles["SubTitle"]))
        rows = [
            [Paragraph(i.get("name", ""), styles["CellText"]),
             _status_para(i.get("status", "N/A"), styles),
             Paragraph(i.get("detail", ""), styles["CellText"])]
            for i in items
        ]
        tbl = _make_table(["Check", "Status", "Detail"], rows,
                          col_widths=[45 * mm, 25 * mm, 90 * mm], styles=styles)
        elements.append(tbl)

    elements.append(Spacer(1, 6 * mm))


def _build_crossline(elements, styles, crossline_qc):
    """Section: Cross-line QC."""
    if crossline_qc is None:
        return

    elements.append(Paragraph("5. Cross-line QC", styles["SectionTitle"]))

    metrics = []
    if hasattr(crossline_qc, "num_intersections"):
        metrics.append(("Intersections", str(crossline_qc.num_intersections)))
    if hasattr(crossline_qc, "depth_diff_mean"):
        metrics.append(("Depth Diff Mean", f"{crossline_qc.depth_diff_mean:.3f} m"))
    if hasattr(crossline_qc, "depth_diff_std"):
        metrics.append(("Depth Diff Std", f"{crossline_qc.depth_diff_std:.3f} m"))
    if hasattr(crossline_qc, "depth_diff_max"):
        metrics.append(("Depth Diff Max", f"{crossline_qc.depth_diff_max:.3f} m"))
    if hasattr(crossline_qc, "depth_diff_rms"):
        metrics.append(("Depth Diff RMS", f"{crossline_qc.depth_diff_rms:.3f} m"))
    if hasattr(crossline_qc, "striping_detected"):
        metrics.append(("Striping Detected", "Yes" if crossline_qc.striping_detected else "No"))
    if hasattr(crossline_qc, "striping_amplitude") and crossline_qc.striping_detected:
        metrics.append(("Striping Amplitude", f"{crossline_qc.striping_amplitude:.3f} m"))

    if metrics:
        rows = [
            [Paragraph(m[0], styles["CellBold"]), Paragraph(m[1], styles["CellText"])]
            for m in metrics
        ]
        tbl = _make_table(["Metric", "Value"], rows,
                          col_widths=[60 * mm, 100 * mm], styles=styles)
        elements.append(tbl)

    # Check items
    items = _get_items(crossline_qc)
    if items:
        elements.append(Spacer(1, 4 * mm))
        rows = [
            [Paragraph(i.get("name", ""), styles["CellText"]),
             _status_para(i.get("status", "N/A"), styles),
             Paragraph(i.get("detail", ""), styles["CellText"])]
            for i in items
        ]
        tbl = _make_table(["Check", "Status", "Detail"], rows,
                          col_widths=[45 * mm, 25 * mm, 90 * mm], styles=styles)
        elements.append(tbl)

    elements.append(Spacer(1, 6 * mm))


def _build_iho_compliance(elements, styles, crossline_qc):
    """Section: IHO S-44 Compliance."""
    if crossline_qc is None:
        return

    elements.append(Paragraph("6. IHO S-44 Compliance", styles["SectionTitle"]))

    order = getattr(crossline_qc, "iho_order", "1a")
    pass_pct = getattr(crossline_qc, "iho_pass_pct", 0.0)
    verdict = getattr(crossline_qc, "iho_verdict", "N/A")

    rows = [
        [Paragraph("Survey Order", styles["CellBold"]),
         Paragraph(order.upper(), styles["CellText"])],
        [Paragraph("IHO Pass Rate", styles["CellBold"]),
         Paragraph(f"{pass_pct:.1f}%", styles["CellText"])],
        [Paragraph("IHO Verdict", styles["CellBold"]),
         _status_para(verdict, styles)],
    ]

    tbl = _make_table(["Parameter", "Value"], rows,
                      col_widths=[60 * mm, 100 * mm], styles=styles)
    elements.append(tbl)
    elements.append(Spacer(1, 6 * mm))


def _build_svp_summary(elements, styles, svp_qc):
    """Section: SVP Summary."""
    if svp_qc is None:
        return

    elements.append(Paragraph("7. SVP Summary", styles["SectionTitle"]))

    metrics = []
    if hasattr(svp_qc, "applied"):
        metrics.append(("SVP Applied", "Yes" if svp_qc.applied else "No"))
    if hasattr(svp_qc, "num_profiles"):
        metrics.append(("Number of Profiles", str(svp_qc.num_profiles)))
    if hasattr(svp_qc, "velocity_range") and svp_qc.velocity_range != (0.0, 0.0):
        vr = svp_qc.velocity_range
        metrics.append(("Velocity Range", f"{vr[0]:.1f} - {vr[1]:.1f} m/s"))
    if hasattr(svp_qc, "outer_beam_indicator"):
        metrics.append(("Outer Beam Indicator", svp_qc.outer_beam_indicator))

    if metrics:
        rows = [
            [Paragraph(m[0], styles["CellBold"]), Paragraph(m[1], styles["CellText"])]
            for m in metrics
        ]
        tbl = _make_table(["Parameter", "Value"], rows,
                          col_widths=[60 * mm, 100 * mm], styles=styles)
        elements.append(tbl)
        elements.append(Spacer(1, 4 * mm))

    # Profile summary
    if hasattr(svp_qc, "profiles_summary") and svp_qc.profiles_summary:
        elements.append(Paragraph("Profile Details", styles["SubTitle"]))
        rows = []
        for p in svp_qc.profiles_summary:
            rows.append([
                Paragraph(str(p.get("index", "")), styles["CellText"]),
                Paragraph(str(p.get("time", "")), styles["CellText"]),
                Paragraph(str(p.get("depth_range", "")), styles["CellText"]),
                Paragraph(str(p.get("num_points", "")), styles["CellText"]),
            ])
        if rows:
            tbl = _make_table(["#", "Time", "Depth Range", "Points"], rows,
                              col_widths=[15 * mm, 50 * mm, 50 * mm, 45 * mm],
                              styles=styles)
            elements.append(tbl)

    # Check items
    items = _get_items(svp_qc)
    if items:
        elements.append(Spacer(1, 4 * mm))
        rows = [
            [Paragraph(i.get("name", ""), styles["CellText"]),
             _status_para(i.get("status", "N/A"), styles),
             Paragraph(i.get("detail", ""), styles["CellText"])]
            for i in items
        ]
        tbl = _make_table(["Check", "Status", "Detail"], rows,
                          col_widths=[45 * mm, 25 * mm, 90 * mm], styles=styles)
        elements.append(tbl)

    elements.append(Spacer(1, 6 * mm))


# ── Public API ─────────────────────────────────────────────────

def generate_pdf_report(
    qc_result,
    output_path: str | Path,
    project_name: str = "",
    vessel_name: str = "",
) -> None:
    """Generate a professional PDF report from MBES QC results.

    Args:
        qc_result: FullQcResult instance (or dict-like with .as_dict()).
        output_path: Path to save the PDF file.
        project_name: Optional project name for cover page.
        vessel_name: Optional vessel name for cover page.
    """
    if not HAS_REPORTLAB:
        print("  [SKIP] reportlab not installed — pip install reportlab")
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = _get_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        topMargin=35 * mm,
        bottomMargin=25 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title="MBES QC Report",
        author="GeoView MBES QC",
    )

    elements: list[Any] = []

    # ── Cover Page ──
    _build_cover(elements, styles, qc_result, project_name, vessel_name)

    # ── 1. Project Summary ──
    _build_project_summary(elements, styles, qc_result)

    # ── 2. File QC ──
    file_qc = getattr(qc_result, "file_qc", None)
    _build_file_qc(elements, styles, file_qc)

    # ── 3. Surface Statistics ──
    surface = getattr(qc_result, "surface", None)
    _build_surface_stats(elements, styles, surface)

    # ── 4. Coverage ──
    coverage = getattr(qc_result, "coverage_qc", None)
    _build_coverage(elements, styles, coverage)

    # ── 5. Cross-line QC ──
    crossline = getattr(qc_result, "crossline_qc", None)
    _build_crossline(elements, styles, crossline)

    # ── 6. IHO Compliance ──
    _build_iho_compliance(elements, styles, crossline)

    # ── 7. SVP Summary ──
    svp = getattr(qc_result, "svp_qc", None)
    _build_svp_summary(elements, styles, svp)

    # Build PDF
    doc.build(elements, onFirstPage=_header_footer, onLaterPages=_header_footer)
    print(f"  [OK] PDF report saved: {output_path}")

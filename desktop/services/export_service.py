"""MBESQC ExportService -- Excel/Word/PPT report generation worker."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from desktop.services.data_service import DataService


class ExportWorker(QObject):
    """Background worker for report generation."""

    finished = Signal(str)   # output path
    error = Signal(str)      # error message

    def __init__(self, project_id: int, fmt: str, output_path: str):
        super().__init__()
        self._project_id = project_id
        self._fmt = fmt
        self._output_path = output_path

    @Slot()
    def run(self):
        try:
            if self._fmt == "excel":
                self._export_excel()
            elif self._fmt == "word":
                self._export_word()
            elif self._fmt == "ppt":
                self._export_ppt()
            else:
                self.error.emit(f"Unsupported format: {self._fmt}")
                return

            self.finished.emit(self._output_path)

        except Exception as e:
            self.error.emit(str(e))

    def _export_excel(self):
        """Generate Excel report with charts embedded."""
        import openpyxl
        from openpyxl.styles import Font as XlFont, PatternFill, Border, Side
        from openpyxl.drawing.image import Image as XlImage

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "QC Summary"

        project = DataService.get_project(self._project_id)
        results = DataService.get_project_qc_results(self._project_id)

        # Styles
        h_fill = PatternFill(start_color="1A2236", end_color="1A2236", fill_type="solid")
        h_font = XlFont(name="Pretendard", size=11, bold=True, color="FFFFFF")
        body_font = XlFont(name="Pretendard", size=11)
        title_font = XlFont(name="Pretendard", size=16, bold=True, color="10B981")
        thin = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )

        # Title
        ws.merge_cells("A1:F1")
        ws["A1"] = f"MBES QC Report -- {project['name']}" if project else "MBES QC Report"
        ws["A1"].font = title_font

        ws["A2"] = f"Vessel: {project.get('vessel', '')}" if project else ""
        ws["A2"].font = XlFont(name="Pretendard", size=11, color="6B7280")
        ws["A3"] = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ws["A3"].font = XlFont(name="Pretendard", size=11, color="6B7280")

        # Results table
        row = 5
        headers = ["File", "Score", "Grade", "Status", "Analyzed"]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = h_font
            cell.fill = h_fill
            cell.border = thin

        done_results = [r for r in results if r.get("status") == "done"]
        for i, r in enumerate(done_results, row + 1):
            f = DataService.get_file(r.get("file_id", 0))
            filename = f["filename"] if f else "---"

            ws.cell(row=i, column=1, value=filename).font = body_font
            ws.cell(row=i, column=2, value=f"{r.get('score', 0):.1f}").font = body_font
            ws.cell(row=i, column=3, value=r.get("grade", "")).font = body_font
            ws.cell(row=i, column=4, value=r.get("status", "")).font = body_font
            ws.cell(row=i, column=5, value=r.get("finished_at", "")[:19]).font = body_font

            for c in range(1, 6):
                ws.cell(row=i, column=c).border = thin

        ws.column_dimensions["A"].width = 40
        ws.column_dimensions["B"].width = 12
        ws.column_dimensions["C"].width = 10
        ws.column_dimensions["D"].width = 12
        ws.column_dimensions["E"].width = 22

        # ── Charts sheet ──
        temp_files = []
        try:
            if done_results:
                latest = done_results[0]
                result_json = latest.get("result_json", "{}")
                try:
                    data = json.loads(result_json)
                except (json.JSONDecodeError, TypeError):
                    data = {}

                if data:
                    from desktop.services.chart_renderer import render_qc_radar
                    ws_charts = wb.create_sheet("Charts")
                    row_pos = 1

                    # QC Radar
                    scores = {}
                    for k in ("file", "vessel", "offset", "motion", "svp",
                              "coverage", "crossline", "surface"):
                        sec = data.get(k, {})
                        if sec:
                            v = sec.get("verdict", "N/A").upper()
                            if v == "PASS": scores[k.title()] = 95
                            elif v == "WARNING": scores[k.title()] = 60
                            elif v == "FAIL": scores[k.title()] = 20

                    if scores:
                        png = render_qc_radar(scores, "QC Score Breakdown")
                        tmp = tempfile.NamedTemporaryFile(
                            delete=False, suffix=".png", prefix="mbesqc_radar_")
                        tmp.write(png)
                        tmp.close()
                        temp_files.append(tmp.name)

                        ws_charts.cell(row=row_pos, column=1, value="QC Score Breakdown")
                        ws_charts.cell(row=row_pos, column=1).font = XlFont(
                            name="Pretendard", size=12, bold=True)
                        row_pos += 1

                        img = XlImage(tmp.name)
                        img.width = 500
                        img.height = 500
                        ws_charts.add_image(img, f"A{row_pos}")
                        row_pos += 28

        except Exception:
            pass  # Charts are optional; don't fail export

        wb.save(self._output_path)

        for tmp_path in temp_files:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _export_word(self):
        """Generate Word report with python-docx."""
        from docx import Document
        from docx.shared import Inches, Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        doc = Document()

        project = DataService.get_project(self._project_id)
        results = DataService.get_project_qc_results(self._project_id)

        # Title
        title = doc.add_heading(level=0)
        run = title.add_run(f"MBES QC Report")
        run.font.size = Pt(24)

        # Project info
        doc.add_paragraph(f"Project: {project['name']}" if project else "")
        doc.add_paragraph(f"Vessel: {project.get('vessel', '')}" if project else "")
        doc.add_paragraph(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        doc.add_paragraph("")

        # Summary table
        done_results = [r for r in results if r.get("status") == "done"]
        if done_results:
            doc.add_heading("QC Results Summary", level=1)
            table = doc.add_table(rows=1 + len(done_results), cols=4)
            table.style = "Table Grid"

            headers = ["File", "Score", "Grade", "Status"]
            for i, h in enumerate(headers):
                table.cell(0, i).text = h

            for i, r in enumerate(done_results):
                f = DataService.get_file(r.get("file_id", 0))
                table.cell(i + 1, 0).text = f["filename"] if f else "---"
                table.cell(i + 1, 1).text = f"{r.get('score', 0):.1f}"
                table.cell(i + 1, 2).text = r.get("grade", "")
                table.cell(i + 1, 3).text = r.get("status", "")

        doc.save(self._output_path)

    def _export_ppt(self):
        """Generate PPT DQR with python-pptx."""
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        project = DataService.get_project(self._project_id)
        results = DataService.get_project_qc_results(self._project_id)

        # Title slide
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(0x0A, 0x0E, 0x17)

        txBox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11), Inches(2))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        p.text = "MBES Data Quality Report"
        p.font.size = Pt(36)
        p.font.color.rgb = RGBColor(0x10, 0xB9, 0x81)
        p.font.bold = True

        p2 = tf.add_paragraph()
        p2.text = f"{project['name']} | {project.get('vessel', '')}" if project else ""
        p2.font.size = Pt(18)
        p2.font.color.rgb = RGBColor(0xD1, 0xD5, 0xDB)

        p3 = tf.add_paragraph()
        p3.text = datetime.now().strftime("%Y-%m-%d")
        p3.font.size = Pt(14)
        p3.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)

        # Summary slide
        done_results = [r for r in results if r.get("status") == "done"]
        if done_results:
            slide2 = prs.slides.add_slide(prs.slide_layouts[6])
            slide2.background.fill.solid()
            slide2.background.fill.fore_color.rgb = RGBColor(0x0A, 0x0E, 0x17)

            txBox2 = slide2.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(1))
            tf2 = txBox2.text_frame
            p = tf2.paragraphs[0]
            p.text = "QC Results Summary"
            p.font.size = Pt(24)
            p.font.color.rgb = RGBColor(0xF9, 0xFA, 0xFB)
            p.font.bold = True

            # Results as text list
            txBox3 = slide2.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(12), Inches(5))
            tf3 = txBox3.text_frame

            for r in done_results:
                f = DataService.get_file(r.get("file_id", 0))
                filename = f["filename"] if f else "---"
                score = r.get("score", 0)
                grade = r.get("grade", "")

                p = tf3.add_paragraph()
                p.text = f"{filename}  |  {score:.1f}  |  {grade}"
                p.font.size = Pt(14)

                if score >= 90:
                    p.font.color.rgb = RGBColor(0x10, 0xB9, 0x81)
                elif score >= 75:
                    p.font.color.rgb = RGBColor(0x3B, 0x82, 0xF6)
                elif score >= 60:
                    p.font.color.rgb = RGBColor(0xF5, 0x9E, 0x0B)
                else:
                    p.font.color.rgb = RGBColor(0xEF, 0x44, 0x44)

        prs.save(self._output_path)

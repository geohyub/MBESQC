"""DQR (Daily QC Report) PPT Generator.

Generates a 10-slide PowerPoint presentation matching GeoView DQR format:
  Slide 1: Cover page
  Slide 2: Project info
  Slide 3: Acquisition Settings (MBES model, frequency, SVP)
  Slide 4: Line Information (area, acquired km)
  Slide 5: Track Plot
  Slide 6: Bathymetric Average Surface
  Slide 7: TVU Surface
  Slide 8: THU Surface
  Slide 9: Density Surface
  Slide 10: Backscatter Surface
"""

from __future__ import annotations

import datetime
from pathlib import Path

from pds_toolkit.models import GsfFile, HvfFile, PdsMetadata

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    HAS_PPTX = True
except ImportError:
    HAS_PPTX = False


def generate_dqr_ppt(
    output_path: str | Path,
    pds_meta: PdsMetadata | None = None,
    gsf_main: GsfFile | None = None,
    hvf: HvfFile | None = None,
    surface_dir: str | Path | None = None,
    project_name: str = "",
    survey_area: str = "",
    total_line_km: float = 0.0,
    qc_results: dict | None = None,
) -> None:
    """Generate DQR PowerPoint report.

    If surface GeoTIFF images exist in surface_dir, they are embedded.
    Otherwise, placeholder text is used.
    """
    if not HAS_PPTX:
        print("  [SKIP] python-pptx not installed")
        return

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Colors
    NAVY = RGBColor(0x2F, 0x54, 0x96)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    GRAY = RGBColor(0x80, 0x80, 0x80)

    # ── Slide 1: Cover ──────────────────────────────────────
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    _add_textbox(slide, "Daily QC Report - MBES",
                 Inches(1), Inches(2), Inches(11), Inches(1.5),
                 font_size=36, bold=True, color=NAVY)
    _add_textbox(slide, project_name or (pds_meta.project_name if pds_meta else "MBES Survey"),
                 Inches(1), Inches(3.5), Inches(11), Inches(0.8),
                 font_size=24, color=GRAY)
    _add_textbox(slide, datetime.datetime.now().strftime("%Y-%m-%d"),
                 Inches(1), Inches(4.5), Inches(11), Inches(0.6),
                 font_size=18, color=GRAY)
    _add_textbox(slide, "GEOVIEW CO., Ltd",
                 Inches(1), Inches(6), Inches(11), Inches(0.6),
                 font_size=14, color=GRAY)

    # ── Slide 2: Project Info ───────────────────────────────
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_textbox(slide, "Project Information",
                 Inches(0.5), Inches(0.3), Inches(12), Inches(0.8),
                 font_size=28, bold=True, color=NAVY)

    info_text = []
    if pds_meta:
        info_text.append(f"Vessel: {pds_meta.vessel_name}")
        info_text.append(f"Survey Type: {pds_meta.survey_type}")
        info_text.append(f"Coordinate System: {pds_meta.coord_system_group} / {pds_meta.coord_system_name}")
        info_text.append(f"Units: {pds_meta.system_units}")
    if hvf:
        for s in hvf.sensors:
            if "DepthSensor" in s.name:
                info_text.append(f"Transducer Offset: X={s.x:.3f} Y={s.y:.3f} Z={s.z:.3f}")
                info_text.append(f"Mount Angle: P={s.pitch:.3f} R={s.roll:.3f} H={s.heading:.3f}")
                break
    _add_textbox(slide, "\n".join(info_text),
                 Inches(0.5), Inches(1.5), Inches(12), Inches(5),
                 font_size=16, color=RGBColor(0x33, 0x33, 0x33))

    # ── Slide 3: Acquisition Settings ───────────────────────
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_numbered_header(slide, "01", "Acquisition Settings")

    settings = []
    if pds_meta:
        geom = pds_meta.sections.get("GEOMETRY", {})
        for k, v in pds_meta.sections.get("GEOMETRY", {}).items():
            if k.startswith("Offset(") and "T50" in v:
                settings.append(f"Model: {v.split(',')[0]}")

    if gsf_main and gsf_main.pings:
        p0 = gsf_main.pings[0]
        settings.append(f"Beams: {p0.num_beams}")
        if p0.depth is not None:
            settings.append(f"Depth Range: {p0.depth.min():.1f} ~ {p0.depth.max():.1f} m")

    if gsf_main and gsf_main.svp_profiles:
        svp = gsf_main.svp_profiles[0]
        if svp.num_points > 0:
            settings.append(f"SVS: {svp.sound_velocity[0]:.1f} m/s")
            settings.append(f"SVP Points: {svp.num_points}")

    _add_textbox(slide, "\n".join(settings) if settings else "No acquisition data available",
                 Inches(0.5), Inches(1.5), Inches(12), Inches(5),
                 font_size=16, color=RGBColor(0x33, 0x33, 0x33))

    # ── Slide 4: Line Information ───────────────────────────
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_numbered_header(slide, "02", "Line Information")

    line_info = [
        f"Survey Area: {survey_area or 'N/A'}",
        f"Acquired Lines (km): {total_line_km:.1f}",
    ]
    if gsf_main and gsf_main.summary:
        s = gsf_main.summary
        line_info.append(f"Depth Range: {s.min_depth:.1f} ~ {s.max_depth:.1f} m")
        line_info.append(f"Lat: {s.min_latitude:.5f} ~ {s.max_latitude:.5f}")
        line_info.append(f"Lon: {s.min_longitude:.5f} ~ {s.max_longitude:.5f}")

    _add_textbox(slide, "\n".join(line_info),
                 Inches(0.5), Inches(1.5), Inches(12), Inches(5),
                 font_size=16, color=RGBColor(0x33, 0x33, 0x33))

    # ── Slide 5: Track Plot ─────────────────────────────────
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_numbered_header(slide, "03", "Track Plot")
    _add_textbox(slide, "[Track plot image - insert from survey software]",
                 Inches(1), Inches(2), Inches(11), Inches(4),
                 font_size=14, color=GRAY, italic=True)

    # ── Slides 6-10: Surface images ─────────────────────────
    surface_slides = [
        ("04", "Bathymetric Average Values Surface", "DTM"),
        ("05", "Bathymetric TVU Surface", "TVU"),
        ("06", "Bathymetric THU Surface", "THU"),
        ("07", "Bathymetric Density Surface", "Density"),
        ("08", "Backscatter Surface", "Backscatter"),
    ]

    for num, title, surf_name in surface_slides:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _add_numbered_header(slide, num, title)

        # Try to embed surface image (render GeoTIFF to PNG first)
        image_embedded = False
        if surface_dir:
            png_path = _render_surface_png(Path(surface_dir), surf_name)
            if png_path and png_path.exists():
                try:
                    slide.shapes.add_picture(
                        str(png_path), Inches(1), Inches(1.5),
                        Inches(11), Inches(5),
                    )
                    image_embedded = True
                except Exception:
                    pass

        if not image_embedded:
            _add_textbox(slide, f"[{title} image - insert from CARIS export]",
                         Inches(1), Inches(2), Inches(11), Inches(4),
                         font_size=14, color=GRAY, italic=True)

        # Add QC comment if available
        if qc_results and surf_name in ("DTM", "TVU", "THU", "Density"):
            _add_textbox(slide, "Comment:",
                         Inches(0.5), Inches(6.5), Inches(12), Inches(0.5),
                         font_size=12, bold=True, color=RGBColor(0x33, 0x33, 0x33))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))


# ── Helpers ─────────────────────────────────────────────────────


def _render_surface_png(surface_dir: Path, surf_name: str) -> Path | None:
    """Render a GeoTIFF surface to a colored PNG for PPT embedding."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        for ext in [".tiff", ".tif"]:
            tiff_path = surface_dir / f"{surf_name}{ext}"
            if tiff_path.exists():
                try:
                    import rasterio
                    with rasterio.open(str(tiff_path)) as src:
                        data = src.read(1)
                except ImportError:
                    return None

                data = np.where(data == -9999, np.nan, data)
                if np.all(np.isnan(data)):
                    return None

                fig, ax = plt.subplots(figsize=(12, 8))
                im = ax.imshow(data, cmap="viridis", aspect="auto")
                plt.colorbar(im, ax=ax, label=surf_name)
                ax.set_title(surf_name)
                ax.set_xlabel("Column")
                ax.set_ylabel("Row")

                png_path = surface_dir / f"{surf_name}.png"
                fig.savefig(str(png_path), dpi=150, bbox_inches="tight")
                plt.close(fig)
                return png_path

        # Also check for pre-existing PNG
        png_path = surface_dir / f"{surf_name}.png"
        if png_path.exists():
            return png_path

    except Exception:
        pass

    return None


def _add_textbox(slide, text, left, top, width, height,
                 font_size=14, bold=False, italic=False,
                 color=RGBColor(0, 0, 0)):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.italic = italic
    p.font.color.rgb = color


def _add_numbered_header(slide, number, title):
    NAVY = RGBColor(0x2F, 0x54, 0x96)
    _add_textbox(slide, title, Inches(0.5), Inches(0.3), Inches(12), Inches(0.8),
                 font_size=28, bold=True, color=NAVY)
    _add_textbox(slide, number, Inches(11.5), Inches(0.3), Inches(1.5), Inches(0.8),
                 font_size=24, bold=True, color=RGBColor(0x99, 0x99, 0x99))

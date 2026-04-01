"""DQR (Daily QC Report) PPT Generator.

Generates an 11-slide PowerPoint presentation — clean, professional light theme.
Designed for projection and printing.

  Slide 1: Cover (navy gradient + white text)
  Slide 2: Project Information (key-value table)
  Slide 3: Acquisition Settings
  Slide 4: Line Information
  Slide 5: Track Plot
  Slide 6-11: Surface images (Depth, StdDev, TVU, THU, Density, Backscatter)
"""

from __future__ import annotations

import datetime
from pathlib import Path

from pds_toolkit.models import GsfFile, HvfFile, PdsMetadata

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.oxml.ns import qn
    HAS_PPTX = True
except ImportError:
    HAS_PPTX = False


# ── Color Palette (Light Professional) ─────────────────────────

_WHITE      = RGBColor(0xFF, 0xFF, 0xFF)
_BG         = RGBColor(0xFA, 0xFA, 0xFC)   # off-white slide bg
_NAVY       = RGBColor(0x1B, 0x2A, 0x4A)   # primary heading
_NAVY_LIGHT = RGBColor(0x2C, 0x3E, 0x6B)   # cover gradient
_BLUE       = RGBColor(0x2D, 0x6A, 0x9F)   # section number badge
_TEAL       = RGBColor(0x0E, 0x9A, 0x83)   # accent / GeoView brand
_TEXT       = RGBColor(0x1F, 0x2D, 0x3D)   # body text
_TEXT_SEC   = RGBColor(0x5A, 0x6A, 0x7E)   # secondary text
_TEXT_MUTED = RGBColor(0x8E, 0x99, 0xA4)   # placeholder
_ROW_EVEN   = RGBColor(0xF4, 0xF6, 0xF8)   # table even row
_ROW_ODD    = RGBColor(0xFF, 0xFF, 0xFF)   # table odd row
_ROW_HEADER = RGBColor(0xE8, 0xED, 0xF2)   # table header
_BORDER     = RGBColor(0xDE, 0xE2, 0xE6)   # subtle border
_ORANGE     = RGBColor(0xE8, 0x7C, 0x2A)   # warning
_RED        = RGBColor(0xD9, 0x3B, 0x3B)   # error


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
    if not HAS_PPTX:
        return

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    _slide_cover(prs, project_name, pds_meta)
    _slide_project_info(prs, pds_meta, hvf)
    _slide_acquisition(prs, pds_meta, gsf_main)
    _slide_line_info(prs, gsf_main, survey_area, total_line_km)
    _slide_track_plot(prs, surface_dir, gsf_main)

    surface_slides = [
        ("04", "Bathymetric Average Values Surface", "Depth"),
        ("05", "Bathymetric Standard Deviation Surface", "Std_Dev"),
        ("06", "Bathymetric TVU Surface", "TVU"),
        ("07", "Bathymetric THU Surface", "THU"),
        ("08", "Bathymetric Density Surface", "Density"),
        ("09", "Backscatter Surface", "Backscatter"),
    ]
    for num, title, surf_name in surface_slides:
        _slide_surface(prs, num, title, surf_name, surface_dir, qc_results)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))


# ── Slide Builders ─────────────────────────────────────────────


def _slide_cover(prs, project_name, pds_meta):
    slide = _blank_slide(prs)

    # Navy background for cover only
    _set_bg(slide, _NAVY)

    # Left accent bar
    _rect(slide, Inches(0), Inches(0), Inches(0.12), Inches(7.5), _TEAL)

    # Top thin line
    _rect(slide, Inches(0.12), Inches(0), prs.slide_width, Inches(0.04), _TEAL)

    # Report type label
    _text(slide, "DAILY QC REPORT",
          Inches(1.2), Inches(1.5), Inches(10), Inches(0.5),
          size=14, color=_TEAL, bold=True, spacing=4)

    # Main title
    _text(slide, "Multibeam Echosounder",
          Inches(1.2), Inches(2.3), Inches(10), Inches(1.2),
          size=42, color=_WHITE, bold=True)

    # Project name
    name = project_name or (pds_meta.project_name if pds_meta else "MBES Survey")
    _text(slide, name,
          Inches(1.2), Inches(3.8), Inches(10), Inches(0.8),
          size=24, color=RGBColor(0xB0, 0xC4, 0xDE))

    # Divider
    _rect(slide, Inches(1.2), Inches(5.0), Inches(3), Inches(0.04), _TEAL)

    # Date
    _text(slide, datetime.datetime.now().strftime("%Y. %m. %d"),
          Inches(1.2), Inches(5.4), Inches(4), Inches(0.5),
          size=16, color=RGBColor(0x8A, 0x9B, 0xB5))

    # Company
    _text(slide, "GEOVIEW CO., Ltd.",
          Inches(1.2), Inches(6.1), Inches(4), Inches(0.5),
          size=13, color=RGBColor(0x6E, 0x82, 0xA0))


def _slide_project_info(prs, pds_meta, hvf):
    slide = _blank_slide(prs)
    _set_bg(slide, _BG)
    _header_bar(slide, "Project Information", prs.slide_width)
    _footer_bar(slide, prs.slide_width, page=2)

    rows = []
    if pds_meta:
        rows.append(("Vessel", pds_meta.vessel_name or "-"))
        rows.append(("Survey Type", pds_meta.survey_type or "-"))
        rows.append(("Coordinate System",
                      f"{pds_meta.coord_system_group} / {pds_meta.coord_system_name}"))
        rows.append(("Units", pds_meta.system_units or "-"))

    if hvf:
        for s in hvf.sensors:
            if "DepthSensor" in s.name or "Multibeam" in s.name:
                rows.append(("Transducer Offset",
                              f"X = {s.x:.3f}   Y = {s.y:.3f}   Z = {s.z:.3f}"))
                rows.append(("Mount Angles",
                              f"Pitch = {s.pitch:.3f}   Roll = {s.roll:.3f}   Heading = {s.heading:.3f}"))
                break
        sensor_names = [s.name for s in hvf.sensors]
        if sensor_names:
            rows.append(("Registered Sensors", ", ".join(sensor_names)))

    if not rows:
        rows.append(("Status", "No project metadata available"))

    _kv_table(slide, rows, Inches(0.8), Inches(1.8), Inches(11.7))


def _slide_acquisition(prs, pds_meta, gsf_main):
    slide = _blank_slide(prs)
    _set_bg(slide, _BG)
    _header_bar(slide, "Acquisition Settings", prs.slide_width, number="01")
    _footer_bar(slide, prs.slide_width, page=3)

    rows = []
    if pds_meta:
        geom = pds_meta.sections.get("GEOMETRY", {})
        for k, v in geom.items():
            if k.startswith("Offset(") and "T50" in v:
                rows.append(("MBES Model", v.split(",")[0]))

    if gsf_main and gsf_main.pings:
        p0 = gsf_main.pings[0]
        rows.append(("Number of Beams", str(p0.num_beams)))
        if p0.depth is not None:
            rows.append(("Depth Range (Ping 1)",
                          f"{p0.depth.min():.1f} ~ {p0.depth.max():.1f} m"))

    if gsf_main and gsf_main.svp_profiles:
        svp = gsf_main.svp_profiles[0]
        if svp.num_points > 0:
            rows.append(("Surface Sound Velocity", f"{svp.sound_velocity[0]:.1f} m/s"))
            rows.append(("SVP Profile Points", str(svp.num_points)))
            rows.append(("SVP Max Depth", f"{svp.depth[-1]:.1f} m"))

    if not rows:
        rows.append(("Status", "No acquisition data available"))

    _kv_table(slide, rows, Inches(0.8), Inches(1.8), Inches(11.7))


def _slide_line_info(prs, gsf_main, survey_area, total_line_km):
    slide = _blank_slide(prs)
    _set_bg(slide, _BG)
    _header_bar(slide, "Line Information", prs.slide_width, number="02")
    _footer_bar(slide, prs.slide_width, page=4)

    rows = [
        ("Survey Area", survey_area or "N/A"),
        ("Acquired Lines", f"{total_line_km:.1f} km"),
    ]
    if gsf_main and gsf_main.summary:
        s = gsf_main.summary
        rows.append(("Depth Range", f"{s.min_depth:.1f} ~ {s.max_depth:.1f} m"))
        rows.append(("Latitude", f"{s.min_latitude:.6f} ~ {s.max_latitude:.6f}"))
        rows.append(("Longitude", f"{s.min_longitude:.6f} ~ {s.max_longitude:.6f}"))
        start_t = getattr(s, "start_time", None) or getattr(s, "min_time", None)
        end_t = getattr(s, "end_time", None) or getattr(s, "max_time", None)
        if start_t and end_t:
            rows.append(("Time Span", f"{start_t} ~ {end_t}"))

    _kv_table(slide, rows, Inches(0.8), Inches(1.8), Inches(11.7))


def _slide_track_plot(prs, surface_dir, gsf_main=None):
    slide = _blank_slide(prs)
    _set_bg(slide, _BG)
    _header_bar(slide, "Track Plot", prs.slide_width, number="03")
    _footer_bar(slide, prs.slide_width, page=5)

    embedded = False

    # 1. Check for existing track plot image
    if surface_dir:
        for name in ["trackplot", "track_plot", "Track", "navigation"]:
            for ext in [".png", ".jpg", ".jpeg"]:
                p = Path(surface_dir) / f"{name}{ext}"
                if p.exists():
                    _image_frame(slide, p,
                                 Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.2))
                    embedded = True
                    break
            if embedded:
                break

    # 2. Auto-generate from GSF lat/lon
    if not embedded and gsf_main and gsf_main.pings:
        track_png = _render_track_plot(gsf_main, Path(surface_dir) if surface_dir else None)
        if track_png and track_png.exists():
            _image_frame(slide, track_png,
                         Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.2))
            embedded = True

    if not embedded:
        _placeholder(slide, "Track Plot",
                     "CARIS Export 또는 측량 소프트웨어에서 생성된 항적 이미지를 삽입하세요")


def _slide_surface(prs, number, title, surf_name, surface_dir, qc_results):
    slide = _blank_slide(prs)
    _set_bg(slide, _BG)
    page = int(number) + 2
    _header_bar(slide, title, prs.slide_width, number=number)
    _footer_bar(slide, prs.slide_width, page=page)

    embedded = False
    if surface_dir:
        png_path = _render_surface_png(Path(surface_dir), surf_name)
        if png_path and png_path.exists():
            _image_frame(slide, png_path,
                         Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.0))
            embedded = True

    if not embedded:
        _placeholder(slide, title,
                     "CARIS RenderRaster로 생성된 서페이스 이미지를 삽입하세요")

    # Comment line
    _text(slide, "Comment :",
          Inches(0.8), Inches(6.85), Inches(1.5), Inches(0.35),
          size=10, bold=True, color=_TEXT_SEC)
    _rect(slide, Inches(2.3), Inches(6.88), Inches(10.2), Inches(0.01), _BORDER)


# ── Layout Components ──────────────────────────────────────────


def _blank_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _set_bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _header_bar(slide, title, slide_width, number=None):
    """White header area with navy title and teal underline."""
    # White header bg
    _rect(slide, Inches(0), Inches(0), slide_width, Inches(1.3), _WHITE)
    # Teal accent underline
    _rect(slide, Inches(0), Inches(1.28), slide_width, Inches(0.04), _TEAL)

    # Title
    _text(slide, title,
          Inches(0.8), Inches(0.35), Inches(9), Inches(0.7),
          size=24, bold=True, color=_NAVY)

    # Number badge (right side, rounded bg)
    if number:
        # Badge background
        badge = slide.shapes.add_shape(
            5,  # ROUNDED_RECTANGLE
            Inches(11.6), Inches(0.35), Inches(1.0), Inches(0.55),
        )
        badge.fill.solid()
        badge.fill.fore_color.rgb = _BLUE
        badge.line.fill.background()

        _text(slide, number,
              Inches(11.6), Inches(0.38), Inches(1.0), Inches(0.5),
              size=18, bold=True, color=_WHITE, align=PP_ALIGN.CENTER)


def _footer_bar(slide, slide_width, page=1):
    """Subtle footer with company name and page number."""
    y = Inches(7.15)
    _rect(slide, Inches(0), y, slide_width, Inches(0.01), _BORDER)
    _text(slide, "GEOVIEW CO., Ltd.  |  Daily QC Report",
          Inches(0.8), Inches(7.18), Inches(6), Inches(0.3),
          size=8, color=_TEXT_MUTED)
    _text(slide, str(page),
          Inches(12.0), Inches(7.18), Inches(0.8), Inches(0.3),
          size=8, color=_TEXT_MUTED, align=PP_ALIGN.RIGHT)


def _kv_table(slide, rows, left, top, width):
    """Professional key-value table with alternating row colors and auto-fit text."""
    key_w = 3.2
    width_in = width / 914400
    val_w = width_in - key_w

    for i, (key, val) in enumerate(rows):
        # Auto row height: taller for long values
        val_len = len(val)
        if val_len > 80:
            row_h = 0.75
            val_size = 10
        elif val_len > 50:
            row_h = 0.60
            val_size = 11
        else:
            row_h = 0.48
            val_size = 12

        y = top + Inches(sum(
            0.75 if len(rows[j][1]) > 80 else 0.60 if len(rows[j][1]) > 50 else 0.48
            for j in range(i)
        ))
        bg = _ROW_EVEN if i % 2 == 0 else _ROW_ODD

        # Row background
        row_shape = slide.shapes.add_shape(
            5,  # ROUNDED_RECTANGLE
            left, y, Inches(width_in), Inches(row_h - 0.04),
        )
        row_shape.fill.solid()
        row_shape.fill.fore_color.rgb = bg
        row_shape.line.color.rgb = _BORDER
        row_shape.line.width = Pt(0.5)

        # Key (left, bold, navy)
        _text(slide, key,
              left + Inches(0.4), y + Inches(0.06),
              Inches(key_w), Inches(row_h - 0.12),
              size=12, bold=True, color=_NAVY)

        # Value (right, auto-sized, word-wrapped)
        _text(slide, val,
              left + Inches(key_w + 0.3), y + Inches(0.06),
              Inches(val_w - 0.3), Inches(row_h - 0.12),
              size=val_size, color=_TEXT)


def _image_frame(slide, image_path, left, top, max_w, max_h):
    """Add image preserving aspect ratio, centered within the available area."""
    from PIL import Image as PILImage
    try:
        with PILImage.open(str(image_path)) as img:
            img_w, img_h = img.size
    except Exception:
        img_w, img_h = 1600, 900  # fallback 16:9

    # Fit within max bounds preserving aspect ratio
    aspect = img_w / img_h
    max_w_in = max_w / 914400  # Emu to inches
    max_h_in = max_h / 914400

    if aspect > (max_w_in / max_h_in):
        # Image is wider → fit to width
        w = max_w_in
        h = w / aspect
    else:
        # Image is taller → fit to height
        h = max_h_in
        w = h * aspect

    # Center within available area
    x = (left / 914400) + (max_w_in - w) / 2
    y = (top / 914400) + (max_h_in - h) / 2

    # Border frame
    border = slide.shapes.add_shape(
        5, Inches(x - 0.03), Inches(y - 0.03),
        Inches(w + 0.06), Inches(h + 0.06),
    )
    border.fill.solid()
    border.fill.fore_color.rgb = _WHITE
    border.line.color.rgb = _BORDER
    border.line.width = Pt(1)

    slide.shapes.add_picture(str(image_path),
                              Inches(x), Inches(y), Inches(w), Inches(h))


def _placeholder(slide, title, hint):
    """Dashed border placeholder for missing images."""
    frame = slide.shapes.add_shape(
        5,  # ROUNDED_RECTANGLE
        Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.2),
    )
    frame.fill.solid()
    frame.fill.fore_color.rgb = _WHITE
    frame.line.color.rgb = _BORDER
    frame.line.width = Pt(1.5)
    frame.line.dash_style = 2  # DASH

    _text(slide, title,
          Inches(4), Inches(3.4), Inches(5.5), Inches(0.5),
          size=16, bold=True, color=_TEXT_MUTED, align=PP_ALIGN.CENTER)
    _text(slide, hint,
          Inches(3), Inches(4.0), Inches(7.5), Inches(0.5),
          size=11, color=_TEXT_MUTED, align=PP_ALIGN.CENTER, italic=True)


# ── Primitives ─────────────────────────────────────────────────


def _rect(slide, left, top, width, height, color):
    shape = slide.shapes.add_shape(1, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def _text(slide, text, left, top, width, height,
          size=14, bold=False, italic=False, color=_TEXT,
          align=PP_ALIGN.LEFT, spacing=None):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(size)
    p.font.bold = bold
    p.font.italic = italic
    p.font.color.rgb = color
    p.font.name = "Pretendard"
    p.alignment = align
    if spacing:
        p.font._element.attrib[qn("a:spc")] = str(spacing * 100)


# ── Surface Image Finder ───────────────────────────────────────


def _render_surface_png(surface_dir: Path, surf_name: str) -> Path | None:
    """Find or render a surface image for embedding."""
    name_lower = surf_name.lower()

    # Check for pre-existing images
    for pattern in [surf_name, f"surface_{name_lower}", name_lower]:
        for ext in [".png", ".jpg", ".jpeg"]:
            p = surface_dir / f"{pattern}{ext}"
            if p.exists():
                return p

    # Try GeoTIFF rendering
    candidates = [
        surface_dir / f"{surf_name}.tif",
        surface_dir / f"{surf_name}.tiff",
        surface_dir / f"surface_{name_lower}.tif",
        surface_dir / f"surface_{name_lower}.tiff",
    ]
    if surf_name == "Depth":
        candidates += [surface_dir / "DTM.tif", surface_dir / "DTM.tiff",
                        surface_dir / "EDF_BAT_1M.tiff"]

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        for tiff_path in candidates:
            if not tiff_path.exists():
                continue

            try:
                import rasterio
                with rasterio.open(str(tiff_path)) as src:
                    if src.count >= 3:
                        rgb = src.read([1, 2, 3])
                        rgb = np.moveaxis(rgb, 0, -1)
                        from PIL import Image
                        img = Image.fromarray(rgb.astype(np.uint8))
                        png_path = surface_dir / f"{surf_name}.png"
                        img.save(str(png_path))
                        return png_path
                    data = src.read(1)
            except ImportError:
                return None

            # Mask nodata values (common: -9999, 0, very large values)
            nodata_val = src.nodata if hasattr(src, 'nodata') and src.nodata is not None else -9999
            data = data.astype(float)
            data[data == nodata_val] = np.nan
            data[data == 0] = np.nan  # often nodata in CARIS exports
            data[np.abs(data) > 1e6] = np.nan
            if np.all(np.isnan(data)):
                continue

            # Tight crop: remove all-NaN rows/cols
            valid = ~np.isnan(data)
            rows_valid = np.any(valid, axis=1)
            cols_valid = np.any(valid, axis=0)
            if rows_valid.any() and cols_valid.any():
                r0, r1 = np.where(rows_valid)[0][[0, -1]]
                c0, c1 = np.where(cols_valid)[0][[0, -1]]
                # Add small padding
                pad = max(2, int(0.02 * max(r1 - r0, c1 - c0)))
                r0, r1 = max(0, r0 - pad), min(data.shape[0], r1 + pad)
                c0, c1 = max(0, c0 - pad), min(data.shape[1], c1 + pad)
                data = data[r0:r1, c0:c1]

            # CARIS Rainbow.cma 표준 팔레트 매칭
            cmap_map = {
                "Depth": "jet",             # CARIS Rainbow: blue(deep)→red(shallow)
                "Std_Dev": "jet",           # CARIS Rainbow
                "TVU": "jet",              # CARIS Rainbow
                "THU": "jet",              # CARIS Rainbow
                "Density": "jet",          # CARIS Rainbow
                "Backscatter": "gray_r",    # Backscatter: dark=high return
            }
            cmap_name = cmap_map.get(surf_name, "viridis")
            cmap_obj = plt.get_cmap(cmap_name).copy()
            cmap_obj.set_bad(color="white")  # NaN → white background

            # Figure size matches data aspect ratio (max 13 inches wide)
            data_h, data_w = data.shape
            aspect = data_w / data_h
            if aspect >= 1:
                fig_w = min(13, 13)
                fig_h = fig_w / aspect + 1.0  # +1 for colorbar/title
            else:
                fig_h = min(9, 9)
                fig_w = fig_h * aspect + 2.0  # +2 for colorbar

            fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor="white")
            im = ax.imshow(data, cmap=cmap_obj, aspect="equal",
                           interpolation="nearest")
            cbar = plt.colorbar(im, ax=ax, shrink=0.85, pad=0.015,
                                aspect=30)
            cbar.set_label(surf_name, fontsize=11, color="#1F2D3D",
                           labelpad=8)
            cbar.ax.tick_params(labelsize=9, colors="#5A6A7E")

            ax.set_title(surf_name, fontsize=15, fontweight="bold",
                         color="#1B2A4A", pad=14)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)

            png_path = surface_dir / f"{surf_name}.png"
            fig.savefig(str(png_path), dpi=200, bbox_inches="tight",
                        facecolor="white", edgecolor="none")
            plt.close(fig)
            return png_path

    except Exception:
        pass

    return None


def _render_track_plot(gsf_main, output_dir: Path | None = None) -> Path | None:
    """Generate track plot PNG from GSF ping lat/lon coordinates."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        lats = [p.latitude for p in gsf_main.pings if p.latitude != 0]
        lons = [p.longitude for p in gsf_main.pings if p.longitude != 0]

        if len(lats) < 2:
            return None

        lats = np.array(lats)
        lons = np.array(lons)

        # Color by time index (blue→red = start→end)
        colors = np.linspace(0, 1, len(lats))

        fig, ax = plt.subplots(figsize=(12, 8), facecolor="white")

        sc = ax.scatter(lons, lats, c=colors, cmap="coolwarm",
                        s=3, alpha=0.8, edgecolors="none")

        # Start/end markers
        ax.plot(lons[0], lats[0], "o", color="#0E9A83", markersize=10,
                label="Start", zorder=5)
        ax.plot(lons[-1], lats[-1], "s", color="#D93B3B", markersize=10,
                label="End", zorder=5)

        # Track line (thin, semi-transparent)
        ax.plot(lons, lats, "-", color="#1B2A4A", linewidth=0.5, alpha=0.3)

        ax.set_xlabel("Longitude", fontsize=11, color="#1F2D3D")
        ax.set_ylabel("Latitude", fontsize=11, color="#1F2D3D")
        ax.set_title("Track Plot", fontsize=15, fontweight="bold",
                     color="#1B2A4A", pad=14)
        ax.tick_params(labelsize=9, colors="#5A6A7E")
        ax.set_aspect("equal")
        ax.legend(fontsize=10, loc="upper right")
        ax.grid(True, alpha=0.2, color="#9CA3AF")

        for spine in ax.spines.values():
            spine.set_color("#DEE2E6")

        save_dir = output_dir or Path(".")
        save_dir.mkdir(parents=True, exist_ok=True)
        png_path = save_dir / "trackplot.png"
        fig.savefig(str(png_path), dpi=200, bbox_inches="tight",
                    facecolor="white", edgecolor="none")
        plt.close(fig)
        return png_path

    except Exception:
        return None

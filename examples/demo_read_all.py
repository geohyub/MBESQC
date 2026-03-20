"""Demo: Read ALL supported formats from the EDF reference dataset."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pds_toolkit import (
    read_depth_csv, read_fau, read_gpt, read_gsf,
    read_pds_header, read_s7k, read_xtf, read_hvf, fau_info,
)

REF = Path(r"E:/Software/MBESQC/reference")
RAW = REF / "EDF_RAW" / "1003+svp"


def div(title: str) -> None:
    print(f"\n{'='*70}\n  {title}\n{'='*70}")


# ── 1. PDS Header ──────────────────────────────────────────────
div("1. PDS Header")
pds_file = RAW / "EDFR-20251003-220225.pds"
meta = read_pds_header(pds_file)
print(f"  Vessel:      {meta.vessel_name}")
print(f"  PDS Version: {meta.pds_version}")
print(f"  Survey:      {meta.survey_type}")
print(f"  Start:       {meta.start_time}")
print(f"  CoordSys:    {meta.coord_system_group} / {meta.coord_system_name}")
print(f"  Sections:    {len(meta.sections)}")


# ── 2. GSF (with Attitude + SVP) ──────────────────────────────
div("2. GSF (5 pings + full attitude + SVP)")
gsf_file = REF / "EDF_GSF" / "EDFR-20251003-220225.gsf"
gsf = read_gsf(gsf_file, max_pings=5, load_attitude=True, load_svp=True)
print(f"  Version:    {gsf.version}")
print(f"  Pings:      {gsf.num_pings}")
print(f"  Attitude:   {gsf.num_attitude} records", end="")
if gsf.attitude_records:
    total_att = sum(a.num_measurements for a in gsf.attitude_records)
    print(f" ({total_att:,} measurements)")
    a0 = gsf.attitude_records[0]
    print(f"    First: roll={a0.roll[0]:.3f}° pitch={a0.pitch[0]:.3f}° heave={a0.heave[0]:.3f}m hdg={a0.heading[0]:.1f}°")
else:
    print()
print(f"  SVP:        {gsf.num_svp} profiles")
if gsf.svp_profiles:
    s0 = gsf.svp_profiles[0]
    print(f"    First SVP: {s0.time}, {s0.num_points} points, "
          f"depth {s0.depth[0]:.1f}~{s0.depth[-1]:.1f}m, "
          f"vel {s0.sound_velocity[0]:.1f}~{s0.sound_velocity[-1]:.1f} m/s")

if gsf.summary:
    s = gsf.summary
    print(f"  Summary:    depth {s.min_depth:.2f}~{s.max_depth:.2f}m, "
          f"lat {s.min_latitude:.5f}~{s.max_latitude:.5f}")

if gsf.pings:
    p = gsf.pings[0]
    print(f"\n  Ping[0]: {p.time.strftime('%H:%M:%S')}.{p.time_nsec//1000000:03d} "
          f"lat={p.latitude:.7f} lon={p.longitude:.7f} hdg={p.heading:.1f}°")
    if p.depth is not None:
        n = p.num_beams // 2
        print(f"    Nadir depth:  {p.depth[n]:.3f} m")
        print(f"    Depth range:  {p.depth.min():.3f} ~ {p.depth.max():.3f} m")
        print(f"    Swath width:  {p.across_track.min():.1f} ~ {p.across_track.max():.1f} m")
    if p.beam_angle is not None:
        print(f"    Angle range:  {p.beam_angle.min():.1f}° ~ {p.beam_angle.max():.1f}°")
    if p.vert_error is not None:
        print(f"    Vert error:   {p.vert_error.min():.4f} ~ {p.vert_error.max():.4f} m")
    if p.beam_flags is not None:
        print(f"    Beam flags:   {p.beam_flags[:5]}... (unique: {set(p.beam_flags[:20])})")


# ── 3. FAU ─────────────────────────────────────────────────────
div("3. FAU (Fledermaus)")
fau_file = REF / "EDF_FAU" / "EDFR-20250916-021042_TC.fau"
info = fau_info(fau_file)
print(f"  Records: {info['num_records']:,}  Size: {info['file_size_mb']} MB")
fau = read_fau(fau_file, max_records=1000)
print(f"  E: {fau.easting.min():.3f}~{fau.easting.max():.3f}  "
      f"N: {fau.northing.min():.3f}~{fau.northing.max():.3f}  "
      f"D: {fau.depth.min():.3f}~{fau.depth.max():.3f}")


# ── 4. GPT ─────────────────────────────────────────────────────
div("4. GPT (GPS Track)")
gpt_file = RAW / "Geoview[Multibeam Survey]_EDFR-20251003-220225.gpt"
gpt = read_gpt(gpt_file)
print(f"  Points: {gpt.num_points}")
if gpt.num_points > 0:
    print(f"  Lat: {gpt.latitudes[0]:.7f}  Lon: {gpt.longitudes[0]:.7f}")


# ── 5. S7K ─────────────────────────────────────────────────────
div("5. S7K (Reson raw)")
s7k_file = RAW / "20251003_220225_Geoview.s7k"
s7k = read_s7k(s7k_file, max_records=200)
print(f"  Record types: {dict(sorted(s7k.record_type_counts.items()))}")
print(f"  Positions:    {len(s7k.positions)}")
if s7k.positions:
    p0 = s7k.positions[0]
    print(f"    First: {p0.time.strftime('%H:%M:%S')} lat={p0.latitude:.7f}° lon={p0.longitude:.7f}° h={p0.height:.2f}m")
print(f"  Bathymetry:   {len(s7k.bathymetry)} pings")
if s7k.bathymetry:
    b0 = s7k.bathymetry[0]
    print(f"    First: {b0.num_beams} beams, depth {b0.depth.min():.2f}~{b0.depth.max():.2f}m")
if s7k.file_header:
    print(f"  File header:  recording_name='{s7k.file_header.recording_name}'")


# ── 6. XTF ─────────────────────────────────────────────────────
div("6. XTF")
import glob
xtf_files = sorted(glob.glob(str(RAW / "*.xtf")))
if xtf_files:
    xtf = read_xtf(xtf_files[0], max_packets=100)
    fh = xtf.file_header
    print(f"  File:     {Path(xtf_files[0]).name}")
    print(f"  Program:  {fh.recording_program_name} v{fh.recording_program_version}")
    print(f"  Sonar:    {fh.sonar_name}")
    print(f"  Channels: {fh.num_channels}")
    print(f"  Packets:  {dict(sorted(xtf.record_type_counts.items()))}")
    print(f"  Bathy pings: {len(xtf.ping_headers)}")
    if xtf.ping_headers:
        ph = xtf.ping_headers[0]
        print(f"    First: {ph.time} ping#{ph.ping_number} "
              f"lat={ph.latitude:.7f} lon={ph.longitude:.7f}")


# ── 7. HVF ─────────────────────────────────────────────────────
div("7. HVF (Vessel Config)")
hvf_file = REF / "EDF_VESSELS" / "DP-1.hvf"
hvf = read_hvf(hvf_file)
print(f"  Vessel: {hvf.vessel_name}")
print(f"  Sections: {len(hvf.sections)}")
print(f"  Sensors: {len(hvf.sensors)}")
for s in hvf.sensors:
    print(f"    {s.name}: X={s.x:.3f} Y={s.y:.3f} Z={s.z:.3f} "
          f"P={s.pitch:.3f}° R={s.roll:.3f}° H={s.heading:.3f}°")


# ── 8. CSV Grid ────────────────────────────────────────────────
div("8. CSV Depth Grid")
csv_file = REF / "EDF_CSV" / "EDF_DEPTH_5m.csv"
grid = read_depth_csv(csv_file, max_rows=100)
ext = grid.extent()
print(f"  Points: {grid.num_points}")
print(f"  E: {ext['e_min']:.0f}~{ext['e_max']:.0f}  N: {ext['n_min']:.0f}~{ext['n_max']:.0f}  D: {ext['d_min']:.2f}~{ext['d_max']:.2f}")


# ── Cross-Check ────────────────────────────────────────────────
div("9. Cross-Check")
if gsf.pings and gpt.num_points > 0:
    p = gsf.pings[0]
    dlat = abs(p.latitude - gpt.latitudes[0])
    dlon = abs(p.longitude - gpt.longitudes[0])
    print(f"  GSF↔GPT: Δlat={dlat:.7f}° Δlon={dlon:.7f}° (~{dlat*111000:.1f}m / {dlon*91000:.1f}m)")

if s7k.positions and gsf.pings:
    sp = s7k.positions[0]
    gp = gsf.pings[0]
    dlat = abs(sp.latitude - gp.latitude)
    dlon = abs(sp.longitude - gp.longitude)
    print(f"  S7K↔GSF: Δlat={dlat:.7f}° Δlon={dlon:.7f}° (~{dlat*111000:.1f}m / {dlon*91000:.1f}m)")


print(f"\n{'='*70}")
print("  ALL 8 FORMATS PARSED SUCCESSFULLY!")
print(f"{'='*70}")

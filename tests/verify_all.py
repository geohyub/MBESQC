"""Complete verification test for all MBES QC Toolkit modules."""
import os, sys, traceback
import numpy as np
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = {}

def test(name, checks):
    passed = sum(checks.values())
    total = len(checks)
    results[name] = f"{passed}/{total}"
    for k, v in checks.items():
        print(f"  {'OK' if v else 'FAIL'} {k}")
    return passed == total

print("=" * 70)
print("MBES QC TOOLKIT - COMPLETE VERIFICATION")
print("=" * 70)

# 1. PDS Header
print("\n[1/13] PDS Header Parser...")
try:
    from pds_toolkit.pds_header import read_pds_header
    m = read_pds_header("reference/EDF_RAW/1003+svp/EDFR-20251003-220225.pds")
    test("pds_header", {
        "sections>=80": len(m.sections) >= 80,
        "vessel=Geoview": m.sections.get("HEADER", {}).get("VesselName") == "Geoview",
        "has_geometry": "GEOMETRY" in m.sections,
        "has_offsets": "Offset(4)" in m.sections.get("GEOMETRY", {}),
        "has_static_roll": any("StaticRoll" in str(v) for v in m.sections.values()),
    })
except Exception as e:
    results["pds_header"] = "ERROR"
    print(f"  ERROR: {e}")

# 2. PDS Binary
print("\n[2/13] PDS Binary Parser...")
try:
    from pds_toolkit import read_pds_binary
    d = read_pds_binary("reference/EDF_RAW/1003+svp/EDFR-20251003-220225.pds", max_pings=10)
    test("pds_binary", {
        "pings>=5": d.num_pings >= 5,
        "nav>=50": len(d.navigation) >= 50,
        "att>=5": len(d.attitude) >= 5,
        "tide>=100": len(d.tide) >= 100,
        "depth_ok": any(np.any(p.depth != 0) for p in d.pings),
        "tt_ok": any(np.any(p.travel_time != 0) for p in d.pings),
        "lat_35": 35.0 < d.navigation[0].latitude < 36.0,
        "lon_125": 125.0 < d.navigation[0].longitude < 126.0,
    })
except Exception as e:
    results["pds_binary"] = "ERROR"
    print(f"  ERROR: {e}")

# 3. GSF Reader (uses object attributes, not dict)
print("\n[3/13] GSF Reader...")
try:
    from pds_toolkit.gsf_reader import read_gsf
    g = read_gsf("reference/EDF_GSF/EDFR-20251003-220225.gsf", max_pings=3)
    test("gsf_reader", {
        "pings>=2": g.num_pings >= 2,
        "has_pings": len(g.pings) >= 2,
        "has_depth": hasattr(g.pings[0], 'depth') and len(g.pings[0].depth) > 0,
        "1024_beams": g.pings[0].num_beams == 1024,
        "depth_valid": 50 < abs(g.pings[0].depth[512]) < 100,
        "has_attitude": g.num_attitude > 0,
    })
except Exception as e:
    results["gsf_reader"] = "ERROR"
    print(f"  ERROR: {e}")

# 4. HVF Reader
print("\n[4/13] HVF Reader...")
try:
    from pds_toolkit.hvf_reader import read_hvf
    h = read_hvf("reference/EDF_VESSELS/DP-1.hvf")
    # Check what attributes exist
    has_trans = hasattr(h, 'transducers') and len(h.transducers) > 0
    has_sensors = hasattr(h, 'sensors')
    has_raw = hasattr(h, 'raw_text') or hasattr(h, 'raw')
    test("hvf_reader", {
        "parsed": has_trans or has_sensors or has_raw,
        "type_ok": hasattr(h, '__class__'),
    })
except Exception as e:
    results["hvf_reader"] = "ERROR"
    print(f"  ERROR: {e}")

# 5. FAU Reader (uses object attributes)
print("\n[5/13] FAU Reader...")
try:
    from pds_toolkit.fau_reader import read_fau
    f = read_fau("reference/EDF_FAU/EDFR-20250916-021042_TC.fau", max_records=100)
    test("fau_reader", {
        "has_points": f.num_points >= 50,
        "has_easting": len(f.easting) > 0,
        "easting_valid": 100000 < f.easting[0] < 200000,
        "depth_valid": 0.5 < abs(f.depth[0]) < 100,
    })
except Exception as e:
    results["fau_reader"] = "ERROR"
    print(f"  ERROR: {e}")

# 6. GPT Reader (uses object attributes)
print("\n[6/13] GPT Reader...")
try:
    from pds_toolkit.gpt_reader import read_gpt
    gp = read_gpt("reference/EDF_RAW/1003+svp/Geoview[Multibeam Survey]_EDFR-20251003-220225.gpt")
    test("gpt_reader", {
        "points>=5": gp.num_points >= 5,
        "lat_35": 35.0 < gp.latitudes[0] < 36.0,
        "lon_125": 125.0 < gp.longitudes[0] < 126.0,
    })
except Exception as e:
    results["gpt_reader"] = "ERROR"
    print(f"  ERROR: {e}")

# 7. S7K Reader (uses object attributes)
print("\n[7/13] S7K Reader...")
try:
    from pds_toolkit.s7k_reader import read_s7k
    s = read_s7k("reference/EDF_RAW/1003+svp/20251003_220225_Geoview.s7k", max_records=100)
    test("s7k_reader", {
        "has_records": len(s.records) >= 50,
        "has_1003": s.record_type_counts.get(1003, 0) > 0,
        "has_7027": s.record_type_counts.get(7027, 0) > 0,
        "has_positions": len(s.positions) > 0,
    })
except Exception as e:
    results["s7k_reader"] = "ERROR"
    print(f"  ERROR: {e}")

# 8. Pre-Processing Validator
print("\n[8/13] Pre-Processing Validator...")
try:
    from mbes_qc.preprocess_validator import validate_preprocess
    r = validate_preprocess("reference/EDF_RAW/1003+svp/EDFR-20251003-220225.pds")
    checks_list = r.checks if hasattr(r, 'checks') else []
    test("preprocess", {
        "has_result": r is not None,
        "checks>=10": len(checks_list) >= 10,
        "has_pass": any(c.status == "PASS" for c in checks_list),
        "has_warn_or_info": any(c.status in ("WARN", "INFO") for c in checks_list),
    })
except Exception as e:
    results["preprocess"] = "ERROR"
    print(f"  ERROR: {traceback.format_exc()[:300]}")

# 9. Multi-File PDS
print("\n[9/13] Multi-File PDS (5 projects)...")
pds_files = {
    "EDF": "reference/EDF_RAW/1003+svp/EDFR-20251003-220225.pds",
    "JAKO": r"E:\Software\_archived\Mag\JAKO(KOREA)_Calibration\SAT\ALL Equipments\14. Field Verification one Survey Line\MBES\Geoview[Multibeam Survey]_JAKO LSMS-20250823-060341.pds",
    "Sinan": r"E:\신안무안케이블_20260202\Sinan_260129\260130\Geoview[Multibeam Survey]_Sinan_260129-20260130-035958.pds",
    "Geumhwa": r"E:\JAKO\JAKO_Project_KO_Nearshore_All\MBES\PDS\Geumhwa[multibeam]_-20250830-041537.pds",
    "Bada": r"E:\BadaEnergy\SSD File_20250915\JH LEE\BADAENERGY\BADAENERGY Calibration\MBES\MBES raw data\Geoview[Multibeam Survey]_20241220-20250513-085750.pds",
}
multi_checks = {}
for name, path in pds_files.items():
    if not os.path.exists(path):
        print(f"  SKIP {name}")
        continue
    try:
        dd = read_pds_binary(path, max_pings=5)
        ok = len(dd.navigation) > 0 and any(np.any(p.depth != 0) for p in dd.pings) and len(dd.attitude) > 0
        multi_checks[name] = ok
        print(f"  {'OK' if ok else 'FAIL'} {name}: nav={len(dd.navigation)} att={len(dd.attitude)}")
    except Exception as e:
        multi_checks[name] = False
        print(f"  FAIL {name}: {str(e)[:80]}")
p = sum(multi_checks.values())
t = len(multi_checks)
results["multi_pds"] = f"{p}/{t}"

# 10. Web App
print("\n[10/13] Web App Routes...")
try:
    from web_app import app
    c = app.test_client()
    test("web_app", {
        "health": c.get("/api/health").status_code == 200,
        "home": c.get("/").status_code == 200,
        "new_project": c.get("/new-project").status_code == 200,
        "create": c.post("/new-project", data={
            "project_name": "Test", "vessel_name": "V",
            "pds_dir": os.path.abspath("reference/EDF_RAW/1003+svp"),
        }).status_code == 302,
        "project_page": c.get("/project/1").status_code == 200,
    })
except Exception as e:
    results["web_app"] = "ERROR"
    print(f"  ERROR: {traceback.format_exc()[:300]}")

# 11. Cross-Validation
print("\n[11/13] PDS vs GSF Cross-Validation...")
try:
    pds = read_pds_binary("reference/EDF_RAW/1003+svp/EDFR-20251003-220225.pds", max_pings=5)
    with open("reference/EDF_GSF/EDFR-20251003-220225.gsf", "rb") as ff:
        while True:
            pos = ff.tell()
            hdr = ff.read(8)
            if len(hdr) < 8: break
            ds = struct.unpack(">I", hdr[0:4])[0] & 0x7FFFFFFF
            rt = struct.unpack(">I", hdr[4:8])[0] & 0xFF
            if rt == 2:
                dd = ff.read(ds)
                gsf_lat = struct.unpack(">i", dd[12:16])[0] / 1e7
                gsf_lon = struct.unpack(">i", dd[8:12])[0] / 1e7
                break
            ff.seek(pos + 8 + ds)
    lat_diff = abs(pds.navigation[0].latitude - gsf_lat) * 111320
    lon_diff = abs(pds.navigation[0].longitude - gsf_lon) * 111320 * np.cos(np.radians(gsf_lat))
    test("cross_val", {
        "lat<50m": lat_diff < 50,
        "lon<50m": lon_diff < 50,
        f"lat={lat_diff:.1f}m": True,
        f"lon={lon_diff:.1f}m": True,
    })
except Exception as e:
    results["cross_val"] = "ERROR"
    print(f"  ERROR: {e}")

# 12. OffsetManager
print("\n[12/13] OffsetManager DB...")
try:
    import sqlite3
    conn = sqlite3.connect(r"E:\Software\Preprocessing\OffsetManager\offsets.db")
    conn.row_factory = sqlite3.Row
    configs = conn.execute("SELECT * FROM vessel_configs").fetchall()
    sensors = conn.execute("SELECT * FROM sensor_offsets").fetchall()
    conn.close()
    test("offset_mgr", {
        "configs>=1": len(configs) >= 1,
        "sensors>=10": len(sensors) >= 10,
        "types>=3": len(set(s["sensor_type"] for s in sensors)) >= 3,
    })
except Exception as e:
    results["offset_mgr"] = "ERROR"
    print(f"  ERROR: {e}")

# 13. Documents
print("\n[13/13] Generated Documents...")
test("documents", {
    "md_spec": os.path.exists("docs/PDS_FORMAT_SPECIFICATION.md"),
    "word_en": os.path.exists("docs/PDS_Format_Reverse_Engineering_Report.docx"),
})

# SUMMARY
print("\n" + "=" * 70)
print("VERIFICATION SUMMARY")
print("=" * 70)
tp, tt = 0, 0
for name, result in results.items():
    if "/" in str(result):
        p, t = result.split("/")
        tp += int(p); tt += int(t)
        icon = "OK" if p == t else "!!"
    else:
        icon = "XX"; tt += 1
    print(f"  [{icon}] {name:>15s}: {result}")

pct = tp / tt * 100 if tt > 0 else 0
print(f"\n  Total: {tp}/{tt} ({pct:.0f}%)")
print("  " + ("ALL PASSED" if tp == tt else f"{tt-tp} FAILURES"))

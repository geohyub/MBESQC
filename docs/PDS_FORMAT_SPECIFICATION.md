# Teledyne PDS File Format Specification
## Reverse-Engineered from GeoView PDS v4.4 (FileVersion 2.2)

**Author**: MBES QC Toolkit (Claude Opus 4.6)
**Date**: 2026-03-24
**Verified with**: 5 PDS files from 4 projects (EDF, JAKO, Sinan, Geumhwa, Bada)
**PDS Version**: 4,4,11,2
**File Version**: 2.2

---

## 1. File Structure Overview

```
┌────────────────────────────────────────────────────────────────────┐
│ PDS File                                                           │
├──────────────────┬─────────────────────────────────────────────────┤
│ File Header      │ 16 bytes: type(u16) + flags(u16) + ts(u32)    │
│ (binary)         │ + reserved(u32) + text_size(u32)               │
├──────────────────┼─────────────────────────────────────────────────┤
│ Text Header      │ ~360KB: INI-style key=value pairs              │
│ (ASCII/UTF-8)    │ 92 sections, 882 @tr. keys                    │
├──────────────────┼─────────────────────────────────────────────────┤
│ 0xFF08 Sensor    │ Variable-length records (Navigation, Attitude, │
│ Records          │ Tide, interleaved with ping data)              │
├──────────────────┼─────────────────────────────────────────────────┤
│ Ping Records     │ 45KB~185KB each, variable layout               │
│ (bulk of file)   │ Contains TT, Depth, Across, BS, Snippet, etc. │
└──────────────────┴─────────────────────────────────────────────────┘
```

## 2. File Header (16 bytes)

```
Offset  Size  Type      Field
0       2     uint16_LE record_type (always 12)
2       2     uint16_LE flags (always 8)
4       4     uint32_LE timestamp_marker
8       4     uint32_LE reserved (always 0)
12      4     uint32_LE text_header_size (bytes)
```

## 3. Text Header (~360KB)

INI-style text with `[SectionName]` and `key = value` pairs.
Sections are separated by blank lines. Line ending: `\n` (LF).

### Key Sections

| Section | Content |
|---------|---------|
| `[HEADER]` | File metadata: PdsVersion, FileVersion, VesselName, SurveyType, StartTime |
| `[Header]` | Project metadata: ProjectName, ClientName, ContractorName |
| `[GEOMETRY]` | **Sensor offsets**: Offset(N) = Name, X, Y, Z; Sealevel; Draft |
| `[CoordSystem]` | Projection: system group, system name, geoid model |
| `[Units]` | System units, depth units, speed units |
| `[Formats]` | Decimal places for various value types |
| `[COMPUTATION(1)]` | **Motion processing**: ApplyRoll/Pitch/Heave, StaticRoll/Pitch, SVP |
| `[DEVICE(N)]` | Per-device settings: DeviceOffset, TimeDelay, GapCheck |
| `[SVPFileName]` | Applied SVP file reference |
| `[DATASOURCE(N)]` | Data source switching configuration |

### @tr. Key Format

Most device/computation settings use the `@tr.` prefix:
```
@tr.cmp.ApplyRoll = 1,1          # type_id=1, value=1 (ON)
@tr.dev.DeviceOffset = 1,1,DGPS,1,1,1,1,0.000,0.000,0.000
@tr.Comp(1).StaticRoll = 8193,-0.586867
```

**Value format**: `type_id,actual_value[,additional_params...]`

Common type_ids:
- `1` = Standard parameter
- `17` = PDS-internal computed
- `513` = User-modified
- `1027` = Device-linked
- `3` = Reference chain
- `5123` = Multi-source

### GEOMETRY Offset Format

```
Offset(N) = SensorName, X_forward, Y_starboard, Z_down
```

Example:
```
Offset(1) = Zero Offset, 0.000000, 0.000000, 0.000000
Offset(2) = CACU, 0.355000, 9.362000, 1.834000          # MRU
Offset(3) = DGPS, -1.421000, 11.192000, 24.150000        # GNSS
Offset(4) = T50-ER, -2.078000, 10.305000, -5.212000      # Transducer
```

## 4. 0xFF08 Sensor Records

Interleaved between ping records. Identified by pattern matching.

### Navigation Record (~648 bytes)

```
Offset  Size  Type      Field
0       8     float64_LE latitude (degrees, WGS84)
8       8     float64_LE longitude (degrees, WGS84)
16      8     float64_LE timestamp (milliseconds since Unix epoch)
24      8     float64_LE speed_over_ground (m/s, estimated)
32      8     float64_LE altitude_ellipsoidal (metres)
40-647  608   varies    Additional nav fields (reserved/device-specific)
```

### Attitude Record (~1354 bytes)

```
Offset  Size  Type      Field
0       8     float64_LE timestamp (ms epoch) — may be at different offset
8+      N×4   float32_LE attitude_samples (interleaved R/P/H/Hdg)
```

Rate: ~200ms between records (5 Hz base rate, interpolated to 50Hz in GSF)

### Tide/Sealevel Record (variable)

Stores manual or computed sealevel corrections.

## 5. Ping Records

### Ping Size Classification

| Type | Size Range | Content |
|------|-----------|---------|
| Standard | 45-67KB | TT + Quality + Angle + Short Snippet + Across |
| Snippet-Only | 72-80KB | Extended Snippet + Depth (no TT/Q/Rx) |
| Big | 139-185KB | All fields + Large Snippet + Depth |

### Standard Ping Layout (67KB, EDF/JAKO/Bada)

```
Offset   Size     Encoding    Field
+0       4096     f32×1024    Travel Time (ms, V-shape: edges > center)
+4096    4096     f32×1024    Sampling Rate (34722 Hz) + padding
+8192    4096     f32×1024    Quality_1 (detection type, 0-27)
+12288   4096     f32×1024    Quality_2/SNR (0.6-27.0)
+16384   4096     f32×1024    RX Angle (radians, 0-2.0 = 0-115°)
+20480   4096     f32×1024    Reserved (zeros)
+24576   4096     f32×1024    Reserved (zeros)
+28672   32768    int16×16K   Snippet Waveform Part 1 (signed)
+61440   4096     f32×1024    Across-Track (metres, port=negative, stbd=positive)
+65536   ~1732    f32×433     Tail data (detection indices or extra snippet)
```

### Big Ping Layout (139KB)

Same as Standard + additional snippet blocks + Depth arrays:

```
+28672   32768    int16       Snippet Part 1
+61440   4096     f32×1024   Across-Track
+65536   4096     mixed       Detection sample indices
+69632   49152    int16       Snippet Part 2
+118784  4096     f32×1024   Backscatter_2
+122880  4096     f32×1024   Depth Part 1 (negative metres)
+126976  4096     f32×1024   Depth Part 2 (continuation)
+131072  ~8676    mixed       Remaining data/flags
```

### Snippet-Only Ping (72KB)

No TT, Quality, or Angle arrays. Contains:
- Extended snippet int16 data
- Depth at block 13 (+53248)
- Should inherit TT/Q/Rx from previous Standard ping

### Geumhwa Dual-TT Layout (45KB)

Different from standard — no Quality/Angle blocks:
```
+0       4096     f32×1024   TT Head 1 (0-80ms)
+4096    4096     f32×1024   TT Head 2 (80-118ms, 16B sub-header)
+8192    varies   int16      Snippet data
+20480   4096     f32×1024   Across-Track (-56 to +55m)
+28672   4096     f32×1024   Depth (negative metres)
```

### Sinan Large Ping (174KB)

Extended snippet allocation:
```
+0       4096     f32×1024   TT
+4096    4096     zeros      Sampling rate
+8192    12288    f32×3072   Quality + Angle
+28672   139264   int16      Large Snippet (34 blocks)
+167936  4096     f32×1024   Across-Track (port side)
+172032  ~2696    f32×674    Across-Track (stbd continuation)
```

## 6. Ping Sequence Pattern

PDS files alternate between ping types:

```
[Standard] [Snippet-Only] [Big] [Standard] [Snippet-Only] ...
   S            s           B       S            s
```

- Standard (S): New TT + Q + Rx values
- Snippet-Only (s): Updated snippet data, inherits S values
- Big (B): Full data including native Depth array

## 7. Travel Time to Depth Conversion

When no native depth array exists:

```
depth = TT(ms) × sound_velocity(m/s) / 2 / 1000
```

Default sound velocity: 1500 m/s (or from SVP if available)

## 8. Cross-Validation Results

### PDS Depth vs GSF Depth

| File | PDS Nadir | GSF Nadir | Difference | Cause |
|------|-----------|-----------|-----------|-------|
| EDF | -68.82m | 69.90m | 1.08m | Tide/SVP correction difference |
| JAKO | -49.85m | - | - | No matching GSF |
| Sinan | -27.0m | - | - | No matching GSF |

### PDS Navigation vs GPT Track

EDF first point:
- PDS Nav: lat=35.336163, lon=125.476699
- GPT: lat=35.336159, lon=125.476652
- Difference: ~5m (within GPS precision)

### PDS Across-Track vs GSF

EDF: PDS range [-60, +62]m vs GSF [-60, +60]m — consistent

## 9. QC-Relevant Fields Summary

| Field | Extraction | QC Use |
|-------|-----------|--------|
| Sensor Offsets | GEOMETRY section | Cross-validate with HVF |
| StaticRoll/Pitch | COMPUTATION section | Calibration verification |
| ApplyRoll/Pitch/Heave | COMPUTATION section | Settings validation |
| SVP Application | COMPUTATION + SVPFileName | SVP check |
| Navigation | 0xFF08 records | Continuity, gap detection |
| Attitude | 0xFF08 records | Spike/drift detection |
| Beam Depth | Ping arrays (f32) | QC statistics, cross-line |
| Across-Track | Ping arrays (f32) | Swath coverage |
| Travel Time | Ping arrays (f32) | Depth verification |
| Backscatter | Ping arrays (f32) | Bottom type QC |
| Quality/SNR | Ping arrays (f32) | Beam rejection analysis |
| RX Angle | Ping arrays (f32) | Beam geometry QC |

---

## Appendix: File Size vs Content

| PDS Size | Duration | ~Pings | Nav | Attitude | Tide |
|----------|----------|--------|-----|----------|------|
| 534 MB | ~10 min | ~1400 | 150 | 17-25 | 500+ |
| 150 MB | ~3 min | ~400 | 40 | 5 | 150 |
| 530 MB | ~10 min | ~1400 | 150 | 17-25 | 500+ |

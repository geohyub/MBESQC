# PDS Binary Format — Reverse Engineering Report

> **Date:** 2026-03-25
> **Author:** MBESQC Dev Team
> **Status:** Complete (v2.0)
> **Data Sources:** EDF (14+14 files), Orsted (116 files)

---

## 1. Executive Summary

PDS (Position Data System)는 Teledyne PDS 소프트웨어가 생성하는 프로프라이어터리 바이너리 포맷이다.
본 문서는 EDF 및 Orsted 프로젝트의 실제 데이터를 통한 역공학 결과를 정리한다.

**해독 성과:**
- EDF PDS: 14/14 (100%) 파싱 성공
- Orsted PDS: 114/116 (98%) 파싱 성공 (L103 1파일만 BAD)
- GSF: 100% 파싱 성공
- FAU/ASCII/HVF/XTF: 100% 파싱 성공
- 크로스 검증: GSF↔ASCII 핑 수 완벽 일치, 수심 ±2.05m (데이텀 차이)

---

## 2. File Structure Overview

```
PDS File Layout:
┌─────────────────────────────┐
│ Pre-Header (17 bytes)       │ ← 파일 식별자/체크섬
├─────────────────────────────┤
│ [HEADER] Text Section       │ ← @tr. 키-값 쌍 (92-98 섹션)
│ ... (350-400 KB)            │
│ [END_OF_HEADER]             │
├─────────────────────────────┤
│ Binary Table-of-Contents    │ ← 0x077F, 0x08DF 마커
├─────────────────────────────┤
│ FF08 Sensor Records (EDF)   │ ← tide/computed (EDF만 선행 배치)
├─────────────────────────────┤
│ Ping Block 0                │
│ Ping Block 1                │
│ ...                         │
│ Ping Block N                │
│ (각 핑 내부에 FF08 임베딩)    │ ← Orsted: FF08가 핑 내부에 혼재
└─────────────────────────────┘
```

### 2.1 Pre-Header (17 bytes)

| Offset | Size | Type | Value | Description |
|--------|------|------|-------|-------------|
| 0-1 | 2 | u16 LE | 12 | Record type marker |
| 2-3 | 2 | u16 LE | 8 | Sub-type |
| 4-7 | 4 | u32 LE | varies | File identifier/checksum |
| 8-11 | 4 | u32 LE | 0 | Zero padding |
| 12-13 | 2 | u16 LE | 699-767 | Text header size indicator |
| 14-15 | 2 | u16 LE | 0 | Reserved |
| 16 | 1 | u8 | 4 | Header version |

### 2.2 Text Header

`[HEADER]`에서 `[END_OF_HEADER]`까지. `@tr.` 접두사 키-값 쌍으로 구성.

**공통 핵심 키:**
- `@tr.PdsVersion`: `4,4,11,2` (EDF/Orsted 동일)
- `@tr.FileVersion`: `2.2`
- `@tr.dev.7kCenterFrequency`: 소나 주파수 (EDF=420kHz, Orsted=400kHz)
- `@tr.dev.7kCustomBeams`: 빔 수 모드 (EDF=1020, Orsted=512)
- `@tr.dev.7kRange`: 탐지 범위 (EDF=90m, Orsted=15m)
- `@tr.dev.7kGain`: 수신 이득 (EDF=5dB, Orsted=58dB)
- `@tr.Comp(1).ApplySvp`: SVP 보정 (EDF=off, Orsted=on)

---

## 3. Ping Record Layout

### 3.1 EDF Layout (Variable Size: 67K / 72K / 140K)

EDF 핑은 **3종류가 교대**로 나타남:
- **Full (140K)**: 34블록 — TT + 수심 + across-track + 후방산란 + snippet + 타임스탬프
- **Medium (72K)**: 18블록 — TT + across-track + 부분 수심
- **Snippet-only (67K)**: 16블록 — TT + snippet만

**Full Ping Block Map (34 blocks × 4096B):**

| Block | Offset | Content | Type | Confidence |
|-------|--------|---------|------|-----------|
| 0 | +0 | Two-Way Travel Time (1024 beams) | f32 | HIGH |
| 1 | +4096 | Sparse config (sampling rate=34722Hz) | f32 | HIGH |
| 2 | +8192 | Range Gate (constant 27.0ms) | f32 | HIGH |
| 3 | +12288 | Detection Window (V-shape, 0.55-27ms) | f32 | HIGH |
| 4 | +16384 | Detection Quality (0-1) | f32 | HIGH |
| 5-6 | +20480 | Along-track angles (near-zero) | f32 | MEDIUM |
| 7-14 | +28672 | Snippet/Water-column (8 blocks) | u16 | HIGH |
| 15 | +61440 | **Across-Track Distance** (signed, m) | f32 | HIGH |
| 16 | +65536 | **Backscatter Intensity** (53-2245) | f32 | HIGH |
| 17-18 | +69632 | Snippet data (2 blocks) | u16 | MEDIUM |
| 19 | +77824 | Along-track angle 3 | f32 | LOW |
| 20-28 | +81920 | Snippet + angle blocks | mixed | LOW |
| 29 | +118784 | **Beam Azimuth** (degrees, ~81 valid) | f32 | HIGH |
| 30 | +122880 | **Depth — Primary Detection** (neg, m) | f32 | HIGH |
| 31 | +126976 | **Depth — Phase Detection** (complementary) | f32 | HIGH |
| +127300 | - | **Beam Flags** (u8: 0=good, 11=flagged) | u8 | HIGH |
| +131402 | - | **Ping Timestamp** (f64, ms since epoch) | f64 | HIGH |
| 32-33 | +131072 | Trailing snippet data | u16 | LOW |

### 3.2 Orsted Layout (Fixed 69,888B = 17 × 4096 + 256)

**모든 핑이 동일 크기** — 고정 stride로 탐색 가능.

| Block | Offset | Content | Type | Confidence |
|-------|--------|---------|------|-----------|
| 0 | +0 | **Two-Way Travel Time** (1024 beams) | f32 | HIGH |
| 1 | +4096 | Sparse config (~28 nonzero values) | f32 | LOW |
| 2 | +8192 | Range Gate (constant 27.0ms) | f32 | HIGH |
| 3 | +12288 | Detection Window (V-shape) | f32 | HIGH |
| 4 | +16384 | Detection Quality (0-1 normalized) | f32 | HIGH |
| 5-6 | +20480 | Quality fields (near-zero) + some FF08 | f32 | MEDIUM |
| 7-10 | +28672 | **Snippet + embedded FF08 records** | mixed | MEDIUM |
| 11 | +45056 | **Across-Track Distance** (signed, m) | f32 | **HIGH** |
| 12 | +49152 | Along-Track Distance (sparse) + FF08 | f32 | MEDIUM |
| 13 | +53248 | **Depth — Primary Detection** (neg, m) | f32 | HIGH |
| 14 | +57344 | **Depth — Phase Detection** (complementary) | f32 | HIGH |
| 15 | +61440 | Beam Flags + FF08 records | mixed | MEDIUM |
| 16 | +65536 | Backscatter + FF08 records | mixed | MEDIUM |
| Tail | +69632 | 56×u16 azimuth indices + 32B config trailer | u16 | LOW |

**Primary + Phase Detection 조합:**
Block 13에 값이 있는 빔은 amplitude detection (975빔),
Block 14에 값이 있는 빔은 phase detection (49빔).
합산하면 **1024빔 완전 커버리지** 달성.

---

## 4. FF08 Sensor Records

### 4.1 공통 구조

```
FF 08 00 [type:u8] [size:u16 LE]
```

### 4.2 레코드 타입 매핑 (크기 기반)

| Size | Content | EDF Type | Orsted Type |
|------|---------|----------|-------------|
| 81B | Navigation (lat/lon/heading) | 1 | 1 |
| 39B | GNSS raw (50Hz) | 2 | 2 |
| 59B | MRU raw | 4 | 4 |
| 59B | Sensor status (heading/roll) | 10 | 8 |
| 59B | Tide/sealevel | 12 | 10 |
| 119B | Attitude (pitch/roll/hdg/heave) | 8 | 6 |
| 29B | Clock/sync | 9 | 7 |
| 155B | Computed ping params | 13 | **없음** |

### 4.3 59B Tide Payload

```
Offset 0:  u16   data_echo (52)
Offset 2:  f64   timestamp (ms since epoch)
Offset 14: bytes  0A 00 0A 00 0A 00 (marker)
Offset 20: f64   sealevel_value (m)
```

### 4.4 119B Attitude Payload

```
Offset 0:  u16   data_echo
Offset 2:  f64   timestamp
Offset 14: f64   heading (degrees)
Offset 22: f64   course (degrees)
Offset 30: f64   roll (degrees)
Offset 38: f64   pitch (degrees)
Offset 46: f64   heave (m)
```

### 4.5 위치 차이

| 항목 | EDF | Orsted |
|------|-----|--------|
| FF08 배치 | 핑 **외부** 별도 영역 | 핑 **내부** 블록 5-16에 임베딩 |
| Tide 레코드 수 | ~522 | ~57 |
| Computed 레코드 | 4,019개 (155B) | 없음 |
| 타입 번호 체계 | 표준 | 재매핑 (같은 크기, 다른 번호) |

---

## 5. Cross-Format Validation

### 5.1 부호 규약 (Sign Convention)

| Format | Depth Convention | Reference |
|--------|-----------------|-----------|
| PDS | **Negative** (아래 = 음수) | Transducer 기준 |
| GSF | **Positive** (아래 = 양수) | Transducer 기준 |
| ASCII | **Negative** (아래 = 음수) | Chart Datum 기준 |

### 5.2 GSF ↔ ASCII 비교 (Orsted, 2885 pings)

| 항목 | 결과 |
|------|------|
| 핑 수 | **완벽 일치** (2885 = 2885) |
| 빔 수 | 95.8% 일치 (ASCII는 플래그 빔 제거) |
| 수심 오프셋 | -2.05m (draft + tide 보정 차이) |
| 타임스탬프 | **<1ms** 일치 |
| 품질값 | ASCII = GSF × 2.0 (정확한 스케일링) |

### 5.3 Datum Chain (수심 참조 체인)

```
PDS Depth (transducer 기준)
  → + draft (흘수)
  → + tide (조위)
  → + heave (동요 보정)
  = ASCII Depth (chart datum 기준)

Offset ≈ 2.05m (Orsted), 가변 (heave/tide에 따라 ±1.2m)
```

---

## 6. Supporting File Formats

| Format | Extension | Description | File Count |
|--------|-----------|-------------|------------|
| GSF | .gsf | Generic Sensor Format (수심+nav) | 1,038 |
| NSF | .nsf | GSF Index (`INDEX-GSF-v02.00`) | 1,038 |
| FAU | .fau | Fugro ASCII Uncorrected (빔별 수심) | 529 |
| ASCII | .txt | Gridded/beam depth export | 117 |
| HVF | .hvf | Heading Verification File (시계열) | 4 |
| XTF | .xtf | Extended Triton Format (SSS data) | 312 |
| GPT | .gpt | GPS Track (lat/lon polyline) | 14 |
| S7K | .s7k | Reson raw sonar (514MB/file) | 9 |
| CSAR | .csar | CARIS Surface Archive | 2 |
| GeoTIFF | .tiff | QC surfaces (DTM/TVU/THU/Slope) | 8 |

---

## 7. Remaining Unknowns

| Item | Description | Impact |
|------|-------------|--------|
| EDF Block 1 sparse values | 28개 nonzero f32 중 3개만 해독 (sampling rate) | Low |
| EDF Blocks 5-6, 19, 23 | Near-zero f32 배열 (steering angles?) | Low |
| Orsted Block 1 | 28 nonzero values — 소나 config 파라미터 | Low |
| Orsted tail 256B | 56 u16 azimuth indices + 32B trailer | Low |
| Snippet block 내부 구조 | u16 페어 인코딩, 빔-to-snippet 매핑 | Medium |
| TT-only pings (EDF 8K) | depth/angle 없는 축소 핑 (4-9/14) | Low |
| Orsted per-ping timestamp | 없음 — FF08 nav에서 보간 필요 | **High** |
| L103 파일 파싱 실패 | 유일한 BAD 파일 (Orsted) | Low |

---

## 8. Parser Architecture (pds_toolkit)

```
pds_toolkit/
├── pds_binary.py      # PDS 바이너리 파서
│   ├── read_pds_binary()   → PdsBinaryData (full parse)
│   ├── pds_nav_only()      → List[PdsNavRecord] (nav only)
│   └── pds_binary_info()   → dict (quick file info)
├── gsf_reader.py      # GSF 파서
│   └── read_gsf()          → GsfFile
└── (future)
    ├── pds_text_header.py  # @tr. key 파서
    └── pds_cross_validator.py  # PDS↔GSF↔ASCII 교차 검증
```

**핑 감지 알고리즘:**
1. TT V-shape 스캔 (EDF: 99% 정확도)
2. Depth-block chain 감지 (Orsted: scored chain with stride=69888)
3. Gap threshold: 71,000 bytes (EDF/Orsted 자동 판별)

---

## 9. Implications for MBES QC Tool

### QC에 필요한 데이터와 소스:

| QC 항목 | 필요 데이터 | Best Source |
|---------|-----------|-------------|
| 수심 통계 | depth per beam | PDS Block 13+14 / GSF |
| 커버리지 | lat/lon + across-track | GSF (가장 안정적) |
| 핑 간격 | timestamps | GSF (sub-ms 정밀도) |
| 빔 품질 | quality flags | PDS Block 4 / GSF flags |
| 후방산란 | backscatter intensity | PDS Block 16 (EDF only) |
| 크로스라인 | depth at overlapping positions | GSF 기반 계산 |
| 조위 보정 | tide values | PDS FF08 (59B, type 12/10) |
| SVP 적용 | sound velocity profile | PDS text header |
| HVF | heading offsets per line | HVF 파일 직접 파싱 |

**권장 전략:** GSF를 1차 소스로, PDS를 보조(센서레코드/설정) 소스로 사용.
ASCII는 최종 납품물 검증 용도.

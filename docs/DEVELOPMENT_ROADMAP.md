# MBESQC Development Roadmap

> **Version:** 1.0
> **Date:** 2026-03-26
> **Status:** Feature-Complete Core, Desktop Migration Planned

---

## 1. Current System Overview

### Architecture
```
MBESQC (~12,800 LOC)
├── pds_toolkit/    (3,900 LOC)  — 9 format readers (PDS, GSF, FAU, HVF, S7K, XTF, CSV, GPT)
├── mbes_qc/        (5,300 LOC)  — 8 QC modules + runner + reporting + export
├── desktop/        (3,600 LOC)  — PySide6 desktop app (production)
├── web_app.py      (600 LOC)    — Flask web app (port 5103, deprecated)
└── docs/                        — PDS format specs, reverse-engineering reports
```

### Data Source Strategy (확정)
```
QC 데이터 소스:
├── GSF (1차)  — 핑/수심/좌표/빔/attitude/SVP — 100% 신뢰
├── PDS (보조) — 센서레코드(nav/attitude/tide) + 소나 설정(@tr. header)
└── ASCII (검증) — 최종 납품물 수심 확인
```

**근거:** Orsted PDS의 FF08 센서레코드가 핑 데이터 블록 내부에 임베딩되어 수심 복구율 31%에 불과.
GSF는 PDS에서 export된 결과이지만 핑 데이터 완전성 100%. (상세: `docs/pds_reverse_engineering_report.md`)

---

## 2. 8 QC Modules — 현재 구현 상태

### A. File QC (파일 무결성)
| 항목 | 상태 | 설명 |
|------|------|------|
| 파일 크기 검증 | ✅ | 비정상 크기 탐지 |
| 네이밍 일관성 | ✅ | 접두사 패턴 매칭 |
| GSF↔PDS 매칭 | ✅ | 파일 쌍 완전성 |
| 시간 연속성 | ✅ | 1시간 이상 갭 탐지 |
| 좌표계 일관성 | ✅ | 파일 간 CRS 확인 |

### B. Vessel QC (선박/오프셋 설정)
| 항목 | 상태 | 설명 |
|------|------|------|
| PDS 헤더 추출 | ✅ | 92+ 섹션, @tr. 키 파싱 |
| HVF 오프셋 비교 | ✅ | Transducer 위치 검증 (0.01m 허용) |
| Roll/Pitch 교정값 | ✅ | Static 교정 확인 |
| Motion 플래그 | ✅ | Apply Roll/Pitch/Heave 확인 |
| SVP 적용 상태 | ✅ | 프로파일 파일명, 적용 여부 |

### C. Offset QC (오프셋 검증)
| 항목 | 상태 | 설명 |
|------|------|------|
| Roll bias | ✅ | Port/Stbd 빔 수심 비대칭 탐지 |
| Pitch bias | ✅ | 정역 라인 나디르 수심 차이 |
| HVF 비교 | ✅ | 적용 오프셋 vs 데이터 추정 bias |
| 판정 기준 | ✅ | Pass <0.1°, Warning >0.5° |

### D. Motion QC (모션 검증)
| 항목 | 상태 | 설명 |
|------|------|------|
| Roll/Pitch/Heave 통계 | ✅ | mean, std, min, max |
| Heading 통계 | ✅ | 라인별 heading 일관성 |
| Spike 탐지 | ✅ | 변화율 이상치 |
| IMU 갭 탐지 | ✅ | 0.5초 이상 누락 |
| 판정 기준 | ✅ | Roll std >3° warning, >10° fail |

### E. SVP QC (음속 보정)
| 항목 | 상태 | 설명 |
|------|------|------|
| SVP 적용 플래그 | ✅ | PDS 헤더에서 확인 |
| 프로파일 수/시간분포 | ✅ | 충분한 시공간 커버리지 |
| 속도 범위 | ✅ | 1400-1600 m/s 합리성 |
| 외곽 빔 굴절 | ✅ | outer/nadir 비율 ≠ 1.0 탐지 |

### F. Coverage QC (커버리지)
| 항목 | 상태 | 설명 |
|------|------|------|
| 라인별 통계 | ✅ | heading, length, depth, swath width |
| 총 면적/라인 길이 | ✅ | 전체 서베이 커버리지 |
| 갭 탐지 | ✅ | 미충전 영역 |
| 오버랩 비율 | ✅ | 기본 최소 10% |
| 트랙라인 내보내기 | ✅ | 시각화용 |

### G. Cross-line QC (크로스라인)
| 항목 | 상태 | 설명 |
|------|------|------|
| 교차점 탐지 | ✅ | 그리드 기반 셀 매칭 |
| 수심 차이 통계 | ✅ | mean, std, RMS, max |
| IHO S-44 판정 | ✅ | TVU 기준 자동 Pass/Fail |
| Striping 탐지 | ✅ | 줄무늬 아티팩트 |

### H. Surface Generation (서피스)
| 항목 | 상태 | 설명 |
|------|------|------|
| DTM (평균 수심) | ✅ | 그리드 셀별 평균 |
| Density (포인트 밀도) | ✅ | 셀당 포인트 수 |
| Std (표준편차) | ✅ | 수심 변동성 |
| Slope (경사) | ✅ | DTM 기울기 (도) |
| TVU/THU 그리드 | ✅ | 수직/수평 불확실성 |
| GeoTIFF 내보내기 | ✅ | rasterio 기반 |

---

## 3. QC Scoring System

8개 모듈의 가중 점수:

| Module | Weight | Pass | Warning | Fail |
|--------|--------|------|---------|------|
| File | 5% | 100 | 70 | 0 |
| Vessel | 10% | 100 | 70 | 0 |
| Offset | 15% | 100 | 70 | 0 |
| Motion | 15% | 100 | 70 | 0 |
| SVP | 10% | 100 | 70 | 0 |
| Coverage | 15% | 100 | 70 | 0 |
| Cross-line | 20% | 100 | 70 | 0 |
| Surface | 10% | 100 | 70 | 0 |

**총점 = Σ(module_score × weight)** → ScoreRing 위젯으로 시각화

---

## 4. Report Output

| Format | 용도 | 상태 |
|--------|------|------|
| **Excel** (.xlsx) | 상세 데이터 + 색상 Pass/Fail | ✅ |
| **Word** (.docx) | 내러티브 보고서 | ✅ |
| **PowerPoint** (.pptx) | DQR (Data Quality Report) | ✅ |
| **GeoTIFF** | DTM/TVU/THU 그리드 서피스 | ✅ |
| **Shapefile** | 트랙라인 | ✅ |
| **KML** | Google Earth | ✅ |

---

## 5. Desktop Migration Plan

### 현재 상태
- **PySide6 Desktop** (`desktop/`): v1.0 배포, 기본 기능 완성
- **Flask Web** (`web_app.py`): 기능적이지만 deprecated 예정

### 마이그레이션 계획
1. ~~Web UI 기능을 Desktop으로 이전~~ → 이미 완료
2. `web_app.py` → 향후 API 서버로만 유지 (UI 제거)
3. Desktop에서 모든 QC 워크플로우 완성

### Desktop 현재 구조
```
desktop/
├── main.py                 — GeoViewApp (PySide6)
├── app_controller.py       — 시그널 기반 네비게이션
├── panels/
│   ├── dashboard_panel.py  — 프로젝트 목록
│   ├── upload_panel.py     — 드래그앤드롭 파일 임포트
│   ├── project_form_panel.py — 선박/오프셋/SVP 설정
│   ├── analysis_panel.py   — 8모듈 QC 결과 + ScoreRing
│   └── project_detail_panel.py — 내보내기/보고서
├── services/
│   ├── data_service.py     — SQLite CRUD
│   ├── analysis_service.py — 워커 스레드 QC 실행
│   ├── export_service.py   — 보고서/서피스 생성
│   └── chart_renderer.py   — 차트 렌더링
└── widgets/
    ├── score_ring.py       — 원형 점수 위젯
    ├── qc_unlock_grid.py   — 모듈별 잠금 해제 그리드
    ├── drop_zone.py        — 파일 드롭 영역
    └── toast.py            — 알림 토스트
```

---

## 6. Supported Data Formats

### Input (9 formats)
| Format | Extension | Reader | LOC | Notes |
|--------|-----------|--------|-----|-------|
| PDS Binary | .pds | pds_binary.py | 1,492 | EDF 100%, Orsted 98% |
| PDS Header | .pds | pds_header.py | 200 | 92+ INI 섹션 |
| GSF | .gsf | gsf_reader.py | 800 | 1024 beams, attitude, SVP |
| FAU | .fau | fau_reader.py | 150 | Fledermaus XYZ |
| HVF | .hvf | hvf_reader.py | 180 | 선박 오프셋/교정 |
| S7K | .s7k | s7k_reader.py | 250 | Reson 7k raw |
| XTF | .xtf | xtf_reader.py | 200 | SSS 데이터 |
| CSV | .csv/.txt | csv_reader.py | 150 | 수심 그리드 |
| GPT | .gpt | gpt_reader.py | 50 | GPS 트랙 |

### 보조 포맷 (파싱 불필요)
| Format | Extension | 용도 |
|--------|-----------|------|
| NSF | .nsf | GSF 인덱스 (INDEX-GSF-v02.00) |
| GeoTIFF | .tiff | QC 서피스 출력 |
| CSAR | .csar | CARIS Surface Archive |

---

## 7. 향후 개선 방향 (Priority Order)

### Phase 1: Desktop 완성 (단기)
- [ ] Web UI 의존 코드 정리
- [ ] Desktop에서 실시간 프로그레스바 (QC 실행 중)
- [ ] 대용량 파일 처리 최적화 (900+ GSF 파일)
- [ ] 라인별 상세 QC 결과 뷰

### Phase 2: QC 고도화 (중기)
- [ ] **Backscatter QC** — 후방산란 균일성 (PDS Block 16 활용)
- [ ] **Tide QC** — 조위 보정 적합성 (FF08 tide records)
- [ ] **Navigation QC** — GNSS 품질/DOP (FF08 nav records)
- [ ] **Beam-level flagging** — 개별 빔 플래그 시각화
- [ ] **IHO S-44 Order 3/4** — 추가 정확도 기준

### Phase 3: 자동화/연동 (장기)
- [ ] Batch QC — 프로젝트 단위 자동 실행
- [ ] OffsetManager 양방향 연동
- [ ] CARIS/EIVA NaviPac 데이터 직접 임포트
- [ ] 3D 스와스 시각화

---

## 8. PDS Format Knowledge (Quick Reference)

### Orsted Ping Layout (stride 69888)
```
Block  0: TT (V-shape)
Block  4: Quality (0-1)
Block 11: Across-track (signed, m)
Block 13: Primary Depth (neg, m)
Block 14: Phase Depth (complementary)
Blocks 5-10, 15-16: FF08 sensor records embedded
```

### EDF Ping Layout (variable: 67K/72K/140K)
```
+0:       TT
+123204:  Depth
+115004:  Across-track
+127300:  Beam flags
+131402:  Timestamp
```

### FF08 Sensor Records (size-based ID)
| Size | Content |
|------|---------|
| 81B | Navigation (lat/lon/heading) |
| 59B | MRU/Tide/Status (type# varies by vessel) |
| 119B | Attitude (pitch/roll/heading/heave) |
| 29B | Clock |
| 155B | Computed (EDF only) |

**상세:** `docs/pds_reverse_engineering_report.md`

---

## 9. Test & Verification

```bash
# 전체 검증 (13 모듈)
cd E:\Software\QC\MBESQC
python tests/verify_all.py

# 데스크탑 실행
python -m desktop

# 프로그래밍 사용
from mbes_qc.runner import run_full_qc
result = run_full_qc(gsf_dir="path/to/gsf", hvf_path="path/to/vessel.hvf")
```

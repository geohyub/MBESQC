# MBESQC

> 멀티빔 음향측심기(MBES) 데이터 품질 검사 데스크톱 애플리케이션

## Overview

해양 조사 현장에서 수집된 MBES 데이터의 QC를 수행하는 PySide6 데스크톱 앱입니다.
사전 처리 검증, 인터랙티브 3D 시각화, insight 내러티브, 그리고 Excel/Word/PDF 보고서 출력을 지원합니다.
센서 오프셋은 OffsetManager API를 우선 사용하며, 시작 시 `MBESQC_OM_BASE_URL`,
`MBESQC_OM_TIMEOUT_SECONDS`, 또는 `python -m desktop --om-base-url ... --om-timeout-seconds ...`로
런타임 경계를 명시적으로 고정할 수 있습니다.

## Key Features

- Pre-processing 검증 가시성 (offset 이력 + approval 상태 연동)
- 인터랙티브 3D 수심 시각화 (PyQtGraph)
- 9개 분석 모듈 (수심, crossline residual, 밀도, 모션, 노이즈 등)
- Insight 내러티브 레이어 — 점수가 아닌 "왜 이 결과인가" 설명
- Excel / Word / PDF / HTML export
- 한국어 / 영어 bilingual UI
- 레거시 Flask 웹 모드 (포트 5103) 병행 지원

## Tech Stack

- **Desktop UI**: PySide6
- **Charts**: PyQtGraph
- **Legacy Web**: Flask 5103
- **DB**: SQLite (mbesqc.db), OffsetManager는 API 우선 + 명시적 DB 경로 보조
- **Export**: openpyxl, python-docx, reportlab

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Operator self-check
python -m desktop --self-check

# Operator self-check with a bounded local proof packet
python -m desktop --self-check --self-check-report .\artifacts\desktop-self-check.json

# PySide6 desktop (권장)
python -m desktop

# Legacy web
python web_app.py
```

## Dependencies

- OffsetManager: API(기본 `http://localhost:5302`) 우선 사용, startup/env override 가능
- Override env vars: `MBESQC_OM_BASE_URL`, `MBESQC_OM_TIMEOUT_SECONDS`
- DB fallback: 필요할 때만 프로젝트/환경 변수의 명시적 DB 경로 + 확인 상태를 통해 보조 사용
- Windows launcher: `run.bat`는 self-check를 먼저 출력한 뒤 desktop을 시작함
- `--self-check-report`는 runtime boundary proof packet만 JSON으로 저장하며, OffsetManager 데이터 무결성이나 project DB proof를 대신하지 않습니다.

## License

Proprietary — Junhyub Kim, GeoView Data QC Team

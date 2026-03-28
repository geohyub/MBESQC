# MBESQC

> 멀티빔 음향측심기(MBES) 데이터 품질 검사 데스크톱 애플리케이션

## Overview

해양 조사 현장에서 수집된 MBES 데이터의 QC를 수행하는 PySide6 데스크톱 앱입니다.
사전 처리 검증, 인터랙티브 3D 시각화, insight 내러티브, 그리고 Excel/Word/PDF 보고서 출력을 지원합니다.
센서 오프셋은 OffsetManager의 `offsets.db`를 직접 참조합니다.

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
- **DB**: SQLite (mbesqc.db), OffsetManager offsets.db 직접 참조
- **Export**: openpyxl, python-docx, reportlab

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# PySide6 desktop (권장)
python desktop/main.py

# Legacy web
python app.py
```

## Dependencies

- OffsetManager: `offsets.db` 경로 설정 필요 (web_app.py `OM_DB_PATH`)

## License

Proprietary — Junhyub Kim, GeoView Data QC Team

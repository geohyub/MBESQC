# GeoView Quality Gate

> 모든 GeoView 프로그램 세션에 공통 적용되는 품질 기준.
> 이 파일은 E:/Software/QUALITY_GATE.md의 사본이며, 각 프로그램 루트에 배치된다.

## 코드 변경 시 필수

- [ ] `py_compile` 전체 통과 (Python) 또는 `npm run build` 통과 (Node.js/Tauri)
- [ ] 기존 테스트 회귀 없음 (`pytest` 또는 `npm run test`)
- [ ] `SESSION_STATUS.md` 해당 섹션 갱신
- [ ] `WORKLOG.md`에 Date/Scope/Summary/Files/Verification/Next 형식 기록

## 안전 규칙 (절대)

- `shutil.rmtree` 사용자 데이터 경로 금지
- 하드코딩 절대 경로 금지 (상대 경로 또는 환경 변수)
- DB 스키마 변경 시 의존 프로그램 영향 범위 확인 필수

## UI 품질 (PySide6 프로그램)

- 이모지 사용 금지
- 과학적 표기 금지 (숫자는 콤마 구분 실제값)
- 차트는 인터랙티브 필수 (PyQtGraph, matplotlib 정적 이미지 금지)
- 다크 테마 (#0A0E17 → #131A2B → #1A2236)
- 폰트: Pretendard / Geist / Geist Mono
- 한국어 기본, ko <-> en 전환 권장

## 문서화

- WORKLOG.md 기록 형식:
  ```
  ## YYYY-MM-DD HH:MM — [프로그램명] [작업 유형]
  Scope:    [경로] — [신규/개선/수정/검증]
  Summary:  한 줄 요약
  Files:    수정 파일 목록
  Verification: py_compile/pytest/smoke 결과
  Next:     다음 작업 또는 남은 리스크
  ```
- SESSION_STATUS.md: 프로그램별 현재 상태 + remaining risk

## 핸드오프

작업 완료 후 다음 에이전트(Claude/Codex)가 WORKLOG + SESSION_STATUS만 읽고
바로 이어받을 수 있는 수준의 컨텍스트를 남겨야 한다.

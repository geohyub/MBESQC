# MBESQC

## 역할: mbes-specialist

너는 GeoView의 MBES QC 전문가다.
멀티빔 음향측심 데이터의 9모듈 QC 분석, 채점, Export를 담당한다.

관련 교훈: `C:/Users/JWONLINETEAM/.claude/projects/E--Software/memory/project_mbesqc_status.md`

## 의존성 주의

OffsetManager 접근은 `desktop/services/om_client.py`의 runtime boundary를 통해서만 핀셋처럼 조절한다.
기본값은 `http://localhost:5302`이며, 시작 시 `MBESQC_OM_BASE_URL` / `MBESQC_OM_TIMEOUT_SECONDS`
또는 `python -m desktop --om-base-url ... --om-timeout-seconds ...` /
`desktop.main.main(om_base_url=..., om_timeout_seconds=...)`로만 오버라이드한다.
숨은 env DB fallback은 사용하지 않는다.

운영자 기본 진입점은 `python -m desktop --self-check`로 OM 경계를 먼저 확인한 뒤
`python -m desktop` 또는 `run.bat`로 본 앱을 시작하는 경로다.

## 품질 게이트

이 프로그램의 모든 코드 변경은 `QUALITY_GATE.md`를 준수해야 한다.

## 세션 시작 전 읽기 순서

1. `QUALITY_GATE.md` (이 디렉토리)
2. `E:/Software/CLAUDE.md` (루트)
3. `E:/Software/SESSION_STATUS.md`
4. `E:/Software/WORKLOG.md`

## 참조

- 루트 문서: `E:/Software/CLAUDE.md`
- 마스터 운용: `E:/Software/PAPERCLIP_MASTER_PROMPT.md`
- 품질 게이트: `QUALITY_GATE.md`
- 전체 소프트웨어 맵: `E:/Software/SOFTWARE_MAP.md`

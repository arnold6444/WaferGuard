# 진행 기록

> 이 문서는 시간순 진행 기록입니다. 과거 시점의 구현 상태는 당시 기준 설명이며, **현재 상태는 맨 아래 최신 항목과 `README.md` / `docs/spec.md`를 우선**합니다.

## 2026-07-18 — Chamber Resistance AI MVP 완료

- [x] Colab 알고리즘과 샘플 데이터 구조 확인
- [x] 샘플 3,000행 분석 결과 재현
- [x] `챔버 이상탐지 / Chamber AI` 메뉴 추가
- [x] 정상 패턴, EQP 날짜 추세, 이상 맵, 이상 점수, 판정표 구현
- [x] EQP 선택 상호작용 구현
- [x] 프론트엔드 production build 성공
- [x] 브라우저 내용 렌더링, 오류 오버레이 없음, EQP55 선택 반영 확인

검증된 샘플 결과:

- OOF MAE: 0.527 Ω
- OOF RMSE: 0.618 Ω
- 행 이상 임계값: 1.097 Ω
- 최종 이상 후보: EQP10, EQP55
- 이상 행: 48 / 3,000 (1.6%)

이 구현은 현재 `Resistance > Static Demo`로 유지됩니다.

## 2026-08-02 — wafer_particle Vision AI 병합 완료

- [x] 기존 저항 분석 기능·기본 탭 유지
- [x] 통계 / 정상 전용 AI / 비교 3개 영상 판정 모드 추가
- [x] 100개 챔버 상태판·TOP 3·30일 추세·시점·방향 구현
- [x] CH-061·047·007 원본·통계 히트맵·AI 히트맵 포함
- [x] 선택한 영상 근거를 `POST /api/v1/inspect`와 Inspection Agent에 연결
- [x] Vision AI 전용 판정 근거와 샘플 해석 경계 추가
- [x] 프론트엔드 production build 성공
- [x] Python 스키마·판정 룰 검증 성공
- [x] 실제 브라우저에서 모드 전환, CH-047 선택, 5개 이미지 로딩 확인
- [x] CH-047이 `WF-CH047-D09` 검사 건으로 등록되고 기존 Inspection Agent 화면이 열리는 것 확인

검증된 `wafer_particle` 샘플 결과:

- 3,000 images / 100 chambers / 300×300 px
- 이상 챔버: CH-061, CH-047, CH-007
- 이상 이미지: 51장
- 통계·AI 판정 일치: 3,000 / 3,000
- 해석 경계: 고유 정상 1종·불량 3종이 반복된 과제 샘플이며 실제 팹 성능을 의미하지 않음

## 2026-08-10 — main 코드/문서 동기화 (정적 구조 확인 시점)

이 항목은 **Multivariate Live 기능 병합 직전의 main 상태를 기록한 historical snapshot**입니다.

당시 확인한 상태:

- Resistance AI: CSV → offline analysis script → `chamberSample.json` → React visualization
- Vision AI: external analyzer → snapshot builder → `waferVisionSample.json`/대표 이미지 → React visualization
- Vision → Inspection Agent API handoff: 구현됨
- 실제 설비 streaming / production threshold / 자동 장비 제어: 미구현
- 기존 MLOps retrain/promote/rollback: workflow 검증용 simulation

아래 Live/MLOps 구현이 같은 날 이후 병합되면서 Resistance 현재 상태가 변경되었습니다.

## 2026-08-10 — Multivariate Chamber Resistance Live/MLOps 구현

- [x] recipe/setpoint, 장비 bias, cleaning/seasoning, 연속 시계열을 가진 `EtchTelemetryGenerator` 구현
- [x] `idle`/`running`/`cleaning`/`maintenance`/`alarm` 및 8종 synthetic anomaly injection 구현
- [x] `chamber_telemetry`, `chamber_predictions`, `chamber_model_registry`와 조회 index 추가
- [x] numeric/categorical shared feature transform, unknown-safe OneHotEncoder, GradientBoosting 실제 `.fit()` 구현
- [x] time-based holdout의 MAE/RMSE, USE_TIME-only baseline, Production 동일 holdout 비교 구현
- [x] robust residual threshold, feature importance, joblib pipeline artifact 저장/재검증 구현
- [x] warm-up bootstrap → Production reload → Actual/Expected/residual/anomaly 저장 구현
- [x] 최근 median error + 신규 clean row readiness, `force=true` demo override, Staging candidate 구현
- [x] artifact 검증, 기존 Production Archived, 선택 버전 Production promotion 구현
- [x] Chamber 전용 status/equipment/telemetry/predictions/models/retrain/promote API 추가
- [x] 기존 Static Demo를 유지하면서 2초 polling Live dashboard, 공정 delta, model/importance UI 추가
- [x] generator·cleaning·gas drift·baseline 비교·RF 원인 anomaly·model lifecycle 테스트 추가

검증 기록:

- `python -m pytest tests/test_chamber.py -q -W error ...` → 6 passed, warning 0
- `python -W error scripts/smoke_test.py` → passed
- `python -m compileall -q app scripts tests` → passed
- Vite production build → passed
- `git diff --check` → passed

## 2026-08-10 — 문서 재동기화

최신 `main` 코드 기준으로 README/spec/plan/decision 문서를 다시 확인했습니다.

- [x] Live와 Static Demo를 명확히 분리
- [x] 로컬 SQLite `outputs/waferguard.db` / PostgreSQL(RDS) 전환 구조 명시
- [x] 기존 workflow 9개 table + Chamber 3개 table이 같은 runtime DB backend를 사용함을 명시
- [x] Chamber sklearn artifact가 `runtime/models/chamber/`에 저장됨을 명시
- [x] 기존 wafer MLOps simulation과 Chamber 실제 sklearn lifecycle을 분리해 설명
- [x] Chamber readiness는 계산하지만 자동 scheduler가 `retrain()`을 호출하지는 않는 현재 경계 명시
- [x] simulator의 `wafer_count_since_clean` sample-driven counter, 순차 equipment clock, 유한 stream runner를 known simplification으로 기록

### 현재 구현 경계

- Chamber Resistance Live: synthetic stream → DB → 실제 sklearn fit/inference → residual anomaly → Staging/Production lifecycle
- Chamber Static Demo: 기존 CSV/USE_TIME offline snapshot 유지
- Vision AI: external `wafer_particle` 분석 결과 snapshot 표시
- Vision → Inspection Agent handoff: 구현됨
- 로컬 DB: SQLite `outputs/waferguard.db`; 운영 전환: PostgreSQL/RDS
- 실제 설비 연결 / 실제 Fab control limit / 자동 장비 제어: 미구현
- Chamber Production 승격: 명시적 promote
- Chamber retrain: readiness gate + API/UI trigger, 자동 주기 retrain scheduler는 아직 미구현
- 기존 wafer MLOps: simulation, Chamber MLOps: 실제 sklearn artifact/registry

## 2026-08-11 — Fab Quality Ops UI/Architecture 개편

- [x] 7개 최상위 메뉴와 Fab Overview 추가
- [x] 8대 공정 profile과 Etch full-detail / 7개 demo-not-connected 구조 추가
- [x] 기존 Chamber Live/Static, Actual/Expected, model lifecycle를 Process Monitoring으로 이동
- [x] `process_events` schema/index/query와 Chamber anomaly projection 추가
- [x] 검사 이전 30분의 temporal candidate와 Lot 인접 Wafer를 Agent evidence에 연결
- [x] Runtime DB 우선 Lot summary/Wafer grid/timeline/누적 defect map 추가
- [x] 기존 InspectionView를 Wafer Detail에서 재사용
- [x] 기존 Vision snapshot을 Wafer Detail의 Vision Evidence와 demo fallback으로 재사용
- [x] 기존 AgentView를 최상위 AI Analysis에서 재사용하고 DB inspection deep-link 보강
- [x] Data & RAG browser에 `process_events` 추가
- [x] 신규 backend test 4개 + Chamber 회귀 6개 warning-free 통과
- [x] 확장 smoke test와 frontend production build 통과
- [x] 실제 브라우저 사용자 흐름과 오류·경고 없는 콘솔 최종 확인

현재 데이터 경계:

- Etch telemetry/model은 synthetic runtime이다.
- Inspection은 WM-811K/proxy 및 synthetic wafer 흐름이다.
- `LOT-VISION-DEMO-042`와 Etch 외 7대 공정 profile은 demo다.
- 누적 defect runtime 좌표는 ROI center proxy다.
- process-to-Wafer 연결은 시간적 후보이며 인과관계가 아니다.

## 2026-08-11 — Fab 실전 운영 구조 개선

- [x] PostgreSQL 16 Compose, `.env.example`, 명시적 backend/startup validation
- [x] 실제 PostgreSQL schema + Lot/telemetry/prediction/detector/event/inspection/RAG/registry CRUD 검증
- [x] `lots`, telemetry Lot/Wafer/DQ, `anomaly_detections` schema/index/API
- [x] 25-wafer Lot, multi-sample wafer, startup/hold/cleaning/recipe lifecycle
- [x] missing/duplicate/reversal/gap/interval/stuck/range/state/recipe DQ gate와 aggregate event
- [x] VALID-only prediction/training, MAD + EWMA, context threshold fallback
- [x] equipment/recipe/lot holdout 지표, data range, readiness 기반 automatic Staging Candidate
- [x] same-Lot/time evidence, 장비·recipe·인접 wafer·누적 defect context
- [x] 6-section Agent output와 causal uncertainty 경계
- [x] 독립 Fab Scenario Orchestrator의 W13~W15 RF drift → inspection E2E
- [x] 최상위 MLOps 기본 화면을 실제 Chamber lifecycle로 변경, 기존 범용 흐름은 Legacy/Demo로 분리
- [x] SQLite warning-free 전체 test 및 frontend production build

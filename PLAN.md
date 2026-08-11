# Goal

WaferGuard를 synthetic AI 데모 모음이 아니라, Lot을 중심으로 공정 이상과 Wafer 품질 변화를 연결하는 Fab Quality Ops 운영 구조로 정리한다.

최종 흐름은 `Lot → Process telemetry → Data Quality Gate → Expected Resistance → Residual detector → Process event → Wafer inspection → Lot/time correlation → Agent → Engineer review → RAG / Model lifecycle`이다. 실제 Fab 데이터가 들어오면 DB·generator·inspection adapter를 최소 변경으로 교체할 수 있어야 한다.

# Scope

## In Scope

- PostgreSQL을 명시적 로컬 개발 기본 환경으로 승격하고 SQLite를 unit test/demo backend로 유지
- Docker Compose PostgreSQL, `.env.example`, startup validation, PostgreSQL integration test
- `lots` 원장과 Lot 조회 API
- Chamber telemetry의 `lot_id`, nullable `wafer_id`, Data Quality 상태
- 기존 Chamber table을 유지하는 `process_events` 의미 event 계층
- Lot/Wafer/recipe/장비 상태를 가진 Etch 생산 lifecycle simulator
- telemetry sample과 wafer completion count 분리
- YAML 기반 lot/wafer/recipe/equipment variation과 설명 가능한 sensor correlation
- 모델 입력 전 Data Quality Gate와 집계 Data Quality event
- 기존 GradientBoosting Expected Resistance 모델 유지
- MAD primary detector + EWMA secondary detector 및 context threshold
- overall/equipment/recipe/lot 평가 지표
- readiness 기반 Candidate 자동 학습과 수동 Promotion
- Lot/time 기반 Process → Wafer → Agent evidence
- Fab scenario orchestrator와 synthetic E2E test
- 실제 Chamber MLOps를 최상위 MLOps 기본 화면으로 승격
- 기존 Vision, Static Resistance, Generic MLOps의 제품 역할 정리
- README와 `docs/spec.md`, `docs/decisions.md`, `docs/progress.md` 동기화

## Out of Scope

- 실제 Fab 물리 simulator
- Etch 외 7개 공정의 실제 telemetry/model
- 실제 장비 제어와 yield 보장
- Production model 자동 승격
- process anomaly를 defect 원인으로 단정
- 기존 AWS/RDS/S3 계약 제거
- Git push, PR, merge, production deployment

# Current State

## Already Implemented

- SQLite/PostgreSQL DB adapter와 local/S3 object storage
- Chamber synthetic telemetry, 실제 sklearn 학습/artifact, Expected Resistance, residual anomaly, 전용 registry와 Staging/Production
- Inspection, Vision/Metrology evidence, RAG, Agent, Human review
- `process_events` anomaly projection과 검사 이전 30분 후보 조회
- Fab Overview, 8개 Process Profile, Etch Process Monitoring
- Wafer Quality의 runtime Lot 집계/demo fallback, Timeline, Accumulated Defect Map, Vision Evidence
- 최상위 AI Analysis와 기존 Agent deep-link

## Missing or Conflicting

- DB backend 기본값이 SQLite이며 PostgreSQL Compose/startup validation/integration test가 없음
- `lots` table이 없고 Lot summary가 inspection rows에서만 역산됨
- Chamber telemetry에 `lot_id`, `wafer_id`, Data Quality 상태가 없음
- Generator가 running telemetry sample마다 `wafer_count_since_clean`을 증가시킴
- 자동 lifecycle이 사실상 running/cleaning뿐이며 startup/hold/shutdown이 없음
- recipe가 Lot이 아니라 sample count 주기로 변경됨
- Data Quality Gate와 aggregate event가 없음
- residual threshold가 global MAD 하나이며 detector 비교/context threshold/group metrics가 없음
- readiness는 있으나 실제 periodic Candidate trigger에 연결되지 않음
- Agent가 evidence는 받지만 출력 계약이 구조화되어 있지 않음
- 최상위 MLOps는 generic wafer simulation이 기본이며 실제 Chamber lifecycle이 Process 화면에만 있음

# Decisions

- 사용자가 `1-A` 선택: `.env.example`과 표준 로컬 실행은 PostgreSQL이며 backend 선택은 명시한다. 설정 누락·연결 실패 시 SQLite로 조용히 fallback하지 않는다.
- 사용자가 `2-A` 선택: `VALID`만 prediction/training으로 전달한다. `WARNING`은 audit telemetry와 aggregate event만 저장하고 `REJECT`는 모델에서 차단한다.
- 사용자가 `3-A` 선택: Generator와 Inspection을 직접 결합하지 않고 별도 Fab Scenario Orchestrator가 wafer completion 이후 lag를 적용해 연결한다.
- SQLite unit tests는 `STORAGE_BACKEND=sqlite`를 명시한다.
- 기존 `f4ge-anomaly-engine` PostgreSQL이 host 5432를 사용 중이므로 다른 컨테이너를 중단하지 않는다. Compose 기본은 5432로 유지하고 현재 검증은 `POSTGRES_PORT=5433`을 사용한다.
- schema 변경은 현재 프로젝트 방식인 `CREATE TABLE IF NOT EXISTS`와 `_ensure_column`을 확장한다.
- `chamber_telemetry`는 raw/processed sample, `chamber_predictions`는 primary 결과, `anomaly_detections`는 detector별 결과, `process_events`는 의미 있는 운영 event 역할을 가진다.
- Robust MAD를 primary detector로 유지하고 EWMA를 secondary detector로 추가한다.
- threshold 우선순위는 `equipment+recipe → equipment → recipe → global`이며 데이터 부족 시 fallback한다.
- 자동 학습은 기존 automation tick과 stream periodic check에 readiness를 연결하고 Staging Candidate까지만 생성한다. Promotion은 사람 호출만 허용한다.
- Process/Wafer 연결은 같은 Lot과 시간 범위를 우선하고, 시간적 후보라는 문구를 UI·Agent·문서에 유지한다.
- 실제 Chamber MLOps를 최상위 MLOps 기본 화면으로 두고 generic workflow는 Legacy/Demo로 표시한다.

# Architecture / Flow

```text
PostgreSQL (default dev / integration / production)
SQLite     (explicit unit test / lightweight demo)

Lot Lifecycle Simulator
  → lots
  → Chamber telemetry + lot_id + optional wafer_id
  → Data Quality Gate
      → VALID: telemetry → model → detections
      → WARNING/REJECT: audit + aggregated data_quality event
  → Expected Resistance Model
  → MAD primary + EWMA secondary
  → process_events

Fab Scenario Orchestrator
  → wafer completion + configured lag
  → Vision / Metrology / Inspection
  → same Lot + time-window process events
  → structured Inspection Agent evidence
  → Engineer Review / RAG
```

# Implementation Steps

## Step 1 — PostgreSQL Local Runtime

Files:

- `compose.yaml`
- `.env.example`
- `app/services/config.py`
- `app/services/db.py`
- `app/main.py`
- `tests/conftest.py`
- `tests/test_postgres_integration.py`
- `README.md`

Work:

- PostgreSQL 16 service, healthcheck, persistent volume, configurable host port
- explicit backend configuration validation and actionable connection errors
- backend-aware DB overview label
- PostgreSQL schema/CRUD integration coverage

Verify:

- `docker compose config`
- `POSTGRES_PORT=5433 docker compose up -d postgres`
- PostgreSQL healthcheck and integration test
- explicit SQLite test suite

## Step 2 — Lot and Event Data Model

Files:

- `app/services/storage.py`
- `app/services/chamber_storage.py`
- `app/services/process_ops.py`
- `app/main.py`

Work:

- `lots` schema/index/upsert/query
- telemetry `lot_id`, nullable `wafer_id`, Data Quality columns
- `anomaly_detections` schema/index/query
- process event query filters for Lot/equipment/process/recipe/time
- existing SQLite idempotent migration

Verify:

- old SQLite DB opens without loss
- Lot/telemetry/event CRUD tests on SQLite and PostgreSQL

## Step 3 — Production Lifecycle Generator

Files:

- `configs/chamber.yaml`
- `app/services/chamber_generator.py`
- `app/services/chamber_runtime.py`
- `scripts/run_chamber_stream.py`
- `tests/test_chamber.py`

Work:

- Lot start/completion, 25-wafer sequence, multi-sample wafer processing
- idle/startup/running/hold/alarm/cleaning/maintenance/shutdown states
- Lot-level recipe, equipment bias, lot transient, wafer variation, cleaning effect
- wafer count increments only at wafer completion
- lifecycle events update `lots` and emit only meaningful `process_events`

Verify:

- one wafer produces multiple telemetry rows
- Lot and wafer boundaries are deterministic under a seed
- cleaning/reset and legacy state override remain valid

## Step 4 — Data Quality Gate

Files:

- `app/services/chamber_data_quality.py`
- `app/services/chamber_runtime.py`
- `app/services/chamber_storage.py`
- `configs/chamber.yaml`
- `tests/test_chamber_data_quality.py`

Work:

- missing, duplicate, reversal, gap, interval, stuck sensor, physical range, state, recipe validation
- `VALID/WARNING/REJECT` result and issue metadata
- strict model gate and valid-only clean training rows
- repeated issue aggregation into `process_events(event_type=data_quality)`

Verify:

- each required rule has deterministic tests
- warning/reject rows never become prediction/training candidates
- repeated stuck sensor creates one aggregate event rather than one per sample

## Step 5 — Detector and MLOps

Files:

- `app/services/chamber_training.py`
- `app/services/chamber_runtime.py`
- `app/services/chamber_storage.py`
- `app/services/automation.py`
- `scripts/run_chamber_stream.py`
- `frontend/src/MlopsWorkspace.jsx`
- `frontend/src/ProcessMonitoring/EtchMonitoring.jsx`

Work:

- context MAD thresholds and global fallback
- EWMA detector and `anomaly_detections` rows
- overall/equipment/recipe/lot holdout metrics
- readiness → automatic Staging Candidate, manual Production promotion
- actual Chamber lifecycle as primary MLOps UI, generic flow as Legacy/Demo

Verify:

- detector comparison/context fallback tests
- Candidate is created only when ready and never auto-promoted
- Production/Candidate metrics and data range are visible

## Step 6 — Process to Wafer and Agent

Files:

- `app/services/process_ops.py`
- `app/services/pipeline.py`
- `app/services/agent.py`
- `app/services/action_card.py`
- `app/services/fab_scenario.py`
- `scripts/run_fab_scenario.py`
- `tests/test_fab_scenario.py`

Work:

- same-Lot then time-window related process event lookup
- lot context, same-lot trend, equipment/recipe history, accumulated defect evidence
- Agent output sections: Observation, Possible Causes, Evidence, Recommended Checks, Recommended Action, Confidence/Uncertainty
- deterministic RF drift → W13~W15 degradation → inspection → Agent evidence scenario

Verify:

- E2E scenario preserves temporal order and Lot identity
- Agent output does not claim causality
- unrelated Lot events are excluded

## Step 7 — UI and Documentation Consolidation

Files:

- `frontend/src/FabOverview.jsx`
- `frontend/src/ProcessMonitoring/*`
- `frontend/src/WaferQuality/*`
- `frontend/src/AIAnalysis/*`
- `frontend/src/DatabaseView.jsx`
- `README.md`
- `docs/spec.md`
- `docs/decisions.md`
- `docs/progress.md`

Work:

- Lot source/status/process alert summary from `lots`
- Data Quality and detector comparison visibility
- Static Resistance remains Etch Legacy/Static Demo
- Vision remains Wafer Detail evidence only
- Generic MLOps clearly marked Legacy/Demo
- real/synthetic/proxy/temporal boundaries synchronized in UI and docs

Verify:

- frontend production build
- browser flow: Fab Overview → Etch → Lot/Wafer → AI Analysis → MLOps/Data
- browser console and API network errors absent

# Definition of Done

- PostgreSQL Compose startup and explicit validation work; SQLite remains explicit fallback.
- `lots`, telemetry Lot context, optional wafer context, detector results, process events exist with useful indexes.
- Generator models Lot/Wafer lifecycle and multiple telemetry samples per wafer.
- Data Quality Gate blocks non-VALID rows from prediction/training and aggregates meaningful quality events.
- real sklearn Expected Resistance training/artifacts/registry remain functional.
- MAD and EWMA results are comparable and context thresholds fallback correctly.
- group-wise evaluation and automatic Staging Candidate creation are available; promotion remains manual.
- process anomalies and inspections correlate by same Lot/time without causal claims.
- Agent uses process, Lot/Wafer, Vision, Metrology, RAG and structured uncertainty.
- current UI consolidation remains intact and real Chamber MLOps becomes primary.
- SQLite tests, PostgreSQL integration tests, E2E scenario, smoke, build and browser verification pass.
- README/docs match the implemented commands, boundaries and remaining synthetic components.

# Verification

```powershell
$env:STORAGE_BACKEND='sqlite'
python -m compileall -q app scripts tests
python -m pytest -q -W error --basetemp .pytest_cache\fab-ops-runtime
python -W error scripts\smoke_test.py

$env:POSTGRES_PORT='5433'
docker compose up -d postgres
$env:STORAGE_BACKEND='postgres'
$env:RDS_HOST='127.0.0.1'
$env:RDS_PORT='5433'
$env:RDS_DB='waferguard'
$env:RDS_USER='waferguard'
$env:RDS_PASSWORD='waferguard'
$env:RUN_POSTGRES_TESTS='1'
python -m pytest tests\test_postgres_integration.py -q -W error

Push-Location frontend
npm.cmd run build
Pop-Location
git diff --check
```

# Progress

- [x] Repository analysis
- [x] Interview decisions
- [x] PLAN.md updated
- [x] PostgreSQL runtime
- [x] Lot/event data model
- [x] Generator lifecycle
- [x] Data Quality Gate
- [x] Detector/MLOps
- [x] Process/Wafer/Agent E2E
- [x] UI/docs consolidation
- [x] Final verification

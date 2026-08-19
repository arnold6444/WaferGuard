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

---

# FAB Multimodal Platform v2 Delta (2026-08-18)

기존 완료 범위는 유지한다. 이 Delta는 Photo/Etch/Deposition/CMP 네 공정을 동일 FAB identity로 연결하고, 성능 튜닝보다 교체 가능한 전체 실행 프레임을 만드는 변경이다.

## Requirement Delta

### Preserve

- 기존 Chamber runtime, Process TS/Vision 후보, MLOps 수동 승격, Inspection Agent/HITL/RAG
- PostgreSQL 기본 개발 경로와 명시적 SQLite 테스트 경로
- 기존 `/api/v1/lots`, process/inspection API와 legacy/demo process profile
- 기존 DB table과 호환 컬럼; destructive migration 금지

### Add

- `fab.v2` Lot/Wafer/Process Run/Equipment/Unit/Recipe/Cycle/State/Phase identity
- 결정적 4공정 Virtual FAB, equipment/unit bias, degradation/maintenance, 4개 latent fault
- run 이후 Metrology +5분, Inspection +10분의 simulated availability
- signed detector result, missing-modality late fusion, evidence-only Candidate RCA
- simulation truth와 inference 저장소의 물리적 분리 및 RCA 후 Top-1/Top-3 평가
- QoS 1 MQTT와 consumer receipt, Direct 디버그 transport, FAB trace/read API, dashboard 연결

### Out of Scope

- 실제 장비 제어, 8공정 전체 runtime, 새 Vision DL 모델
- synthetic 데이터에 맞춘 threshold/모델 성능 튜닝
- 대량 생성·재학습·benchmark, 자동 Production 승격
- backend/frontend Docker image, 외부 publication

## Architecture Delta

```text
fab_simulator.yaml + fault_catalog.yaml
  → VirtualFabGenerator
  → fab.v2 envelope
  → Direct (debug) 또는 Mosquitto QoS 1 (default)
  → 동일 FabMessageRouter
      ├─ DB writer → FAB additive tables
      └─ Detector → fab_detector_results (GT column 없음)
  → signed-margin late fusion
  → persisted evidence-only RCA
  → RCA 완료 후에만 simulation_faults 평가
  → Trace/API/Dashboard + 기존 Agent/HITL/MLOps
```

- image는 object storage에 저장하고 transport에는 key와 observed feature만 보낸다.
- public transport/API는 fault ID, injected label, GT mask를 제거한다.
- `FAB_SYNTHETIC_DEBUG=1`일 때만 별도 debug repository의 mask/fault를 read model에 포함한다.
- 기존 artifact는 보존하되 `feature_contract`, feature/config fingerprint가 모두 일치할 때만 FAB v2 추론에 사용한다. 호환 후보가 없으면 versioned context baseline을 사용하며 자동 promote하지 않는다.

## Implementation Delta

- [x] Config/schema/fingerprint와 additive SQLite/PostgreSQL storage
- [x] 4공정 generator, lifecycle, fault propagation, metrology/inspection
- [x] standard detector output, fusion, evidence-ranked RCA, evaluation-only GT reader
- [x] MQTT/direct transport, idempotent receipt, unified orchestrator/CLI
- [x] FAB equipment/live/trace/run/RCA/metrics API
- [x] Fab Overview, Process Monitoring, Wafer Quality, AI Analysis 연결과 legacy fallback
- [x] PostgreSQL + Mosquitto Compose, 실행/튜닝 문서

## Verification Delta

이번 checkpoint는 사용자 결정에 따라 framework-first로 검증한다. 모델 재학습, 대량 Lot/image 생성, 성능 최적화는 실행하지 않는다.

```powershell
$env:STORAGE_BACKEND='sqlite'
python -m pytest -q tests/test_fab_v2_generator.py tests/test_fab_v2_fusion.py `
  tests/test_fab_storage.py tests/test_fab_api.py tests/test_fab_mqtt.py `
  tests/test_fab_detection.py tests/test_fab_runtime.py tests/test_fab_orchestrator.py

python scripts/run_fab_stream.py --lots 1 --wafers-per-lot 1 --process cmp --transport direct --seed 17
docker compose config --quiet
Push-Location frontend; npm.cmd run build; Pop-Location
git diff --check
```

현재 계약 검증: FAB focused `30 passed`. 생성 품질과 모델 성능 평가는 이후 별도 작업으로 남긴다.

---

# Local Analysis Notebook & Delivery Delta (2026-08-18)

## Goal

개인 학습에서는 로컬 Jupyter notebook으로 데이터를 셀 단위 분석하고, 설치형 프로그램과 자동 파이프라인에서는 동일 로직을 Python 모듈/CLI로 실행한다. 분석 결과는 로컬 JSON 계약을 통해 기존 대시보드에 표시한다.

## Scope

### In Scope

- `data/input`의 CSV/Parquet 입력과 `data/output`의 분석·모델 결과
- 데이터 구조, 결측/중복, 기술 통계, correlation heatmap, feature importance, 기본 평가
- notebook과 CLI가 공유하는 `fab_analysis.py`
- 현재 FAB feature/config fingerprint를 포함하는 선택적 Staging 후보 artifact
- 최신 분석 JSON 조회 API와 AI Analysis 요약 패널
- 실행 문서 1개와 구조 문서 1개로 FAB v2 안내 통합
- 확인된 cache/build/log 산출물과 중복 코드 정리
- commit, push, PR #11 CI 확인 및 main 병합

### Out of Scope

- Google Drive/S3를 notebook 데이터 교환 경로로 사용
- notebook 실행 시 자동 대량 생성, 자동 Production 승격
- 임의 외부 CSV 컬럼을 production feature contract로 묵시적 변환
- CI skip, assertion 약화, workflow 비활성화로 상태를 꾸미는 처리

## Decisions

- notebook은 orchestration만 담당하고 분석/학습 구현은 import 가능한 `.py` 모듈에 둔다.
- 원본과 결과는 각각 `data/input`, `data/output`에 두며 내용은 Git에서 제외한다.
- feature importance는 label이 있으면 supervised forest, 없으면 Isolation Forest 결과를 설명하는 surrogate forest로 명시한다.
- Production 호환 후보는 exact runtime feature names일 때만 저장하고 등록은 Staging까지만 허용한다.
- dashboard는 `data/output/dashboard_summary.json`을 `/api/v1/fab/analysis/latest`로 노출한다.
- 기존 runtime DB/image/model은 사용자 로컬 상태이므로 삭제하지 않고 cache, build, stale log만 제거한다.

## Implementation Steps

1. 로컬 data 경로와 선택형 notebook dependency를 추가한다.
2. EDA, feature importance, 후보 artifact, dashboard JSON용 공통 Python 모듈과 CLI를 구현한다.
3. 셀 단위 notebook을 공통 모듈 위에 구성한다.
4. FastAPI read endpoint와 AI Analysis 패널을 연결한다.
5. `RUN_LOCAL.md`, `ARCHITECTURE.md`로 v2 실행/구조 문서를 통합하고 중복 문서를 제거한다.
6. cache/build/log와 확인된 중복 코드를 정리한다.
7. targeted test, notebook smoke, API, frontend build, full CI-equivalent 검증 후 publish/merge한다.

## Definition of Done

- 로컬 파일을 넣고 notebook 셀 또는 CLI로 같은 분석 결과를 만들 수 있다.
- 데이터 구조, 결측/중복, 통계, heatmap, feature importance, metric이 notebook에 있다.
- raw data와 결과는 로컬에만 남고 Git에 포함되지 않는다.
- 분석 JSON이 API와 AI Analysis 화면에 표시된다.
- 실행/구조 문서가 실제 명령과 data/artifact 위치를 설명한다.
- 불필요한 산출물은 제거되며 기존 runtime state와 공개 계약은 보존된다.
- 로컬 검증과 GitHub CI가 통과하고 PR #11이 main에 병합된다.

---

# FAB Equipment Investigation & Anomaly Workbench Delta (2026-08-18)

## Goal

네 공정 runtime의 상태를 같은 기준으로 표시하고, 실행 중이거나 최근 완료된 process run을 장비 중심으로 조사할 수 있게 한다. 기존 EDA notebook은 실제 Ground Truth와 baseline prediction을 분리한 leakage-safe 이상탐지 성능 실험 Workbench로 교체한다.

## Scope

### In Scope

- Photo/Etch/Deposition/CMP의 `Synthetic Runtime` 지원 상태와 실제 최근 수신/가동 상태 분리
- 실행 중 run 우선, 없으면 최신 완료 run을 보여주는 Process Monitoring
- Equipment/Unit/Recipe, sensor tag, phase, anomaly evidence, 공정별 metrology/inspection modality 표시
- Etch CD-SEM 선폭/프로파일 등 공정별 현실적인 계측 자산과 metric 계약
- 한/영 전환 시 header control 폭과 layout 안정화
- detector prediction과 `simulation_faults` 평가 GT의 명시적 분리
- process-run chronological Train/Validation/Test split, normal-only Train, leakage guard
- 기존 네 TS 후보, prefix 기반 feature-set ablation, validation threshold/leaderboard/metric/lock/manifest 구조
- 기본 OFF인 experiment/final-test/artifact notebook과 Python 재사용 모듈
- 기존 dashboard summary의 additive 확장

### Out of Scope

- 실제 데이터 학습, hyperparameter search, benchmark, final test 실행
- candidate artifact 생성/등록, Staging/Production 승격
- notebook 전체 실행, 모델 재학습, 대량 synthetic 생성
- 실제 FAB calibration 또는 계측 장비 제어
- 기존 네 공정 밖의 runtime 추가

## Decisions

- 네 공정은 모두 같은 `Synthetic Runtime` capability로 표시한다. 녹색 연결 표시는 실제 inventory/최근 row에만 사용하고 Demo와 혼용하지 않는다.
- `Current Process Run`은 실행 중 row를 우선하며, stream이 이미 끝났으면 최신 완료 run을 `Latest completed`로 표시한다.
- 장비 판단은 sensor tag, relation deviation, metrology/inspection evidence와 engineer review를 함께 보여주되 원인 단정 대신 Candidate/Recommended check 표현을 유지한다.
- `baseline_detector_is_anomaly`는 비교용 prediction이고 label/feature가 아니다. Ground Truth는 `simulation_faults`에서 평가 경로로만 join한다.
- identity, baseline prediction, 모든 GT column은 candidate feature allowlist에서 제외한다.
- split, preprocessing, threshold 선택은 Train/Validation 계약을 지키며 Test는 명시적 최종 1회 실행 전까지 봉인한다.
- notebook 기본 플래그 `RUN_EXPERIMENT`, `RUN_FINAL_TEST`, `CREATE_CANDIDATE`는 모두 `False`다.

## Architecture / Flow

```text
FAB equipment config
  -> equipment class / unit / recipe / sensor tags
  -> runtime telemetry + state + detector context
  -> live API (running-first, latest-completed fallback)
  -> equipment-focused Process Monitoring
  -> process-specific metrology/inspection evidence

Runtime feature export
  + baseline detector prediction (comparison only)
  + simulation_faults GT (evaluation only)
  -> label audit / leakage guard
  -> chronological grouped split
  -> feature-set + candidate configuration preview
  -> opt-in validation experiment
  -> winner lock -> opt-in final test -> opt-in artifact
```

## Implementation Steps

1. Inventory/live API와 process cards의 capability/connection/run-state 계약을 바로잡는다.
2. 네 공정 장비 class, sensor tag catalog, 공정별 metrology/inspection modality/metric을 additive 확장한다.
3. Process Monitoring에 최신 run 상태, tag/equipment context, 계측 evidence를 연결하고 언어 토글 layout을 고정한다.
4. runtime export label contract와 evaluation-only GT join을 구현한다.
5. grouped chronological split, leakage guard, feature groups/sets, candidate/search preview, pure metric/threshold/leaderboard/manifest 기능을 구현한다.
6. notebook을 안내부터 Summary까지 opt-in experiment 구조로 재작성하고 dashboard summary 호환을 유지한다.
7. static/contract/pure-function test, import/compile, notebook JSON parse, frontend build로만 검증한다.

## Definition of Done

- Photo/Etch/Deposition/CMP가 모두 같은 Synthetic Runtime 범주이며 실제 연결 여부가 별도로 보인다.
- 선택 공정의 실행 중 또는 최신 완료 run identity/telemetry가 빈 placeholder 대신 표시된다.
- 공정별 equipment/unit/recipe, sensor tags, metrology tool/modality/metric이 구체적으로 확인된다.
- 한/영 전환으로 상단 layout이 흔들리지 않는다.
- baseline prediction과 synthetic GT가 분리되고 feature leakage가 차단된다.
- 한 process run/wafer가 여러 split에 겹치지 않으며 normal-only Train을 지원한다.
- 네 기존 TS 후보와 다섯 feature-set의 validation 비교 구조가 있다.
- threshold는 validation에서 F2/FPR/Precision 순으로 선택할 수 있다.
- F1/F2/FPR/false alarms per hour/detection delay/fault/context metric과 leaderboard/lock/manifest 계약이 있다.
- 기본 notebook 실행 경로는 데이터 검증/EDA/split preview만 수행하고 어떠한 model fit/artifact 생성도 하지 않는다.
- 기존 FAB API/dashboard/Production runtime 계약이 유지된다.

## Verification

이번 Delta에서 Codex는 실제 모델 학습 또는 notebook 실행을 하지 않는다.

```powershell
$env:STORAGE_BACKEND='sqlite'
python -m compileall -q app scripts tests
python -m pytest -q -W error tests/test_fab_analysis.py tests/test_fab_experiment.py tests/test_fab_storage.py tests/test_fab_api.py
python -c "import json, pathlib; json.loads(pathlib.Path('notebooks/fab_local_analysis.ipynb').read_text(encoding='utf-8'))"
Push-Location frontend; npm.cmd run build; Pop-Location
git diff --check
```

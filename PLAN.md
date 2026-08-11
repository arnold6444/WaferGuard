# WaferGuard Plan

> 현재 상태 기준: 2026-08-11, merged PR #8 `Operationalize WaferGuard fab monitoring runtime` 이후.

## Goal

WaferGuard를 단순 synthetic AI 데모 모음이 아니라, **Lot을 중심으로 공정 이상과 Wafer 품질 변화를 연결하는 Fab Quality Ops 운영 보조 구조**로 발전시킨다.

현재 구현된 핵심 흐름은 다음과 같다.

```text
Lot lifecycle
  → Etch telemetry
  → Data Quality Gate
  → Expected Resistance model
  → Robust MAD / EWMA detector
  → process_events
  → Wafer inspection
  → same-Lot / time-window evidence
  → Inspection Agent
  → Engineer review / RAG
  → Chamber model lifecycle
```

실제 Fab 데이터가 들어왔을 때는 데이터 source가 바뀌더라도 이후 detection/inspection/agent workflow를 최대한 유지할 수 있어야 한다.

---

# Current State

## Implemented

### Runtime / DB

- PostgreSQL 16 Docker Compose를 표준 local runtime으로 사용한다.
- `STORAGE_BACKEND` 선택은 명시적이며 PostgreSQL 설정/연결 실패 시 SQLite로 조용히 fallback하지 않는다.
- SQLite는 unit test / lightweight demo 용도로 명시적으로 사용할 수 있다.
- `lots`, `process_events`, inspection/RAG/agent workflow tables와 Chamber 전용 tables가 같은 DB backend에서 동작한다.
- PostgreSQL integration test가 존재한다.

### Etch lifecycle simulator

- 25-wafer Lot lifecycle
- wafer당 multi-sample telemetry
- idle/startup/running/hold/alarm/cleaning/maintenance/shutdown state
- recipe/setpoint
- equipment/lot/wafer variation
- cleaning/seasoning
- reproducible seed
- synthetic anomaly injection

### Data Quality

- missing field
- duplicate / timestamp reversal
- interval/gap
- stuck sensor
- physical range
- state/recipe validation
- `VALID / WARNING / REJECT`
- `VALID`만 prediction/training에 사용
- 반복 DQ issue의 aggregate `process_events`

### Chamber ML

현재 Chamber ML은 **범용 tag anomaly framework가 아니라 Expected Resistance regression + residual detection** 구조다.

```text
VALID + running + quality=good rows
  → sklearn Pipeline
  → GradientBoostingRegressor
  → Expected Resistance
  → Actual - Expected residual
  → Robust MAD primary
  → EWMA secondary
```

구현됨:

- numeric/categorical preprocessing
- unknown-safe OneHotEncoder
- time-based holdout
- MAE/RMSE
- USE_TIME-only baseline 비교
- equipment/recipe/lot group metrics
- context threshold fallback
- feature importance
- joblib model artifact
- bootstrap Production model
- readiness 기반 Staging Candidate
- automatic candidate check in Chamber stream
- manual Production promotion

### Process → Wafer → Agent

- Chamber anomaly/DQ/lifecycle를 `process_events`로 projection
- 같은 Lot을 우선하고 검사 이전 시간창에서 process evidence 조회
- 다른 Lot event 배제
- temporal candidate를 causal root cause로 표현하지 않음
- Lot/Wafer/Metrology/Vision/process/RAG evidence를 Inspection Agent에 전달
- structured Agent output과 Human Review/RAG feedback

### UI

- Fab Overview
- Process Monitoring
- Wafer Quality
- AI Analysis
- MLOps
- Data & RAG
- Settings
- Etch는 connected synthetic runtime
- Etch 외 공정은 명시적 Demo profile
- Vision snapshot은 Wafer Detail의 Proxy evidence
- 기존 Static Resistance / generic wafer MLOps는 Legacy/Demo로 보존

---

# Current Boundaries

현재 구현되어 있지 않은 것을 구현된 것처럼 설명하지 않는다.

## ML / Training boundary

현재 별도의 다음 pipeline은 없다.

```text
Offline bulk dataset
→ Train / Validation / Test
→ explicit training job
→ Validation threshold selection
→ final Test evaluation
```

현재는 synthetic stream/accelerated stream이 DB에 clean row를 축적하고, warm-up 또는 readiness 조건에 따라 학습한다.

## Real-world portability boundary

현재 `configs/chamber.yaml`은 simulator/recipe/DQ/model parameter를 설정하지만 다음 범용 기능은 아직 없다.

- arbitrary source tag → canonical tag mapping
- tag unit/type/required/optional profile
- 설비마다 다른 tag set을 기반으로 동적 feature schema 생성
- machine-state source value mapping
- Environment Profile validator
- profile별 model/scaler/feature compatibility check
- 실제 external telemetry adapter를 YAML만으로 온보딩
- 새 환경 정상 historical data를 이용한 독립 offline retraining command

또한 현재 `chamber_training.py`의 feature schema는 Etch/Resistance use case에 맞게 코드에 정의되어 있다.

## Detection boundary

현재 detector는 다음이다.

- Expected Resistance residual 기반 Robust MAD
- residual의 EWMA

다음 범용 구조는 아직 없다.

- tag별 independent univariate model
- tag window 기반 spike/drop/drift/stuck detector ensemble
- arbitrary tag set의 multivariate window detector
- relationship-break 전용 detector/evaluation
- LSTM/TCN/Transformer anomaly model

## Product boundary

- 실제 Fab equipment stream/control 미연결
- 실제 process spec/control limit 미보장
- Vision은 external analysis snapshot/proxy
- Etch 외 공정 model 미구현
- Production model 자동 promotion 미허용
- process/inspection correlation은 인과관계가 아님

---

# Next Phase

세부 설계는 [`docs/training_realworld_roadmap.md`](docs/training_realworld_roadmap.md)를 따른다.

## Phase 1 — Portability + Offline Evaluation

성능 튜닝보다 먼저 최소 이식 구조와 올바른 평가 pipeline을 만든다.

### Environment Profile

- profile id/version
- source tag → canonical tag mapping
- unit/type
- required/optional
- physical range
- expected sampling interval
- machine-state mapping
- training/detector tag selection

### Offline dataset pipeline

- synthetic data를 wall-clock wait 없이 bulk 생성하거나 historical telemetry를 import
- Train / Validation / Test를 run/Lot/cycle/time-block 단위로 분리
- split 이후 Window 생성
- Validation에서 threshold 결정
- Test는 최종 성능 평가에만 사용

### Artifact compatibility

- profile id/version
- canonical tag set
- sampling interval
- feature schema/version
- window size
- training data range
- metric / threshold

을 artifact metadata에 저장하고 Live inference 전에 compatibility를 검증한다.

### Baseline detector 확장

현재 Resistance pipeline은 유지한다.

추가 baseline 후보:

```text
Univariate
Tag window → Robust Z / EWMA / Isolation Forest

Multivariate
Time × tags window → temporal/relation features → Isolation Forest
```

두 detector는 순차 filter가 아니라 병렬로 실행한다.

### Relationship anomaly

개별 값은 정상 범위지만 tag 관계만 깨지는 `relationship_break` synthetic scenario를 추가하여 Multivariate detector 필요성을 검증한다.

---

## Phase 2 — Model Performance

Phase 1 pipeline 안에서 성능을 개선한다.

우선순위:

1. Data leakage 없는 evaluation 확립
2. Window size 비교
3. Feature selection
4. state/recipe context 개선
5. threshold calibration
6. Isolation Forest / baseline hyperparameter tuning
7. anomaly type별 Precision/Recall/F1
8. false-positive 분석
9. 서로 다른 synthetic profile에서 재학습/평가

현재 한 synthetic distribution에만 최적화하지 않는다.

---

## Phase 3 — Advanced Time-Series Model

Baseline 한계를 확인한 후 필요할 때만 비교한다.

후보:

- LSTM/GRU Autoencoder
- TCN Autoencoder
- Transformer Autoencoder
- concept drift / incremental retraining

Deep Learning 채택 조건은 동일한 split/test set에서 baseline 대비 실제 개선이 확인되는 것이다.

---

# Target Real-World Onboarding Flow

최종 목표 UX:

```text
1. Environment Profile 작성
2. source tag / unit / state mapping
3. 정상 historical data 제공 또는 baseline collection
4. profile validation
5. train
6. validate / evaluate
7. model artifact 등록
8. live inference
```

목표는 모델 하나를 모든 설비에 그대로 복사하는 것이 아니라:

```text
새 환경
+ 새 정상 데이터
+ 새 Profile
→ 동일 Training Pipeline
→ 해당 환경용 모델
```

이다.

즉 **Model portability보다 Training Pipeline portability를 우선한다.**

---

# Verification Baseline

현재 merged runtime을 수정할 때 최소 다음을 유지한다.

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
```

새 ML phase에서는 여기에 다음을 추가한다.

- deterministic dataset split test
- train/validation/test leakage test
- profile validation test
- model/profile compatibility test
- univariate anomaly scenario
- relationship-break multivariate scenario
- detector별/combined evaluation report

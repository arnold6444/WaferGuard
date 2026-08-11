# WaferGuard

**반도체 Fab Quality Ops를 위한 멀티 에이전트 운영 보조 시스템**

WaferGuard는 반도체 공정 설비의 이상과 Wafer 품질 이상을 함께 모니터링하고, 공정 시계열·이미지·Metrology·과거 사례를 연결하여 **이상 원인 후보와 대응안**을 제안하는 Fab Quality Ops 데모 플랫폼입니다.

현재 저장소는 실제 Fab 설비를 직접 제어하는 production system이 아닙니다. **Synthetic Etch runtime, proxy inspection/Vision data, 실제 sklearn 학습 파이프라인, PostgreSQL 기반 운영 흐름**을 결합하여 Lot 중심의 탐지→검사→Agent→Review→MLOps 흐름을 검증합니다.

> 최신 코드 기준 설명입니다. 과거 Static Resistance/Vision 데모는 삭제하지 않고 Legacy/Proxy 경로로 유지합니다.

---

## 핵심 흐름

```text
Fab Overview
      ↓
Etch Lot / Wafer lifecycle
      ↓
Multivariate process telemetry
      ↓
Data Quality Gate
      ↓
Expected Resistance model
      ↓
Robust MAD + EWMA residual detection
      ↓
process_events
      ↓
Wafer Quality / Inspection
      ↓
same-Lot + time-window evidence
      ↓
Inspection Agent + RAG
      ↓
Engineer review / approval
      ↓
RAG feedback + Chamber MLOps
```

WaferGuard의 핵심은 이상 점수 하나를 보여주는 데서 끝내지 않고 **공정 이상 후보와 Wafer 품질 evidence를 Lot 기준으로 연결하여 운영 판단 흐름까지 보여주는 것**입니다.

---

## 현재 구현된 기능

### Fab Overview

- 8대 공정 상태
- Active Lot
- connected equipment
- warning / critical 요약
- 최근 process event
- Runtime/Synthetic/Proxy/Demo 출처 표시

Etch와 Inspection은 runtime/proxy runtime에 연결되어 있고, 실제 데이터가 없는 공정은 `Demo profile`로 표시합니다.

### Process Monitoring / Etch

현재 가장 깊게 구현된 공정은 Etch입니다.

- 25-wafer Lot lifecycle
- wafer당 multiple telemetry samples
- `idle / startup / running / hold / alarm / cleaning / maintenance / shutdown`
- recipe / setpoint
- pressure / source RF / bias RF / gas / temperature
- equipment / Lot / wafer variation
- cleaning / seasoning
- synthetic anomaly injection
- Actual / Expected Resistance / residual
- model / detector / DQ 상태

Etch 화면은 `Overview · Equipment · Process Trend · Anomaly · Model` 중심으로 구성됩니다.

### Wafer Quality

- runtime inspection DB 우선 Lot 집계
- 25-wafer status board
- Wafer timeline
- accumulated defect map
- Wafer Detail
- Vision Evidence

Runtime inspection이 없을 때는 기존 `waferVisionSample.json` 기반 `Demo / Proxy` dataset을 명시적으로 사용합니다.

### Vision Evidence

현재 WaferGuard 프론트엔드 내부에서 Vision 모델을 실시간 재학습하거나 추론하지 않습니다.

외부 `wafer_particle` 분석 결과의 저장된 snapshot을 사용합니다.

- Statistical result
- ResNet18 feature + PaDiM-style result
- comparison result
- 대표 heatmap / image
- Inspection Agent handoff

현재 snapshot:

- 3,000 images
- 100 chambers
- 300×300 px
- sample anomaly chambers: CH-007, CH-047, CH-061

이 결과는 실제 Fab 성능이나 수율 개선을 의미하지 않습니다.

### AI Analysis / Inspection Agent

LangGraph 기반 Agent가 다음 evidence를 함께 사용합니다.

- current Wafer
- Metrology / Vision rule hit
- 같은 Lot의 인접 Wafer
- equipment / recipe 정보
- 검사 이전 시간창의 process event 후보
- RAG 과거 사례
- engineer feedback

Agent 출력은 Observation / Possible Causes / Evidence / Recommended Checks / Recommended Action / Confidence·Uncertainty 경계를 유지하며 process event를 causal root cause로 단정하지 않습니다.

### Human-in-the-Loop

- `needs_review`
- `approved`
- `false_alarm`
- pending approval

확정된 engineer review만 검색 가능한 RAG case로 다시 저장합니다.

### MLOps

현재 최상위 MLOps 화면의 기본 대상은 **실제 sklearn artifact lifecycle을 가진 Chamber model**입니다.

- Production / Staging registry
- MAE / RMSE
- equipment / recipe / Lot group metric
- training data range
- feature importance
- Data Quality
- MAD / EWMA detector
- retraining readiness
- automatic Staging Candidate check
- manual Production promotion

기존 generic wafer retrain/promote/rollback은 `Legacy / Demo` workflow simulation으로 유지됩니다.

---

# 현재 Chamber ML은 정확히 무엇인가?

현재 Chamber ML을 범용 "여러 tag 시계열 이상탐지 모델"로 오해하면 안 됩니다.

현재 구현은 **여러 공정 변수를 이용해 정상 Expected Resistance를 예측한 뒤 실제 Resistance와의 residual을 감시하는 구조**입니다.

```text
pressure
RF power
bias RF
flows
temperature
USE_TIME
recipe/equipment/gas category
        ↓
sklearn preprocessing
        ↓
GradientBoostingRegressor
        ↓
Expected Resistance
        ↓
Actual - Expected
        ↓
Robust MAD / EWMA detector
```

구현 위치:

- `app/services/chamber_training.py`
- `app/services/chamber_runtime.py`
- `app/services/chamber_data_quality.py`
- `app/services/chamber_storage.py`
- `configs/chamber.yaml`

### 학습

현재 학습은 다음 clean row를 사용합니다.

```text
machine_state = running
quality = good
Data Quality = VALID
```

충분한 clean row가 DB에 쌓이면 최초 Production model을 bootstrap합니다.

이후에는 새 clean row 수, 최근 residual error, 경과 시간을 이용해 retraining readiness를 계산하고 stream의 periodic check 또는 명시적 API 호출로 **Staging Candidate까지만** 학습할 수 있습니다.

Production 승격은 자동으로 하지 않습니다.

### 검증

현재 `chamber_training.py`는 시간순 holdout을 사용해:

- MAE
- RMSE
- USE_TIME-only baseline 비교
- 기존 Production 동일 holdout 비교
- equipment / recipe / Lot group metric

을 계산합니다.

Residual threshold는 robust MAD 기반이며 context threshold는:

```text
equipment + recipe
→ equipment
→ recipe
→ global
```

순서로 fallback합니다.

---

# 아직 구현되지 않은 ML 구조

다음 기능은 **현재 코드에 이미 구현된 기능이 아니라 다음 확장 대상**입니다.

### 별도 Offline Train / Validation / Test Pipeline

현재는 accelerated/live synthetic stream이 DB에 row를 쌓고 그 clean row로 bootstrap/retrain합니다.

아직 다음과 같은 독립 workflow는 없습니다.

```text
100시간치 데이터 즉시 생성/import
→ Train dataset
→ Validation dataset
→ Test dataset
→ explicit train command
→ Validation에서 threshold 선택
→ Test 최종 성능 평가
```

### Tag별 + 다변량 병렬 이상탐지

현재는 Expected Resistance residual의 MAD/EWMA를 사용합니다.

아직 다음 범용 구조는 없습니다.

```text
                 ┌→ Tag별 Univariate Detector
Telemetry Window ┤
                 └→ Multivariate Relationship Detector
```

향후에는 개별 tag의 spike/drop/drift/stuck과 **개별 값은 정상이나 tag 관계만 깨지는 anomaly**를 별도로 검증하는 구조가 필요합니다.

### 범용 시계열 Deep Learning

LSTM/TCN/Transformer Autoencoder는 현재 구현되어 있지 않습니다.

Baseline 구조와 평가 pipeline을 먼저 만든 뒤 동일 test set에서 실제 성능 향상이 확인될 경우 추가하는 것이 로드맵입니다.

상세 내용은 [`docs/training_realworld_roadmap.md`](docs/training_realworld_roadmap.md)를 참고하세요.

---

# 새로운 실제 설비에 바로 YAML만 바꿔 적용 가능한가?

**아직 완전히 그렇지는 않습니다.**

현재 `configs/chamber.yaml`에서 다음은 조절할 수 있습니다.

- sampling interval
- equipment count
- Lot/wafer lifecycle
- cleaning/seasoning
- Data Quality rule
- physical range
- recipe / setpoint / gas
- model warm-up / retraining parameter
- synthetic anomaly strength

하지만 현재 Etch telemetry field와 ML feature schema는 Python 코드에 구체적으로 정의되어 있습니다.

따라서 현재는 다음 범용 기능이 없습니다.

- 실제 source tag 이름 → canonical tag mapping
- tag별 unit/type/required/optional 정의
- 설비마다 다른 tag 구성으로 model feature를 자동 생성
- 실제 machine-state value mapping
- Environment Profile validator
- Profile ↔ model/scaler/feature-schema compatibility check
- 새로운 공장의 정상 historical data로 독립 재학습하는 CLI

최종 목표는 다음과 같습니다.

```text
새로운 Fab / 설비
      ↓
Environment Profile YAML
      ↓
source tag / unit / state mapping
      ↓
정상 historical data 또는 baseline collection
      ↓
동일 Training Pipeline
      ↓
해당 환경용 Model Artifact
      ↓
Live Detection
```

즉 **모델 하나를 모든 설비에 복사하는 것보다, 새로운 환경에서도 같은 학습/검증 pipeline으로 새 모델을 만들 수 있게 하는 것**을 우선합니다.

상세 설계 및 구현 우선순위는 [`docs/training_realworld_roadmap.md`](docs/training_realworld_roadmap.md)에 정리되어 있습니다.

---

## Data Quality / Detector Layer

`configs/chamber.yaml`의 현재 DQ gate는 다음을 검사합니다.

- required field
- timestamp duplicate/reversal
- interval/gap
- stuck sensor
- physical range
- valid machine state
- recipe

정책:

```text
VALID
→ persist + prediction + training 가능

WARNING
→ audit/event 용도, model 차단

REJECT
→ model 차단
```

Residual detector 결과는 `anomaly_detections`에 detector별로 저장됩니다.

- `robust_mad`: primary
- `ewma_abs_residual`: secondary

의미 있는 Chamber anomaly는 원본 prediction/detection table을 삭제하지 않고 공통 `process_events` layer로 projection합니다.

---

## Synthetic Fab Scenario

Generator와 Inspection은 직접 결합하지 않습니다.

별도 orchestrator가 wafer completion과 configured inspection lag를 연결합니다.

```bash
python scripts/run_fab_scenario.py --wafer-samples 4 --lag-minutes 5
```

기본 demo scenario는 W13~W15의 RF drift와 검사 품질 저하를 시간적으로 함께 만듭니다.

이는 E2E workflow 검증용이며 **RF drift가 defect의 실제 원인이라고 주장하지 않습니다.**

---

## Tech Stack

| 영역 | 현재 구현 |
|---|---|
| Backend | FastAPI · Pydantic · LangGraph StateGraph |
| LLM | GPT-4o-mini via Luxia Gateway · tool calling |
| RAG | Luxia embedding · cosine search · rerank · DB embedding |
| Frontend | React 19 · Vite 7 · Recharts 3 |
| Runtime DB | PostgreSQL 16 standard local/runtime · explicit SQLite test/demo |
| Object Storage | local `outputs/` · optional S3 |
| Workflow DB | Lot/Inspection/RAG/Agent 11 tables + Chamber 4 tables |
| Chamber ML | pandas · scikit-learn Pipeline · GradientBoosting · joblib · PyYAML |
| AWS | boto3 · S3 · RDS · Secrets Manager · SNS · Lambda/EventBridge |

---

## Local Run

### Prerequisites

| 도구 | 권장/최소 |
|---|---|
| Python | 3.11 |
| Node.js | 20.19+ 또는 22.12+ |
| npm | Node.js 설치에 포함 |
| Docker | PostgreSQL local runtime 사용 시 필요 |

### 1. Clone & Python dependencies

```bash
git clone https://github.com/arnold6444/WaferGuard.git
cd WaferGuard

conda create -n waferguard python=3.11 -y
conda activate waferguard
pip install -r requirements.txt
```

### 2. Frontend dependencies

```bash
cd frontend
npm install
cd ..
```

PowerShell execution policy 때문에 `npm.ps1` 실행이 막히면 `npm.cmd`를 사용할 수 있습니다.

```powershell
npm.cmd install
npm.cmd run dev
```

### 3. PostgreSQL

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
docker compose ps
```

PostgreSQL backend는 명시적으로 선택되며 연결 실패 시 SQLite로 자동 fallback하지 않습니다.

호스트 `5432`가 이미 사용 중이면 `.env`의 `POSTGRES_PORT`와 `RDS_PORT`를 함께 다른 포트로 변경합니다.

SQLite는 test/lightweight demo에서 명시적으로 사용합니다.

```powershell
$env:STORAGE_BACKEND='sqlite'
```

### 4. Frontend API URL

`frontend/.env.local`:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 5. Run

Backend:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm run dev
```

Chamber stream:

```bash
python scripts/run_chamber_stream.py --equipment-count 3 --interval 1
```

빠른 accelerated simulation:

```bash
python scripts/run_chamber_stream.py --equipment-count 3 --interval 0 --samples 180
```

Anomaly injection:

```bash
python scripts/run_chamber_stream.py \
  --equipment-count 3 \
  --interval 0 \
  --samples 220 \
  --anomaly rf_power_drift \
  --anomaly-after 140
```

접속:

```text
Dashboard : http://127.0.0.1:5173
API docs  : http://127.0.0.1:8000/docs
Health    : http://127.0.0.1:8000/health
```

---

## Validation

SQLite/unit/demo path:

```powershell
$env:STORAGE_BACKEND='sqlite'
python -m compileall -q app scripts tests
python -m pytest -q -W error
python -W error scripts/smoke_test.py
```

PostgreSQL integration:

```powershell
$env:POSTGRES_PORT='5433'
docker compose up -d postgres

$env:STORAGE_BACKEND='postgres'
$env:RDS_HOST='127.0.0.1'
$env:RDS_PORT='5433'
$env:RDS_DB='waferguard'
$env:RDS_USER='waferguard'
$env:RDS_PASSWORD='waferguard'
$env:RUN_POSTGRES_TESTS='1'

python -m pytest tests/test_postgres_integration.py -q -W error
```

Frontend:

```powershell
cd frontend
npm.cmd run build
```

---

## 주요 API

| Method | Endpoint | 역할 |
|---|---|---|
| GET | `/health` | API/DB 상태 |
| GET | `/api/v1/fab/overview` | Fab Overview read model |
| GET | `/api/v1/process/profiles` | 8대 공정 profile metadata |
| GET | `/api/v1/process/events` | 공통 process event 조회 |
| GET | `/api/v1/lots` | Lot 원장 조회 |
| GET | `/api/v1/quality/lots` | inspection DB Lot 요약 |
| GET | `/api/v1/quality/lots/{lot_id}` | Wafer timeline/detail 집계 |
| POST | `/api/v1/inspect` | inspection 생성 + risk/action card/agent workflow |
| GET | `/api/v1/inspections` | inspection 목록 |
| GET | `/api/v1/inspect/{id}/trace` | Agent trace |
| POST | `/api/v1/inspect/{id}/chat/stream` | evidence 기반 streaming chat |
| POST | `/api/v1/review/{id}` | Engineer review + RAG feedback |
| GET | `/api/v1/chamber/status` | warm-up / Production / readiness |
| GET | `/api/v1/chamber/equipment` | equipment 최신 상태 |
| GET | `/api/v1/chamber/telemetry` | Chamber telemetry |
| GET | `/api/v1/chamber/predictions` | Actual/Expected/residual |
| GET | `/api/v1/chamber/detections` | MAD/EWMA detector 결과 |
| GET | `/api/v1/chamber/models` | Chamber model registry |
| POST | `/api/v1/chamber/retrain` | Staging Candidate 학습 |
| POST | `/api/v1/chamber/models/{version}/promote` | 명시적 Production 승격 |
| GET/POST | `/api/v1/automation/status`, `/api/v1/automation/tick` | 운영 automation 상태/tick |
| GET | `/api/v1/db/overview` | DB overview |

전체 계약은 FastAPI Swagger `/docs`를 기준으로 확인하세요.

---

## Project Structure

```text
app/
  main.py
  services/
    pipeline.py
    agent.py
    action_card.py
    rag.py
    storage.py
    db.py
    process_ops.py
    fab_scenario.py

    chamber_generator.py
    chamber_data_quality.py
    chamber_training.py
    chamber_runtime.py
    chamber_storage.py

frontend/
  src/
    FabOverview.jsx
    ProcessMonitoring/
    WaferQuality/
    AIAnalysis/
    MlopsWorkspace.jsx
    DatabaseView.jsx

configs/
  chamber.yaml

scripts/
  run_chamber_stream.py
  run_fab_scenario.py
  smoke_test.py

docs/
  spec.md
  decisions.md
  progress.md
  training_realworld_roadmap.md

runtime/models/chamber/   # fitted model artifacts, gitignored
outputs/                  # local generated runtime assets, gitignored
```

---

## 문서

- [`docs/spec.md`](docs/spec.md): 현재 제품/데이터 계약
- [`docs/decisions.md`](docs/decisions.md): 주요 설계 결정과 superseded 기록
- [`docs/progress.md`](docs/progress.md): 시간순 구현 기록
- [`docs/training_realworld_roadmap.md`](docs/training_realworld_roadmap.md): Offline 학습·tag/multivariate detector·실환경 Profile 확장 계획
- [`PLAN.md`](PLAN.md): 현재 구현 상태와 다음 단계

---

## 현재 구현 경계 요약

- Etch telemetry/model은 synthetic runtime입니다.
- Chamber ML은 실제 sklearn `.fit()`과 artifact를 사용하지만 synthetic process data에서 학습합니다.
- 현재 모델은 fixed Etch feature schema의 Expected Resistance regression + residual detection입니다.
- 별도 Offline Train/Validation/Test pipeline은 아직 없습니다.
- arbitrary real-world tag mapping/Profile 기반 재학습은 아직 없습니다.
- tag별 independent detector + generic multivariate relation detector는 아직 없습니다.
- Vision은 external analysis snapshot/proxy evidence입니다.
- Etch 외 공정은 connected model이 아닙니다.
- process event와 Wafer inspection의 연결은 temporal evidence이며 인과관계가 아닙니다.
- Production model 자동 승격 및 실제 장비 제어는 하지 않습니다.

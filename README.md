# WaferGuard

**반도체 Fab Quality Ops를 위한 멀티 에이전트 운영 보조 시스템**

WaferGuard는 반도체 공정에서 발생한 이상 신호를 한 화면에서 확인하고, 검사 결과를 **리스크 평가 → RAG 기반 원인/조치 추천 → 엔지니어 승인 → MLOps 판단** 흐름으로 연결하는 데모/MVP입니다.

현재 저장소는 실제 Fab 설비를 직접 제어하는 시스템이 아니라, **샘플·프록시 데이터와 시뮬레이션을 이용해 운영 의사결정 흐름을 검증하는 구조**입니다.

- Chamber Resistance는 synthetic multivariate telemetry를 DB·실제 sklearn 모델·residual 이상탐지와 연결한 **Live** 모드와 기존 `USE_TIME` 기반 **Static Demo**를 함께 제공합니다.
- Wafer Vision은 외부 `wafer_particle` 분석 결과 snapshot을 표시하고 선택한 evidence를 기존 Inspection Agent로 전달합니다.
- 기존 wafer MLOps는 workflow simulation이며, Chamber MLOps는 별도 registry/artifact에서 실제 sklearn 학습을 수행합니다.

## 📝 Overview

```text
Chamber Resistance Live / Static Demo
            +
Wafer Vision snapshot
            │
            ▼
      FastAPI Backend
            │
            ├── Chamber DB / sklearn lifecycle
            │
            └── POST /api/v1/inspect
                     │
                     ▼
          Risk + Metrology / Vision rules
                     │
                     ▼
       Inspection Agent + RAG + Action Card
                     │
                     ▼
          Human approval / review
                     │
            ┌────────┴────────┐
            ▼                 ▼
      RAG feedback       MLOps delegation
```

### ⭐️ Key Features

- **Chamber Resistance AI**: recipe/setpoint, RF, pressure, gas, temperature, cleaning history, use history를 가진 연속 synthetic telemetry로 실제 Gradient Boosting pipeline을 학습하고 Actual/Expected residual을 저장·탐지합니다.
- **Resistance Static Demo**: 기존 `DATE`, `EQP`, `USE_TIME`, `RESISTANCE` offline 분석 결과를 유지합니다.
- **Wafer Vision AI**: `wafer_particle` 분석 서버에서 미리 생성한 통계/정상 전용 AI 비교 결과와 대표 히트맵을 탐색합니다.
- **Live Inspection**: wafer/process 입력을 받아 리스크 점수, metrology/vision rule hit, Action Card, 이미지 근거를 생성합니다.
- **Inspection Agent**: LangGraph 기반 도구 호출 루프로 과거 사례와 공정 근거를 조회하고 추정 원인과 대응 방안을 제안합니다.
- **Human-in-the-Loop RAG**: `approved` 또는 `false_alarm`으로 확정된 엔지니어 review만 RAG knowledge로 다시 저장합니다.
- **MLOps Agent**: 기존 wafer model/drift/retrain/promote/rollback workflow를 시뮬레이션하고 high-risk action은 approval gate를 거칩니다.
- **Data & RAG Console**: 검사 이력, Agent trace, approval, 모델/드리프트, RAG 문서 등 운영 DB를 조회합니다.

---

## 📡 Chamber Resistance AI

Resistance 화면은 **Live / Static Demo** 두 모드입니다.

### Live

```text
configs/chamber.yaml
        │
        ▼
EtchTelemetryGenerator
(chamber_generator.py)
        │
        ▼
Multivariate telemetry
Pressure / Source RF / Bias RF / Gas / Temp
Recipe / Use time / Since clean / Wafer count
        │
        ▼
chamber_telemetry
        │
        ├── no Production model
        │      └─ clean warm-up → 실제 sklearn fit → Production v1
        │
        └── Production model 있음
               └─ predict Expected Resistance
                         │
                 Actual - Expected
                         │
                         ▼
                     Residual
                         │
                         ▼
                chamber_predictions
                         │
                         ▼
                 React Live Dashboard
```

주요 구현 파일:

- `configs/chamber.yaml`: recipe/setpoint, cleaning, model threshold/config
- `app/services/chamber_generator.py`: stateful synthetic Etch telemetry
- `app/services/chamber_storage.py`: Chamber DB access
- `app/services/chamber_training.py`: sklearn fit/evaluation/joblib artifact
- `app/services/chamber_runtime.py`: warm-up/inference/readiness/retrain/promote
- `scripts/run_chamber_stream.py`: local stream runner
- `frontend/src/ChamberView.jsx`: Live/Static UI

지원 synthetic anomaly:

- `resistance_spike`
- `resistance_drift`
- `pressure_drift`
- `rf_power_drift`
- `gas_flow_drift`
- `temperature_drift`
- `stuck_sensor`
- `step_change`

> simulator range, coefficient, feature importance는 실제 Fab calibration 값이나 물리 법칙을 의미하지 않습니다. pipeline/lifecycle 검증용 synthetic 값입니다.

### Model / validation

- Pipeline: `ColumnTransformer + OneHotEncoder(handle_unknown="ignore") + GradientBoostingRegressor(loss="huber")`
- Validation: 과거 → 미래 time-based holdout
- Metrics: MAE / RMSE
- Comparison: 동일 holdout에서 multivariate candidate vs `USE_TIME` only baseline vs 기존 Production
- Artifact: `runtime/models/chamber/resistance-vN.joblib`
- Online detection: `abs(Actual - Expected)` residual threshold

### Chamber lifecycle

```text
No Production
    │
    └─ clean rows 충분
           ↓
       bootstrap fit
           ↓
      Production v1
           │
           ▼
      Live inference
           │
           ▼
 retraining readiness 계산
(new clean rows + recent error + elapsed time)
           │
           │ API/UI retrain trigger
           ▼
      Candidate fit
           │
   Production 비교 통과
           ▼
         Staging
           │
      explicit Promote
           ▼
new Production / old Archived
```

중요한 현재 경계:

- **readiness 계산은 자동**으로 할 수 있지만, readiness를 주기적으로 검사해 `retrain()`을 직접 실행하는 Chamber scheduler는 아직 없습니다.
- 실제 retrain은 `/api/v1/chamber/retrain` 또는 Live UI action으로 시작합니다.
- Production promotion도 명시적 API/UI action입니다.
- 기존 `/api/v1/automation/tick` / Lambda + EventBridge는 기존 wafer/운영 workflow용이며 Chamber retraining scheduler와 동일하지 않습니다.

### Static Demo

기존 `DATE`, `EQP`, `USE_TIME`, `RESISTANCE` 3,000행 offline 분석과 EQP10/EQP55 결과는 그대로 유지합니다.

```bash
python scripts/build_chamber_dashboard_data.py --input "/path/to/sample.csv" --output "frontend/src/data/chamberSample.json"
```

---

## 🖼️ Wafer Vision AI

`Chamber AI`의 Vision 탭은 `frontend/src/data/waferVisionSample.json`과 대표 이미지를 사용합니다.

- 통계 방식: 픽셀 중앙값·MAD 기반 결과
- 정상 전용 AI: ResNet18 feature + PaDiM-style 결과
- `statistical` / `ai` / `comparison` 모드는 **미리 계산된 결과의 표시 기준을 전환**합니다.
- 현재 WaferGuard React 안에서 Vision 모델을 실시간 추론하지 않습니다.
- 현재 snapshot: 3,000 images / 100 chambers / 300×300 px
- 샘플 이상 챔버: CH-007, CH-047, CH-061

선택한 챔버에서 `Inspection Agent에 전달`을 누르면 `vision_*` evidence가 `POST /api/v1/inspect`로 전달됩니다.

현재 sample Vision rule:

- 통계 `>= 2.0` + AI `>= 50.0` → Critical
- 둘 중 하나만 위 기준 초과 → Warning
- 통계 `>= 1.2` 또는 AI `>= 35.0` → caution-level Warning

> 위 값은 샘플 기준이며 실제 Fab spec/control limit가 아닙니다.

Vision snapshot 재생성:

```bash
python scripts/build_wafer_vision_dashboard_data.py --base-url http://127.0.0.1:<port> --dataset-id <dataset-id>
```

`wafer_particle` 서버 전체와 원본 dataset은 이 저장소에 포함하지 않습니다.

---

## 🤖 Inspection Agent / RAG

```text
Inspection 생성
     │
     ▼
Risk + Process + Metrology + Vision evidence
     │
     ▼
Inspection Agent (LangGraph)
     │
     ├── RAG 과거 사례 검색
     ├── DB / Image / MLOps tools
     └── 원인/조치 판단
             │
             ▼
         Action Card
             │
             ▼
        Engineer Review
             │
       ┌─────┴─────────┐
       ▼               ▼
approved/false_alarm  needs_review
       │               │
       ▼               └─ RAG 저장 안 함
RAG knowledge feedback
```

- `approved` / `false_alarm` 확정 사례만 검색 가능한 RAG knowledge로 저장합니다.
- `needs_review`는 미확정 상태이므로 knowledge feedback 대상이 아닙니다.

---

## 📈 기존 Wafer MLOps Agent

기존 WaferGuard MLOps Agent는 model registry와 drift history를 확인하고 retrain/promote/rollback workflow를 검증하는 **simulation**입니다.

- `model_registry`
- `drift_events`
- `retraining_jobs`
- approval gate
- `/api/v1/automation/tick`
- AWS Lambda + EventBridge automation

이 흐름은 Chamber의 실제 sklearn `chamber_model_registry`와 분리되어 있습니다.

---

## 🗄️ DB / Storage

WaferGuard는 기능별로 별도 DB 서버를 만들지 않고 **하나의 runtime DB backend 안에서 table을 분리**합니다.

### Local

```text
SQLite
└─ outputs/waferguard.db
```

### AWS / 운영 전환

```text
STORAGE_BACKEND=postgres
        ↓
PostgreSQL / RDS
```

### DB tables

기존 workflow 9개:

- `inspections`
- `model_registry`
- `drift_events`
- `retraining_jobs`
- `alerts`
- `handoff_reports`
- `agent_traces`
- `pending_approvals`
- `rag_documents`

Chamber 3개:

- `chamber_telemetry`: 원본 synthetic process telemetry
- `chamber_predictions`: Actual/Expected/residual/anomaly
- `chamber_model_registry`: Chamber model version/stage/metrics/artifact metadata

### File/Object storage

```text
이미지 / CSV / PDF / report
    ├─ local: outputs/
    └─ AWS: S3 (IMAGE_BACKEND=s3)

Chamber sklearn model
    └─ runtime/models/chamber/*.joblib
```

---

## 🧑‍💻 Tech Stack

| 영역 | 현재 구현 |
|------|-----------|
| Backend | FastAPI · Pydantic · LangGraph StateGraph |
| LLM | GPT-4o-mini via Luxia Gateway · text/image 입력 · tool calling |
| RAG | Luxia embedding 1024-d · cosine search · rerank · DB BLOB embedding |
| Frontend | React 19 · Vite 7 · Recharts 3 |
| Runtime DB | SQLite / PostgreSQL(RDS) |
| Object Storage | local `outputs/` / S3 |
| Chamber ML | pandas · scikit-learn · GradientBoosting · joblib · PyYAML |
| AWS | boto3 · S3 · RDS · Secrets Manager · SNS · Lambda/EventBridge |

---

## 🛜 AWS Deployment

WaferGuard는 로컬 모드를 기본값으로 유지하면서 환경변수로 AWS backend를 선택할 수 있습니다.

| AWS 서비스 | 역할 | 코드 연결 |
|------------|------|-----------|
| EC2 | FastAPI + production frontend | `app.main` / `frontend/dist` |
| S3 | 이미지·CSV·PDF object storage | `app/services/object_store.py` |
| RDS PostgreSQL | runtime DB | `app/services/db.py` |
| Secrets Manager | environment secret | `app/services/config.py`, `app/services/aws.py` |
| SNS | high-risk alert fan-out | `app/services/aws.py` |
| CloudWatch | 운영 로그/메트릭 | deployment/runtime |
| Lambda + EventBridge | 기존 automation tick | `infra/lambda/automation_tick.py` |

상세 내용은 `docs/AWS_Deploy_Guide.md`와 `docs/AWS_*.md`를 참고하세요.

---

## Prerequisites

| 도구 | 권장/최소 버전 |
|------|----------------|
| Python | 3.11 |
| Node.js | 20.19+ 또는 22.12+ |
| npm | Node.js에 포함된 최신 npm 권장 |

---

## Local Demo Guide

### 1. Backend dependencies

```bash
git clone https://github.com/arnold6444/WaferGuard.git
cd WaferGuard

python -m venv .venv
```

Windows에서 Python 3.11을 명시하려면:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Conda를 쓰는 경우:

```bash
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

PowerShell execution policy 때문에 `npm.ps1`이 막히면 현재 터미널에 한해 다음을 사용할 수 있습니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
npm run dev
```

또는 `npm.cmd run dev`를 사용할 수 있습니다.

### 3. Frontend API URL

`frontend/.env.local`:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 4. LLM Access (optional)

프로젝트 루트 `.env`:

```text
LUXIA_API_KEY=발급받은_키
```

키가 없어도 앱은 기동되며 fallback/stub 경로를 사용할 수 있습니다.

### 5. 실행

**터미널 1 — Backend**

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**터미널 2 — Frontend**

```bash
cd frontend
npm run dev
```

**터미널 3 — Chamber Live stream**

빠른 테스트:

```bash
python scripts/run_chamber_stream.py --equipment-count 3 --interval 0 --samples 180
```

1초 간격 demo:

```bash
python scripts/run_chamber_stream.py --equipment-count 3 --interval 1 --samples 500
```

RF drift injection:

```bash
python scripts/run_chamber_stream.py --equipment-count 3 --interval 1 --samples 200 --anomaly rf_power_drift --anomaly-after 30
```

접속 주소:

```text
Dashboard : http://127.0.0.1:5173
API docs  : http://127.0.0.1:8000/docs
Health    : http://127.0.0.1:8000/health
```

`/health` 예시:

```json
{"status":"ok","service":"waferguard-api"}
```

### 6. Smoke / test / build

```bash
python scripts/smoke_test.py
python -m pytest tests/test_chamber.py -q
python -m compileall -q app scripts tests

cd frontend
npm run build
```

---

## 주요 API

| Method | Endpoint | 역할 |
|--------|----------|------|
| GET | `/health` | service health |
| POST | `/api/v1/inspect` | 검사 생성 + risk/action card + agent workflow |
| GET | `/api/v1/inspections` | 최근 검사 목록 |
| GET | `/api/v1/inspect/{id}/trace` | 검사 Agent trace |
| POST | `/api/v1/inspect/{id}/chat/stream` | 검사 근거 기반 streaming chat |
| POST | `/api/v1/review/{id}` | 엔지니어 review + 확정 사례 RAG 저장 |
| GET/POST | `/api/v1/automation/status`, `/api/v1/automation/tick` | 기존 automation 상태/실행 |
| GET | `/api/v1/rag/search` | RAG 검색 |
| GET | `/api/v1/db/overview` | runtime DB 개요 |
| GET | `/api/v1/chamber/status` | warm-up/Production/readiness |
| GET | `/api/v1/chamber/equipment` | 장비별 최신 상태 |
| GET | `/api/v1/chamber/telemetry` | Chamber telemetry |
| GET | `/api/v1/chamber/predictions` | Actual/Expected/residual/anomaly |
| GET | `/api/v1/chamber/models` | Chamber model registry |
| POST | `/api/v1/chamber/retrain` | readiness gate 후 실제 Candidate fit |
| POST | `/api/v1/chamber/models/{version}/promote` | artifact 검증 후 Production 승격 |
| GET | `/api/v1/mlops/state` | 기존 wafer MLOps state |
| POST | `/api/v1/mlops/agent/run` | MLOps Agent |
| POST | `/api/v1/mlops/drift` | drift simulation |
| POST | `/api/v1/mlops/retrain` | retrain simulation |
| POST | `/api/v1/models/promote` | wafer staging promote |
| POST | `/api/v1/models/rollback` | wafer rollback |
| GET | `/api/v1/pending-approvals` | 승인 대기 action |

전체 API 계약은 실행 후 FastAPI Swagger(`/docs`)에서 확인할 수 있습니다.

---

## Project Structure

```text
app/
  main.py
  services/
    schemas.py
    pipeline.py
    risk.py
    action_card.py
    agent.py
    tools.py
    rag.py
    defect_chat.py
    mlops.py
    chamber_generator.py
    chamber_storage.py
    chamber_training.py
    chamber_runtime.py
    automation.py
    storage.py
    db.py
    object_store.py
    luxia_client.py
    aws.py
    synthetic_wafer.py
  data/

configs/
  chamber.yaml

frontend/
  src/App.jsx
  src/ChamberView.jsx
  src/WaferVisionView.jsx
  src/InspectionWorkspace.jsx
  src/MlopsWorkspace.jsx
  src/DatabaseView.jsx
  src/SettingsView.jsx
  src/data/chamberSample.json
  src/data/waferVisionSample.json

scripts/
  run_chamber_stream.py
  build_chamber_dashboard_data.py
  build_wafer_vision_dashboard_data.py
  build_wm811k_subset.py
  smoke_test.py

infra/
  lambda/automation_tick.py

tests/
  test_chamber.py

outputs/                     # SQLite DB / local images / reports (gitignore)
runtime/models/chamber/      # Chamber sklearn artifacts (gitignore)
```

---

## 현재 구현 경계

- Chamber Resistance Live는 **synthetic stream → DB → 실제 sklearn fit/inference → residual anomaly → Staging/Production lifecycle**을 연결합니다.
- 기존 CSV 기반 `USE_TIME` 분석은 Static Demo로 유지됩니다.
- Wafer Vision은 외부 `wafer_particle` 결과 snapshot을 사용합니다.
- Vision → Inspection Agent API handoff는 구현되어 있습니다.
- 실제 장비 ingest, 실제 Fab control limit, 자동 장비 제어, production 성능 보장은 범위 밖입니다.
- 기존 wafer MLOps는 simulation이고 Chamber retrain은 실제 sklearn 학습입니다.
- Chamber readiness 기반 **자동 주기 retrain scheduler는 아직 없습니다.**
- `wafer_count_since_clean`은 현재 running sample마다 증가하는 demo counter입니다.
- 여러 equipment sample은 하나의 generator clock을 순차 사용합니다.
- `run_chamber_stream.py`는 local demo용 유한 runner이며 production streaming daemon이 아닙니다.

상세 Chamber 사양과 결정 기록은 `docs/spec.md`, `docs/decisions.md`, `docs/progress.md`를 참고하세요.

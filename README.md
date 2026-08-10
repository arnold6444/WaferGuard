# WaferGuard

**반도체 Fab Quality Ops를 위한 멀티 에이전트 운영 보조 시스템**

WaferGuard는 반도체 공정에서 발생한 이상 신호를 한 화면에서 확인하고, 검사 결과를 **리스크 평가 → RAG 기반 원인/조치 추천 → 엔지니어 승인 → MLOps 판단** 흐름으로 연결하는 데모/MVP입니다.

현재 저장소는 실제 Fab 설비를 직접 제어하는 시스템이 아니라, **샘플·프록시 데이터와 시뮬레이션을 이용해 운영 의사결정 흐름을 검증하는 구조**입니다. 특히 Chamber AI의 저항/영상 결과는 현재 정적 분석 스냅샷을 대시보드에 포함하고 있으며, 선택한 영상 근거를 기존 Inspection Agent 파이프라인으로 전달할 수 있습니다.

## 📝 Overview

WaferGuard의 핵심은 결함을 단순히 분류하는 데서 끝내지 않고, 탐지 이후에 필요한 운영 판단을 하나의 워크플로로 묶는 것입니다.

```text
Chamber / Wafer evidence
        ↓
POST /api/v1/inspect
        ↓
Risk + Metrology / Vision rules
        ↓
Inspection Agent + RAG + Action Card
        ↓
Human approval / review
        ↓
RAG knowledge feedback + MLOps delegation
```

### ⭐️ Key Features

- **Chamber Resistance AI**: `USE_TIME → 정상 RESISTANCE` 관계를 Gradient Boosting으로 모델링한 샘플 분석 결과를 표시합니다. EQP 그룹 단위 OOF 검증과 MAD 기반 설비 판정을 사용합니다.
- **Wafer Vision AI**: `wafer_particle` 분석 서버에서 미리 생성한 통계/정상 전용 AI 비교 결과와 대표 히트맵을 대시보드에서 탐색합니다.
- **Live Inspection**: wafer/process 입력을 받아 리스크 점수, metrology/vision rule hit, Action Card, 이미지 근거를 생성하고 검사 이력으로 저장합니다.
- **Inspection Agent**: LangGraph 기반 도구 호출 루프로 과거 사례와 공정 근거를 조회하고 추정 원인과 대응 방안을 제안합니다.
- **Human-in-the-Loop RAG**: 엔지니어 리뷰 중 `approved` 또는 `false_alarm`으로 확정된 사례만 RAG 지식으로 다시 저장합니다. 미확정 `needs_review` 상태는 학습 사례로 저장하지 않습니다.
- **MLOps Agent**: drift/model 상태를 확인하고 재학습 권고, 승인 요청, promote/rollback 시뮬레이션을 수행합니다. Inspection Agent에서 fleet-level 문제로 판단되면 MLOps Agent로 위임할 수 있습니다.
- **Approval Gate**: critical alert나 retraining 같은 high-risk tool action은 pending approval에 저장한 뒤 엔지니어 승인/거절로 처리합니다.
- **Data & RAG Console**: 검사 이력, Agent trace, approval, 모델/드리프트, RAG 문서 등 운영 DB를 읽어볼 수 있습니다.

## 🎯 Demo

### 📡 Chamber Resistance AI

![USE TIME 기반 챔버 저항 이상탐지 대시보드](docs/assets/chamber-dashboard-preview.png)

현재 React 화면은 `frontend/src/data/chamberSample.json`을 읽어 표시합니다. 즉, 브라우저가 실행될 때 모델을 다시 학습하는 구조가 아니라 **오프라인 분석 스크립트로 결과 JSON을 만들고 프론트엔드가 그 스냅샷을 시각화하는 구조**입니다.

- 입력 샘플: `DATE`, `EQP`, `USE_TIME`, `RESISTANCE`
- 모델: `GradientBoostingRegressor(loss="huber")`
- 검증: EQP 그룹 단위 5-fold OOF
- 설비 후보: EQP별 Median/Q95 절대오차 + scaled MAD 기준
- 현재 샘플: 3,000행 / 100 EQP
- 샘플 이상 후보: EQP10, EQP55

샘플 JSON을 다시 만들려면 런타임 기본 의존성 외에 `pandas`, `scikit-learn`이 추가로 필요합니다.

```bash
pip install pandas scikit-learn
python scripts/build_chamber_dashboard_data.py --input "/path/to/sample.csv" --output "frontend/src/data/chamberSample.json"
```

> 원본 `sample.csv`는 저장소에 포함하지 않습니다.

### 🖼️ Wafer Vision AI

`Chamber AI`의 두 번째 탭은 `frontend/src/data/waferVisionSample.json`과 대표 이미지를 사용합니다.

- 통계 방식: 픽셀 중앙값·MAD 기반 결과
- 정상 전용 AI: ResNet18 feature + PaDiM-style 결과
- 비교 모드: 두 방식의 **미리 계산된 결과 중 어떤 기준을 화면에 표시할지 전환**합니다. 현재 WaferGuard 프론트엔드 안에서 모델 추론을 실시간으로 다시 수행하지는 않습니다.
- 현재 스냅샷: 3,000 images / 100 chambers / 300×300 px
- 샘플 이상 챔버: CH-007, CH-047, CH-061
- 샘플 이상 이미지: 51장

선택한 챔버에서 `Inspection Agent에 전달`을 누르면 통계/AI 점수, 방향, 시점, 이상 면적을 전용 `vision_*` 필드로 `POST /api/v1/inspect`에 보내고 기존 Inspection Agent 화면으로 연결합니다.

현재 Action Card의 Vision rule은 샘플 기준으로 다음 임계값을 사용합니다.

- 통계 `>= 2.0` + AI `>= 50.0`: Critical evidence
- 둘 중 하나만 위 기준 초과: Warning
- 통계 `>= 1.2` 또는 AI `>= 35.0`: Caution-level warning

이 값은 **실제 Fab spec/control limit가 아니라 현재 샘플용 임계값**입니다.

`wafer_particle` 분석 서버가 별도로 실행 중이라면 비교 결과와 대표 자산을 다시 생성할 수 있습니다.

```bash
python scripts/build_wafer_vision_dashboard_data.py --base-url http://127.0.0.1:<port> --dataset-id <dataset-id>
```

> `wafer_particle` 분석 서버 전체는 이 저장소에 포함되어 있지 않습니다. WaferGuard에는 생성된 결과 스냅샷과 대표 자산만 포함합니다.

### 🔎 Live Inspection

![실시간 검사 콘솔](docs/assets/dashboard.png)

- `POST /api/v1/inspect`로 검사 건을 생성합니다.
- wafer map / heatmap / overlay / ROI와 공정·metrology 근거를 함께 저장합니다.
- Medium/High 리스크 케이스는 Agent 분석 및 review workflow로 연결됩니다.
- 검사 건별 Agent trace 조회, 재실행, 근거 기반 chat, SSE streaming을 지원합니다.

### 🤖 Inspection Agent

![Inspection Agent 판단](docs/assets/agent_inspection.png)

- LangGraph 기반 Agent가 RAG와 도구를 사용해 검사 근거를 조회합니다.
- 과거 사례, 공정 정보, metrology/vision rule hit를 바탕으로 Action Card와 권장 조치를 구성합니다.
- 엔지니어가 `approved` 또는 `false_alarm`으로 결론을 내린 사례는 다시 검색 가능한 지식으로 저장됩니다.

### 📈 MLOps Agent

![MLOps 위임 & 자율성](docs/assets/agent_mlops.png)

- 모델 registry와 drift history를 조회합니다.
- 재학습, promote, rollback은 현재 시뮬레이션 형태로 구현되어 있습니다.
- high-risk action은 approval gate를 통과해야 실행됩니다.
- 자동화 tick은 `/api/v1/automation/tick`으로 실행하며 AWS에서는 Lambda + EventBridge로 호출할 수 있습니다.

## ✍️ Agentic Flow

![에이전트 아키텍처](docs/assets/agent_architecture.png)

> 위 다이어그램은 기존 Inspection/MLOps Agent 흐름을 설명합니다. 최신 Chamber Resistance/Vision AI는 이 흐름의 **상류 evidence source**로 연결됩니다.

## 🧑‍💻 Tech Stack

| 영역 | 현재 구현 |
|------|-----------|
| Backend | FastAPI · Pydantic · LangGraph StateGraph |
| LLM | GPT-4o-mini via Luxia Gateway · text/image 입력 · tool calling |
| RAG | Luxia embedding 1024-d · cosine search · rerank · DB BLOB embedding |
| Frontend | React 19 · Vite 7 · Recharts 3 |
| Runtime DB | 기본 SQLite / `STORAGE_BACKEND=postgres` 시 PostgreSQL(RDS) |
| Object Storage | 기본 local `outputs/` / `IMAGE_BACKEND=s3` 시 S3 |
| Workflow tables | 9개: inspections · model_registry · drift_events · retraining_jobs · alerts · handoff_reports · agent_traces · pending_approvals · rag_documents |
| Chamber offline analysis | NumPy · pandas · scikit-learn (`build_chamber_dashboard_data.py`에서만 필요) |
| AWS integration | boto3 · S3 · RDS · Secrets Manager · SNS · Lambda/EventBridge |

## 🛜 AWS Deployment

WaferGuard는 로컬 모드를 기본값으로 유지하면서 환경변수로 AWS backend를 선택할 수 있습니다.

![AWS 아키텍처](docs/assets/aws-architecture.png)

| AWS 서비스 | 역할 | 코드 연결 |
|------------|------|-----------|
| **EC2** | FastAPI + production frontend 실행 | `app.main` / `frontend/dist` |
| **S3** | 이미지·CSV·PDF object storage | `app/services/object_store.py` |
| **RDS (PostgreSQL)** | workflow DB | `app/services/db.py` |
| **Secrets Manager** | 환경 secret 로딩 | `app/services/config.py`, `app/services/aws.py` |
| **SNS** | high-risk alert fan-out | `app/services/aws.py` + alert workflow |
| **CloudWatch** | 배포 환경 로그/메트릭 관찰 | uvicorn/AWS 운영 구성 |
| **Lambda + EventBridge** | 주기적인 automation tick 호출 | `infra/lambda/automation_tick.py` |

상세 배포 절차는 [`docs/AWS_Deploy_Guide.md`](docs/AWS_Deploy_Guide.md), 마이그레이션 및 비용 관련 내용은 [`docs/`](docs/)의 AWS 문서를 참고하세요.

## Prerequisites

| 도구 | 권장/최소 버전 |
|------|----------------|
| Python | 3.11 |
| Node.js | **20.19+ 또는 22.12+** (현재 Vite 7 기준) |
| npm | 위 Node.js 설치에 포함된 최신 npm 권장 |

## Local Demo Guide

### 1. Clone & Install Backend Dependencies

```bash
git clone https://github.com/arnold6444/WaferGuard.git
cd WaferGuard

conda create -n waferguard python=3.11 -y
conda activate waferguard
pip install -r requirements.txt
```

### 2. Install Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

### 3. Configure LLM Access (optional)

실제 Luxia LLM 호출을 사용하려면 프로젝트 루트에 `.env`를 만듭니다.

```bash
LUXIA_API_KEY=발급받은_키
```

`LUXIA_API_KEY`가 없어도 앱은 기동되며, Luxia client는 stub/fallback 경로를 사용합니다. 실제 Agent LLM 응답을 확인하려면 API 키가 필요합니다.

### 4. Configure Frontend API URL

`frontend/.env.local`을 만들면 Windows PowerShell/bash 구분 없이 같은 방식으로 실행할 수 있습니다.

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 5. Run Backend & Frontend

**터미널 1 — Backend**

```bash
conda activate waferguard
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**터미널 2 — Frontend**

```bash
cd frontend
npm run dev
```

접속 주소:

```text
Dashboard : http://127.0.0.1:5173
API docs  : http://127.0.0.1:8000/docs
Health    : http://127.0.0.1:8000/health
```

현재 `/health` 응답:

```json
{"status":"ok","service":"waferguard-api"}
```

### 6. Smoke Test & Build

`smoke_test.py`는 실행 중인 서버에 HTTP 요청을 보내는 방식이 아니라 **FastAPI `TestClient`로 앱을 직접 import해서 검사**하므로 백엔드 서버를 따로 띄워둘 필요가 없습니다.

```bash
conda activate waferguard
python scripts/smoke_test.py

cd frontend
npm run build
```

## 주요 API

| Method | Endpoint | 역할 |
|--------|----------|------|
| GET | `/health` | 서비스 상태 |
| POST | `/api/v1/inspect` | 검사 생성 + risk/action card + agent workflow |
| GET | `/api/v1/inspections` | 최근 검사 목록 |
| GET | `/api/v1/inspect/{id}/trace` | 검사 Agent trace |
| POST | `/api/v1/inspect/{id}/chat/stream` | 검사 근거 기반 streaming chat |
| POST | `/api/v1/review/{id}` | 엔지니어 review + 확정 사례 RAG 저장 |
| GET/POST | `/api/v1/automation/status`, `/api/v1/automation/tick` | 자동화 상태/실행 |
| GET | `/api/v1/rag/search` | 과거 사례 검색 |
| GET | `/api/v1/db/overview` | workflow DB 개요 |
| GET | `/api/v1/mlops/state` | MLOps 상태 |
| POST | `/api/v1/mlops/agent/run` | MLOps Agent 실행 |
| POST | `/api/v1/mlops/drift` | drift 시뮬레이션 |
| POST | `/api/v1/mlops/retrain` | retraining 시뮬레이션 |
| POST | `/api/v1/models/promote` | staging model promote |
| POST | `/api/v1/models/rollback` | model rollback |
| GET | `/api/v1/pending-approvals` | 승인 대기 action 목록 |

전체 API 계약은 실행 후 FastAPI Swagger(`/docs`)에서 확인할 수 있습니다.

## Project Structure

```text
app/
  main.py                    # FastAPI routes + frontend dist mount
  services/
    schemas.py               # Pydantic request models
    pipeline.py              # POST /api/v1/inspect 핵심 흐름
    risk.py                  # risk score / level
    action_card.py           # metrology + vision rules / Action Card
    agent.py                 # Inspection/MLOps LangGraph agent
    tools.py                 # Agent tool registry
    rag.py                   # RAG index / case retrieval
    defect_chat.py           # inspection chat + SSE streaming
    mlops.py                 # drift/retrain/promote/rollback simulation
    automation.py            # periodic automation tick
    storage.py               # workflow persistence / 9-table schema
    db.py                    # SQLite ↔ PostgreSQL abstraction
    object_store.py          # local outputs ↔ S3 abstraction
    luxia_client.py          # Luxia chat/embedding/rerank client
    aws.py                   # AWS clients
    synthetic_wafer.py       # wafer map / heatmap / ROI generation
  data/                      # RAG/evaluation fixture + WM-811K subset

frontend/
  src/App.jsx                # 5개 운영 섹션 navigation
  src/ChamberView.jsx        # Resistance AI + Vision AI tabs
  src/WaferVisionView.jsx    # vision snapshot + Inspection Agent handoff
  src/InspectionWorkspace.jsx
  src/MlopsWorkspace.jsx
  src/DatabaseView.jsx
  src/SettingsView.jsx
  src/data/chamberSample.json
  src/data/waferVisionSample.json

scripts/
  smoke_test.py
  build_wm811k_subset.py
  build_chamber_dashboard_data.py
  build_wafer_vision_dashboard_data.py

infra/
  lambda/automation_tick.py

docs/
  spec.md
  decisions.md
  progress.md
  AWS_*.md

outputs/                     # local runtime DB/images/reports (gitignore)
```

## 현재 구현 경계

- Chamber Resistance 모델은 FastAPI 요청마다 학습/추론하지 않고 **오프라인 JSON 생성 방식**입니다.
- Wafer Vision 탭 역시 **외부 `wafer_particle` 분석 결과의 정적 스냅샷**을 사용합니다.
- Vision 결과를 Inspection Agent로 넘기는 API 연동은 구현되어 있습니다.
- 실제 설비 스트리밍, 실제 Fab control limit, 자동 장비 제어, production 성능 보장은 현재 범위가 아닙니다.
- MLOps retrain/promote/rollback은 운영 흐름 검증을 위한 시뮬레이션입니다.

# WaferGuard

**반도체 Fab Quality Ops를 위한 멀티 에이전트 운영 보조 시스템**

WaferGuard는 반도체 공정 설비의 이상과 Wafer 품질 이상을 함께 모니터링하고, 공정 시계열·이미지·Metrology·과거 사례를 연결하여 이상 원인 후보와 대응안을 제안하는 Fab Quality Ops 데모 플랫폼입니다.

현재 저장소는 실제 Fab 설비를 직접 제어하는 시스템이 아니라, **샘플·프록시 데이터와 시뮬레이션을 이용해 운영 의사결정 흐름을 검증하는 구조**입니다. Lot lifecycle, Data Quality Gate, 실제 sklearn Expected Resistance 모델, 공정별 temporal synthetic anomaly runtime, Vision proxy, PostgreSQL, RCA Agent, MLOps lifecycle을 하나의 흐름으로 연결합니다.

## 📝 Overview

```text
Fab Overview
        ↓
Process Monitoring
  ├ Etch specialized Chamber runtime
  └ Photo / Deposition / CMP temporal multimodal runtime
        ↓
PostgreSQL process/runtime evidence
        +
Wafer Quality / Vision / Metrology
        ↓
Inspection + RCA Agent
        ↓
원인 후보 + 관련 Tag + 조치안
        ↓
Model Registry / Staging / Production / Rollback
```

### ⭐️ Key Features

- **Fab Overview**: 공정 상태, Active Lot, connected equipment, warning/critical, 최근 이상 후보를 한 화면에서 표시합니다.
- **Process Monitoring / Etch**: 기존 특화 Chamber telemetry, Expected Resistance, residual detector, DQ gate, model registry를 유지합니다.
- **Process Monitoring / Photo·Deposition·CMP**: 공정 phase, 시간 연속성(AR), Tag correlation, 장비 bias, 8-sample rolling feature를 포함한 synthetic time-series와 wafer-like Vision을 실시간 생성·탐지합니다.
- **Temporal anomaly injection**: drift, change point, spike, stuck, oscillation, noise increase, correlation break를 지원합니다.
- **TS model selection**: Robust-Z, Mahalanobis, IsolationForest, OneClassSVM을 validation F2 우선으로 비교합니다.
- **Vision model selection**: IsolationForest와 OneClassSVM을 실제 joblib artifact로 학습·비교합니다.
- **Model lifecycle**: process × modality별 Production / Staging / Archived, guarded promotion, rollback을 지원하며 승격된 artifact가 다음 inference에 실제 사용됩니다.
- **PostgreSQL evidence**: runtime sample과 anomaly event를 저장하고 같은 Lot/equipment의 RCA evidence로 재사용합니다.
- **RCA Agent**: 최근 process event, Vision evidence, 관련 Tag 후보, 과거 사례를 조회하여 원인 후보와 권장 조치를 구성합니다.
- **Live Dashboard**: raw Tag trend, process phase, Vision input, deviation diagnostic, score/threshold/**anomaly margin**, Production model, current-run precision/recall/F2를 표시합니다.
- **Korean / English UI**: 상단과 Settings에서 언어를 선택하며 주요 workspace의 static UI는 선택한 언어만 표시합니다.
- **Light / Dark UI**: header/sidebar/main/panel/table/input/chart가 동일 theme token을 사용하며 선택값은 새로고침 후에도 유지됩니다.

> Generic Vision의 deviation map은 입력의 공간적 편차를 보기 위한 진단 이미지이며 model attribution/localization heatmap이 아닙니다.

## 🎯 Generic Multimodal Runtime

### 공정 profile

| Process | Time-series examples | Vision defects |
|---|---|---|
| Photo | exposure dose, focus, track/developer temperature, overlay | bridge, missing pattern, misalignment, residue |
| Deposition | chamber pressure, substrate temperature, precursor/carrier gas, RF | particle, pinhole, thickness non-uniformity |
| CMP | down force, platen/carrier speed, slurry flow, motor current | scratch, residue, dishing, erosion |

각 공정은 고정된 독립 random row가 아니라 process phase를 따라 변화합니다.

```text
Photo       idle → coat/track → expose → develop
Deposition  pumpdown → heatup → deposition → purge
CMP         load → ramp → polish → rinse
```

TS feature contract:

```text
현재 Tag z-score
+ 직전 sample 대비 delta
+ Tag 관계 error
+ 최근 8 sample rolling mean deviation
+ 최근 8 sample rolling std
```

Anomaly 판단은 raw score 자체의 부호가 아니라 threshold와의 차이로 봅니다.

```text
Anomaly Margin = score - threshold
margin < 0  → NORMAL
margin >= 0 → ANOMALY
```

따라서 anomaly score가 음수여도 정상일 수 있습니다.

상세 구조는 [`docs/process_multimodal_runtime.md`](docs/process_multimodal_runtime.md)를 참고하세요.

## 🧑‍💻 Tech Stack

| 영역 | 현재 구현 |
|---|---|
| Backend | FastAPI · Pydantic · LangGraph StateGraph |
| Frontend | React 19 · Vite 7 · Recharts 3 |
| Runtime DB | PostgreSQL 16 표준 · 명시적 unit/demo만 SQLite |
| TS ML | scikit-learn · Robust-Z · LedoitWolf Mahalanobis · IsolationForest · OneClassSVM · joblib |
| Vision ML | handcrafted image features · IsolationForest · OneClassSVM · joblib |
| Object Storage | 기본 local `outputs/` / `IMAGE_BACKEND=s3` 시 S3 |
| MLOps | Process model registry · Staging / Production / Archived · promotion gate · rollback |
| Agent | Inspection/RCA Agent · RAG · approval workflow |

## Prerequisites

| 도구 | 권장/최소 버전 |
|---|---|
| Python | 3.11 |
| Node.js | 20.19+ 또는 22.12+ |
| npm | Node.js 설치에 포함된 최신 npm 권장 |
| Docker | PostgreSQL 로컬 실행용 |

## Local Demo Guide

### 1. Backend dependencies

```powershell
cd C:\Users\user\Desktop\WaferGuard
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Frontend dependencies

```powershell
cd C:\Users\user\Desktop\WaferGuard\frontend
npm.cmd install
```

### 3. PostgreSQL

프로젝트 루트에서 `.env`가 없다면:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

DB 실행:

```powershell
docker compose up -d postgres
docker compose ps
```

기본 설정은 PostgreSQL `127.0.0.1:5432`, DB/user/password `waferguard`입니다. 5432가 이미 사용 중이면 `.env`의 `POSTGRES_PORT`와 `RDS_PORT`를 함께 변경합니다.

### 4. Backend

새 PowerShell:

```powershell
cd C:\Users\user\Desktop\WaferGuard
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

확인:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

### 5. Frontend

새 PowerShell:

```powershell
cd C:\Users\user\Desktop\WaferGuard\frontend
npm.cmd run dev
```

접속:

```text
http://127.0.0.1:5173
```

### 6. Photo + Deposition + CMP live stream

새 PowerShell:

```powershell
cd C:\Users\user\Desktop\WaferGuard
.\.venv\Scripts\Activate.ps1
python scripts/run_process_stream.py --process all --modality both --samples 300 --interval 1
```

한 공정의 TS anomaly를 명시적으로 보고 싶으면:

```powershell
python scripts/run_process_stream.py --process cmp --modality timeseries --samples 150 --interval 1 --anomaly correlation_break --anomaly-after 60
```

Vision defect 예:

```powershell
python scripts/run_process_stream.py --process cmp --modality vision --samples 100 --interval 1 --anomaly scratch --anomaly-after 40
```

### 7. Etch live stream

Etch는 기존 특화 runtime을 사용합니다.

```powershell
python scripts/run_chamber_stream.py --equipment-count 3 --interval 1
```

## Model Lifecycle

모델 목록:

```powershell
python scripts/manage_process_models.py list --process cmp
```

현재 TS health:

```powershell
python scripts/manage_process_models.py health --process cmp --modality timeseries
```

Staging 재학습:

```powershell
python scripts/manage_process_models.py retrain --process cmp --modality timeseries --force
```

승격:

```powershell
python scripts/manage_process_models.py promote --process cmp --modality timeseries --version <VERSION>
```

Rollback:

```powershell
python scripts/manage_process_models.py rollback --process cmp --modality timeseries
```

## RCA

최근 공정 evidence를 PostgreSQL에서 다시 조회하여 RCA Agent에 전달합니다.

```powershell
python scripts/run_process_rca.py --process cmp --equipment-id CMP-01 --lot-id LOT-MM-DEMO-001 --minutes 30
```

## 주요 API

| Method | Endpoint | 역할 |
|---|---|---|
| GET | `/health` | API + DB 상태 |
| GET | `/api/v1/fab/overview` | Fab Overview |
| GET | `/api/v1/process/profiles` | 공정 profile metadata |
| GET | `/api/v1/process/events` | 공통 process anomaly event |
| GET | `/api/v1/quality/lots` | Wafer Quality Lot 요약 |
| GET | `/api/v1/quality/lots/{lot_id}` | Wafer detail/timeline |
| POST | `/api/v1/inspect` | inspection + risk/action card + Agent workflow |
| GET | `/api/v1/chamber/status` | Etch Chamber runtime 상태 |
| GET | `/api/v1/chamber/models` | Etch Chamber model registry |
| POST | `/api/v1/chamber/retrain` | Etch candidate 학습 |
| POST | `/api/v1/chamber/models/{version}/promote` | Etch Production 승격 |
| GET | `/api/v1/db/overview` | 운영 DB 개요 |
| GET | `/api/v1/rag/search` | 유사 사례 검색 |

## Persistence

Generic process runtime:

```text
PostgreSQL
├ process_runtime_samples
├ process_model_registry
└ process_events

runtime/models/process/<process>/<modality>/<version>.joblib
outputs/process_runtime/<process>/live.json
outputs/process_runtime/<process>/*.png
```

## Important Boundaries

- Synthetic range, correlation, anomaly rule, performance는 실제 Fab spec이 아닙니다.
- 현재 runtime metric은 synthetic GT 기반입니다. 실제 Fab에서는 time-based holdout, drift, delayed quality label로 교체해야 합니다.
- Generic Vision은 아직 image-feature anomaly model이며 실제 PatchCore/PaDiM/DINO localization은 후속 확장입니다.
- Korean/English 전환은 App shell과 주요 workspace의 static UI에 적용됩니다. 일부 기존 legacy 상세 컴포넌트와 DB/RAG/LLM 원문은 자동 번역하지 않습니다.
- WaferGuard는 실제 Fab 설비를 제어하거나 자동 조치하는 production system이 아니라 운영 의사결정 workflow를 검증하는 데모/실험 플랫폼입니다.

# WaferGuard Fab Quality Ops 사양

## 제품 구조

```text
Fab Overview
Process Monitoring
Wafer Quality
AI Analysis
MLOps
Data & RAG
Settings
```

WaferGuard는 실제 Fab 제어 시스템이 아니라 synthetic Lot/telemetry, proxy inspection, 저장된 Vision snapshot으로 운영 의사결정 흐름을 검증하는 데모/MVP다.

```text
Lot → Chamber lifecycle → Data Quality Gate → Expected Resistance
    → MAD/EWMA detector → process_events → delayed Inspection
    → same-Lot/time evidence → structured Agent → Engineer review/RAG
```

## Fab Overview

- `GET /api/v1/fab/overview`의 runtime read model을 사용한다.
- Active Lot과 Inspection 상태는 검사 DB를 우선 사용한다.
- Etch 상태는 Chamber equipment/prediction을 우선 사용한다.
- 연결되지 않은 공정은 결정론적 `Demo profile` 상태로 표시한다.
- 모든 카드와 alert는 `Runtime DB`, `Synthetic runtime`, `Proxy runtime`, `Demo profile` 출처를 함께 표시한다.

## Process Monitoring

### 공통 profile

`GET /api/v1/process/profiles`는 다음 8개 공정의 표시 metadata를 제공한다.

```text
Oxidation · Photo · Etch · Deposition · Implant · Metal · CMP · Inspection
```

각 profile은 `process_id`, `display_name`, `equipment_type`, `connection`, `data_source`, `parameters[]`를 가진다. parameter에는 label, unit, sample normal range, display order가 포함된다.

Etch와 Inspection만 runtime/proxy runtime에 연결되어 있다. 나머지는 `Demo profile · Not connected`다.

### Etch

기존 Chamber Resistance 구현을 다음 화면으로 재사용한다.

- Overview: 설비/telemetry/prediction/process event 요약
- Equipment: recipe, machine state, Actual/Expected/Residual
- Process Trend: Resistance와 pressure/RF/gas/temperature deviation의 공통 시간축
- Anomaly: `process_events` history와 candidate signal
- Model: Production/Staging, MAE/RMSE/threshold, feature importance, retrain/promote, Static Demo

기존 `chamber_telemetry`, `chamber_predictions`, `chamber_model_registry`와 API는 유지한다.

## Process Event Layer

`process_events`는 공정별 원본 테이블을 대체하지 않는 공통 projection이다.

```text
id
process_step
equipment_id
recipe_id
lot_id
wafer_id
observed_at
event_type
severity
metadata_json
source
created_at
```

Chamber residual anomaly가 발생하면 `chamber_synthetic_runtime` source로 event를 upsert한다. 조회는 `GET /api/v1/process/events`를 사용한다.

검사 context는 같은 Lot을 먼저 강제하고 inspection timestamp 이전 30분의 동일/숫자 alias equipment event를 `related_process_events`에 포함한다. 다른 Lot의 이벤트는 장비와 시각이 같아도 제외한다. 이는 `Temporal signal candidate`이며 인과관계가 아니다.

## Wafer Quality

### 데이터 우선순위

1. `GET /api/v1/quality/lots`와 `/lots/{lot_id}`의 runtime inspection DB 집계
2. runtime 데이터가 없을 때 기존 `waferVisionSample.json`을 사용한 `LOT-VISION-DEMO-042`

Demo dataset은 기존 snapshot과 대표 이미지를 직접 재사용하며 `Demo / Proxy`로 표시한다.

### 화면

- Overview: Lot summary와 25장 Wafer 상태판
- Timeline: risk, Vision score, defect count, overlay 변화
- Defect Map: Lot/Equipment/Process/Recipe/Time filter와 ROI-center proxy 누적 map
- Wafer Detail: 기존 InspectionView의 map/image/overlay/Metrology를 재사용
- Vision Evidence: statistical/AI/comparison snapshot과 Agent handoff

Runtime record가 없는 Wafer는 `Not inspected`로 표시한다. 누적 map의 runtime 좌표는 저장된 ROI 중심의 정규화 값이며 실제 die-level segmentation 좌표가 아니다.

## AI Analysis

최상위 AI Analysis는 기존 `AgentView`를 재사용한다.

Agent evidence에는 다음이 포함될 수 있다.

- 현재 Wafer와 Metrology/Vision rule hit
- 같은 Lot의 최근 인접 Wafer
- 반복 equipment/defect 정보
- 검사 이전 30분의 process event 후보
- 기존 RAG 유사 사례와 engineer feedback

화면은 Current Incident, Root Cause Candidates, Similar Cases, Recommended Actions, Agent Trace 흐름을 표시한다. 최종 조치는 기존 Human Review와 approval/RAG feedback 계약을 따른다.

## Vision Evidence

- source: 외부 `wafer_particle` 분석 결과의 저장된 snapshot
- modes: Statistical, AI, Comparison
- 현재 React 화면에서 모델을 다시 실행하거나 재학습하지 않는다.
- `Inspection Agent에 전달`하면 전용 `vision_*` 필드로 신규 inspection을 생성한 뒤 AI Analysis로 이동한다.
- 임계값은 샘플 기준이며 실제 Fab control limit가 아니다.

## Lot / Data Quality / Chamber MLOps

- `lots`가 product, recipe route, status, start/end, current step, wafer count를 보존한다.
- telemetry는 `lot_id`, nullable `wafer_id`, `data_quality_status/issues`를 가진다.
- simulator는 wafer당 여러 sample을 만들고 wafer 완료 시에만 count를 증가시킨다.
- `VALID`만 prediction/training, `WARNING/REJECT`는 audit/event만 허용한다.
- detector threshold는 `equipment+recipe → equipment → recipe → global` 순서로 fallback한다.
- context threshold는 sparse/overfit group이 지나치게 민감해지지 않도록 global threshold의 75%보다 낮아지지 않는다.
- MAD primary와 EWMA secondary 결과는 `anomaly_detections`에 각각 저장한다.
- 평가 metadata는 overall과 equipment/recipe/lot group, data time range를 포함한다.
- readiness 기반 자동 학습은 Staging Candidate까지만 생성하고 Promotion은 수동이다.
- Fab Scenario Orchestrator가 generator와 inspection 사이의 wafer completion/lag를 연결한다.

Agent 최종 출력은 `Observation`, `Possible Causes`, `Evidence`, `Recommended Checks`, `Recommended Action`, `Confidence / Uncertainty` 섹션을 모두 가진다.

## 데이터/배포 호환성

- 표준 로컬/운영 DB: PostgreSQL (`STORAGE_BACKEND=postgres`, Docker Compose PostgreSQL 16)
- 명시적 unit test/lightweight demo DB: SQLite (`STORAGE_BACKEND=sqlite`)
- backend 설정/연결 실패 시 조용한 SQLite fallback은 하지 않는다.
- 기본 object storage: local `outputs/`
- 선택 object storage: S3 (`IMAGE_BACKEND=s3`)
- `process_events`는 기존 DB abstraction과 Data & RAG browser에 포함된다.

## 구현 경계

- 실제 detector/model은 Etch 외 공정에 구현하지 않는다.
- 실제 Fab spec, alarm limit, causal relation, 수율 개선을 주장하지 않는다.
- 실제 설비 stream/control은 연결하지 않는다.
- 기존 wafer MLOps는 workflow simulation이며 Chamber retrain은 별도 sklearn artifact lifecycle이다.

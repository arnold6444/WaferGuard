# WaferGuard Architecture Decision Record

> 이 문서는 시간순 결정 기록입니다. 과거 결정이 최신 구현과 충돌하는 경우 **Superseded** 표시와 최신 결정을 우선합니다. 현재 동작은 `README.md`, `docs/spec.md`, `PLAN.md`를 기준으로 확인합니다.

## 2026-07-18 — 샘플 Resistance 결과를 정적 JSON으로 제공

**Status: Historical / retained as Static Demo**

- 초기 MVP는 제공된 sample data를 빠르게 시각화하기 위해 결과 JSON을 빌드 시 포함했다.
- 원본 CSV 전체는 저장소에 복제하지 않고 집계/예측 결과만 포함한다.
- `scripts/build_chamber_dashboard_data.py`로 재생성할 수 있다.

현재 이 경로는 `Resistance > Static Demo`로 보존되어 있으며 Live Chamber ML의 현재 구조가 아니다.

## 2026-07-18 — Demo와 실제 운영의 경계를 명시

**Status: Active**

- synthetic/sample/proxy 결과를 실제 Fab 성능으로 표현하지 않는다.
- sample threshold/range/coefficient를 실제 control limit로 표현하지 않는다.
- process event와 defect의 시간적 연관을 실제 원인으로 단정하지 않는다.

## 2026-08-02 — Vision 결과를 snapshot evidence로 통합

**Status: Active, product placement changed**

- WaferGuard는 `wafer_particle` 분석 서버 전체를 복제하지 않는다.
- 분석 결과 JSON과 대표 image/heatmap snapshot만 포함한다.
- 통계/AI 결과는 전용 `vision_*` evidence로 Inspection Agent에 전달한다.
- sample threshold는 실제 Fab control limit가 아니다.

초기에는 Chamber AI 하위 탭에 있었지만, 현재 제품 구조에서는 Wafer Quality / Wafer Detail의 Vision Evidence로 배치된다.

## 2026-08-10 — Resistance를 Live + Static Demo 이중 구조로 확장

**Status: Active**

- 기존 `USE_TIME → RESISTANCE` offline 분석은 Static Demo로 보존한다.
- Live 모드는 `configs/chamber.yaml`을 기반으로 stateful synthetic Etch telemetry를 생성한다.
- browser가 ML을 수행하지 않고 Python runtime이 DB에 telemetry/prediction/detection을 적재하고 React가 API를 읽는다.

## 2026-08-10 — Chamber sklearn lifecycle을 generic wafer MLOps simulation과 분리

**Status: Active**

- 기존 `model_registry`, `drift_events`, `retraining_jobs`는 generic wafer MLOps workflow simulation을 유지한다.
- Chamber는 별도 `chamber_model_registry`와 `runtime/models/chamber/*.joblib` artifact를 사용한다.
- Chamber 학습은 실제 `sklearn.pipeline.Pipeline.fit()`을 실행한다.
- 최초 bootstrap은 Production을 생성할 수 있다.
- 이후 candidate는 Staging으로 등록한다.
- Production promotion은 사람의 명시적 호출만 허용한다.

## 2026-08-10 — Chamber 학습/검증 방식

**Status: Active**

- 학습 대상은 `running + quality=good + Data Quality VALID` clean rows다.
- 현재 모델은 여러 Etch process feature로 `resistance`를 예측하는 `GradientBoostingRegressor`다.
- time-based past→future holdout으로 MAE/RMSE를 계산한다.
- 같은 holdout에서 USE_TIME-only baseline과 candidate를 비교한다.
- equipment/recipe/gas categorical value는 unknown-safe OneHotEncoder로 처리한다.
- artifact에는 preprocessing과 regressor를 함께 저장하고 registry 등록 전에 reload 검증한다.
- residual threshold는 robust MAD 기반이다.

현재 구조는 별도 offline Train/Validation/Test dataset pipeline이나 generic tag-level anomaly framework가 아니다.

## 2026-08-10 — DB backend / 저장 위치

**Status: Superseded by 2026-08-11 PostgreSQL runtime decision**

당시에는 SQLite local 기본 경로와 PostgreSQL/RDS 전환을 설명했다. 최신 구현에서는 다음 결정을 우선한다.

- 표준 local/runtime backend: PostgreSQL 16
- SQLite: 명시적 unit test/lightweight demo
- 설정 누락/연결 실패 시 SQLite 자동 fallback 금지
- workflow tables와 Chamber tables는 동일하게 선택된 backend를 사용
- Chamber artifact는 `runtime/models/chamber/`
- image/report object는 local `outputs/` 또는 S3

현재 schema 기준 workflow 계층은 Lot/Inspection/RAG/Agent 관련 11개 table, Chamber 계층은 4개 table이다.

## 2026-08-10 — Chamber retraining 자동화 경계

**Status: Superseded by 2026-08-11 automatic Staging Candidate decision**

초기 Live 구현에서는 readiness 계산 후 API/UI의 명시적 retrain trigger가 필요했다.

현재는:

- runtime이 recent residual/new clean rows/time interval로 readiness를 계산한다.
- `run_chamber_stream.py`가 configured sample interval마다 `maybe_auto_retrain()`을 확인할 수 있다.
- readiness가 만족되면 automatic Staging Candidate를 생성할 수 있다.
- 이미 Staging Candidate가 있으면 추가 automatic candidate를 만들지 않는다.
- Production promotion은 여전히 자동으로 하지 않는다.

기존 `/api/v1/automation/tick`은 generic 운영 workflow와 연결되며 Chamber stream의 candidate check와 동일한 기능으로 해석하지 않는다.

## 2026-08-10 — Simulator 단순화를 숨기지 않는다

**Status: Active, 일부 항목은 이후 개선됨**

- synthetic coefficient/range/feature importance는 실제 Fab calibration 값이 아니다.
- generator는 실제 plasma/etch physics simulator가 아니다.
- local stream runner는 production ingestion daemon이 아니다.

과거의 sample-driven wafer counter, 단순 lifecycle 등은 이후 25-wafer Lot/multi-sample wafer lifecycle 구현으로 개선되었다. 최신 동작은 `chamber_generator.py`를 기준으로 한다.

## 2026-08-11 — Chamber 중심 UI를 Fab Quality Ops 정보 구조로 개편

**Status: Active**

- 최상위 메뉴를 `Fab Overview · Process Monitoring · Wafer Quality · AI Analysis · MLOps · Data & RAG · Settings`로 구성한다.
- Chamber Resistance는 Process Monitoring / Etch의 connected 구현으로 재사용한다.
- Vision snapshot은 Wafer Detail의 proxy evidence/demo fallback으로 재사용한다.
- 기존 generic wafer MLOps와 Static Resistance는 Legacy/Demo로 표시한다.

## 2026-08-11 — Runtime DB 우선, 명시적 Demo fallback

**Status: Active**

- Lot/Wafer 화면은 runtime inspection DB 집계를 우선한다.
- runtime data가 없을 때만 명시적 Vision snapshot demo를 사용한다.
- Runtime DB / Synthetic runtime / Proxy runtime / Demo profile 출처를 구분한다.
- Not connected 공정을 실제 live 상태처럼 표현하지 않는다.

## 2026-08-11 — Process Event Projection

**Status: Active**

- `process_events`는 기존 Chamber 원본 tables를 대체하지 않는다.
- Chamber anomaly, DQ, lifecycle의 의미 있는 event를 공통 operational layer로 projection한다.
- 검사와 process event는 같은 Lot을 우선하고 검사 이전 시간창의 temporal candidate로 연결한다.
- 다른 Lot의 event는 evidence에서 배제한다.
- UI/Agent가 이를 causal root cause로 단정하지 않는다.

## 2026-08-11 — PostgreSQL / DQ / Detector / Scenario 운영 구조

**Status: Active**

- PostgreSQL 16을 표준 local development/runtime backend로 사용한다.
- SQLite는 명시적 test/demo backend다.
- Data Quality Gate는 strict 정책으로 `VALID`만 prediction/training에 전달한다.
- Generator와 Inspection은 직접 결합하지 않고 `FabScenarioOrchestrator`가 wafer completion과 inspection lag를 연결한다.
- Robust MAD를 primary, EWMA를 secondary residual detector로 사용한다.
- context threshold는 `equipment+recipe → equipment → recipe → global` 순서다.
- readiness 충족 시 Staging Candidate 자동 생성은 허용하지만 Production 자동 승격은 금지한다.

## 2026-08-11 — 현재 ML을 범용 시계열 anomaly framework로 과장하지 않는다

**Status: Active**

현재 구현은:

```text
fixed Etch feature schema
→ Expected Resistance regression
→ residual
→ MAD/EWMA
```

이다.

다음은 아직 구현된 기능이 아니다.

- arbitrary real-world tag mapping
- Environment Profile 기반 dynamic feature schema
- 별도 Offline Train/Validation/Test pipeline
- tag별 independent univariate detector
- arbitrary multivariate relationship detector
- relationship-break evaluation
- LSTM/TCN/Transformer anomaly model

따라서 문서/UI에서 위 기능을 현재 제공 기능처럼 표현하지 않는다.

## 2026-08-11 — 다음 우선순위: Pipeline portability 후 성능 개선

**Status: Planned**

새로운 설비에 적용할 때 특정 synthetic schema에 맞춘 model 성능만 먼저 높이지 않는다.

우선순위는:

```text
Environment Profile / Canonical Tag Mapping
→ Offline Train/Validation/Test
→ model/profile compatibility
→ Univariate + Multivariate baseline
→ 여러 Profile에서 재학습 검증
→ feature/threshold/model 성능 개선
→ 필요 시 Deep Learning
```

이다.

목표는 같은 model artifact를 모든 설비에 강제로 사용하는 것이 아니라 **새 환경의 profile과 정상 데이터를 사용해 동일 Training Pipeline으로 해당 환경용 모델을 다시 만들 수 있게 하는 것**이다.

상세 로드맵은 `docs/training_realworld_roadmap.md`에 기록한다.

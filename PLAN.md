# Goal

WaferGuard의 기존 정적 `USE_TIME -> RESISTANCE` 분석을 유지하면서, 재현 가능한 다변량 Etch telemetry를 생성하고 DB 저장, 실제 sklearn 학습, Production 추론, residual 이상탐지, Staging/Production 승격, Live dashboard까지 하나의 로컬 흐름으로 연결한다.

# Scope

## 구현 완료된 MVP

- recipe setpoint, actual 값, 장비 bias, cleaning/seasoning, machine state를 보존하는 stateful synthetic generator
- telemetry, prediction, Chamber model registry 전용 DB 테이블과 조회 함수
- numeric/categorical 전처리와 `GradientBoostingRegressor`를 묶은 실제 sklearn pipeline 및 joblib artifact
- time-based holdout, MAE/RMSE, USE_TIME-only baseline 비교, Production 동일 holdout 비교
- warm-up bootstrap, Production inference, clean-data retraining readiness, Staging 등록, 명시적 promote
- Chamber status/equipment/telemetry/predictions/models/retrain/promote API
- 기존 정적 화면을 보존하는 Live/Static Demo dashboard
- generator, cleaning, machine-state, gas anomaly, model lifecycle 회귀 테스트와 smoke 검증
- README/spec/progress/decision 문서를 실제 구현과 일치하도록 갱신

## 현재 제외 또는 후속 보완

- 실제 Fab 물리 계수 또는 실제 장비 정확도 주장
- 실제 설비/메시지 버스 연결
- 실제 Fab control limit와 자동 장비 제어
- 자동 Production 승격
- Chamber readiness를 주기적으로 검사해 `retrain()`을 자동 호출하는 scheduler
- 별도 DB migration framework와 신규 인증 인프라
- 기존 wafer model registry/MLOps simulation을 Chamber registry와 통합하는 작업

# Current State

- `main`은 FastAPI + React/Vite 구조이며 로컬 SQLite와 PostgreSQL adapter를 지원한다.
- Chamber Resistance는 **Live + Static Demo** 두 모드다.
- Live 모드는 `configs/chamber.yaml` 기반 stateful Etch simulator에서 pressure/RF/gas/temperature/use/cleaning telemetry를 생성한다.
- Live telemetry는 `chamber_telemetry`에 저장되고, 실제 sklearn Pipeline을 학습해 `runtime/models/chamber/resistance-vN.joblib` artifact를 만든다.
- Production 모델 추론 결과는 Actual/Expected/residual/anomaly/model version과 함께 `chamber_predictions`에 저장한다.
- Chamber model stage와 성능/threshold/artifact metadata는 `chamber_model_registry`에 저장한다.
- 기존 inspection/RAG/wafer MLOps 9개 테이블과 Chamber 3개 테이블은 같은 runtime DB backend를 사용한다.
- 기본 로컬 DB는 `outputs/waferguard.db` SQLite이며, `STORAGE_BACKEND=postgres`에서 PostgreSQL(RDS)로 전환한다.
- Python requirements에는 pandas, scikit-learn, joblib, PyYAML이 포함되어 있다.
- 기존 wafer retraining/promote/rollback은 workflow simulation이고 Chamber retraining은 별도 실제 sklearn 학습 경로다.

# Decisions

- 기존 wafer API와 registry는 그대로 두고 Chamber 코드는 `chamber_*` 모듈과 테이블로 분리한다.
- gas는 고정 3채널(name/setpoint/actual)로 저장하고 학습 시 flow/delta/ratio feature로 변환한다.
- `running`, `quality=good`, model non-anomaly 데이터를 기본 학습 후보로 사용한다. synthetic anomaly ground truth 필터는 synthetic row에만 적용한다.
- 학습과 추론은 같은 feature builder와 저장된 sklearn pipeline을 사용한다. unknown recipe/equipment/gas는 `OneHotEncoder(handle_unknown="ignore")`로 처리한다.
- 최초 bootstrap만 Production으로 등록한다. 이후 retraining 결과는 Staging으로 등록하고 명시적으로 promote한다.
- readiness는 최근 prediction의 median absolute error, Production 이후 신규 clean row, 경과 시간을 함께 본다. `force=true`는 로컬 demo용 override다.
- readiness 계산은 구현되어 있지만 이를 주기적으로 자동 호출하는 Chamber scheduler는 아직 없다.
- promotion은 artifact 존재/로드를 확인한 뒤 현재 Production을 Archived로 전환하고 선택 버전 하나만 Production으로 만든다.
- SQLite/PostgreSQL 공통성을 위해 Chamber row ID는 UUID text를 사용하고 SQL column 목록을 명시한다.

# Architecture / Flow

```text
EtchTelemetryGenerator
  -> chamber_telemetry INSERT
  -> Production model 없음: clean warm-up 축적 -> bootstrap fit -> Production
  -> Production model 있음: shared feature transform -> predict
  -> expected/residual/score/anomaly/model version -> chamber_predictions INSERT
  -> recent residual + new clean rows -> retraining readiness
  -> manual API/UI trigger 또는 force retrain -> time holdout candidate fit + Production 비교 -> Staging
  -> explicit promote -> previous Production Archived -> selected artifact Production
```

# Implementation Steps

1. `configs/chamber.yaml`, `app/services/chamber_generator.py`
   - recipe, state transition, cleaning, correlated setpoint/actual values, 8종 anomaly 구현
   - seed 재현성과 시계열 연속성 테스트
2. `app/services/storage.py`, `app/services/chamber_storage.py`
   - Chamber 3개 테이블/index, 명시적 insert/query, DB browser allowlist 구현
3. `app/services/chamber_training.py`, `app/services/chamber_runtime.py`
   - shared feature builder, pipeline fit/load, holdout 비교, bootstrap/inference/readiness/retrain/promote 구현
4. `app/services/schemas.py`, `app/main.py`, `scripts/run_chamber_stream.py`
   - Chamber API와 로컬 stream CLI 연결
5. `frontend/src/ChamberView.jsx`, `frontend/src/styles.css`
   - Live polling, Actual/Expected/anomaly, parameter deviation, model/version/importance UI 추가
6. `README.md`, `docs/spec.md`, `docs/progress.md`, `docs/decisions.md`, `docs/implementation-plan.md`
   - 최신 구현과 한계, 저장 구조, 실행 방법 반영
7. `tests/test_chamber.py`
   - generator/model/DB/lifecycle 테스트 후 compile, pytest, build, API smoke 검증

# Definition of Done

- 여러 장비의 multivariate telemetry가 DB에 쌓인다.
- clean 이후 since-clean counter가 reset되고 비-running row가 학습/추론에서 제외된다.
- 실제 `.fit()` 결과와 pipeline artifact가 생성되고 multivariate holdout 성능이 USE_TIME baseline보다 낫다.
- Production 추론 결과에 actual/expected/residual/anomaly/model version이 저장된다.
- readiness gate, Staging retrain, artifact 검증, 명시적 promote가 동작한다.
- Live dashboard가 API 데이터를 표시하고 Static Demo는 기존 화면을 유지한다.
- 기존 FastAPI compile, Chamber tests, frontend production build가 통과한다.

# Known Simplifications

- `wafer_count_since_clean`은 현재 simulator에서 running sample마다 증가하는 demo counter이며 실제 wafer cycle 완료 이벤트와 분리되어 있지 않다.
- 여러 equipment sample은 하나의 generator clock을 순차 사용하므로 동일 round의 timestamp가 완전히 동일하지 않다.
- `run_chamber_stream.py`는 기본적으로 유한 sample 수를 생성하며 항상 켜져 있는 production stream daemon은 아니다.
- Chamber retraining readiness는 계산하지만 자동 scheduler가 아직 직접 `retrain()`을 실행하지 않는다.

# Verification

```bash
python -m compileall -q app scripts tests
python -m pytest tests/test_chamber.py -q
python scripts/run_chamber_stream.py --equipment-count 2 --interval 0 --samples 150
cd frontend
npm run build
```

# Progress

- [x] 최신 `main` 기준 저장소 분석 및 범위/결정 확정
- [x] Backend 구현
- [x] Dashboard 구현
- [x] Chamber 실제 sklearn lifecycle 구현
- [x] 문서 동기화
- [x] 검증 및 main 병합

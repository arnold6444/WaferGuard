# Goal

WaferGuard의 기존 정적 `USE_TIME -> RESISTANCE` 분석을 유지하면서, 재현 가능한 다변량 Etch telemetry를 실시간 생성하고 DB 저장, 실제 sklearn 학습, Production 추론, residual 이상탐지, Staging/Production 승격, Live dashboard까지 하나의 로컬 흐름으로 연결한다.

# Scope

## MVP

- recipe setpoint, actual 값, 장비 bias, cleaning/seasoning, machine state를 보존하는 stateful synthetic generator
- telemetry, prediction, Chamber model registry 전용 DB 테이블과 조회 함수
- numeric/categorical 전처리와 `GradientBoostingRegressor`를 묶은 실제 sklearn pipeline 및 joblib artifact
- time-based holdout, MAE/RMSE, training residual threshold, feature importance
- warm-up bootstrap, Production inference, clean-data retraining readiness, Staging 등록, 명시적 promote
- Chamber status/equipment/telemetry/predictions/models/retrain/promote API
- 기존 정적 화면을 보존하는 Live/Static Demo dashboard
- generator, cleaning, machine-state, gas anomaly, model lifecycle 회귀 테스트와 smoke 검증
- README/spec/progress를 실제 구현과 일치하도록 갱신

## 제외

- 실제 Fab 물리 계수 또는 실제 장비 정확도 주장
- 기존 wafer model registry/MLOps 동작 변경
- 자동 Production 승격
- 외부 message bus, 별도 DB migration framework, 신규 인증/배포 인프라

# Current State

- `main`은 FastAPI + React/Vite 구조이며 로컬 SQLite와 PostgreSQL adapter를 지원한다.
- Chamber 화면은 `frontend/src/data/chamberSample.json`을 읽는 정적 offline 분석이다.
- 기존 DB에는 inspection/wafer MLOps 테이블만 있고 Chamber telemetry/prediction/model 테이블이 없다.
- 기존 wafer retraining은 demo lifecycle이므로 Chamber 실제 학습과 분리해야 한다.
- Python requirements에는 pandas, scikit-learn, joblib, PyYAML이 아직 없다.

# Decisions

- 기존 wafer API와 registry는 그대로 두고 Chamber 코드는 `chamber_*` 모듈과 테이블로 분리한다.
- gas는 고정 3채널(name/setpoint/actual)로 저장하고 학습 시 flow/delta/ratio feature로 변환한다.
- `running`, `quality=good`, model non-anomaly 데이터를 기본 학습 후보로 사용한다. synthetic anomaly ground truth 필터는 synthetic row에만 적용한다.
- 학습과 추론은 같은 feature builder와 저장된 sklearn pipeline을 사용한다. unknown recipe/equipment/gas는 `OneHotEncoder(handle_unknown="ignore")`로 처리한다.
- 최초 bootstrap만 검증된 candidate를 Production으로 등록한다. 이후 retraining 결과는 Staging으로만 등록한다.
- readiness는 최근 prediction 최소 개수의 median absolute error와 Production 이후 축적된 신규 clean row를 함께 본다. 수동 demo는 `force=true`로 우회할 수 있다.
- promotion은 artifact 존재를 확인한 뒤 현재 Production을 Archived로 전환하고 선택 버전 하나만 Production으로 만든다.
- SQLite/PostgreSQL 공통성을 위해 Chamber row ID는 UUID text를 사용하고 SQL column 목록을 명시한다.

# Architecture / Flow

```text
EtchTelemetryGenerator
  -> chamber_telemetry INSERT
  -> Production model 없음: clean warm-up 축적 -> bootstrap fit -> Production
  -> Production model 있음: shared feature transform -> predict
  -> expected/residual/score/anomaly/model version -> chamber_predictions INSERT
  -> recent residual + new clean rows -> retraining readiness
  -> forced/ready retrain -> time holdout candidate fit + Production 비교 -> Staging
  -> explicit promote -> previous Production Archived -> selected artifact Production
```

# Implementation Steps

1. `configs/chamber.yaml`, `app/services/chamber_generator.py`
   - recipe, state transition, cleaning, correlated setpoint/actual values, 8종 anomaly 구현
   - seed 재현성과 시계열 연속성 테스트
2. `app/services/storage.py`, `app/services/chamber_storage.py`
   - 3개 테이블/index, 명시적 insert/query, DB browser allowlist 구현
3. `app/services/chamber_training.py`, `app/services/chamber_runtime.py`
   - shared feature builder, pipeline fit/load, holdout 비교, bootstrap/inference/readiness/retrain/promote 구현
4. `app/services/schemas.py`, `app/main.py`, `scripts/run_chamber_stream.py`
   - Chamber API와 로컬 stream CLI 연결
5. `frontend/src/ChamberView.jsx`, `frontend/src/styles.css`
   - Live polling, Actual/Expected/anomaly, parameter deviation, model/version/importance UI 추가
6. `README.md`, `docs/spec.md`, `docs/progress.md`
   - synthetic 한계와 실제 실행 방법 반영
7. `tests/test_chamber.py`
   - generator/model/DB/lifecycle 테스트 후 compile, pytest, build, API smoke 검증

# Definition of Done

- 여러 장비의 연속적인 multivariate telemetry가 DB에 쌓인다.
- clean 이후 since-clean counter가 reset되고 비-running row가 학습/추론에서 제외된다.
- 실제 `.fit()` 결과와 pipeline artifact가 생성되고 multivariate holdout 성능이 USE_TIME baseline보다 낫다.
- Production 추론 결과에 actual/expected/residual/anomaly/model version이 저장된다.
- readiness gate, Staging retrain, artifact 검증, 명시적 promote가 동작한다.
- Live dashboard가 API 데이터를 표시하고 Static Demo는 기존 화면을 유지한다.
- 기존 FastAPI compile, Chamber tests, frontend production build가 통과한다.

# Verification

```bash
python -m compileall -q app scripts tests
python -m pytest tests/test_chamber.py -q
python scripts/run_chamber_stream.py --equipment-count 2 --interval 0 --samples 150
npm run build
git diff --check
```

# Progress

- [x] 최신 `main` 기준 저장소 분석 및 범위/결정 확정
- [x] Backend 구현
- [x] Dashboard/문서 구현
- [x] 전체 검증
- [x] 최종 검토 및 로컬 커밋

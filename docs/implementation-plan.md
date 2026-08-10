# Chamber Resistance AI 구현 계획

이 문서는 초기 구현 계획과 현재 완료 상태를 함께 기록합니다. 현재 동작 사양은 `docs/spec.md`, 전체 시스템 설명은 `README.md`를 기준으로 봅니다.

## 2026-07-18 — Static Resistance MVP

1. Colab 코드와 `sample.csv` 구조·결과를 확인한다.
2. Colab의 전처리·OOF·MAD·정상 재학습·최종 판정을 재현한다.
3. 브라우저에서 바로 읽을 수 있는 샘플 분석 JSON을 만든다.
4. 기존 React/Vite 대시보드에 Chamber AI 화면과 메뉴를 추가한다.
5. 정상 패턴, EQP 추세, 이상 맵, 순위, 판정표를 Recharts로 구현한다.
6. 빌드 후 브라우저에서 렌더링과 EQP 선택 동작을 확인한다.

상태: **완료. 현재 `Resistance > Static Demo`로 유지됨.**

## 2026-08-02 — Wafer Vision AI 병합

1. `wafer_particle` 로컬 API의 통계·AI·비교 결과와 산출물 라우트를 확인한다.
2. 100개 챔버 요약, 상위 3개 상세, 30일 추세와 대표 산출물을 정적 페이로드로 생성한다.
3. 기존 `ChamberView` 안에 저항·영상 하위 탭을 구성해 기존 기능을 유지한다.
4. 통계·AI·비교 선택, 챔버 상태판, 순위, 추세, 영상 근거를 구현한다.
5. 영상 근거 필드와 판정 룰을 FastAPI 검사 파이프라인에 연결한다.
6. 빌드·Python 룰 테스트·실제 브라우저·Inspection Agent 전달을 확인한다.

상태: **완료. Vision은 현재도 외부 분석 결과 snapshot을 표시하는 구조임.**

## 2026-08-10 — Multivariate Chamber Resistance Live/MLOps

### 1. Stateful Etch simulator

- `configs/chamber.yaml`에 recipe, pressure/RF/temperature/gas setpoint, cleaning/model 설정을 정의한다.
- `app/services/chamber_generator.py`에서 equipment별 bias, previous value, slow drift, noise, clean/seasoning state를 보존한다.
- `idle`, `running`, `cleaning`, `maintenance`, `alarm`을 지원한다.
- resistance/process anomaly 8종을 주입할 수 있게 한다.

상태: **완료.**

### 2. Chamber persistence

- 기존 `app/services/db.py` backend abstraction을 그대로 사용한다.
- `chamber_telemetry`, `chamber_predictions`, `chamber_model_registry`를 추가한다.
- 로컬은 SQLite `outputs/waferguard.db`, 운영은 `STORAGE_BACKEND=postgres`에서 PostgreSQL/RDS를 사용한다.
- 기존 workflow 9개 table과 Chamber 3개 table은 같은 DB backend 안에서 관리한다.

상태: **완료.**

### 3. 실제 sklearn training/inference

- numeric/categorical shared feature builder를 만든다.
- `ColumnTransformer + OneHotEncoder(handle_unknown="ignore") + GradientBoostingRegressor(loss="huber")` Pipeline을 실제 `.fit()`한다.
- 과거→미래 time holdout MAE/RMSE를 계산한다.
- 동일 holdout에 `USE_TIME` only baseline을 별도 학습해 multivariate candidate와 비교한다.
- pipeline + preprocessing을 `runtime/models/chamber/resistance-vN.joblib`로 저장하고 다시 load해 검증한다.

상태: **완료.**

### 4. Runtime lifecycle

- Production이 없으면 clean running row를 warm-up으로 모은다.
- bootstrap threshold 도달 시 최초 Production model을 실제 학습한다.
- 이후 각 running sample에 Expected Resistance를 예측하고 Actual과의 residual/abs error/anomaly를 저장한다.
- 신규 clean row, 최근 median abs error, 시간 조건으로 retraining readiness를 계산한다.
- retrain candidate가 Production 비교를 통과하면 Staging으로 등록한다.
- explicit promote 시 기존 Production은 Archived, 선택 candidate는 Production으로 바꾼다.

상태: **완료. 단, readiness를 주기적으로 검사해 retrain을 자동 호출하는 Chamber scheduler는 아직 없음.**

### 5. API / Dashboard

- `GET /api/v1/chamber/status`
- `GET /api/v1/chamber/equipment`
- `GET /api/v1/chamber/telemetry`
- `GET /api/v1/chamber/predictions`
- `GET /api/v1/chamber/models`
- `POST /api/v1/chamber/retrain`
- `POST /api/v1/chamber/models/{version}/promote`
- React에서 2초 polling으로 Actual/Expected, anomaly, process deviation, model lifecycle을 표시한다.
- 기존 offline Resistance는 `Static Demo`로 유지한다.

상태: **완료.**

### 6. 검증

- generator reproducibility / continuity
- cleaning reset / state 지원
- gas drift가 setpoint가 아니라 actual을 움직이는지
- multivariate candidate가 USE_TIME-only baseline보다 나은지
- unknown category inference
- bootstrap Production 생성
- RF drift anomaly residual 증가
- retrain → Staging → promote → Archived/Production 전환
- missing artifact promotion guard
- smoke test / compileall / Vite build

상태: **완료.**

## 후속 개선 후보

1. `wafer_count_since_clean`을 telemetry sample count가 아니라 실제 wafer/cycle 완료 이벤트와 분리한다.
2. 여러 equipment의 동일 round timestamp를 같은 clock tick으로 묶는다.
3. 무한/daemon 형태의 production-like stream runner를 별도로 둔다.
4. Chamber readiness를 주기적으로 검사하고 조건 만족 시 자동으로 candidate retrain을 호출하는 scheduler를 추가한다.
5. 실제 설비 ingest adapter와 Fab calibration profile을 별도 계층으로 추가한다.
6. 실제 운영 환경에서는 synthetic ground truth 없이 data quality/anomaly quarantine 정책으로 training set을 구성한다.

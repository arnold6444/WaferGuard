# Chamber Resistance AI 결정 기록

## 2026-07-18 — 샘플 결과를 정적 JSON으로 제공

> Historical decision. 이 결정은 현재 `Static Demo`에 그대로 유지되며, 이후 2026-08-10 Live 구현이 추가되었습니다.

- 초기 MVP는 사용자가 제공한 샘플 데이터를 보여주는 목적이므로 모델 결과를 빌드 시 포함되는 JSON으로 생성했다.
- 원본 CSV는 저장소에 복사하지 않고 집계·예측 결과만 포함한다.
- 새 데이터로 교체할 때는 `scripts/build_chamber_dashboard_data.py`로 JSON을 다시 만든다.

## 2026-07-18 — 샘플과 실제 운영의 경계 표시

- 화면 제목과 설명에 sample/demo 성격을 표시한다.
- 결과가 실제 팹 성능이나 수율 개선 효과를 의미하지 않는다는 경계를 명시한다.

## 2026-08-02 — 저항 분석을 유지하고 영상 분석을 하위 탭으로 병합

- 기존 `Chamber AI` 메뉴와 저항 분석을 교체하지 않는다.
- Resistance와 Vision을 같은 Chamber AI 영역에서 제공한다.
- Vision은 `wafer_particle` 분석 결과 스냅샷을 시각화하고 Inspection Agent로 evidence를 전달한다.

## 2026-08-02 — 재생성 가능한 Vision 결과 스냅샷을 포함

- WaferGuard는 `wafer_particle` 서버 코드 전체를 복제하지 않고, 실제 비교 분석 API 결과에서 생성한 JSON과 대표 산출물을 포함한다.
- `scripts/build_wafer_vision_dashboard_data.py`로 새 로컬 분석 결과를 다시 가져올 수 있다.
- 원본 3,000장을 중복 저장하지 않고 대표 원본·히트맵과 누적 결과만 포함한다.

## 2026-08-02 — 영상 근거를 기존 Agent 스키마에 명시적으로 추가

- 통계·AI 점수, 시점, 방향, 이상 면적을 임의 `defect_count`로 환산하지 않고 전용 `vision_*` 필드에 저장한다.
- Action Card는 Vision rule hit를 별도 evidence로 사용한다.
- Vision 임계값은 샘플 기준이며 실제 Fab control limit로 해석하지 않는다.

## 2026-08-10 — Resistance를 Live + Static Demo 이중 구조로 확장

- 기존 `USE_TIME -> RESISTANCE` offline 분석은 삭제하지 않고 `Static Demo`로 보존한다.
- 새 `Live` 모드는 `configs/chamber.yaml` recipe/setpoint를 기준으로 stateful synthetic Etch telemetry를 생성한다.
- Live는 browser 내부 계산이 아니라 Python stream/runtime가 DB에 telemetry/prediction을 적재하고 React가 FastAPI API를 polling하는 구조로 한다.

## 2026-08-10 — Chamber 실제 sklearn lifecycle을 기존 wafer MLOps simulation과 분리

- 기존 `model_registry`, `drift_events`, `retraining_jobs`는 wafer MLOps workflow simulation을 유지한다.
- Chamber는 별도 `chamber_model_registry`와 `runtime/models/chamber/resistance-vN.joblib` artifact를 사용한다.
- Chamber 학습은 실제 `sklearn.pipeline.Pipeline.fit()`을 실행한다.
- 최초 bootstrap은 Production을 만들고 이후 candidate는 Staging으로 등록한 뒤 명시적으로 promote한다.
- 자동 Production 승격은 하지 않는다.

## 2026-08-10 — Live 학습/검증 규칙

- 학습은 `running`, `quality=good` row를 기본 대상으로 한다.
- synthetic anomaly ground truth와 기존 Production anomaly 판정을 활용해 오염 가능성이 높은 row를 제외한다.
- time-based past→future holdout으로 MAE/RMSE를 계산한다.
- 같은 holdout에 대해 `USE_TIME` only baseline과 multivariate candidate를 비교한다.
- unknown equipment/recipe/gas category는 `OneHotEncoder(handle_unknown="ignore")`로 처리한다.
- artifact는 preprocessing과 regressor를 한 번에 joblib로 저장하고 등록 전에 다시 load해 검증한다.

## 2026-08-10 — DB backend와 저장 위치

- 로컬 기본 DB는 SQLite `outputs/waferguard.db`다.
- `STORAGE_BACKEND=postgres`에서는 같은 logical tables를 PostgreSQL/RDS에서 사용한다.
- 기존 workflow 9개 테이블과 Chamber 3개 테이블은 같은 DB backend 안에서 분리된 table로 관리한다.
- 이미지/리포트는 local `outputs/` 또는 S3 object storage를 사용한다.
- Chamber sklearn artifact는 `runtime/models/chamber/`에 저장한다.

## 2026-08-10 — 현재 자동화 경계

- Chamber runtime은 최근 residual, 신규 clean row, 경과 시간을 사용해 retraining readiness를 계산한다.
- 실제 retrain은 `/api/v1/chamber/retrain` 또는 UI action이 호출해야 시작된다.
- 기존 `/api/v1/automation/tick`과 AWS Lambda/EventBridge automation은 wafer/운영 workflow용이며 Chamber retraining scheduler와 동일한 기능이 아니다.
- 따라서 현재 Chamber는 **readiness 자동 계산 + 명시적 retrain trigger + 명시적 promote** 구조다.

## 2026-08-10 — Simulator 단순화는 문서에서 숨기지 않는다

- synthetic coefficient/range/feature importance는 실제 Fab calibration 값이 아니다.
- 현재 `wafer_count_since_clean`은 running telemetry sample마다 증가하는 demo counter다.
- 여러 equipment sample은 하나의 generator clock을 순차 사용한다.
- `run_chamber_stream.py`는 유한 sample/시간 실행용 local simulator이며 production daemon이 아니다.

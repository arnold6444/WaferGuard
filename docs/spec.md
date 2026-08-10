# Chamber AI 통합 사양

> 이 문서는 **Chamber Resistance AI + Wafer Vision AI 기능 범위**를 설명합니다. WaferGuard 전체 시스템 사양은 `README.md`를 기준으로 봅니다.

## 현재 구현 방식

Chamber AI는 하나의 메뉴에서 Resistance와 Vision을 제공하며, Resistance 안에는 Live/Static Demo 모드가 있습니다.

1. **Resistance Live**: stateful simulator → Chamber DB → 실제 sklearn Production model → residual/anomaly DB → 2초 polling dashboard 흐름입니다.
2. **Resistance Static Demo**: 기존 CSV offline 분석 결과인 `chamberSample.json`을 그대로 시각화합니다.
3. **Vision AI**: 별도 `wafer_particle` 분석 API에서 미리 생성한 `waferVisionSample.json`과 대표 이미지 스냅샷을 시각화합니다.

브라우저 요청마다 모델을 다시 학습하지 않습니다. 학습은 stream warm-up 또는 명시적 Chamber retrain 요청에서만 실행됩니다.

## Resistance Live MVP

### Simulator

- `EtchTelemetryGenerator`가 장비별 이전 값, recipe, use time, clean counter, seasoning, slow drift와 noise를 보존합니다.
- `idle`, `running`, `cleaning`, `maintenance`, `alarm`을 지원하며 학습/추론은 `running` row만 사용합니다.
- recipe는 pressure/source RF/bias RF/temperature와 서로 다른 3개 gas channel setpoint를 가집니다.
- actual은 `setpoint + equipment bias + slow drift + short noise + optional anomaly`로 생성됩니다.
- cleaning은 `use_time_since_clean`, `wafer_count_since_clean`, seasoning을 reset하지만 `use_time_total`은 유지합니다.
- `resistance_spike`, `resistance_drift`, `pressure_drift`, `rf_power_drift`, `gas_flow_drift`, `temperature_drift`, `stuck_sensor`, `step_change`를 지원합니다.

모든 range와 synthetic coefficient는 실제 Fab에서 측정된 값이나 물리 계수가 아닙니다. 다변량 prediction/residual pipeline 검증용입니다.

### Model과 validation

- target: `resistance`
- numeric: use/clean history, pressure/RF/gas/temperature actual 및 setpoint delta
- categorical: `equipment_id`, `recipe_id`, gas name
- pipeline: `ColumnTransformer` + unknown-safe `OneHotEncoder` + `GradientBoostingRegressor(loss="huber")`
- validation: 과거 train → 미래 holdout 순서를 지키는 time-based 80/20 split
- metric: 실제 MAE/RMSE, 같은 holdout의 USE_TIME-only baseline, 현재 Production 비교
- threshold: train과 future holdout residual 각각의 robust median/MAD 기준 중 큰 값
- artifact: preprocessing과 model을 함께 `runtime/models/chamber/resistance-vN.joblib`에 저장

### DB와 lifecycle

- `chamber_telemetry`: recipe/state/setpoint/actual/clean history/Resistance/quality/synthetic ground truth
- `chamber_predictions`: model version, Actual/Expected, residual, abs error, anomaly score/threshold/result
- `chamber_model_registry`: artifact, MAE/RMSE, threshold, feature importance, holdout metadata, Staging/Production/Archived
- Production 부재 시 clean running row가 `bootstrap_min_rows`에 도달하면 실제 `.fit()` 후 v1을 Production으로 등록합니다.
- retraining candidate는 good/running, synthetic non-anomaly, Production non-anomaly row만 사용합니다.
- readiness는 충분한 신규 clean row와 최근 최소 prediction의 median absolute error 또는 시간 조건을 함께 확인합니다. `force=true`는 로컬 demo용 readiness override이며 성능 비교는 생략하지 않습니다.
- candidate가 같은 미래 holdout의 Production 비교를 통과해야 Staging으로 등록됩니다.
- promotion은 artifact를 실제 load/검증한 뒤 기존 Production을 Archived로 바꾸고 선택 버전만 Production으로 만듭니다.

### API와 화면

- `GET /api/v1/chamber/status`, `/equipment`, `/telemetry`, `/predictions`, `/models`
- `POST /api/v1/chamber/retrain`
- `POST /api/v1/chamber/models/{version}/promote`
- Live 화면은 선택 장비의 Actual/Expected/anomaly, 같은 시점의 pressure/RF/gas/temperature delta, model version과 synthetic-data feature importance를 표시합니다.

### Static Demo 유지

기존 `DATE`, `EQP`, `USE_TIME`, `RESISTANCE` 3,000행 분석, EQP 그룹 OOF, MAD 기반 EQP10/EQP55 결과는 `Static Demo`에 유지됩니다.

```bash
python scripts/build_chamber_dashboard_data.py --input "/path/to/sample.csv" --output "frontend/src/data/chamberSample.json"
```

## Wafer Vision AI 병합

- 기존 Resistance AI를 기본 탭으로 유지하고 같은 `Chamber AI` 안에 Vision AI 탭을 추가합니다.
- 화면에는 `statistical`, `ai`, `comparison` 세 표시 모드가 있습니다.
- 이 모드 전환은 **이미 생성된 통계/AI 결과를 어떤 기준으로 보여줄지 변경하는 UI 기능**이며, 현재 React 안에서 모델을 새로 실행하지 않습니다.
- 100개 챔버 상태판, 이상 순위, 최초 발생 시점·방향, 30일 점수 추세를 표시합니다.
- 선택 챔버의 원본, 통계 히트맵, AI 히트맵과 전체 누적 위치를 표시합니다.
- 현재 샘플 스냅샷은 3,000 images / 100 chambers / 300×300 px이며 CH-007, CH-047, CH-061을 이상 후보로 포함합니다.

### Inspection Agent 연동

Vision AI 화면에서 `Inspection Agent에 전달`을 실행하면 `POST /api/v1/inspect`에 다음 전용 필드를 전달합니다.

- `vision_source`
- `vision_stat_score`
- `vision_ai_score`
- `vision_direction`
- `vision_sequence_label`
- `vision_first_anomaly`
- `vision_anomaly_area_ratio`
- `operator_note`

영상 근거가 있는 경우 `defect_count`를 영상 점수에서 임의 환산하지 않습니다. 대신 `process_context.vision_evidence`에 별도 근거로 저장하고 Action Card의 Vision rule에서 사용합니다.

### 현재 Vision rule

- 통계 `>= 2.0` 그리고 AI `>= 50.0` → `Critical` rule hit
- 통계 `>= 2.0` 또는 AI `>= 50.0` 중 하나만 만족 → `Warning`
- 통계 `>= 1.2` 또는 AI `>= 35.0` → caution-level `Warning`

이 임계값은 과제 샘플을 위한 값이며 실제 Fab spec/control limit나 일반화 성능을 의미하지 않습니다.

### Vision 결과 재생성

`wafer_particle` 로컬 분석 API가 별도로 실행 중일 때 다음 스크립트가 comparison 결과와 대표 자산을 가져옵니다.

```bash
python scripts/build_wafer_vision_dashboard_data.py --base-url http://127.0.0.1:<port> --dataset-id <dataset-id>
```

WaferGuard 저장소에는 `wafer_particle` 서버 전체나 원본 3,000장을 복제하지 않고, 대시보드에 필요한 JSON과 대표 이미지 자산만 포함합니다.

## 기존 WaferGuard와 유지되는 연결

- 기존 Live Inspection 기능 유지
- 기존 Inspection Agent / RAG / Action Card 유지
- 기존 MLOps Agent / approval workflow 유지
- Vision evidence를 검사 건으로 등록한 뒤 기존 Inspection Agent 화면으로 이동
- `approved` 또는 `false_alarm`으로 확정된 엔지니어 review는 기존 RAG knowledge feedback 흐름에 포함 가능

## MVP 밖

- 브라우저에서 임의 CSV를 업로드해 Resistance 모델을 즉시 다시 학습하는 기능
- WaferGuard FastAPI 내부에서 `wafer_particle` 모델을 실시간 실행하는 기능
- 실제 장비 스트리밍 및 생산 알람 규칙 연결
- 영상만으로 파티클·물리 원인을 확정하는 기능
- `wafer_particle` 전체 서버를 WaferGuard 런타임에 복제하거나 자동 재학습하는 기능
- 실제 Fab control limit, 수율 개선 효과 또는 생산 적용 성능 주장

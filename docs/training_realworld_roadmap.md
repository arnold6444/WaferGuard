# WaferGuard Training & Real-World Portability Roadmap

이 문서는 **현재 `main`에 구현된 Chamber ML 구조**와, 새로운 Fab/설비로 가져가기 위해 필요한 다음 단계의 경계를 분리해서 기록한다.

## 1. 현재 구현 상태

현재 Etch Chamber Live ML은 범용 시계열 이상탐지 프레임워크가 아니라 다음 목적의 구체적인 파이프라인이다.

```text
Synthetic Etch telemetry
  → Data Quality Gate
  → VALID + running + quality=good row 축적
  → sklearn Expected Resistance model
  → Actual - Expected residual
  → Robust MAD primary detector + EWMA secondary detector
  → process_events / dashboard / MLOps lifecycle
```

### 현재 모델

- Target: `resistance`
- Model: `sklearn.pipeline.Pipeline` + `GradientBoostingRegressor(loss="huber")`
- Numeric features: USE_TIME, pressure/RF/gas/temperature 계열 값과 setpoint deviation 등
- Categorical features: `equipment_id`, `recipe_id`, gas name
- Unknown category: `OneHotEncoder(handle_unknown="ignore")`
- Validation: 시간순 holdout으로 MAE/RMSE 측정
- Threshold: training/holdout residual의 robust MAD 기반 threshold
- Context threshold fallback: `equipment+recipe → equipment → recipe → global`
- Artifact: `runtime/models/chamber/*.joblib`
- Lifecycle: bootstrap Production → readiness 확인 → automatic/manual Staging Candidate → manual Production promotion

### 현재 학습 데이터 공급 방식

현재는 별도의 offline `train/validation/test` dataset builder가 있는 구조가 아니다.

`run_chamber_stream.py` 또는 API runtime이 synthetic telemetry를 DB에 쌓고, 충분한 `VALID + running + quality=good` row가 모이면 bootstrap training을 수행한다. 이후 새 clean row와 residual drift/시간 조건으로 retraining readiness를 계산한다.

즉 현재 구조는 다음에 가깝다.

```text
Live/accelerated synthetic stream
  → DB clean rows
  → bootstrap/retrain
```

다음 구조는 아직 구현되지 않았다.

```text
Offline bulk dataset generation
  → Train / Validation / Test split
  → explicit training job
  → threshold selection on Validation
  → final Test evaluation
```

## 2. 현재 실환경 이식성의 한계

`configs/chamber.yaml`은 다음을 설정할 수 있다.

- equipment count / sampling interval / seed
- Lot/wafer lifecycle
- cleaning/seasoning
- Data Quality threshold와 physical range
- model warm-up/retrain 관련 parameter
- synthetic anomaly 강도
- recipe별 setpoint와 gas recipe

하지만 현재 Training code의 feature schema와 generator/runtime telemetry schema는 Python 코드에 구체적으로 정의되어 있다.

따라서 아직 다음 기능은 제공하지 않는다.

- 실제 source tag 이름 → canonical tag 이름 mapping
- 설비마다 다른 tag 개수/구성의 동적 feature schema
- tag unit/type/required/optional metadata를 이용한 onboarding
- 설비별 machine-state value mapping
- profile별 model/scaler/feature-schema compatibility validation
- 실제 외부 telemetry adapter를 profile만 바꿔 연결하는 범용 구조
- 새로운 환경의 정상 데이터를 이용한 독립 offline retraining workflow

즉 **현재 YAML은 Chamber simulator/model 설정 파일이지, 임의의 실제 설비를 코드 수정 없이 온보딩하는 범용 Environment Profile은 아니다.**

## 3. 목표 구조

새로운 Fab/설비에서는 코드 수정이 아니라 profile과 정상 데이터를 통해 새 모델을 만들 수 있어야 한다.

```text
Real machine / Replay / Synthetic source
              ↓
        Input Adapter
              ↓
     Environment Profile
              ↓
     Canonical Telemetry
              ↓
      Data Quality Gate
              ↓
        Window Builder
              ↓
   ┌──────────┴──────────┐
   ↓                     ↓
Univariate            Multivariate
Detector              Detector
   ↓                     ↓
   └──────────┬──────────┘
              ↓
       Anomaly Decision
              ↓
 process_events / Agent / UI
```

## 4. Environment Profile 제안

향후 `configs/profiles/<profile>.yaml` 형태로 환경 정의를 분리하는 것을 권장한다.

개념 예시는 다음과 같다.

```yaml
profile:
  id: fab-a-etch-01
  version: 1

device:
  process: etch
  equipment_type: chamber

sampling:
  interval_ms: 1000

tags:
  spindle_speed:
    source_tag: SPINDLE_RPM
    type: continuous
    unit: rpm
    required: true
    physical_range: [0, 20000]

  motor_current:
    source_tag: MOTOR_AMP
    type: continuous
    unit: A
    required: true

machine_state:
  source_tag: OPERATING_MODE
  mapping:
    AUTO_RUN: running
    READY: idle
    FAULT: alarm

training:
  window_seconds: 30
  state_conditioning: true

  univariate:
    enabled: true
    tags: [spindle_speed, motor_current]

  multivariate:
    enabled: true
    tags: [spindle_speed, motor_current]
```

실제 필드 이름은 기존 config convention을 유지하면서 설계한다.

## 5. Canonical Tag Mapping

실제 설비마다 tag 이름은 달라도 내부 detector는 canonical name을 사용한다.

```text
SPINDLE_RPM     ─┐
MAIN_SPINDLE    ─┼→ profile mapping → spindle_speed
SPEED_ACTUAL    ─┘
```

이 계층을 두면 source adapter와 detector가 직접 결합되지 않는다.

새 환경에서 tag가 빠져 있을 경우:

- `required: true`: profile validation 실패
- `required: false`: 해당 feature/detector에서 제외

으로 처리하는 것이 적절하다.

## 6. Offline Training / Validation Pipeline

실환경 확장 전에 학습과 실시간 추론을 분리한다.

### Training

```text
Historical or bulk-generated normal telemetry
  → Profile validation
  → Quality check
  → time/run/lot 기준 split
  → Window/feature generation
  → fit
  → Validation threshold selection
  → Test evaluation
  → model/scaler/metadata artifact
```

### Inference

```text
Live telemetry
  → 동일 profile + preprocessing
  → recent window
  → saved model/scaler
  → anomaly score
```

시계열 row random split은 사용하지 않는다. 같은 Lot/cycle의 인접 window가 train/test에 동시에 들어가는 leakage를 막기 위해 simulation run, Lot, cycle 또는 time block 단위로 먼저 split한다.

## 7. Univariate + Multivariate 이상탐지

현재 Resistance pipeline은 여러 공정 변수를 사용해 하나의 target을 예측하는 **multivariate regression + residual detection** 구조다.

향후 범용 설비 이상탐지에는 다음 두 detector를 병렬로 두는 것을 권장한다.

### Univariate detector

개별 tag의 시간 변화 탐지.

```text
Tag window
  → mean/std/slope/delta/stuck/noise feature
  → EWMA / Robust Z / Isolation Forest
```

주요 대상:

- spike
- drop
- drift
- stuck sensor
- noise increase
- change point

### Multivariate detector

개별 tag 값은 정상 범위지만 tag 사이 관계가 깨지는 이상 탐지.

```text
Time × Tags window
  → tag별 temporal feature
  + correlation / ratio / lag relation
  → Isolation Forest baseline
```

두 detector는 순차 필터가 아니라 동일 window에 **병렬 실행**한다. Univariate에서 anomaly가 나와도 Multivariate를 skip하지 않는다.

## 8. Relationship Break 검증

다변량 detector를 검증하려면 개별 값은 정상 범위지만 관계만 깨지는 synthetic anomaly가 필요하다.

예:

```text
Normal
speed=3000, torque=40, current≈10

Relationship break
speed=3000, torque=40, current=5
```

`current=5` 자체가 정상 범위라면 기대 결과는 다음과 같다.

```text
Univariate  = normal
Multivariate = anomaly
```

## 9. Model Artifact / Compatibility

새 환경에서는 모델 하나를 모든 설비에 공유하는 것보다 **같은 Training Pipeline으로 환경별 모델을 다시 만드는 것**을 우선한다.

Artifact metadata에는 최소 다음 정보를 저장한다.

- profile id/version
- model type/version
- canonical tag set
- feature schema/version
- sampling interval
- window size
- machine state/context
- training data range
- validation/test metrics
- threshold
- code/config version

Live inference 전에 현재 profile과 artifact의 tag/feature/sampling schema가 맞는지 검증해야 한다.

## 10. 권장 구현 순서

### Phase 1 — Portability + 올바른 평가 구조

먼저 구현한다.

- Environment Profile schema/validator
- source tag → canonical tag mapping
- machine-state mapping
- required/optional tag
- profile-driven tag selection
- offline bulk dataset generation/import
- Train/Validation/Test 분리
- model/scaler/metadata save/load
- profile/model compatibility check
- univariate + multivariate baseline
- relationship-break test

### Phase 2 — 성능 개선

Phase 1 구조 안에서 진행한다.

- window size tuning
- feature selection
- state/recipe-aware baseline 개선
- Isolation Forest / threshold tuning
- detector별 score calibration
- false-positive 분석
- anomaly-type별 Precision/Recall/F1
- 여러 synthetic profile에서 재학습/평가

### Phase 3 — 필요할 때 Deep Learning

Baseline 한계를 확인한 뒤 비교한다.

- LSTM/GRU Autoencoder
- TCN Autoencoder
- Transformer Autoencoder
- concept drift / incremental retraining

딥러닝 도입 자체가 목표가 되어서는 안 된다. 동일 split과 동일 test set에서 baseline 대비 개선이 확인될 때만 채택한다.

## 11. 실환경 Onboarding 목표 UX

최종적으로 새 환경에서 사람이 하는 일은 다음으로 제한하는 것이 목표다.

```text
1. Environment Profile 작성
2. source tag / unit / state mapping
3. 정상 historical data 제공 또는 baseline collection
4. train --profile <profile>
5. validate/evaluate
6. run --profile <profile>
```

시스템은 다음을 자동 수행한다.

```text
Profile validation
→ Data Quality validation
→ Canonical mapping
→ split/window/feature
→ model + scaler training
→ Validation threshold
→ Test metrics
→ Artifact registry
→ Live compatibility check
```

## 12. 우선순위 결론

**모델 성능만 먼저 최대화하지 않는다.**

현재 고정 Chamber schema에서 성능을 과도하게 튜닝한 뒤 실환경 구조를 추가하면 feature/tag/config assumptions 때문에 재작업 가능성이 높다.

권장 순서는 다음과 같다.

```text
1. Profile/Canonical schema 최소 구조
2. Offline Train/Validation/Test pipeline
3. 현재 GradientBoosting/MAD/EWMA baseline을 그 구조에서 재현
4. Univariate + Multivariate baseline 추가
5. 여러 Profile에서 portability 검증
6. Feature/threshold/model 성능 개선
7. 필요할 때만 Deep Learning
```

이 접근의 목표는 "한 synthetic dataset에서 F1이 높은 모델"이 아니라 **새로운 설비에서도 정상 데이터와 profile을 주면 다시 학습하고 검증할 수 있는 시스템**이다.

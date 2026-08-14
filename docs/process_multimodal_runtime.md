# Multi-process Multimodal Runtime

WaferGuard의 기존 Etch Chamber runtime을 보존하면서 Photo, Deposition, CMP를 공통 실험 계약으로 실행하기 위한 synthetic runtime입니다.

## Scope

- 공정별 **stateful time-series generator**
  - 공정 phase
  - 이전 값과의 연속성(AR)
  - Tag 간 correlation
  - 장비별 bias
  - 8-sample rolling behavior
  - drift / change point / spike / stuck / oscillation / noise increase / correlation break
- 공정별 wafer-like Vision synthetic generator
- 정상 데이터 기반 후보 모델 학습
- TS: Robust-Z / Mahalanobis / IsolationForest / OneClassSVM 비교
- Vision: IsolationForest / OneClassSVM 비교
- F2 우선 winner 선택
- 실제 joblib artifact 저장
- Production / Staging / Archived lifecycle
- runtime health / guarded promotion / rollback
- sample inference + GT + PostgreSQL 저장
- anomaly를 기존 `process_events`에 투영
- Photo / Deposition / CMP 원본 Tag + phase + Vision input을 Dashboard에서 실시간 확인
- Vision input 옆 진단용 pixel deviation map 표시 (모델 localization 아님)
- 같은 Lot/equipment의 Inspection/RCA에서 process evidence 재사용
- Dashboard 전역 Korean/English 선택 및 Light/Dark theme 저장

> 모든 범위, 이미지, 관계, 성능 수치는 synthetic/proxy입니다. 실제 Fab control limit 또는 실제 공정 성능을 의미하지 않습니다.

## Profiles

`configs/process_runtime.yaml`

- `photo`: idle → coat/track → expose → develop
  - exposure/focus/track/developer/overlay
  - bridge/missing-pattern/misalignment/residue
- `deposition`: pumpdown → heatup → deposition → purge
  - pressure/temperature/precursor/carrier gas/RF/time
  - particle/pinhole/thickness non-uniformity
- `cmp`: load → ramp → polish → rinse
  - down-force/platen/carrier/slurry/current
  - scratch/residue/dishing/erosion
- `etch`: 기존 특화 Chamber runtime을 계속 사용하며 generic profile은 공통 metadata/RCA 계약을 위한 보조 profile입니다.

Vision defect의 `root_cause_tags`는 정답 원인이 아니라 RCA에서 먼저 확인할 후보 Tag입니다.

## Time-series feature contract

Photo / Deposition / CMP 시계열 모델은 단일 시점 raw 값만 보지 않고 다음 feature를 함께 사용합니다.

```text
현재 Tag z-score
+ 직전 sample 대비 delta
+ config Tag 관계 error
+ 최근 8 sample 대비 rolling mean deviation
+ 최근 8 sample rolling std
```

Generator와 학습/실시간 추론이 같은 feature contract(`temporal-correlated-window-v2`)를 사용합니다. 이전 독립-random 또는 구버전 temporal Production artifact가 남아 있으면 runtime/MLOps가 현재 contract의 모델로 교체합니다.

### TS candidate models

Validation에서 다음 4개를 동일 데이터로 비교합니다.

1. `robust_z_univariate` — feature별 robust z-score의 최대 편차
2. `mahalanobis_multivariate` — shrinkage covariance 기반 다변량 거리
3. `isolation_forest`
4. `one_class_svm`

Winner는 F2를 우선하고, 동률/비슷한 경우 false-positive rate와 precision을 함께 봅니다. `false_positive_per_hour_at_1hz`는 synthetic 1 Hz 가정의 비교 지표이며 실제 장비 sampling rate에 그대로 적용하면 안 됩니다.

Anomaly 판단은 raw score의 부호가 아니라 threshold와의 차이로 봅니다.

```text
Anomaly Margin = score - threshold
margin < 0  -> normal
margin >= 0 -> anomaly
```

따라서 raw anomaly score가 음수여도 정상일 수 있습니다.

## Run live streams

Photo + Deposition + CMP를 동시에 Dashboard에 흘리려면:

```powershell
python scripts/run_process_stream.py --process all --modality both --samples 300 --interval 1
```

한 공정만 실행:

```powershell
python scripts/run_process_stream.py --process deposition --modality both --samples 120 --interval 1
```

시계열 이상 주입 예:

```powershell
python scripts/run_process_stream.py --process deposition --modality timeseries --samples 120 --anomaly pressure_drift --anomaly-after 40
python scripts/run_process_stream.py --process cmp --modality timeseries --samples 120 --anomaly correlation_break --anomaly-after 40
python scripts/run_process_stream.py --process photo --modality timeseries --samples 120 --anomaly sensor_stuck --anomaly-after 40
```

Vision defect 주입 예:

```powershell
python scripts/run_process_stream.py --process cmp --modality vision --samples 80 --anomaly scratch --anomaly-after 30
```

Etch는 전용 runtime을 실행합니다.

```powershell
python scripts/run_chamber_stream.py --equipment-count 3 --interval 1
```

각 generic stream은 PostgreSQL `process_runtime_samples`와 anomaly `process_events`를 기록하고, local object storage의 다음 자산을 갱신합니다.

```text
outputs/process_runtime/<process>/live.json
outputs/process_runtime/<process>/*.png
```

Dashboard는 live snapshot을 2초마다 polling하여 정상 sample도 포함한 raw Tag/phase/Vision을 표시합니다. Dashboard의 live precision/recall/F2는 과거 DB 누적과 섞지 않고 **현재 실행의 최근 live history**에서 계산합니다. PostgreSQL 원본 이력은 그대로 보존됩니다.

## Dashboard

Photo / Deposition / CMP Process Monitoring:

- 각 Tag raw synthetic 값과 실시간 추세
- 현재 process phase / progress
- equipment / Lot / wafer
- TS score / threshold / **margin** / Production model
- 최신 Vision input / 최근 wafer thumbnails
- 진단용 spatial deviation map
- Vision score / threshold / margin / injected defect / Production model
- RCA Tag 후보
- PostgreSQL anomaly history
- 현재 live history 기준 synthetic precision / recall / F2

Etch는 기존 Chamber Resistance runtime과 전용 Dashboard를 유지합니다.

### Language / Theme

Dashboard 상단 또는 Settings에서 다음 값을 선택할 수 있습니다.

- Language: `한국어` / `English`
- Appearance: `Light` / `Dark`

선택값은 `localStorage`에 저장되어 새로고침 뒤에도 유지됩니다. App shell과 이번 작업에서 직접 연결한 주요 workspace(Fab Overview, Process Monitoring, Wafer Quality shell, AI Analysis shell, MLOps workspace, Data/RAG browser, Settings)의 탭/버튼/제목/설명은 선택한 언어 하나만 표시합니다.

단, 기존 대형 legacy 하위 컴포넌트(예: 세부 Agent trace/Inspection/Vision legacy 화면)에 원래부터 저장되어 있던 일부 정적 한국어와 DB/RAG/LLM에서 들어오는 원문 데이터는 자동 번역하지 않습니다. 이 부분은 향후 전체 i18n 리팩터링 범위입니다.

Theme은 header/sidebar/main/panel/table/input/chart 공통 CSS token을 사용합니다. 기존 stylesheet에서 dark token 뒤에 light `:root`가 다시 덮어쓰던 selector 문제를 final theme layer로 수정했고, 기존 Recharts의 고정색도 theme token으로 덮어써 dark/light에서 대비를 유지합니다.

## Model lifecycle

현재 모델 확인:

```powershell
python scripts/manage_process_models.py list --process deposition
```

TS Production bootstrap:

```powershell
python scripts/manage_process_models.py bootstrap --process deposition --modality timeseries
```

Vision Production bootstrap:

```powershell
python scripts/manage_process_models.py bootstrap --process deposition --modality vision
```

runtime health:

```powershell
python scripts/manage_process_models.py health --process deposition --modality timeseries
```

Staging candidate:

```powershell
python scripts/manage_process_models.py retrain --process deposition --modality timeseries --force
```

Staging candidate가 기존 Production보다 F2가 낮거나 false-positive rate가 0.02보다 더 악화되면 promotion gate가 거절합니다.

Production 승격:

```powershell
python scripts/manage_process_models.py promote --process deposition --modality timeseries --version deposition-timeseries-v2
```

승격 이후 다음 inference부터 새 Production artifact를 사용합니다.

Rollback:

```powershell
python scripts/manage_process_models.py rollback --process deposition --modality timeseries
```

## Persistence

- `process_model_registry`: process × modality 모델/metric/artifact/stage
- `process_runtime_samples`: runtime prediction, injected GT, score, threshold, payload, image key
- `process_events`: 탐지된 공정 anomaly 공통 event

모델 artifact:

```text
runtime/models/process/<process>/<modality>/<version>.joblib
```

## Verification

GitHub Actions 검증은 다음 항목을 포함합니다.

- Unit and regression tests (SQLite)
- PostgreSQL integration test
- Frontend install + production build
- Python compile check

테스트는 temporal continuity/phase, configured correlation과 correlation-break, 전체 TS anomaly 유형, 4-way candidate comparison, rolling feature contract, current-live metric scope, 실제 artifact 생성과 Staging→Production 교체까지 포함합니다.

## Current boundary

이 runtime은 실데이터가 없는 공정에서 학습/평가/MLOps/RCA 계약을 검증하기 위한 synthetic 경로입니다. 현재 runtime F2/FP 기준도 synthetic GT 평가입니다. 실제 Fab에서는 시간 기반 holdout, drift detector, delayed quality label, 실제 recipe/equipment context로 교체해야 합니다.

현재 generic Vision 모델은 handcrafted image feature + anomaly model입니다. Dashboard deviation map은 입력의 공간적 편차를 보는 진단 이미지이며 model attribution/heatmap이 아닙니다. 실제 이미지 데이터가 연결되면 PatchCore/PaDiM/DINO 계열 artifact와 실제 localization output으로 교체하는 것이 다음 Vision 확장 지점입니다.

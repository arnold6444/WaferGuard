# Multi-process Multimodal Runtime

WaferGuard의 기존 Etch Chamber runtime을 보존하면서 Photo, Etch, Deposition, CMP를 같은 실험 계약으로 비교하기 위한 synthetic runtime입니다.

## Scope

- 공정별 time-series synthetic generator
- 공정별 wafer-like vision synthetic generator
- 정상 데이터만으로 후보 모델 학습
- IsolationForest / OneClassSVM validation 비교
- F2 우선 winner 선택
- 실제 joblib artifact 저장
- Production / Staging / Archived model lifecycle
- runtime health 비교 / guarded promotion / rollback
- 실시간 sample inference + GT 저장
- 탐지 결과를 기존 `process_events`에 투영
- Photo / Deposition / CMP 원본 Tag 값 + Vision input을 Dashboard에서 실시간 확인
- Vision input 옆에 진단용 pixel deviation map 표시 (모델 localization 아님)
- 같은 lot/equipment의 이후 Inspection/RCA 실행 시 기존 RCA Agent evidence로 자동 연결

> 모든 범위, 이미지, 성능 수치는 synthetic/proxy입니다. 실제 Fab control limit 또는 실제 공정 성능을 의미하지 않습니다.

## Profiles

`configs/process_runtime.yaml`

- `photo`: exposure/focus/track/developer/overlay + bridge/missing-pattern/misalignment/residue
- `etch`: pressure/RF/gas/temperature + over/under-etch/residue/non-uniformity
- `deposition`: pressure/temperature/precursor/RF/time + particle/pinhole/thickness non-uniformity
- `cmp`: down-force/speed/slurry/current + scratch/residue/dishing/erosion

각 vision defect에는 RCA에서 참고할 `root_cause_tags` 후보를 함께 둡니다. 이는 정답 원인이 아니라 검사할 tag 후보입니다.

## Run live streams

Photo + Deposition + CMP를 한 번에 로컬 Dashboard에 흘리려면:

```powershell
python scripts/run_process_stream.py --process all --modality both --samples 300 --interval 1
```

한 공정만 실행할 수도 있습니다.

```powershell
python scripts/run_process_stream.py --process deposition --modality both --samples 80 --interval 1
```

시계열 이상 주입 예:

```powershell
python scripts/run_process_stream.py --process deposition --modality timeseries --samples 80 --anomaly pressure_drift --anomaly-after 40
```

Vision defect 주입 예:

```powershell
python scripts/run_process_stream.py --process cmp --modality vision --samples 60 --anomaly scratch --anomaly-after 30
```

`--modality both`에서 anomaly 이름이 한 modality에만 존재하면 해당 modality에만 GT가 주입됩니다.

각 stream은 PostgreSQL의 `process_runtime_samples`와 anomaly `process_events`를 계속 기록하면서, local object storage에 `outputs/process_runtime/<process>/live.json`과 Vision PNG/deviation PNG를 갱신합니다. Dashboard는 live snapshot을 2초마다 polling해 정상 sample까지 포함한 실제 Tag 값을 표시합니다.

## Dashboard

Photo / Deposition / CMP의 Process Monitoring에서 다음을 볼 수 있습니다.

- 각 Tag의 최근 raw synthetic 값과 실시간 추세 그래프
- 현재 equipment / lot / wafer
- time-series anomaly score / threshold / Production model version
- 최신 Vision 원본 이미지와 최근 wafer 썸네일
- 진단용 spatial deviation map
- Vision anomaly score / threshold / injected GT defect / Production model version
- 관련 RCA tag 후보
- PostgreSQL `process_events`에 기록된 anomaly history
- synthetic GT 기반 runtime precision / recall / F2

Etch는 기존 특화 Chamber Resistance runtime과 전용 Dashboard를 유지합니다.

## Model lifecycle

현재 모델 확인:

```powershell
python scripts/manage_process_models.py list --process deposition
```

Production 모델 초기 bootstrap:

```powershell
python scripts/manage_process_models.py bootstrap --process deposition --modality vision
```

runtime health 확인:

```powershell
python scripts/manage_process_models.py health --process deposition --modality vision
```

새 Staging candidate 학습:

```powershell
python scripts/manage_process_models.py retrain --process deposition --modality vision --force
```

Validation에서는 IsolationForest와 OneClassSVM을 비교하고 F2를 우선하여 winner를 선택합니다. Staging candidate가 기존 Production보다 F2가 낮거나 false-positive rate가 0.02보다 더 악화되면 promotion gate가 거절합니다.

Staging candidate를 Production으로 승격:

```powershell
python scripts/manage_process_models.py promote --process deposition --modality vision --version deposition-vision-v2
```

승격 후 다음 inference부터 새 Production artifact가 사용됩니다.

문제가 생기면 가장 최근 Archived 모델로 rollback:

```powershell
python scripts/manage_process_models.py rollback --process deposition --modality vision
```

## Persistence

추가 테이블:

- `process_model_registry`: process × modality별 모델/metric/artifact/stage
- `process_runtime_samples`: 실제 runtime prediction, injected GT, score, threshold, image key

모델 artifact:

```text
runtime/models/process/<process>/<modality>/<version>.joblib
```

Vision/live Dashboard 자산:

```text
outputs/process_runtime/<process>/live.json
outputs/process_runtime/<process>/*.png
```

탐지된 anomaly event는 새 전용 event table을 만들지 않고 기존 `process_events`에 저장합니다. 따라서 기존 Fab Overview/Inspection correlation/RCA 흐름을 재사용합니다.

## Current boundary

이번 runtime은 실데이터가 없는 공정의 학습/평가/MLOps 계약을 검증하기 위한 synthetic 실험 경로입니다. 기존 Etch Chamber runtime은 계속 더 현실적인 특화 시계열 경로로 유지합니다.

현재 health/degradation 판단은 synthetic runtime GT가 있는 실험 경로용입니다. 실제 Fab에서는 ground truth 대신 시간 기반 validation, drift detector, delayed quality label 등으로 교체해야 합니다.

현재 generic Vision model은 이미지에서 추출한 handcrafted feature + anomaly model입니다. Dashboard의 deviation map은 입력의 공간적 차이를 보기 위한 진단 이미지일 뿐 model attribution/heatmap이 아닙니다. 실제 이미지 데이터가 연결되면 PatchCore/PaDiM/DINO 계열 artifact adapter와 해당 localization map으로 교체하는 것이 다음 Vision 확장 지점입니다.

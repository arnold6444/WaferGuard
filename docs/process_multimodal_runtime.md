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
- 실시간 sample inference + GT 저장
- 탐지 결과를 기존 `process_events`에 투영
- 같은 lot/equipment의 이후 Inspection 실행 시 기존 RCA Agent의 `related_process_events` evidence로 자동 연결

> 모든 범위, 이미지, 성능 수치는 synthetic/proxy입니다. 실제 Fab control limit 또는 실제 공정 성능을 의미하지 않습니다.

## Profiles

`configs/process_runtime.yaml`

- `photo`: exposure/focus/track/developer/overlay + bridge/missing-pattern/misalignment/residue
- `etch`: pressure/RF/gas/temperature + over/under-etch/residue/non-uniformity
- `deposition`: pressure/temperature/precursor/RF/time + particle/pinhole/thickness non-uniformity
- `cmp`: down-force/speed/slurry/current + scratch/residue/dishing/erosion

각 vision defect에는 RCA에서 참고할 `root_cause_tags` 후보를 함께 둡니다. 이는 정답 원인이 아니라 검사할 tag 후보입니다.

## Run a live synthetic stream

PostgreSQL을 먼저 실행한 뒤:

```powershell
python scripts/run_process_stream.py --process deposition --modality both --samples 80 --interval 1
```

시계열 이상 주입 예:

```powershell
python scripts/run_process_stream.py --process etch --modality timeseries --samples 80 --anomaly pressure_drift --anomaly-after 40
```

Vision defect 주입 예:

```powershell
python scripts/run_process_stream.py --process cmp --modality vision --samples 60 --anomaly scratch --anomaly-after 30
```

`--modality both`에서 anomaly 이름이 한 modality에만 존재하면 해당 modality에만 GT가 주입됩니다.

## Model lifecycle

현재 모델 확인:

```powershell
python scripts/manage_process_models.py list --process deposition
```

새 candidate 학습:

```powershell
python scripts/manage_process_models.py train --process deposition --modality vision
```

학습 결과는 Staging으로 등록됩니다. Validation에서는 IsolationForest와 OneClassSVM을 비교하고 F2를 우선하여 winner를 선택합니다.

Staging candidate를 Production으로 승격:

```powershell
python scripts/manage_process_models.py promote --process deposition --modality vision --version deposition-vision-v2
```

승격 후 다음 inference부터 새 Production artifact가 사용됩니다.

## Persistence

추가 테이블:

- `process_model_registry`: process × modality별 모델/metric/artifact/stage
- `process_runtime_samples`: 실제 runtime prediction, injected GT, score, threshold, image key

모델 artifact:

```text
runtime/models/process/<process>/<modality>/<version>.joblib
```

Vision PNG:

```text
outputs/process_runtime/<process>/...
```

탐지된 event는 새 전용 event table을 만들지 않고 기존 `process_events`에 저장합니다. 따라서 기존 Fab Overview/Inspection correlation/RCA 흐름을 재사용합니다.

## Current boundary

이번 runtime은 실데이터가 없는 공정의 학습/평가/MLOps 계약을 검증하기 위한 backend 실험 경로입니다. 기존 Etch Chamber runtime은 계속 더 현실적인 특화 시계열 경로로 유지합니다.

다음 단계는 다음 두 가지입니다.

1. 실제 이미지 데이터셋이 준비되면 handcrafted vision feature 대신 PatchCore/PaDiM/DINO 계열 artifact adapter를 같은 registry contract에 연결
2. Dashboard에서 process/model/runtime metrics를 직접 조회하고 Staging → Production 승인 UI를 연결

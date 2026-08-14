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
- Photo / Deposition / CMP Process Monitoring에서 anomaly event 실시간 polling
- PostgreSQL `process_events`를 다시 조회해 관련 tag까지 기존 LangGraph Agent evidence로 넘기는 RCA bridge
- 같은 lot/equipment의 이후 Inspection 실행 시 기존 RCA Agent의 `related_process_events` evidence로도 자동 연결

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

Photo / Deposition / CMP는 Process Monitoring에서 `process_events`를 2초마다 읽어 최근 시계열/비전 anomaly, score/threshold, 모델 버전, RCA 관련 tag 후보를 표시합니다. Etch는 기존 특화 Chamber 화면을 유지합니다.

## PostgreSQL-backed RCA

실시간 detector가 만든 결과는 먼저 `process_events`에 저장됩니다. RCA 실행 시 generator의 메모리 상태를 직접 쓰지 않고 PostgreSQL에서 같은 설비/Lot/Wafer의 최근 event를 다시 조회합니다.

```powershell
python scripts/run_process_rca.py --process cmp --equipment-id CMP-01 --lot-id LOT-MM-DEMO-001 --minutes 30
```

RCA evidence에는 다음이 포함됩니다.

- time-series / vision modality
- anomaly score / threshold
- injected anomaly 또는 detected signal
- process profile의 관련 tag 후보
- 저장된 Vision image URL

이 evidence는 기존 LangGraph Inspection Agent로 전달되어 `Observation / Possible Causes / Evidence / Recommended Checks / Recommended Action / Confidence` 형식으로 판단합니다. 관련 tag는 원인 확정값이 아니라 우선 확인할 후보입니다.

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

Vision PNG:

```text
outputs/process_runtime/<process>/...
```

탐지된 event는 새 전용 event table을 만들지 않고 기존 `process_events`에 저장합니다. 따라서 기존 Fab Overview/Inspection correlation/RCA 흐름을 재사용합니다.

## Current boundary

이번 runtime은 실데이터가 없는 공정의 학습/평가/MLOps 계약을 검증하기 위한 synthetic 실험 경로입니다. 기존 Etch Chamber runtime은 계속 더 현실적인 특화 시계열 경로로 유지합니다.

현재 health/degradation 판단은 synthetic runtime GT가 있는 실험 경로용입니다. 실제 Fab에서는 ground truth 대신 시간 기반 validation, drift detector, delayed quality label 등으로 교체해야 합니다.

Dashboard는 runtime anomaly evidence를 읽어 표시하지만 generic process model의 retrain/promote/rollback 실행은 아직 CLI를 사용합니다.

다음 실제 데이터 단계에서는 handcrafted vision feature를 PatchCore/PaDiM/DINO 계열 artifact adapter로 교체해 같은 registry contract를 유지하는 것이 다음 확장 지점입니다.

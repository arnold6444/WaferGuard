# WaferGuard 핵심 구조

## 전체 흐름

```text
Virtual FAB (Photo → Etch → Deposition → CMP)
  → fab.v2 identity/event
  → MQTT QoS 1 또는 Direct debug
  → 공통 Message Router
      ├─ PostgreSQL/SQLite 저장
      └─ TS / Vision / Metrology detector
  → signed-margin Late Fusion
  → evidence-ranked Candidate RCA
  → FastAPI
  → React Dashboard / Agent / HITL / MLOps
```

## 주요 폴더

```text
app/main.py                  FastAPI와 API endpoint
app/services/fab_*.py       FAB 생성, 저장, 검출, fusion, RCA, 분석
configs/                    FAB 장비/recipe/fault 설정
scripts/run_fab_stream.py   실제 FAB event 실행 entrypoint
scripts/run_fab_analysis.py notebook 없는 로컬 분석 entrypoint
notebooks/                  셀 단위 개인 분석 UI
data/input/                 사용자가 넣는 CSV/Parquet (Git 제외)
data/output/                분석 JSON/CSV/model (Git 제외)
frontend/src/               React 운영 화면
compose.yaml                PostgreSQL과 Mosquitto
```

## Identity와 데이터

모든 tick은 Lot, Wafer, Process Run, Equipment, Unit, Recipe, Cycle, State, Phase, UTC 시간을 유지합니다. Wafer는 `LOT-ID-W01`, 장비 Unit은 `(equipment_id, unit_id)` 복합 identity를 사용합니다.

이미지는 로컬 object storage인 `outputs/`에 저장하고 MQTT에는 key와 metadata만 전달합니다. `simulation_faults`와 GT mask는 평가/debug 전용이며 일반 detector, RCA, API에는 노출하지 않습니다.

## 장비 중심 Context

네 공정은 공정 이름만 보여주지 않고 `Equipment / Unit / Recipe / Sensor Tag / Unit`을 함께 보존합니다.

| 공정 | 대표 장비 | 주요 sensor/context | 기본 post-process evidence |
|---|---|---|---|
| Photo | scanner + coat/develop track | focus, exposure, stage, resist/track condition | overlay/CD 계측, 광학 pattern inspection |
| Etch | plasma etcher | RF power, pressure, gas flow, endpoint, chamber condition | CD-SEM linewidth/profile, top-down SEM; 필요 시 FIB 단면 TEM/STEM 검토 제안 |
| Deposition | CVD/PVD tool | precursor/MFC, pressure, temperature, RF | ellipsometry/reflectometry film thickness·uniformity |
| CMP | polisher | platen/head speed, slurry flow, pressure, motor/current | remaining film·uniformity, surface defect scan |

Metrology/inspection payload는 `instrument_class`, `inspection_modality`, `image_type`, `sampling_level`, `measurements`를 포함합니다. Synthetic 값은 실제 FAB 보정치가 아니며, RCA는 항상 `Candidate root cause`와 확인해야 할 장비/계측 항목을 제안합니다. 담당자 Review가 누적되기 전에는 자동 판정이나 자동 조치를 하지 않습니다.

## Notebook과 설치형 Python

```text
CSV/Parquet 또는 local DB export
  → app/services/fab_analysis.py
      ├─ baseline detector prediction / simulation GT 분리
      ├─ schema, missing, duplicate, label/leakage audit
      └─ non-training correlation / feature effect
  → app/services/fab_experiment.py
      ├─ wafer/run grouped chronological Train/Validation/Test
      ├─ normal-only Train + 기존 4 candidate 비교
      ├─ Validation threshold·F2·delay·fault/context/seed 분석
      ├─ 선택적 sealed Final Test
      ├─ 선택적 fingerprint-compatible candidate artifact
      └─ data/output/fab_analysis/dashboard_summary.json
         → GET /api/v1/fab/analysis/latest
         → AI Analysis 화면
```

Notebook은 위 Python 모듈을 호출하는 셀 단위 UI입니다. 핵심 로직이 notebook 안에 묶여 있지 않으므로 설치형 프로그램이나 pipeline에서는 `scripts/run_fab_analysis.py` 또는 서비스를 직접 호출해 같은 split, metric, artifact 계약을 사용할 수 있습니다.

## 모델 경계

- 기본 runtime은 versioned context baseline으로 실행 가능합니다.
- Workbench의 세 실행 gate는 기본적으로 꺼져 있으며 분석만으로 모델 fit을 시작하지 않습니다.
- feature/model/hyperparameter/threshold는 Validation에서만 선택하고 Test는 고정 후보를 한 번 평가하는 용도입니다.
- 기존 detector 예측은 label이 아니며 GT와 feature에서 모두 분리합니다.
- 로컬 실험은 성능 탐색용이며 자동 등록/Production 승격을 하지 않습니다.
- FAB 후보 artifact는 runtime에서 export한 exact feature names와 현재 config fingerprint가 일치해야 합니다.
- 후보는 Staging 등록 후 사람이 검토하여 수동 승격합니다.

## Dashboard 반영 방식

Notebook/CLI가 `data/output/fab_analysis/dashboard_summary.json`을 원자적 결과 계약으로 만듭니다. Backend는 이 요약만 읽고 React AI Analysis 화면은 dataset 상태, 운영 baseline, Validation winner, sealed Final Test, fault별 결과를 단계별로 표시합니다. 기존 v1 요약도 fallback으로 읽으며 원본 학습 파일이나 모델 내부 객체는 브라우저로 보내지 않습니다.

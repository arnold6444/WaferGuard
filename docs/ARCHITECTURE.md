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

## Notebook과 설치형 Python

```text
CSV/Parquet 또는 local DB export
  → app/services/fab_analysis.py
      ├─ schema/missing/duplicate/summary
      ├─ correlation matrix
      ├─ feature importance와 기본 metric
      ├─ 선택적 fingerprint-compatible artifact
      └─ dashboard_summary.json
         → GET /api/v1/fab/analysis/latest
         → AI Analysis 화면
```

Notebook은 위 Python 모듈을 호출만 합니다. 따라서 notebook을 제거해도 `scripts/run_fab_analysis.py` 또는 다른 파이프라인에서 같은 함수와 결과 계약을 그대로 사용할 수 있습니다.

## 모델 경계

- 기본 runtime은 versioned context baseline으로 실행 가능합니다.
- 로컬 분석은 성능 탐색용이며 자동 Production 승격을 하지 않습니다.
- FAB 후보 artifact는 runtime에서 export한 exact feature names와 현재 config fingerprint가 일치해야 합니다.
- 후보는 Staging 등록 후 사람이 검토하여 수동 승격합니다.

## Dashboard 반영 방식

Notebook/CLI가 `data/output/dashboard_summary.json`을 원자적 결과 계약으로 만듭니다. Backend는 이 요약만 읽고 React AI Analysis 화면은 dataset 크기, 결측/중복, 분석 방식, 상위 feature importance와 correlation을 표시합니다. 원본 학습 파일이나 모델 내부 객체는 브라우저로 보내지 않습니다.

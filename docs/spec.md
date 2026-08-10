# Chamber AI 통합 사양

> 이 문서는 **Chamber Resistance AI + Wafer Vision AI 기능 범위**를 설명합니다. WaferGuard 전체 시스템 사양은 `README.md`를 기준으로 봅니다.

## 현재 구현 방식

Chamber AI는 하나의 메뉴 안에서 두 종류의 분석 결과를 보여줍니다.

1. **Resistance AI**: 오프라인 Python 스크립트가 CSV를 분석해 `frontend/src/data/chamberSample.json`을 생성하고, React가 이 결과를 시각화합니다.
2. **Vision AI**: 별도 `wafer_particle` 분석 API에서 미리 생성한 결과를 `frontend/src/data/waferVisionSample.json`과 대표 이미지로 저장하고, React가 이 스냅샷을 시각화합니다.

따라서 현재 두 모델은 브라우저 요청마다 다시 학습/추론하는 서비스가 아닙니다. 다만 Vision AI에서 선택한 분석 근거를 기존 WaferGuard FastAPI/Inspection Agent 흐름으로 전달하는 연동은 구현되어 있습니다.

## Resistance AI MVP

- `챔버 이상탐지 / Chamber AI`를 WaferGuard의 첫 메뉴로 제공합니다.
- 샘플 분석 범위, OOF MAE, 행 이상 임계값, 이상 EQP 수를 요약합니다.
- 정상 RESISTANCE 패턴: 실제 중앙값/IQR과 정상 재학습 모델 예측값을 표시합니다.
- EQP 날짜 추세: 실제값, 정상 예측값, 행 이상 시점을 표시합니다.
- EQP 이상 맵: Median error와 Q95 error, MAD 경계를 함께 표시합니다.
- 이상 점수 순위와 상위 EQP 판정표를 제공합니다.
- EQP 선택 시 해당 설비의 날짜별 실제값/예측값 추세가 즉시 바뀝니다.

### Resistance 데이터·판정 기준

- 필수 입력 컬럼: `DATE`, `EQP`, `USE_TIME`, `RESISTANCE`
- 모델: `GradientBoostingRegressor(loss="huber")`
- 검증: EQP 그룹 단위 5-fold OOF
- 1차 후보: EQP별 Median 또는 Q95 절대오차가 `중앙값 + 3.0 × scaled MAD` 초과
- 정상 모델: 1차 후보를 제외한 EQP로 재학습
- 행 이상: 정상 후보 OOF 절대오차의 상위 1% 초과
- 최종 EQP 이상: 정상 EQP 분포의 Median 또는 Q95 경계 초과
- 현재 샘플: 3,000행 / 100 EQP / 이상 후보 EQP10, EQP55

### Resistance 재생성

`build_chamber_dashboard_data.py`는 runtime `requirements.txt`에 없는 `pandas`, `scikit-learn`을 추가로 사용합니다.

```bash
pip install pandas scikit-learn
python scripts/build_chamber_dashboard_data.py --input "/path/to/sample.csv" --output "frontend/src/data/chamberSample.json"
```

원본 CSV는 저장소에 포함하지 않습니다.

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

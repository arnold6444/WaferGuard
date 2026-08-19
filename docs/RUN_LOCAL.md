# WaferGuard 로컬 실행

Python 3.11과 Docker Desktop을 사용합니다. 모든 데이터와 결과는 이 프로젝트 폴더 안에 저장되며 Google Drive는 필요하지 않습니다.

## 1. 최초 설치

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

5432 포트를 다른 프로그램이 사용 중이면 `.env`에서 아래 두 값을 **같이** `5433`으로 바꿉니다.

```dotenv
RDS_PORT=5433
POSTGRES_PORT=5433
```

## 2. DB와 MQTT 시작

```powershell
docker compose up -d postgres mosquitto
docker compose ps
```

두 서비스가 `healthy`이면 준비된 상태입니다.

## 3. FAB 데이터 실행

Photo, Etch, Deposition, CMP는 모두 같은 **Synthetic Runtime 지원 공정**입니다. 다만 화면의 상태는 실제 데이터 상태를 그대로 표시합니다.

- `LIVE`: 해당 공정의 Run이 현재 실행 중
- `LATEST STORED`: 유한 스트림이 끝났고 최근 Run이 DB에 저장됨
- `RUNTIME READY`: 실행 코드는 연결됐지만 아직 생성된 Run이 없음
- `NOT CONNECTED`: 이번 v2 범위 밖의 legacy/demo 공정

아래 명령은 CMP만 한 번 실행합니다. 기본 `--interval 0`이므로 빠르게 완료되고, 완료 후 화면에는 `LATEST STORED`로 보이는 것이 정상입니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_stream.py --lots 1 --wafers-per-lot 1 --process cmp --seed 42 --transport mqtt
```

Dashboard를 먼저 연 뒤 네 공정의 상태 변화를 눈으로 보려면 `--process`를 생략하고 메시지 간격을 줍니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_stream.py --lots 1 --wafers-per-lot 1 --seed 42 --transport mqtt --interval 0.2
```

빠른 디버그는 MQTT 대신 동일 handler를 직접 호출합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_stream.py --lots 1 --wafers-per-lot 1 --process cmp --seed 42 --transport direct
```

## 4. Backend와 Dashboard

터미널 1:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

터미널 2:

```powershell
Set-Location frontend
npm install
npm run dev
```

- Dashboard: `http://127.0.0.1:5173`
- API 문서: `http://127.0.0.1:8000/docs`

## 5. 이상탐지 Performance Workbench

사용자가 넣는 학습/실험 데이터 위치:

```text
data/input/<process>_runtime_features.csv
```

CSV 또는 Parquet를 사용할 수 있고 Google Drive는 필요하지 않습니다. 현재 DB의 detector feature를 먼저 내보내려면 다음 명령을 사용합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_analysis.py --input data\input\cmp_runtime_features.csv --process cmp --export-runtime
```

이 기본 실행은 모델을 fit하지 않습니다. 데이터 품질, label 감사, grouped chronological split, correlation, 비학습 feature effect와 기존 detector baseline만 준비합니다. 결과는 아래에 생성됩니다.

```text
data/output/fab_analysis/dashboard_summary.json
data/output/fab_analysis/dataset_profile.json
data/output/fab_analysis/split_manifest.csv
data/output/fab_analysis/feature_importance.csv
data/output/fab_analysis/correlation_matrix.csv
data/output/fab_analysis/experiment_manifest.json
```

두 디렉터리의 실제 데이터는 Git에 올라가지 않습니다.

### Notebook

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe -m jupyter lab notebooks\fab_local_analysis.ipynb
```

Notebook 상단에서 `INPUT_PATH`, `PROCESS_ID`를 바꿉니다. 아래 세 플래그는 기본값이 모두 `False`입니다.

```python
RUN_EXPERIMENT = False
RUN_FINAL_TEST = False
CREATE_CANDIDATE = False
```

기본 셀만으로 데이터 구조, 결측/중복, label leakage, Train/Validation/Test 분리, correlation heatmap, feature effect, 기존 detector baseline과 후보 실험표를 확인할 수 있습니다. 실제 모델 비교는 사용자가 `RUN_EXPERIMENT=True`로 바꿀 때만 Train fit/Validation threshold 선택을 수행합니다. `RUN_FINAL_TEST`는 후보를 고정한 뒤 Test를 한 번만 확인할 때, `CREATE_CANDIDATE`는 최종 후보 파일을 만들 때만 켭니다.

`baseline_detector_is_anomaly`는 기존 운영 detector의 예측이며 정답 label이 아닙니다. Synthetic 평가의 정답은 별도 `ground_truth_is_anomaly`이고, 실제 현장 데이터처럼 GT가 없으면 supervised F2/Recall은 계산하지 않고 정상 구간 기반 비지도 실험만 해석해야 합니다.

### Python CLI

Notebook 없이 같은 계약을 파이프라인에서 실행합니다. 기본 명령은 모델을 학습하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_analysis.py --input data\input\cmp_runtime_features.csv --process cmp
```

사용자가 실험을 시작할 때만 `--run-experiment`를 명시합니다. `--run-final-test`와 `--create-candidate`도 각각 봉인 Test 평가와 artifact 생성을 명시적으로 허용하는 opt-in입니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_analysis.py --input data\input\cmp_runtime_features.csv --process cmp --run-experiment
```

분석 후 Dashboard의 **AI Analysis → Local Data Analysis**에서 결과를 확인합니다. 화면은 요약 JSON만 읽으며 원본 데이터를 노출하지 않습니다.

## 6. Candidate 등록

후보 생성까지 직접 허용한 경우에만 `dashboard_summary.json`의 artifact 경로와 Validation/Final metric을 검토한 뒤 Staging으로 등록합니다.

```powershell
.\.venv\Scripts\python.exe scripts\register_fab_candidate.py `
  --artifact data\output\fab_analysis\<candidate-version>.joblib --process cmp `
  --precision <value> --recall <value> --f2 <value> --false-positive-rate <value>
```

Production 승격은 기존 MLOps 화면/수동 API에서만 수행합니다.

## 7. 종료와 데이터 초기화

실행한 터미널에서는 먼저 `Ctrl+C`를 누릅니다. `run_fab_stream.py`는 유한 실행이라 보통 자동 종료되지만, 중간에 멈추려면 같은 방식으로 `Ctrl+C`를 누르면 됩니다.

터미널을 닫아서 생성기 PID를 잃어버린 경우에는 아래 명령으로 **FAB 생성기만** 찾아 종료합니다.

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*WaferGuard*run_fab_stream.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

PostgreSQL과 Mosquitto를 중지하되 DB 데이터는 보존하려면 다음 명령을 사용합니다.

```powershell
docker compose stop postgres mosquitto
```

테스트 기록과 로컬 생성 파일만 삭제하려면 프로젝트 루트에서 실행합니다. 소스 코드와 입력용 `.gitkeep`은 삭제하지 않습니다.

```powershell
Remove-Item -LiteralPath .pytest_cache,outputs,runtime,frontend\dist -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem app,scripts,tests -Directory -Recurse -Filter __pycache__ |
  Remove-Item -Recurse -Force
```

PostgreSQL 데이터까지 완전히 초기화하려면 아래 명령을 사용합니다. 이 명령은 Compose 컨테이너와 DB 볼륨을 영구 삭제합니다.

```powershell
docker compose down --volumes --remove-orphans
```

다시 시작할 때는 `docker compose up -d postgres mosquitto`를 실행하면 빈 DB가 자동 생성됩니다.

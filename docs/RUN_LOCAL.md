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

## 3. 가장 작은 FAB 실행

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_stream.py --lots 1 --wafers-per-lot 1 --process cmp --seed 42 --transport mqtt
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

## 5. 로컬 데이터 분석

학습/분석 원본 위치:

```text
data/input/fab_training.csv
```

CSV 또는 Parquet 파일을 사용할 수 있습니다. 결과는 아래에 생성됩니다.

```text
data/output/dashboard_summary.json
data/output/feature_importance.csv
data/output/correlation_matrix.csv
data/output/<candidate-version>.joblib
```

두 디렉터리의 실제 데이터는 Git에 올라가지 않습니다.

### Notebook

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe -m jupyter lab notebooks\fab_local_analysis.ipynb
```

Notebook 상단 설정 셀에서 파일, 공정, target을 변경합니다. 기본 `CREATE_CANDIDATE=False`이므로 데이터 구조, 결측/중복, 통계, correlation heatmap, feature importance만 실행합니다.

### Python CLI

Notebook 없이 같은 분석을 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_analysis.py --input data\input\fab_training.csv --process cmp --target is_anomaly
```

현재 로컬 DB의 실제 runtime feature를 먼저 CSV로 내보내려면:

```powershell
.\.venv\Scripts\python.exe scripts\run_fab_analysis.py --input data\input\cmp_runtime_features.csv --process cmp --export-runtime
```

호환 candidate artifact도 만들려면 마지막에 `--create-candidate`를 붙입니다. 임의 CSV에는 FAB fingerprint가 없으므로 이 옵션은 runtime export 데이터에서만 허용됩니다.

분석 후 Dashboard의 **AI Analysis → Local Data Analysis**에서 결과를 확인합니다. 화면은 요약 JSON만 읽으며 원본 데이터를 노출하지 않습니다.

## 6. Candidate 등록

`dashboard_summary.json`의 artifact 경로와 registration metric을 확인한 후 Staging으로 등록합니다.

```powershell
.\.venv\Scripts\python.exe scripts\register_fab_candidate.py `
  --artifact data\output\<candidate-version>.joblib --process cmp `
  --precision <value> --recall <value> --f2 <value> --false-positive-rate <value>
```

Production 승격은 기존 MLOps 화면/수동 API에서만 수행합니다.

## 7. 종료

```powershell
docker compose stop postgres mosquitto
```

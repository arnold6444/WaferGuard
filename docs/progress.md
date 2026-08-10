# 진행 기록

## 2026-07-18 — Chamber Resistance AI MVP 완료

- [x] Colab 알고리즘과 샘플 데이터 구조 확인
- [x] 샘플 3,000행 분석 결과 재현
- [x] `챔버 이상탐지 / Chamber AI` 메뉴 추가
- [x] 정상 패턴, EQP 날짜 추세, 이상 맵, 이상 점수, 판정표 구현
- [x] EQP 선택 상호작용 구현
- [x] 프론트엔드 production build 성공
- [x] 브라우저 내용 렌더링, 오류 오버레이 없음, EQP55 선택 반영 확인

검증된 샘플 결과:

- OOF MAE: 0.527 Ω
- OOF RMSE: 0.618 Ω
- 행 이상 임계값: 1.097 Ω
- 최종 이상 후보: EQP10, EQP55
- 이상 행: 48 / 3,000 (1.6%)

다음 단계 후보:

- 사용자가 업로드한 CSV를 서버에서 분석하는 API
- 실제 장비 데이터 수집 주기 및 알림 규칙 연결
- 엔지니어 승인 결과를 WaferGuard RAG/Inspection Agent 흐름과 연결

## 2026-08-02 — wafer_particle Vision AI 병합 완료

- [x] 기존 저항 분석 기능·기본 탭 유지
- [x] 통계 / 정상 전용 AI / 비교 3개 영상 판정 모드 추가
- [x] 100개 챔버 상태판·TOP 3·30일 추세·시점·방향 구현
- [x] CH-061·047·007 원본·통계 히트맵·AI 히트맵 포함
- [x] 선택한 영상 근거를 `POST /api/v1/inspect`와 Inspection Agent에 연결
- [x] Vision AI 전용 판정 근거와 샘플 해석 경계 추가
- [x] 프론트엔드 production build 성공
- [x] Python 스키마·판정 룰 검증 성공
- [x] 실제 브라우저에서 모드 전환, CH-047 선택, 5개 이미지 로딩 확인
- [x] CH-047이 `WF-CH047-D09` 검사 건으로 등록되고 기존 Inspection Agent 화면이 열리는 것 확인

검증된 `wafer_particle` 샘플 결과:

- 3,000 images / 100 chambers / 300×300 px
- 이상 챔버: CH-061, CH-047, CH-007
- 이상 이미지: 51장
- 통계·AI 판정 일치: 3,000 / 3,000
- 해석 경계: 고유 정상 1종·불량 3종이 반복된 과제 샘플이며 실제 팹 성능을 의미하지 않음

## 2026-08-10 — main 코드/문서 동기화

현재 `main` 브랜치의 실제 구현을 다시 확인하고 README 및 Chamber AI 사양을 코드 기준으로 정리했습니다.

- [x] Chamber Resistance/Vision AI가 **런타임 실시간 모델이 아니라 정적 분석 스냅샷을 표시하는 구조**임을 명확히 문서화
- [x] Vision 모드 전환이 모델 재추론이 아니라 미리 생성된 statistical/AI 결과의 표시 기준 전환임을 반영
- [x] `vision_*` 전용 필드가 `POST /api/v1/inspect` → `process_context.vision_evidence` → Action Card rule로 전달되는 현재 흐름 반영
- [x] 엔지니어 review는 `approved`/`false_alarm` 확정 사례만 RAG knowledge로 저장하는 실제 로직 반영
- [x] 저장소가 local SQLite/local object storage와 PostgreSQL(RDS)/S3를 환경변수로 전환하는 dual-backend 구조임을 반영
- [x] README의 잘못된 `app/schemas.py` 경로를 실제 `app/services/schemas.py`로 수정
- [x] `/health` 실제 응답 `{"status":"ok","service":"waferguard-api"}` 반영
- [x] `scripts/smoke_test.py`가 실행 중인 서버가 아니라 FastAPI `TestClient`를 직접 사용하는 구조임을 반영
- [x] `build_chamber_dashboard_data.py` 재생성 시 필요한 optional dependency(`pandas`, `scikit-learn`) 명시
- [x] 현재 frontend가 React 19 + Vite 7을 사용하므로 Node.js 요구사항을 Vite 7 기준으로 수정

현재 문서 기준 구현 경계:

- Resistance AI: CSV → offline analysis script → `chamberSample.json` → React visualization
- Vision AI: external analyzer → snapshot builder → `waferVisionSample.json`/대표 이미지 → React visualization
- Vision → Inspection Agent API handoff: 구현됨
- 실제 설비 streaming / production threshold / 자동 장비 제어: 미구현
- MLOps retrain/promote/rollback: workflow 검증용 simulation

## 2026-08-10 — Multivariate Chamber Resistance Live/MLOps 구현

- [x] recipe/setpoint, 장비 bias, cleaning/seasoning, 연속 시계열을 가진 `EtchTelemetryGenerator` 구현
- [x] `idle`/`running`/`cleaning`/`maintenance`/`alarm` 및 8종 synthetic anomaly injection 구현
- [x] `chamber_telemetry`, `chamber_predictions`, `chamber_model_registry`와 조회 index 추가
- [x] numeric/categorical shared feature transform, unknown-safe OneHotEncoder, GradientBoosting 실제 `.fit()` 구현
- [x] time-based holdout의 MAE/RMSE, USE_TIME-only baseline, Production 동일 holdout 비교 구현
- [x] robust residual threshold, feature importance, joblib pipeline artifact 저장/재검증 구현
- [x] warm-up bootstrap → Production reload → Actual/Expected/residual/anomaly 저장 구현
- [x] 최근 median error + 신규 clean row readiness, `force=true` demo override, Staging candidate 구현
- [x] artifact 검증, 기존 Production Archived, 선택 버전 Production promotion 구현
- [x] Chamber 전용 status/equipment/telemetry/predictions/models/retrain/promote API 추가
- [x] 기존 Static Demo를 유지하면서 2초 polling Live dashboard, 공정 delta, model/importance UI 추가
- [x] generator·cleaning·gas drift·baseline 비교·RF 원인 anomaly·model lifecycle 테스트 추가

구현 경계:

- simulator range/계수/feature importance는 실제 Fab calibration 값이 아님
- 실제 설비 연결과 production alarm/control limit는 범위 밖
- 기존 wafer MLOps simulation과 Chamber 실제 sklearn registry/lifecycle은 분리

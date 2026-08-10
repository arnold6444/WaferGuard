# Chamber AI 통합 사양

## MVP

- `챔버 이상탐지 / Chamber AI`를 WaferGuard의 첫 메뉴로 제공한다.
- 샘플 분석 범위, OOF MAE, 행 이상 임계값, 이상 EQP 수를 요약한다.
- 정상 RESISTANCE 패턴: 실제 중앙값/IQR과 정상 재학습 모델 예측값을 표시한다.
- EQP 날짜 추세: 실제값, 정상 예측값, 행 이상 시점을 표시한다.
- EQP 이상 맵: Median error와 Q95 error, MAD 경계를 함께 표시한다.
- 이상 점수 순위와 상위 12개 EQP 판정표를 제공한다.
- EQP10/EQP55 및 상위 EQP 선택 시 추세 화면이 즉시 바뀐다.

## Wafer Vision AI 병합

- 기존 저항 분석을 기본 탭으로 유지하고, 같은 `Chamber AI` 안에 `웨이퍼 영상 분석` 탭을 추가한다.
- 통계, 정상 전용 AI, 두 방식 비교 모드를 사용자가 선택한다.
- 100개 챔버 상태판, 이상 순위, 최초 발생 시점·방향, 30일 점수 추세를 표시한다.
- 선택 챔버의 원본, 통계 히트맵, AI 히트맵과 전체 누적 위치를 표시한다.
- 영상 점수·시점·방향·면적을 전용 필드로 `POST /api/v1/inspect`에 전달하고 기존 Inspection Agent 화면을 연다.
- 영상 통계 2.0 이상과 AI 50.0 이상이 동시 발생하면 Vision AI Critical 근거로 저장하되, 샘플 임계값임을 Action Card에 명시한다.

## 데이터·판정 기준

- 모델: `GradientBoostingRegressor(loss="huber")`
- 검증: EQP 그룹 단위 5-fold OOF
- 1차 후보: EQP별 Median 또는 Q95 절대오차가 `중앙값 + 3.0 × scaled MAD` 초과
- 정상 모델: 1차 후보를 제외한 EQP로 재학습
- 행 이상: 정상 후보 OOF 절대오차의 상위 1% 초과
- 최종 EQP 이상: 정상 EQP 분포의 Median 또는 Q95 경계 초과

## MVP 밖

- 브라우저에서 임의 CSV를 업로드해 모델을 다시 학습하는 기능
- 실제 장비 스트리밍 및 알람 전송
- 영상만으로 파티클·물리 원인을 확정하는 기능
- `wafer_particle` 전체 서버를 WaferGuard 런타임에 복제하거나 자동 재학습하는 기능
- 팹 성능, 수율 개선 효과 또는 생산 적용 성능 주장

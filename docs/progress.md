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

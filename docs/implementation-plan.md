# Chamber Resistance AI 구현 계획

1. Colab 코드와 `sample.csv` 구조·결과를 확인한다.
2. Colab의 전처리·OOF·MAD·정상 재학습·최종 판정을 재현한다.
3. 브라우저에서 바로 읽을 수 있는 샘플 분석 JSON을 만든다.
4. 기존 React/Vite 대시보드에 Chamber AI 화면과 메뉴를 추가한다.
5. 정상 패턴, EQP 추세, 이상 맵, 순위, 판정표를 Recharts로 구현한다.
6. 빌드 후 브라우저에서 렌더링과 EQP 선택 동작을 확인한다.

## 2026-08-02 — Wafer Vision AI 병합

1. `wafer_particle` 로컬 API의 통계·AI·비교 결과와 산출물 라우트를 확인한다.
2. 100개 챔버 요약, 상위 3개 상세, 30일 추세와 대표 산출물을 정적 페이로드로 생성한다.
3. 기존 `ChamberView` 안에 저항·영상 하위 탭을 구성해 기존 기능을 유지한다.
4. 통계·AI·비교 선택, 챔버 상태판, 순위, 추세, 영상 근거를 구현한다.
5. 영상 근거 필드와 판정 룰을 FastAPI 검사 파이프라인에 연결한다.
6. 빌드·Python 룰 테스트·실제 브라우저·Inspection Agent 전달을 확인한다.

# Chamber Resistance AI 요구사항 정리

## 목표

- 기존 WaferGuard 대시보드에 Colab의 `USE TIME → 정상 RESISTANCE 예측 → 이상탐지` 흐름을 추가한다.
- 제공된 `sample.csv`를 데모 데이터로 사용한다.
- 모델 결과를 숫자 표에만 두지 않고 정상 패턴, 이상 설비, 이상 시점을 운영 화면에서 바로 확인할 수 있게 한다.

## 확인된 입력

- 필수 컬럼: `DATE`, `EQP`, `USE TIME`, `RESISTANCE`
- 샘플 범위: 2025-11-01 ~ 2025-11-30, 100 EQP, 3,000행
- Colab 기준: Gradient Boosting, EQP 그룹 5-fold OOF, MAD 기반 EQP 판정, 정상 오차 상위 1% 행 판정

## 화면 우선순위

1. 샘플 분석임을 명확히 표시한다.
2. 전체 정상 RESISTANCE 패턴과 모델 예측을 첫 화면에서 보여준다.
3. 이상 후보 EQP와 근거 수치를 바로 비교한다.
4. EQP를 선택하면 날짜별 실제값과 정상 예측값을 함께 보여준다.

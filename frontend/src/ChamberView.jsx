import React, { useMemo, useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import chamberSample from "./data/chamberSample.json";
import { Icon, Metric, Panel, SubTabs } from "./lib";
import WaferVisionView from "./WaferVisionView";


const CHART_COLORS = {
  accent: "#1B8FAE",
  actual: "#47677D",
  high: "#CC3A2C",
  med: "#B9791A",
  low: "#2E9A5C",
  muted: "#AAB7C6",
  band: "rgba(27, 143, 174, 0.16)",
};


function ChartTooltip({ active, payload, label, unit = " Ω" }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chamber-tooltip">
      <div className="mono chamber-tooltip-label">{label}</div>
      {payload
        .filter(item => item.value != null && item.name !== "band")
        .map(item => (
          <div key={item.dataKey || item.name} className="chamber-tooltip-row">
            <span className="chamber-tooltip-dot" style={{ background: item.color }} />
            <span>{item.name}</span>
            <strong className="mono">{Array.isArray(item.value) ? item.value.join(" – ") : item.value}{unit}</strong>
          </div>
        ))}
    </div>
  );
}


function StatCard({ label, value, unit, sub, icon, tone = "accent" }) {
  const colors = {
    accent: "var(--accent)",
    high: "var(--high)",
    low: "var(--low)",
    med: "var(--med)",
  };
  return (
    <div className="panel chamber-stat">
      <span className="chamber-stat-icon" style={{ color: colors[tone], background: `var(--${tone}-dim, var(--accent-dim))` }}>
        <Icon name={icon} size={16} />
      </span>
      <Metric label={label} value={value} unit={unit} sub={sub} accent={colors[tone]} />
    </div>
  );
}


function AnomalyBadge({ anomaly }) {
  return anomaly ? (
    <span className="chamber-status chamber-status-high"><span />이상 후보</span>
  ) : (
    <span className="chamber-status chamber-status-low"><span />정상 범위</span>
  );
}


function DetectionCard({ equipment, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`chamber-alert-card focusable${selected ? " is-selected" : ""}`}
      onClick={() => onSelect(equipment.eqp)}
      aria-pressed={selected}
    >
      <div className="chamber-alert-card-head">
        <div>
          <span className="mono chamber-alert-eqp">{equipment.eqp.toUpperCase()}</span>
          <span className="chamber-alert-kicker">robust score</span>
        </div>
        <span className="mono chamber-score">{equipment.score.toFixed(2)}</span>
      </div>
      <div className="chamber-alert-bar"><span style={{ width: `${Math.min(100, equipment.score * 3.2)}%` }} /></div>
      <div className="chamber-alert-evidence">
        <span>행 이상 <strong>{equipment.anomalyRows}건</strong></span>
        <span>최장 연속 <strong>{equipment.consecutive}일</strong></span>
        <span>Q95 <strong>{equipment.q95Error.toFixed(3)} Ω</strong></span>
      </div>
    </button>
  );
}


function FlowStep({ index, title, detail, active }) {
  return (
    <div className={`chamber-flow-step${active ? " is-active" : ""}`}>
      <span className="mono chamber-flow-index">0{index}</span>
      <div>
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
    </div>
  );
}


function ResistanceAnalysis() {
  const [selectedEqp, setSelectedEqp] = useState(chamberSample.topEquipment[0].eqp);
  const selectedSummary = chamberSample.anomalyMap.find(item => item.eqp === selectedEqp) || chamberSample.topEquipment[0];
  const selectedSeries = chamberSample.series[selectedEqp] || [];
  const pattern = useMemo(
    () => chamberSample.pattern.map(item => ({ ...item, band: [item.q25, item.q75] })),
    [],
  );
  const anomalyPoints = chamberSample.anomalyMap.filter(item => item.anomaly);
  const normalPoints = chamberSample.anomalyMap.filter(item => !item.anomaly);
  const rankedBars = chamberSample.topEquipment.slice(0, 8).map(item => ({ ...item, label: item.eqp.toUpperCase() })).reverse();
  const anomalySeries = selectedSeries.filter(item => item.anomaly);

  return (
    <div className="chamber-page">
      <section className="panel chamber-hero">
        <div className="chamber-hero-copy">
          <div className="chamber-eyebrow"><span />CHAMBER RESISTANCE · SAMPLE ANALYSIS</div>
          <h2>USE TIME에 따른 정상 저항 패턴을 먼저 학습하고,<br />벗어나는 설비와 시점을 찾아냅니다.</h2>
          <p>
            코랩의 Gradient Boosting + EQP 그룹 OOF 검증 + MAD 기반 2단계 판정을 WaferGuard 운영 화면으로 옮겼습니다.
            현재 결과는 <strong>sample.csv 3,000행</strong>을 분석한 데모이며 실제 팹 성능을 의미하지 않습니다.
          </p>
          <div className="chamber-hero-meta">
            <span><Icon name="file" size={13} />sample.csv</span>
            <span><Icon name="history" size={13} />{chamberSample.source.period}</span>
            <span><Icon name="layers" size={13} />{chamberSample.source.days}일 × {chamberSample.source.equipment} EQP</span>
          </div>
        </div>
        <div className="chamber-hero-signal" aria-label="분석 결과 요약">
          <div className="chamber-signal-ring">
            <span className="mono">{chamberSample.summary.anomalyEquipment}</span>
            <small>ANOMALY<br />CANDIDATES</small>
          </div>
          <div>
            <AnomalyBadge anomaly />
            <p><strong>EQP10 · EQP55</strong><br />정상 패턴 이탈이 반복됐습니다.</p>
          </div>
        </div>
      </section>

      <div className="chamber-stats">
        <StatCard label="분석 설비" value={chamberSample.source.equipment} unit="EQP" sub={`${chamberSample.source.rows.toLocaleString()} measurements`} icon="layers" />
        <StatCard label="이상 후보" value={chamberSample.summary.anomalyEquipment} unit="EQP" sub={`${chamberSample.summary.normalEquipment} EQP 정상 범위`} icon="alert" tone="high" />
        <StatCard label="OOF 모델 오차" value={chamberSample.model.mae.toFixed(3)} unit="Ω MAE" sub={`${chamberSample.model.validation}`} icon="gauge" tone="low" />
        <StatCard label="행 이상 임계값" value={chamberSample.thresholds.rowError.toFixed(3)} unit="Ω" sub={`정상 오차 상위 ${(chamberSample.thresholds.rowAlpha * 100).toFixed(0)}% 기준`} icon="activity" tone="med" />
      </div>

      <div className="chamber-grid chamber-grid-primary">
        <Panel
          title="정상 RESISTANCE 패턴"
          icon="activity"
          right={<span className="chip">USE TIME 20–1,000 h</span>}
          className="chamber-pattern-panel"
        >
          <div className="chamber-panel-copy">
            청록선은 이상 후보를 제외하고 다시 학습한 정상 예측값이며, 음영은 샘플 전체의 실제 IQR입니다.
          </div>
          <div className="chamber-chart chamber-chart-lg">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={pattern} margin={{ top: 12, right: 10, bottom: 2, left: -8 }}>
                <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="useTime" type="number" domain={[0, 1000]} tickCount={6} tick={{ fontSize: 10, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="h" />
                <YAxis domain={[80, 96]} tick={{ fontSize: 10, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="Ω" />
                <Tooltip content={<ChartTooltip label="USE TIME" />} />
                <Legend iconType="line" wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
                <Area type="monotone" dataKey="band" name="실제 IQR" stroke="none" fill={CHART_COLORS.band} activeDot={false} />
                <Line type="monotone" dataKey="median" name="실제 중앙값" stroke={CHART_COLORS.actual} strokeWidth={1.6} dot={false} />
                <Line type="monotone" dataKey="expected" name="정상 예측" stroke={CHART_COLORS.accent} strokeWidth={2.8} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="탐지 판정" icon="alert" right={<span className="chip chamber-chip-high">2 EQUIPMENT</span>}>
          <div className="chamber-detection-stack">
            {chamberSample.topEquipment.slice(0, 2).map(item => (
              <DetectionCard key={item.eqp} equipment={item} selected={selectedEqp === item.eqp} onSelect={setSelectedEqp} />
            ))}
          </div>
          <div className="divider" style={{ margin: "15px 0 13px" }} />
          <div className="chamber-threshold-grid">
            <div><span>Median error</span><strong className="mono">{chamberSample.thresholds.medianError.toFixed(3)} Ω</strong></div>
            <div><span>Q95 error</span><strong className="mono">{chamberSample.thresholds.q95Error.toFixed(3)} Ω</strong></div>
            <div><span>Robust MAD k</span><strong className="mono">{chamberSample.thresholds.madK.toFixed(1)}</strong></div>
          </div>
        </Panel>
      </div>

      <div className="chamber-grid chamber-grid-secondary">
        <Panel
          title={`${selectedEqp.toUpperCase()} · 날짜별 실제값 vs 정상 예측`}
          icon="pulse"
          right={<AnomalyBadge anomaly={selectedSummary.anomaly} />}
        >
          <div className="chamber-equipment-tabs" role="list" aria-label="설비 선택">
            {chamberSample.topEquipment.slice(0, 8).map(item => (
              <button
                type="button"
                key={item.eqp}
                className={`focusable${selectedEqp === item.eqp ? " is-active" : ""}${item.anomaly ? " is-anomaly" : ""}`}
                onClick={() => setSelectedEqp(item.eqp)}
              >
                {item.eqp.toUpperCase()}
              </button>
            ))}
          </div>
          <div className="chamber-chart chamber-chart-md">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={selectedSeries} margin={{ top: 10, right: 10, bottom: 0, left: -7 }}>
                <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 9.5, fill: "var(--text-3)" }} axisLine={false} tickLine={false} interval={4} />
                <YAxis domain={["dataMin - 1", "dataMax + 1"]} tick={{ fontSize: 10, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="Ω" />
                <Tooltip content={<ChartTooltip />} />
                <Legend iconType="line" wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="expected" name="정상 예측" stroke={CHART_COLORS.accent} strokeWidth={2.4} dot={false} />
                <Line type="monotone" dataKey="actual" name="실제 측정" stroke={CHART_COLORS.actual} strokeWidth={1.6} dot={{ r: 2, fill: CHART_COLORS.actual }} />
                <Scatter data={anomalySeries} dataKey="actual" name="이상 시점" fill={CHART_COLORS.high} shape="circle" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="chamber-selected-summary">
            <span>행 이상 <strong className="mono">{selectedSummary.anomalyRows} / 30</strong></span>
            <span>최장 연속 <strong className="mono">{selectedSummary.consecutive}일</strong></span>
            <span>Median error <strong className="mono">{selectedSummary.medianError.toFixed(3)} Ω</strong></span>
            <span>Q95 error <strong className="mono">{selectedSummary.q95Error.toFixed(3)} Ω</strong></span>
          </div>
        </Panel>

        <Panel title="EQP 이상 맵" icon="gauge" right={<span className="chip">Median × Q95</span>}>
          <div className="chamber-panel-copy">
            점선 오른쪽 또는 위쪽은 정상 EQP 분포에서 MAD 3.0배 이상 벗어난 영역입니다.
          </div>
          <div className="chamber-chart chamber-chart-md">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 10, right: 12, bottom: 8, left: -8 }}>
                <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" />
                <XAxis type="number" dataKey="medianError" name="Median error" domain={[0.3, 0.95]} tick={{ fontSize: 9.5, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="Ω" />
                <YAxis type="number" dataKey="q95Error" name="Q95 error" domain={[0.7, 2.8]} tick={{ fontSize: 9.5, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="Ω" />
                <ReferenceLine x={chamberSample.thresholds.medianError} stroke={CHART_COLORS.med} strokeDasharray="5 4" />
                <ReferenceLine y={chamberSample.thresholds.q95Error} stroke={CHART_COLORS.med} strokeDasharray="5 4" />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }) => {
                  const item = payload?.[0]?.payload;
                  if (!active || !item) return null;
                  return (
                    <div className="chamber-tooltip">
                      <div className="mono chamber-tooltip-label">{item.eqp.toUpperCase()}</div>
                      <div className="chamber-tooltip-row"><span>Median error</span><strong className="mono">{item.medianError.toFixed(3)} Ω</strong></div>
                      <div className="chamber-tooltip-row"><span>Q95 error</span><strong className="mono">{item.q95Error.toFixed(3)} Ω</strong></div>
                      <div className="chamber-tooltip-row"><span>Score</span><strong className="mono">{item.score.toFixed(2)}</strong></div>
                    </div>
                  );
                }} />
                <Scatter name="정상 EQP" data={normalPoints} fill={CHART_COLORS.muted} opacity={0.72} />
                <Scatter name="이상 후보" data={anomalyPoints} fill={CHART_COLORS.high} />
                <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <div className="chamber-grid chamber-grid-tertiary">
        <Panel title="이상 점수 상위 EQP" icon="layers" right={<span className="chip">TOP 8</span>}>
          <div className="chamber-chart chamber-chart-rank">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rankedBars} layout="vertical" margin={{ top: 2, right: 20, bottom: 2, left: 2 }}>
                <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 9.5, fill: "var(--text-3)" }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="label" width={56} tick={{ fontSize: 10, fill: "var(--text-2)", fontFamily: "monospace" }} axisLine={false} tickLine={false} />
                <Tooltip content={<ChartTooltip unit="" />} />
                <ReferenceLine x={chamberSample.thresholds.madK} stroke={CHART_COLORS.med} strokeDasharray="5 4" label={{ value: "MAD 3.0", fill: CHART_COLORS.med, fontSize: 9 }} />
                <Bar dataKey="score" name="Robust score" radius={[0, 4, 4, 0]}>
                  {rankedBars.map(item => <Cell key={item.eqp} fill={item.anomaly ? CHART_COLORS.high : CHART_COLORS.accent} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="코랩 탐지 흐름" icon="cpu" right={<span className="chip">2-STAGE ROBUST</span>}>
          <div className="chamber-flow">
            <FlowStep index={1} title="정상 패턴 OOF 예측" detail="같은 EQP가 학습·검증에 섞이지 않도록 5개 설비 그룹으로 분리합니다." active />
            <FlowStep index={2} title="1차 이상 설비 제거" detail="EQP별 Median/Q95 오차가 MAD 3.0 경계를 넘는지 확인합니다." active />
            <FlowStep index={3} title="정상 후보로 재학습" detail="1차 후보를 제외한 98개 EQP로 정상 RESISTANCE 곡선을 다시 만듭니다." />
            <FlowStep index={4} title="행 + 설비 최종 판정" detail="상위 1% 행 오차와 EQP 지속 오차를 함께 보여줍니다." />
          </div>
        </Panel>
      </div>

      <Panel title="EQP 판정 테이블" icon="file" right={<span className="chip">SAMPLE · TOP 12</span>}>
        <div className="chamber-table-wrap">
          <table className="chamber-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>EQP</th>
                <th>판정</th>
                <th>Robust score</th>
                <th>Median error</th>
                <th>Q95 error</th>
                <th>이상 행</th>
                <th>연속 이상</th>
              </tr>
            </thead>
            <tbody>
              {chamberSample.topEquipment.map((item, index) => (
                <tr key={item.eqp} className={selectedEqp === item.eqp ? "is-selected" : ""} onClick={() => setSelectedEqp(item.eqp)}>
                  <td className="mono">{String(index + 1).padStart(2, "0")}</td>
                  <td><button type="button" className="chamber-eqp-link focusable" onClick={() => setSelectedEqp(item.eqp)}>{item.eqp.toUpperCase()}</button></td>
                  <td><AnomalyBadge anomaly={item.anomaly} /></td>
                  <td className="mono chamber-score-cell">{item.score.toFixed(2)}</td>
                  <td className="mono">{item.medianError.toFixed(3)} Ω</td>
                  <td className="mono">{item.q95Error.toFixed(3)} Ω</td>
                  <td className="mono">{item.anomalyRows} / 30</td>
                  <td className="mono">{item.consecutive}일</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}


export default function ChamberView({ onOpenInspection }) {
  const [analysisMode, setAnalysisMode] = useState("resistance");
  return (
    <>
      <SubTabs
        tabs={[
          { id: "resistance", icon: "activity", label: "저항 패턴 분석", en: "Resistance AI", badge: 2 },
          { id: "vision", icon: "zoom", label: "웨이퍼 영상 분석", en: "Vision AI", badge: 3 },
        ]}
        active={analysisMode}
        onChange={setAnalysisMode}
      />
      {analysisMode === "resistance"
        ? <ResistanceAnalysis />
        : <WaferVisionView onOpenInspection={onOpenInspection} />}
    </>
  );
}

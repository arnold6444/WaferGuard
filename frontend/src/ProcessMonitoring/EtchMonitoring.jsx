import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import ChamberView from "../ChamberView";
import { buildQuery } from "../fabApi";
import { Icon, Panel } from "../lib";
import { useUi } from "../UiContext";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

function MetricCard({ label, value, unit, detail, tone = "accent" }) {
  return (
    <div className="panel fab-metric-card">
      <span className={`source-dot tone-${tone}`} />
      <div className="label-cap">{label}</div>
      <div><strong className="mono">{value}</strong>{unit && <small>{unit}</small>}</div>
      <p>{detail}</p>
    </div>
  );
}

function SourceNotice() {
  const { text } = useUi();
  return <div className="source-notice"><Icon name="alert" size={13} />{text("Legacy Chamber stream · 위 FAB v2 Process Run과 별도 데이터 소스이며, 표시 단위와 범위는 실제 Fab calibration 값이 아닙니다.", "Legacy Chamber stream · This is a separate data source from the FAB v2 Process Run above; display units and ranges are not Fab-calibrated values.")}</div>;
}

function EmptyState({ error }) {
  const { text } = useUi();
  return <div className="panel process-empty"><Icon name="activity" size={22} /><strong>{error || text("Etch stream을 기다리고 있습니다.", "Waiting for the Etch stream.")}</strong><code>python scripts/run_chamber_stream.py --equipment-count 3 --interval 1</code></div>;
}

function EquipmentSelector({ equipment, selected, onSelect }) {
  return (
    <div className="panel process-equipment-strip">
      {equipment.map(item => (
        <button key={item.equipment_id} type="button" className={`focusable process-equipment-button ${selected === item.equipment_id ? "is-selected" : ""} ${item.is_anomaly ? "is-warning" : ""}`} onClick={() => onSelect(item.equipment_id)}>
          <span className={`status-dot status-${item.is_anomaly ? "warning" : item.machine_state === "running" ? "normal" : "offline"}`} />
          <strong className="mono">{item.equipment_id}</strong>
          <small>{item.machine_state || "unknown"}</small>
        </button>
      ))}
    </div>
  );
}

export default function EtchMonitoring({ section = "overview", filters, onEquipmentSelect }) {
  const { text } = useUi();
  const [data, setData] = useState({ status: null, equipment: [], predictions: [], detections: [], models: [], events: [] });
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showStatic, setShowStatic] = useState(false);

  const load = useCallback(async () => {
    try {
      const query = selected ? `?equipment_id=${encodeURIComponent(selected)}&limit=180` : "?limit=180";
      const eventQuery = buildQuery({ process_step: "Etch", equipment_id: filters?.equipment, recipe_id: filters?.recipe, limit: 40 });
      const responses = await Promise.all([
        fetch(`${API_BASE}/api/v1/chamber/status`),
        fetch(`${API_BASE}/api/v1/chamber/equipment`),
        fetch(`${API_BASE}/api/v1/chamber/predictions${query}`),
        fetch(`${API_BASE}/api/v1/chamber/detections?limit=360`),
        fetch(`${API_BASE}/api/v1/chamber/models`),
        fetch(`${API_BASE}/api/v1/process/events${eventQuery}`),
      ]);
      if (!responses.every(response => response.ok)) throw new Error(text("Etch API 응답을 확인해 주세요.", "Check the Etch API responses."));
      const [status, equipment, predictions, detections, models, events] = await Promise.all(responses.map(response => response.json()));
      setData({ status, equipment, predictions, detections, models, events });
      if (!selected && equipment.length) setSelected(equipment[0].equipment_id);
      setError("");
    } catch (nextError) {
      setError(nextError.message || text("Etch 데이터를 불러오지 못했습니다.", "Could not load Etch data."));
    }
  }, [filters?.equipment, filters?.recipe, selected, text]);

  useEffect(() => {
    if (filters?.equipment && filters.equipment !== "all") setSelected(filters.equipment);
  }, [filters?.equipment]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  const chart = useMemo(() => data.predictions.map(item => ({
    ...item,
    time: item.observed_at?.slice(11, 19),
    pressure: item.pressure_delta,
    sourceRf: item.source_rf_delta,
    biasRf: item.bias_rf_delta,
    gas: item.gas_flow_delta,
    temperature: item.temperature_delta,
  })), [data.predictions]);
  const anomalies = chart.filter(item => Boolean(item.is_anomaly));
  const latest = chart.at(-1);
  const current = data.equipment.find(item => item.equipment_id === selected);
  const production = data.status?.production_model;
  const importance = (production?.feature_importance || []).filter(item => item.importance > 0).slice(0, 8).reverse();

  async function retrain() {
    setMessage(text("재학습 조건을 확인하고 있습니다…", "Checking retraining conditions…"));
    const response = await fetch(`${API_BASE}/api/v1/chamber/retrain`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trigger_type: "manual", force: false }) });
    const body = await response.json();
    setMessage(response.ok ? text(`${body.candidate.version}이 Staging에 등록됐습니다.`, `${body.candidate.version} was registered in Staging.`) : text(`대기: ${body.detail?.reason || "조건 미충족"}`, `Waiting: ${body.detail?.reason || "conditions not met"}`));
    await load();
  }

  async function promote(version) {
    const response = await fetch(`${API_BASE}/api/v1/chamber/models/${encodeURIComponent(version)}/promote`, { method: "POST" });
    const body = await response.json();
    setMessage(response.ok ? text(`${body.version}이 Production으로 승격됐습니다.`, `${body.version} was promoted to Production.`) : text(`승격 실패: ${body.detail || "artifact 확인 필요"}`, `Promotion failed: ${body.detail || "check artifact"}`));
    await load();
  }

  function selectEquipment(equipmentId) {
    setSelected(equipmentId);
    onEquipmentSelect?.(equipmentId);
  }

  if (error && !data.equipment.length) return <><SourceNotice /><EmptyState error={error} /></>;

  const tooltipStyle = { background: "var(--panel)", borderColor: "var(--border)", color: "var(--text)" };

  return (
    <div className="process-section">
      <SourceNotice />
      {message && <div className="chamber-action-message">{message}</div>}

      {section === "overview" && (
        <>
          <section className="panel process-hero">
            <div>
              <div className="chamber-eyebrow"><span />ETCH · LEGACY CHAMBER RESISTANCE STREAM</div>
              <h2>{text("설비 상태와 residual 이상을 한 흐름에서 확인합니다.", "Monitor equipment condition and residual anomalies in one flow.")}</h2>
              <p>{text("기존 Chamber Resistance runtime과 MLOps 화면을 보존한 별도 telemetry 흐름입니다. 위 FAB v2 Run 상태와 같은 stream으로 해석하지 않습니다.", "This separate telemetry flow preserves the existing Chamber Resistance runtime and MLOps views. Do not interpret it as the same stream as the FAB v2 Run above.")}</p>
            </div>
            <span className={`chamber-status ${data.status?.state === "LIVE" ? "chamber-status-low" : "chamber-status-med"}`}><span />LEGACY {data.status?.state || "CONNECTING"}</span>
          </section>
          <div className="fab-metrics">
            <MetricCard label={text("설비", "Equipment")} value={data.equipment.length} unit={text("대", "tools")} detail={`${data.equipment.filter(item => item.is_anomaly).length} ${text("이상", "anomaly")}`} />
            <MetricCard label="Telemetry" value={(data.status?.telemetry_rows || 0).toLocaleString()} unit={text("행", "rows")} detail={`DQ valid ${data.status?.data_quality?.VALID || 0} · warning ${data.status?.data_quality?.WARNING || 0}`} tone="low" />
            <MetricCard label={text("예측", "Predictions")} value={(data.status?.prediction_rows || 0).toLocaleString()} unit={text("행", "rows")} detail={production?.version || text("워밍업", "warming up")} tone="med" />
            <MetricCard label={text("최근 이벤트", "Recent Events")} value={data.events.length} unit={text("건", "events")} detail="process_events" tone="high" />
          </div>
          <Panel title={text("최근 Etch 이상", "Recent Etch Anomalies")} icon="alert" right={<span className="chip">TEMPORAL SIGNALS</span>}>
            <div className="process-event-list">
              {data.events.slice(0, 6).map(event => <div key={event.id}><span className={`status-dot status-${event.severity === "critical" ? "critical" : "warning"}`} /><time className="mono">{event.observed_at?.slice(11, 19)}</time><strong className="mono">{event.equipment_id}</strong><span>{String(event.event_type).replaceAll("_", " ")}</span><small>{event.source}</small></div>)}
              {!data.events.length && <div className="process-list-empty">{text("아직 투영된 anomaly event가 없습니다.", "No projected anomaly events yet.")}</div>}
            </div>
          </Panel>
        </>
      )}

      {section === "equipment" && (
        <>
          <EquipmentSelector equipment={data.equipment} selected={selected} onSelect={selectEquipment} />
          <div className="process-equipment-detail">
            <Panel title={`${selected || "ETCH"} · ${text("설비 상태", "Equipment Condition")}`} icon="cpu" right={<span className={`chamber-status ${current?.is_anomaly ? "chamber-status-high" : "chamber-status-low"}`}><span />{current?.is_anomaly ? text("경고", "WARNING") : text("정상", "NORMAL")}</span>}>
              <div className="chamber-process-grid">
                <div><span>Recipe</span><strong className="mono">{current?.recipe_id || "—"}</strong><em>runtime</em></div>
                <div><span>{text("장비 상태", "Machine State")}</span><strong>{current?.machine_state || "—"}</strong><em>{current?.quality || "—"}</em></div>
                <div><span>{text("저항", "Resistance")}</span><strong>{current?.resistance == null ? "—" : Number(current.resistance).toFixed(3)}<small>Ω</small></strong><em>{text("실측", "Actual")}</em></div>
                <div><span>{text("예상값", "Expected")}</span><strong>{current?.expected_resistance == null ? "—" : Number(current.expected_resistance).toFixed(3)}<small>Ω</small></strong><em>{current?.model_version || "—"}</em></div>
                <div><span>Residual</span><strong>{current?.residual == null ? "—" : Number(current.residual).toFixed(3)}<small>Ω</small></strong><em>{text("후보 신호", "candidate signal")}</em></div>
                <div><span>{text("업데이트", "Updated")}</span><strong className="mono">{current?.observed_at?.slice(11, 19) || "—"}</strong><em>synthetic clock</em></div>
              </div>
            </Panel>
          </div>
        </>
      )}

      {section === "trend" && (
        <>
          <EquipmentSelector equipment={data.equipment} selected={selected} onSelect={selectEquipment} />
          <Panel title={`${selected || "ETCH"} · ${text("실측 vs 예상 저항", "Actual vs Expected Resistance")}`} icon="pulse" right={<span className="chip">2s POLLING</span>}>
            <div className="chamber-chart chamber-chart-lg"><ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: 220 }}><ComposedChart data={chart} margin={{ top: 10, right: 12, bottom: 0, left: -5 }}><CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} minTickGap={28} /><YAxis tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="Ω" /><Tooltip contentStyle={tooltipStyle} /><Legend wrapperStyle={{ fontSize: 10 }} /><Line type="monotone" dataKey="expected_resistance" name={text("예상", "Expected")} stroke="var(--chart-a)" strokeWidth={2.4} dot={false} /><Line type="monotone" dataKey="actual_resistance" name={text("실측", "Actual")} stroke="var(--chart-b)" strokeWidth={1.8} dot={false} /><Scatter data={anomalies} dataKey="actual_resistance" name={text("이상", "Anomaly")} fill="var(--high)" /></ComposedChart></ResponsiveContainer></div>
          </Panel>
          <Panel title={text("공정 파라미터 편차", "Process Parameter Deviation")} icon="activity" right={<span className="chip">SAME TIME AXIS</span>}>
            <div className="chamber-chart chamber-chart-md"><ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: 220 }}><ComposedChart data={chart} margin={{ top: 10, right: 12, bottom: 0, left: -5 }}><CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} minTickGap={28} /><YAxis tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} /><Tooltip contentStyle={tooltipStyle} /><Legend wrapperStyle={{ fontSize: 10 }} /><Line type="monotone" dataKey="pressure" name="Pressure Δ" stroke="var(--chart-purple)" dot={false} /><Line type="monotone" dataKey="sourceRf" name="Source RF Δ" stroke="var(--chart-a)" dot={false} /><Line type="monotone" dataKey="biasRf" name="Bias RF Δ" stroke="var(--chart-amber)" dot={false} /><Line type="monotone" dataKey="gas" name="Gas Δ" stroke="var(--chart-green)" dot={false} /><Line type="monotone" dataKey="temperature" name="Temp Δ" stroke="var(--chart-red)" dot={false} /></ComposedChart></ResponsiveContainer></div>
          </Panel>
        </>
      )}

      {section === "anomaly" && (
        <>
          <EquipmentSelector equipment={data.equipment} selected={selected} onSelect={selectEquipment} />
          <div className="fab-metrics">
            <MetricCard label={text("이상 지점", "Anomaly Points")} value={anomalies.length} unit={text("개", "points")} detail={selected || text("전체 설비", "all equipment")} tone="high" />
            <MetricCard label={text("최신 Residual", "Latest Residual")} value={latest?.residual == null ? "—" : Number(latest.residual).toFixed(3)} unit="Ω" detail={`${text("임계값", "threshold")} ${latest?.threshold == null ? "—" : Number(latest.threshold).toFixed(3)}`} tone="med" />
            <MetricCard label={text("원인 후보", "Candidate Cause")} value={latest?.is_anomaly ? text("검토", "Review") : text("정상", "Normal")} detail={text("setpoint 편차 근거", "setpoint deviation evidence")} />
            <MetricCard label={text("이벤트 윈도우", "Event Window")} value="30" unit="min" detail={text("Wafer 연관 조회", "Wafer correlation lookup")} tone="low" />
          </div>
          <Panel title={text("이상 이력", "Anomaly History")} icon="alert" right={<span className="chip">NOT CAUSAL</span>}>
            <div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>{text("시간", "Time")}</th><th>{text("설비", "Equipment")}</th><th>{text("신호", "Signal")}</th><th>{text("심각도", "Severity")}</th><th>Residual</th><th>{text("출처", "Source")}</th></tr></thead><tbody>{data.events.map(event => <tr key={event.id}><td className="mono">{event.observed_at}</td><td className="mono">{event.equipment_id}</td><td>{String(event.event_type).replaceAll("_", " ")}</td><td>{event.severity}</td><td className="mono">{event.metadata?.residual == null ? "—" : Number(event.metadata.residual).toFixed(3)}</td><td>{event.source}</td></tr>)}</tbody></table></div>
          </Panel>
        </>
      )}

      {section === "model" && (
        <>
          <div className="source-notice"><Icon name="activity" size={13} />{text("Primary · 실제 Chamber telemetry → DQ gate → Expected Resistance → detector → registry 흐름입니다.", "Primary · Live Chamber telemetry → DQ gate → Expected Resistance → detector → registry flow.")}</div>
          <div className="process-model-actions">
            <button type="button" className="btn btn-accent" onClick={retrain} disabled={!production}><Icon name="refresh" size={13} />{text("재학습 조건 확인", "Check Retraining")}</button>
            <button type="button" className="btn" onClick={() => setShowStatic(value => !value)}><Icon name="history" size={13} />{showStatic ? text("Static Demo 닫기", "Close Static Demo") : text("Static Resistance Demo", "Static Resistance Demo")}</button>
          </div>
          <div className="chamber-grid chamber-grid-secondary">
            <Panel title={text("Synthetic 데이터 feature importance", "Synthetic-data Feature Importance")} icon="layers" right={<span className="chip">TOP 8</span>}>
              <div className="chamber-chart chamber-chart-md"><ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: 220 }}><BarChart data={importance} layout="vertical" margin={{ top: 4, right: 20, bottom: 2, left: 2 }}><CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" horizontal={false} /><XAxis type="number" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} /><YAxis type="category" dataKey="feature" width={145} tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} /><Tooltip contentStyle={tooltipStyle} /><Bar dataKey="importance" fill="var(--chart-a)" radius={[0, 4, 4, 0]} /></BarChart></ResponsiveContainer></div>
            </Panel>
            <Panel title={text("Production 모델", "Production Model")} icon="gauge" right={<span className="chip">{production?.version || "WARMING UP"}</span>}>
              <div className="chamber-process-grid">
                <div><span>MAE</span><strong>{production ? Number(production.mae).toFixed(3) : "—"}<small>Ω</small></strong><em>future holdout</em></div>
                <div><span>RMSE</span><strong>{production ? Number(production.rmse).toFixed(3) : "—"}<small>Ω</small></strong><em>future holdout</em></div>
                <div><span>{text("임계값", "Threshold")}</span><strong>{production ? Number(production.threshold).toFixed(3) : "—"}<small>Ω</small></strong><em>robust residual</em></div>
                <div><span>Stage</span><strong>{production?.stage || "—"}</strong><em>manual promote</em></div>
                <div><span>{text("데이터 시작", "Data Start")}</span><strong className="mono">{production?.metadata?.data_time_range?.start?.slice(0, 19) || "—"}</strong><em>training range</em></div>
                <div><span>{text("데이터 종료", "Data End")}</span><strong className="mono">{production?.metadata?.data_time_range?.end?.slice(0, 19) || "—"}</strong><em>training range</em></div>
              </div>
            </Panel>
          </div>
          <div className="chamber-grid chamber-grid-secondary">
            <Panel title="Data Quality Gate" icon="alert" right={<span className="chip">VALID ONLY → MODEL</span>}>
              <div className="chamber-process-grid">{Object.entries(data.status?.data_quality || { VALID: 0, WARNING: 0, REJECT: 0 }).map(([key, value]) => <div key={key}><span>{key}</span><strong className="mono">{Number(value).toLocaleString()}</strong><em>{key === "VALID" ? "prediction / training" : "audit only"}</em></div>)}</div>
            </Panel>
            <Panel title={text("Detector 비교", "Detector Comparison")} icon="activity" right={<span className="chip">MAD PRIMARY + EWMA</span>}>
              <div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>Detector</th><th>{text("행", "Rows")}</th><th>{text("탐지", "Flags")}</th><th>{text("평균 점수", "Avg Score")}</th><th>{text("평균 임계값", "Avg Threshold")}</th></tr></thead><tbody>{(data.status?.detectors || []).map(detector => <tr key={detector.detector_name}><td>{detector.detector_name}</td><td>{detector.total}</td><td>{detector.anomaly_count}</td><td className="mono">{Number(detector.average_score || 0).toFixed(3)}</td><td className="mono">{Number(detector.average_threshold || 0).toFixed(3)}</td></tr>)}</tbody></table></div>
            </Panel>
          </div>
          <Panel title={text("Chamber 모델 lifecycle", "Chamber Model Lifecycle")} icon="gauge" right={<span className="chip">STAGING → PRODUCTION</span>}>
            <div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>Version</th><th>Stage</th><th>MAE</th><th>RMSE</th><th>{text("행", "Rows")}</th><th>{text("비교", "Comparison")}</th><th>{text("작업", "Action")}</th></tr></thead><tbody>{data.models.map(model => <tr key={model.version}><td className="mono">{model.version}</td><td>{model.stage}</td><td className="mono">{Number(model.mae).toFixed(3)}</td><td className="mono">{Number(model.rmse).toFixed(3)}</td><td>{model.training_rows}</td><td>{model.metadata?.passes_comparison === false ? text("Production 미달", "Below Production") : text("통과 / 기준", "Pass / Baseline")}</td><td>{model.stage === "Staging" ? <button type="button" className="btn btn-sm" onClick={() => promote(model.version)}>{text("승격", "Promote")}</button> : "—"}</td></tr>)}</tbody></table></div>
          </Panel>
          {production?.metadata?.group_metrics && <Panel title={text("Holdout 그룹 지표", "Holdout Group Metrics")} icon="layers" right={<span className="chip">EQUIPMENT / RECIPE / LOT</span>}><div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>Group</th><th>Key</th><th>{text("행", "Rows")}</th><th>MAE</th><th>RMSE</th></tr></thead><tbody>{Object.entries(production.metadata.group_metrics).flatMap(([group, values]) => Object.entries(values || {}).map(([key, metric]) => <tr key={`${group}-${key}`}><td>{group}</td><td className="mono">{key}</td><td>{metric.rows}</td><td className="mono">{Number(metric.mae).toFixed(3)}</td><td className="mono">{Number(metric.rmse).toFixed(3)}</td></tr>))}</tbody></table></div></Panel>}
          {showStatic && <div className="process-static-demo"><ChamberView initialMode="static" /></div>}
        </>
      )}
    </div>
  );
}

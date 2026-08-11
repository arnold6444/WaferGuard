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
import { Icon, Panel } from "../lib";

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
  return (
    <div className="source-notice">
      <Icon name="alert" size={13} />
      Synthetic runtime · 표시 단위, 범위, 중요도는 실제 Fab calibration 값이 아닙니다.
    </div>
  );
}

function EmptyState({ error }) {
  return (
    <div className="panel process-empty">
      <Icon name="activity" size={22} />
      <strong>{error || "Etch stream을 기다리고 있습니다."}</strong>
      <code>python scripts/run_chamber_stream.py --equipment-count 3 --interval 1</code>
    </div>
  );
}

function EquipmentSelector({ equipment, selected, onSelect }) {
  return (
    <div className="panel process-equipment-strip">
      {equipment.map(item => (
        <button
          key={item.equipment_id}
          type="button"
          className={`focusable process-equipment-button ${selected === item.equipment_id ? "is-selected" : ""} ${item.is_anomaly ? "is-warning" : ""}`}
          onClick={() => onSelect(item.equipment_id)}
        >
          <span className={`status-dot status-${item.is_anomaly ? "warning" : item.machine_state === "running" ? "normal" : "offline"}`} />
          <strong className="mono">{item.equipment_id}</strong>
          <small>{item.machine_state || "unknown"}</small>
        </button>
      ))}
    </div>
  );
}

export default function EtchMonitoring({ section = "overview" }) {
  const [data, setData] = useState({ status: null, equipment: [], predictions: [], detections: [], models: [], events: [] });
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showStatic, setShowStatic] = useState(false);

  const load = useCallback(async () => {
    try {
      const query = selected ? `?equipment_id=${encodeURIComponent(selected)}&limit=180` : "?limit=180";
      const responses = await Promise.all([
        fetch(`${API_BASE}/api/v1/chamber/status`),
        fetch(`${API_BASE}/api/v1/chamber/equipment`),
        fetch(`${API_BASE}/api/v1/chamber/predictions${query}`),
        fetch(`${API_BASE}/api/v1/chamber/detections?limit=360`),
        fetch(`${API_BASE}/api/v1/chamber/models`),
        fetch(`${API_BASE}/api/v1/process/events?process_step=Etch&limit=40`),
      ]);
      if (!responses.every(response => response.ok)) throw new Error("Etch API 응답을 확인해 주세요.");
      const [status, equipment, predictions, detections, models, events] = await Promise.all(responses.map(response => response.json()));
      setData({ status, equipment, predictions, detections, models, events });
      if (!selected && equipment.length) setSelected(equipment[0].equipment_id);
      setError("");
    } catch (nextError) {
      setError(nextError.message || "Etch 데이터를 불러오지 못했습니다.");
    }
  }, [selected]);

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
    setMessage("재학습 조건을 확인하고 있습니다…");
    const response = await fetch(`${API_BASE}/api/v1/chamber/retrain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_type: "manual", force: false }),
    });
    const body = await response.json();
    setMessage(response.ok ? `${body.candidate.version}이 Staging에 등록됐습니다.` : `대기: ${body.detail?.reason || "조건 미충족"}`);
    await load();
  }

  async function promote(version) {
    const response = await fetch(`${API_BASE}/api/v1/chamber/models/${encodeURIComponent(version)}/promote`, { method: "POST" });
    const body = await response.json();
    setMessage(response.ok ? `${body.version}이 Production으로 승격됐습니다.` : `승격 실패: ${body.detail || "artifact 확인 필요"}`);
    await load();
  }

  if (error && !data.equipment.length) return <><SourceNotice /><EmptyState error={error} /></>;

  return (
    <div className="process-section">
      <SourceNotice />
      {message && <div className="chamber-action-message">{message}</div>}

      {section === "overview" && (
        <>
          <section className="panel process-hero">
            <div>
              <div className="chamber-eyebrow"><span />ETCH · RESISTANCE / EQUIPMENT CONDITION</div>
              <h2>설비 상태와 residual 이상을 한 흐름에서 확인합니다.</h2>
              <p>기존 Chamber Resistance Live를 Process Monitoring의 첫 connected 구현으로 재사용합니다.</p>
            </div>
            <span className={`chamber-status ${data.status?.state === "LIVE" ? "chamber-status-low" : "chamber-status-med"}`}><span />{data.status?.state || "CONNECTING"}</span>
          </section>
          <div className="fab-metrics">
            <MetricCard label="Equipment" value={data.equipment.length} unit="tools" detail={`${data.equipment.filter(item => item.is_anomaly).length} anomaly`} />
            <MetricCard label="Telemetry" value={(data.status?.telemetry_rows || 0).toLocaleString()} unit="rows" detail={`DQ valid ${data.status?.data_quality?.VALID || 0} · warning ${data.status?.data_quality?.WARNING || 0}`} tone="low" />
            <MetricCard label="Predictions" value={(data.status?.prediction_rows || 0).toLocaleString()} unit="rows" detail={production?.version || "warming up"} tone="med" />
            <MetricCard label="Recent Events" value={data.events.length} unit="events" detail="process_events projection" tone="high" />
          </div>
          <Panel title="최근 Etch anomaly" icon="alert" right={<span className="chip">TEMPORAL SIGNALS</span>}>
            <div className="process-event-list">
              {data.events.slice(0, 6).map(event => (
                <div key={event.id}>
                  <span className={`status-dot status-${event.severity === "critical" ? "critical" : "warning"}`} />
                  <time className="mono">{event.observed_at?.slice(11, 19)}</time>
                  <strong className="mono">{event.equipment_id}</strong>
                  <span>{String(event.event_type).replaceAll("_", " ")}</span>
                  <small>{event.source}</small>
                </div>
              ))}
              {!data.events.length && <div className="process-list-empty">아직 투영된 anomaly event가 없습니다.</div>}
            </div>
          </Panel>
        </>
      )}

      {section === "equipment" && (
        <>
          <EquipmentSelector equipment={data.equipment} selected={selected} onSelect={setSelected} />
          <div className="process-equipment-detail">
            <Panel title={`${selected || "ETCH"} · Equipment Condition`} icon="cpu" right={<span className={`chamber-status ${current?.is_anomaly ? "chamber-status-high" : "chamber-status-low"}`}><span />{current?.is_anomaly ? "WARNING" : "NORMAL"}</span>}>
              <div className="chamber-process-grid">
                <div><span>Recipe</span><strong className="mono">{current?.recipe_id || "—"}</strong><em>runtime</em></div>
                <div><span>Machine State</span><strong>{current?.machine_state || "—"}</strong><em>{current?.quality || "—"}</em></div>
                <div><span>Resistance</span><strong>{current?.resistance == null ? "—" : Number(current.resistance).toFixed(3)}<small>Ω</small></strong><em>Actual</em></div>
                <div><span>Expected</span><strong>{current?.expected_resistance == null ? "—" : Number(current.expected_resistance).toFixed(3)}<small>Ω</small></strong><em>{current?.model_version || "—"}</em></div>
                <div><span>Residual</span><strong>{current?.residual == null ? "—" : Number(current.residual).toFixed(3)}<small>Ω</small></strong><em>candidate signal</em></div>
                <div><span>Updated</span><strong className="mono">{current?.observed_at?.slice(11, 19) || "—"}</strong><em>synthetic clock</em></div>
              </div>
            </Panel>
          </div>
        </>
      )}

      {section === "trend" && (
        <>
          <EquipmentSelector equipment={data.equipment} selected={selected} onSelect={setSelected} />
          <Panel title={`${selected || "ETCH"} · Actual vs Expected Resistance`} icon="pulse" right={<span className="chip">2초 POLLING</span>}>
            <div className="chamber-chart chamber-chart-lg">
              <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: 220 }}>
                <ComposedChart data={chart} margin={{ top: 10, right: 12, bottom: 0, left: -5 }}>
                  <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} minTickGap={28} />
                  <YAxis tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} unit="Ω" />
                  <Tooltip /><Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line type="monotone" dataKey="expected_resistance" name="Expected" stroke="#3f7fbf" strokeWidth={2.4} dot={false} />
                  <Line type="monotone" dataKey="actual_resistance" name="Actual" stroke="#172b4d" strokeWidth={1.8} dot={false} />
                  <Scatter data={anomalies} dataKey="actual_resistance" name="Anomaly" fill="var(--high)" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Panel>
          <Panel title="공정 parameter deviation" icon="activity" right={<span className="chip">SAME TIME AXIS</span>}>
            <div className="chamber-chart chamber-chart-md">
              <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: 220 }}>
                <ComposedChart data={chart} margin={{ top: 10, right: 12, bottom: 0, left: -5 }}>
                  <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} minTickGap={28} />
                  <YAxis tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} />
                  <Tooltip /><Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line type="monotone" dataKey="pressure" name="Pressure Δ" stroke="#7c5ac7" dot={false} />
                  <Line type="monotone" dataKey="sourceRf" name="Source RF Δ" stroke="#3f7fbf" dot={false} />
                  <Line type="monotone" dataKey="biasRf" name="Bias RF Δ" stroke="#d58b24" dot={false} />
                  <Line type="monotone" dataKey="gas" name="Gas Δ" stroke="#2e9a5c" dot={false} />
                  <Line type="monotone" dataKey="temperature" name="Temp Δ" stroke="#c54f4f" dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </>
      )}

      {section === "anomaly" && (
        <>
          <EquipmentSelector equipment={data.equipment} selected={selected} onSelect={setSelected} />
          <div className="fab-metrics">
            <MetricCard label="Anomaly Points" value={anomalies.length} unit="points" detail={`${selected || "all equipment"}`} tone="high" />
            <MetricCard label="Latest Residual" value={latest?.residual == null ? "—" : Number(latest.residual).toFixed(3)} unit="Ω" detail={`threshold ${latest?.threshold == null ? "—" : Number(latest.threshold).toFixed(3)}`} tone="med" />
            <MetricCard label="Candidate Cause" value={latest?.is_anomaly ? "Review" : "Normal"} detail="setpoint deviation evidence" />
            <MetricCard label="Event Window" value="30" unit="min" detail="Wafer correlation lookup" tone="low" />
          </div>
          <Panel title="Anomaly history" icon="alert" right={<span className="chip">NOT CAUSAL</span>}>
            <div className="chamber-table-wrap">
              <table className="chamber-table">
                <thead><tr><th>Time</th><th>Equipment</th><th>Signal</th><th>Severity</th><th>Residual</th><th>Source</th></tr></thead>
                <tbody>
                  {data.events.map(event => <tr key={event.id}><td className="mono">{event.observed_at}</td><td className="mono">{event.equipment_id}</td><td>{String(event.event_type).replaceAll("_", " ")}</td><td>{event.severity}</td><td className="mono">{event.metadata?.residual == null ? "—" : Number(event.metadata.residual).toFixed(3)}</td><td>{event.source}</td></tr>)}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}

      {section === "model" && (
        <>
          <div className="source-notice"><Icon name="activity" size={13} />Primary · 실제 Chamber telemetry → DQ gate → Expected Resistance → detector → registry 흐름입니다.</div>
          <div className="process-model-actions">
            <button type="button" className="btn btn-accent" onClick={retrain} disabled={!production}><Icon name="refresh" size={13} />재학습 조건 확인</button>
            <button type="button" className="btn" onClick={() => setShowStatic(value => !value)}><Icon name="history" size={13} />{showStatic ? "Static Demo 닫기" : "Static Resistance Demo"}</button>
          </div>
          <div className="chamber-grid chamber-grid-secondary">
            <Panel title="Synthetic-data feature importance" icon="layers" right={<span className="chip">TOP 8</span>}>
              <div className="chamber-chart chamber-chart-md">
                <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: 220 }}>
                  <BarChart data={importance} layout="vertical" margin={{ top: 4, right: 20, bottom: 2, left: 2 }}>
                    <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 9 }} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="feature" width={145} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} />
                    <Tooltip /><Bar dataKey="importance" fill="#3f7fbf" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
            <Panel title="Production model" icon="gauge" right={<span className="chip">{production?.version || "WARMING UP"}</span>}>
              <div className="chamber-process-grid">
                <div><span>MAE</span><strong>{production ? Number(production.mae).toFixed(3) : "—"}<small>Ω</small></strong><em>future holdout</em></div>
                <div><span>RMSE</span><strong>{production ? Number(production.rmse).toFixed(3) : "—"}<small>Ω</small></strong><em>future holdout</em></div>
                <div><span>Threshold</span><strong>{production ? Number(production.threshold).toFixed(3) : "—"}<small>Ω</small></strong><em>robust residual</em></div>
                <div><span>Stage</span><strong>{production?.stage || "—"}</strong><em>manual promote</em></div>
                <div><span>Data Start</span><strong className="mono">{production?.metadata?.data_time_range?.start?.slice(0, 19) || "—"}</strong><em>training range</em></div>
                <div><span>Data End</span><strong className="mono">{production?.metadata?.data_time_range?.end?.slice(0, 19) || "—"}</strong><em>training range</em></div>
              </div>
            </Panel>
          </div>
          <div className="chamber-grid chamber-grid-secondary">
            <Panel title="Data Quality Gate" icon="alert" right={<span className="chip">VALID ONLY → MODEL</span>}>
              <div className="chamber-process-grid">
                {Object.entries(data.status?.data_quality || { VALID: 0, WARNING: 0, REJECT: 0 }).map(([key, value]) => <div key={key}><span>{key}</span><strong className="mono">{Number(value).toLocaleString()}</strong><em>{key === "VALID" ? "prediction / training" : "audit only"}</em></div>)}
              </div>
            </Panel>
            <Panel title="Detector comparison" icon="activity" right={<span className="chip">MAD PRIMARY + EWMA</span>}>
              <div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>Detector</th><th>Rows</th><th>Flags</th><th>Avg Score</th><th>Avg Threshold</th></tr></thead><tbody>{(data.status?.detectors || []).map(detector => <tr key={detector.detector_name}><td>{detector.detector_name}</td><td>{detector.total}</td><td>{detector.anomaly_count}</td><td className="mono">{Number(detector.average_score || 0).toFixed(3)}</td><td className="mono">{Number(detector.average_threshold || 0).toFixed(3)}</td></tr>)}</tbody></table></div>
            </Panel>
          </div>
          <Panel title="Chamber model lifecycle" icon="gauge" right={<span className="chip">STAGING → PRODUCTION</span>}>
            <div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>Version</th><th>Stage</th><th>MAE</th><th>RMSE</th><th>Rows</th><th>Comparison</th><th>Action</th></tr></thead><tbody>{data.models.map(model => <tr key={model.version}><td className="mono">{model.version}</td><td>{model.stage}</td><td className="mono">{Number(model.mae).toFixed(3)}</td><td className="mono">{Number(model.rmse).toFixed(3)}</td><td>{model.training_rows}</td><td>{model.metadata?.passes_comparison === false ? "Below Production" : "Pass / Baseline"}</td><td>{model.stage === "Staging" ? <button type="button" className="btn btn-sm" onClick={() => promote(model.version)}>Promote</button> : "—"}</td></tr>)}</tbody></table></div>
          </Panel>
          {production?.metadata?.group_metrics && <Panel title="Holdout group metrics" icon="layers" right={<span className="chip">EQUIPMENT / RECIPE / LOT</span>}><div className="chamber-table-wrap"><table className="chamber-table"><thead><tr><th>Group</th><th>Key</th><th>Rows</th><th>MAE</th><th>RMSE</th></tr></thead><tbody>{Object.entries(production.metadata.group_metrics).flatMap(([group, values]) => Object.entries(values || {}).map(([key, metric]) => <tr key={`${group}-${key}`}><td>{group}</td><td className="mono">{key}</td><td>{metric.rows}</td><td className="mono">{Number(metric.mae).toFixed(3)}</td><td className="mono">{Number(metric.rmse).toFixed(3)}</td></tr>))}</tbody></table></div></Panel>}
          {showStatic && <div className="process-static-demo"><ChamberView initialMode="static" /></div>}
        </>
      )}
    </div>
  );
}

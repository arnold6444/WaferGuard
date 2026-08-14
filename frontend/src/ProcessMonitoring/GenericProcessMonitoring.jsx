import React, { useCallback, useEffect, useMemo, useState } from "react";

import { Icon, Panel } from "../lib";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const STEP_BY_PROCESS = {
  photo: "Lithography",
  deposition: "Deposition",
  cmp: "CMP",
};

function SourceNotice({ processId }) {
  return (
    <div className="source-notice">
      <Icon name="alert" size={13} />
      {processId.toUpperCase()} synthetic multimodal runtime · 생성 범위/결함/성능은 실제 Fab calibration 값이 아닙니다.
    </div>
  );
}

function EmptyState({ processId, error }) {
  return (
    <div className="panel process-empty">
      <Icon name="activity" size={22} />
      <strong>{error || `${processId} runtime event를 기다리고 있습니다.`}</strong>
      <code>python scripts/run_process_stream.py --process {processId} --modality both --samples 80 --interval 1</code>
    </div>
  );
}

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

function formatScore(value) {
  return value == null ? "—" : Number(value).toFixed(4);
}

function EventTable({ events }) {
  return (
    <div className="chamber-table-wrap">
      <table className="chamber-table">
        <thead><tr><th>Time</th><th>Equipment</th><th>Modality</th><th>Signal</th><th>Score / Threshold</th><th>Related tags</th><th>Model</th></tr></thead>
        <tbody>
          {events.map(event => (
            <tr key={event.id}>
              <td className="mono">{event.observed_at?.replace("T", " ")}</td>
              <td className="mono">{event.equipment_id}</td>
              <td>{event.metadata?.modality || "—"}</td>
              <td>{String(event.event_type || "").replaceAll("_", " ")}</td>
              <td className="mono">{formatScore(event.metadata?.anomaly_score)} / {formatScore(event.metadata?.threshold)}</td>
              <td>{(event.metadata?.related_tags || []).join(", ") || "—"}</td>
              <td className="mono">{event.metadata?.model_version || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function GenericProcessMonitoring({ profile, section = "overview" }) {
  const processId = profile.process_id;
  const processStep = STEP_BY_PROCESS[processId] || profile.display_name;
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/process/events?process_step=${encodeURIComponent(processStep)}&limit=160`);
      if (!response.ok) throw new Error(`${profile.display_name} process event API를 확인해 주세요.`);
      const body = await response.json();
      setEvents(body.filter(item => item.source === "process_multimodal_synthetic_runtime"));
      setError("");
    } catch (nextError) {
      setError(nextError.message || `${profile.display_name} runtime 데이터를 불러오지 못했습니다.`);
    }
  }, [processStep, profile.display_name]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  const stats = useMemo(() => {
    const byEquipment = new Map();
    let timeSeries = 0;
    let vision = 0;
    const versions = { timeseries: null, vision: null };
    for (const event of events) {
      const modality = event.metadata?.modality;
      if (modality === "timeseries") timeSeries += 1;
      if (modality === "vision") vision += 1;
      if (modality && !versions[modality]) versions[modality] = event.metadata?.model_version || null;
      const current = byEquipment.get(event.equipment_id) || { equipment_id: event.equipment_id, events: 0, critical: 0, latest: event.observed_at };
      current.events += 1;
      if (event.severity === "critical") current.critical += 1;
      if (String(event.observed_at) > String(current.latest)) current.latest = event.observed_at;
      byEquipment.set(event.equipment_id, current);
    }
    return { timeSeries, vision, versions, equipment: [...byEquipment.values()] };
  }, [events]);

  const latest = events[0];
  const params = profile.parameters || [];

  if (error && !events.length) return <><SourceNotice processId={processId} /><EmptyState processId={processId} error={error} /></>;

  return (
    <div className="process-section">
      <SourceNotice processId={processId} />

      {section === "overview" && (
        <>
          <section className="panel process-hero">
            <div>
              <div className="chamber-eyebrow"><span />{processId.toUpperCase()} · TIME-SERIES + VISION</div>
              <h2>{profile.display_name} 공정의 두 modality를 같은 anomaly event로 연결합니다.</h2>
              <p>정상 synthetic 데이터로 모델을 학습하고, 실시간 추론 결과를 PostgreSQL process_events에 투영해 이후 Inspection/RCA에서 재사용합니다.</p>
            </div>
            <span className={`chamber-status ${events.length ? "chamber-status-low" : "chamber-status-med"}`}><span />{events.length ? "CONNECTED" : "WAITING"}</span>
          </section>
          <div className="fab-metrics">
            <MetricCard label="Recent Events" value={events.length} unit="events" detail="PostgreSQL process_events" tone="high" />
            <MetricCard label="Time-series" value={stats.timeSeries} unit="alerts" detail={stats.versions.timeseries || "no anomaly event yet"} />
            <MetricCard label="Vision" value={stats.vision} unit="alerts" detail={stats.versions.vision || "no anomaly event yet"} tone="med" />
            <MetricCard label="Equipment" value={stats.equipment.length} unit="tools" detail="event-bearing equipment" tone="low" />
          </div>
          <Panel title={`${profile.display_name} parameter profile`} icon="layers" right={<span className="chip">SYNTHETIC PROFILE</span>}>
            <div className="process-profile-grid">
              {params.map(item => <div key={item.id}><span>{item.label}</span><strong className="mono">{item.unit}</strong><small>{item.normal_range}</small></div>)}
            </div>
          </Panel>
          <Panel title={`최근 ${profile.display_name} anomaly`} icon="alert" right={<span className="chip">RCA EVIDENCE</span>}>
            {events.length ? <EventTable events={events.slice(0, 8)} /> : <EmptyState processId={processId} />}
          </Panel>
        </>
      )}

      {section === "equipment" && (
        <Panel title={`${profile.display_name} equipment with detected events`} icon="cpu" right={<span className="chip">EVENT VIEW</span>}>
          <div className="process-profile-grid">
            {stats.equipment.map(item => <div key={item.equipment_id}><span className="mono">{item.equipment_id}</span><strong>{item.events} events</strong><small>{item.critical} critical · {item.latest?.slice(11, 19)}</small></div>)}
            {!stats.equipment.length && <EmptyState processId={processId} />}
          </div>
        </Panel>
      )}

      {section === "trend" && (
        <>
          <div className="fab-metrics">
            <MetricCard label="Latest Score" value={formatScore(latest?.metadata?.anomaly_score)} detail={`threshold ${formatScore(latest?.metadata?.threshold)}`} tone="high" />
            <MetricCard label="Modality" value={latest?.metadata?.modality || "—"} detail={latest?.event_type || "waiting"} />
            <MetricCard label="GT Injected" value={latest?.metadata?.ground_truth ? "YES" : "NO"} detail={latest?.metadata?.injected_anomaly || "model-only detection"} tone="med" />
            <MetricCard label="RCA tags" value={(latest?.metadata?.related_tags || []).length} unit="tags" detail={(latest?.metadata?.related_tags || []).join(", ") || "—"} tone="low" />
          </div>
          <Panel title="Detected score history" icon="pulse" right={<span className="chip">DETECTED EVENTS ONLY</span>}>
            {events.length ? <EventTable events={events.slice(0, 30)} /> : <EmptyState processId={processId} />}
          </Panel>
        </>
      )}

      {section === "anomaly" && (
        <Panel title={`${profile.display_name} multimodal anomaly history`} icon="alert" right={<span className="chip">TEMPORAL · NOT CAUSAL</span>}>
          {events.length ? <EventTable events={events} /> : <EmptyState processId={processId} />}
        </Panel>
      )}

      {section === "model" && (
        <>
          <div className="fab-metrics">
            <MetricCard label="Time-series Production" value={stats.versions.timeseries || "—"} detail="latest detected event model" />
            <MetricCard label="Vision Production" value={stats.versions.vision || "—"} detail="latest detected event model" tone="med" />
            <MetricCard label="Selection Metric" value="F2" detail="Recall weighted candidate selection" tone="high" />
            <MetricCard label="Lifecycle" value="Staging → Prod" detail="guarded promotion + rollback" tone="low" />
          </div>
          <Panel title="Model lifecycle commands" icon="box" right={<span className="chip">REAL ARTIFACT</span>}>
            <div className="process-empty">
              <strong>현재 generic runtime의 모델 작업은 CLI로 실행합니다.</strong>
              <code>python scripts/manage_process_models.py health --process {processId} --modality vision</code>
              <code>python scripts/manage_process_models.py retrain --process {processId} --modality vision --force</code>
              <code>python scripts/manage_process_models.py promote --process {processId} --modality vision --version {processId}-vision-v2</code>
              <small>승격 후 다음 추론부터 새 joblib Production artifact를 사용합니다. 실제 Fab 데이터가 연결되면 synthetic GT health 기준은 validation/drift 기준으로 교체해야 합니다.</small>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

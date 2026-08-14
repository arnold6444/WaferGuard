import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Icon, Panel } from "../lib";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const STEP_BY_PROCESS = {
  photo: "Lithography",
  deposition: "Deposition",
  cmp: "CMP",
};

const TAG_LABELS = {
  exposure_dose: "Exposure Dose",
  focus_offset: "Focus Offset",
  track_temperature: "Track Temp",
  developer_temperature: "Developer Temp",
  overlay_nm: "Overlay",
  chamber_pressure: "Chamber Pressure",
  substrate_temperature: "Substrate Temp",
  precursor_flow: "Precursor Flow",
  carrier_gas_flow: "Carrier Gas Flow",
  rf_power: "RF Power",
  deposition_time: "Deposition Time",
  down_force: "Down Force",
  platen_speed: "Platen Speed",
  carrier_speed: "Carrier Speed",
  slurry_flow: "Slurry Flow",
  motor_current: "Motor Current",
};

function assetUrl(value) {
  if (!value) return "";
  if (/^https?:\/\//.test(value)) return value;
  return `${API_BASE}${value.startsWith("/") ? value : `/${value}`}`;
}

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
      <strong>{error || `${processId} runtime 데이터를 기다리고 있습니다.`}</strong>
      <code>python scripts/run_process_stream.py --process {processId} --modality both --samples 300 --interval 1</code>
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

function formatValue(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  if (Math.abs(number) >= 100) return number.toFixed(1);
  if (Math.abs(number) >= 10) return number.toFixed(2);
  return number.toFixed(3);
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

function TagTrendGrid({ history }) {
  const series = useMemo(() => {
    const rows = history
      .filter(item => item.timeseries?.payload)
      .map(item => ({
        index: item.index,
        time: item.observed_at?.slice(11, 19),
        flag: Boolean(item.timeseries?.flag),
        ...item.timeseries.payload,
      }));
    const latest = rows.at(-1) || {};
    const keys = Object.keys(latest).filter(key => !["index", "time", "flag"].includes(key));
    return { rows, keys };
  }, [history]);

  if (!series.rows.length) return <div className="process-list-empty">시계열 stream을 기다리고 있습니다.</div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))", gap: 12 }}>
      {series.keys.map((tag, idx) => {
        const latest = series.rows.at(-1)?.[tag];
        return (
          <div key={tag} className="panel" style={{ padding: 12, minHeight: 190 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline", marginBottom: 8 }}>
              <strong>{TAG_LABELS[tag] || tag}</strong>
              <span className="mono">{formatValue(latest)}</span>
            </div>
            <div style={{ height: 145 }}>
              <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 260, height: 145 }}>
                <LineChart data={series.rows} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} minTickGap={25} />
                  <YAxis domain={["auto", "auto"]} tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} />
                  <Tooltip formatter={value => [formatValue(value), TAG_LABELS[tag] || tag]} />
                  <Line type="monotone" dataKey={tag} name={TAG_LABELS[tag] || tag} stroke={idx % 2 ? "#3f7fbf" : "#172b4d"} strokeWidth={1.8} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function VisionViewer({ history }) {
  const visionRows = useMemo(() => history.filter(item => item.vision?.image_url), [history]);
  const latest = visionRows.at(-1);
  if (!latest) return <div className="process-list-empty">Vision stream을 기다리고 있습니다.</div>;
  const vision = latest.vision;
  const recent = visionRows.slice(-8).reverse();

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 1.1fr) minmax(280px, 1fr)", gap: 16 }}>
      <div>
        <div style={{ display: "grid", gridTemplateColumns: vision.deviation_url ? "1fr 1fr" : "1fr", gap: 10 }}>
          <figure style={{ margin: 0 }}>
            <img src={assetUrl(vision.image_url)} alt={`${latest.wafer_id} synthetic wafer`} style={{ width: "100%", borderRadius: 10, display: "block", background: "#050a10" }} />
            <figcaption style={{ fontSize: 11, marginTop: 6, color: "var(--text-3)" }}>Synthetic Vision Input</figcaption>
          </figure>
          {vision.deviation_url && (
            <figure style={{ margin: 0 }}>
              <img src={assetUrl(vision.deviation_url)} alt={`${latest.wafer_id} deviation map`} style={{ width: "100%", borderRadius: 10, display: "block", background: "#050a10" }} />
              <figcaption style={{ fontSize: 11, marginTop: 6, color: "var(--text-3)" }}>Deviation Map · 모델 localization 아님</figcaption>
            </figure>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, marginTop: 10 }}>
          {recent.map(item => (
            <div key={`${item.index}-${item.wafer_id}`} style={{ opacity: item.vision.flag ? 1 : 0.72 }}>
              <img src={assetUrl(item.vision.image_url)} alt={item.wafer_id} style={{ width: "100%", display: "block", borderRadius: 6 }} />
              <small className="mono">{item.wafer_id}{item.vision.flag ? " · ALERT" : ""}</small>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="chamber-process-grid">
          <div><span>Wafer</span><strong className="mono">{latest.wafer_id}</strong><em>{latest.observed_at?.slice(11, 19)}</em></div>
          <div><span>Detection</span><strong>{vision.flag ? "ANOMALY" : "NORMAL"}</strong><em>{vision.ground_truth ? "GT injected" : "normal GT"}</em></div>
          <div><span>Score</span><strong className="mono">{formatScore(vision.score)}</strong><em>threshold {formatScore(vision.threshold)}</em></div>
          <div><span>Defect</span><strong>{vision.injected_anomaly || "—"}</strong><em>synthetic label</em></div>
          <div><span>Model</span><strong className="mono">{vision.model || "—"}</strong><em>Production artifact</em></div>
          <div><span>RCA Tags</span><strong>{vision.related_tags?.length || 0}</strong><em>{vision.related_tags?.join(", ") || "—"}</em></div>
        </div>
        <div className="source-notice" style={{ marginTop: 12 }}>
          <Icon name="alert" size={13} />
          현재 generic Vision 모델은 이미지 feature 기반 anomaly model입니다. 오른쪽 map은 입력의 공간적 deviation을 보여주는 진단용이며 모델 heatmap으로 해석하면 안 됩니다.
        </div>
      </div>
    </div>
  );
}

export default function GenericProcessMonitoring({ profile, section = "overview" }) {
  const processId = profile.process_id;
  const processStep = STEP_BY_PROCESS[processId] || profile.display_name;
  const [events, setEvents] = useState([]);
  const [live, setLive] = useState({ history: [], metrics: {} });
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [eventResponse, liveResponse] = await Promise.all([
        fetch(`${API_BASE}/api/v1/process/events?process_step=${encodeURIComponent(processStep)}&limit=160`, { cache: "no-store" }),
        fetch(`${API_BASE}/outputs/process_runtime/${processId}/live.json?t=${Date.now()}`, { cache: "no-store" }),
      ]);
      if (!eventResponse.ok) throw new Error(`${profile.display_name} process event API를 확인해 주세요.`);
      const body = await eventResponse.json();
      setEvents(body.filter(item => item.source === "process_multimodal_synthetic_runtime"));
      if (liveResponse.ok) setLive(await liveResponse.json());
      setError("");
    } catch (nextError) {
      setError(nextError.message || `${profile.display_name} runtime 데이터를 불러오지 못했습니다.`);
    }
  }, [processId, processStep, profile.display_name]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  const history = live.history || [];
  const latest = history.at(-1);
  const latestTs = [...history].reverse().find(item => item.timeseries)?.timeseries;
  const latestVision = [...history].reverse().find(item => item.vision)?.vision;
  const connected = history.length > 0;

  const stats = useMemo(() => {
    const byEquipment = new Map();
    let timeSeries = 0;
    let vision = 0;
    for (const event of events) {
      const modality = event.metadata?.modality;
      if (modality === "timeseries") timeSeries += 1;
      if (modality === "vision") vision += 1;
      const current = byEquipment.get(event.equipment_id) || { equipment_id: event.equipment_id, events: 0, critical: 0, latest: event.observed_at };
      current.events += 1;
      if (event.severity === "critical") current.critical += 1;
      if (String(event.observed_at) > String(current.latest)) current.latest = event.observed_at;
      byEquipment.set(event.equipment_id, current);
    }
    return { timeSeries, vision, equipment: [...byEquipment.values()] };
  }, [events]);

  if (error && !connected && !events.length) return <><SourceNotice processId={processId} /><EmptyState processId={processId} error={error} /></>;

  return (
    <div className="process-section">
      <SourceNotice processId={processId} />

      {section === "overview" && (
        <>
          <section className="panel process-hero">
            <div>
              <div className="chamber-eyebrow"><span />{processId.toUpperCase()} · LIVE TIME-SERIES + VISION</div>
              <h2>{profile.display_name} 공정의 원본 synthetic 데이터와 탐지 결과를 같이 봅니다.</h2>
              <p>stream → 실제 모델 artifact 추론 → PostgreSQL 저장과 동시에 최근 원본 Tag/Vision을 Dashboard에서 실시간 확인합니다.</p>
            </div>
            <span className={`chamber-status ${connected ? "chamber-status-low" : "chamber-status-med"}`}><span />{connected ? "CONNECTED" : "WAITING"}</span>
          </section>
          <div className="fab-metrics">
            <MetricCard label="Live Samples" value={history.length} unit="rows" detail={latest?.equipment_id || "stream waiting"} tone="low" />
            <MetricCard label="Time-series Model" value={latestTs?.model || "—"} detail={latestTs ? `${latestTs.flag ? "anomaly" : "normal"} · score ${formatScore(latestTs.score)}` : "no stream"} />
            <MetricCard label="Vision Model" value={latestVision?.model || "—"} detail={latestVision ? `${latestVision.flag ? "anomaly" : "normal"} · score ${formatScore(latestVision.score)}` : "no stream"} tone="med" />
            <MetricCard label="Detected Events" value={events.length} unit="events" detail={`${stats.timeSeries} time-series · ${stats.vision} vision`} tone="high" />
          </div>
          <Panel title={`${profile.display_name} · Live tag values`} icon="pulse" right={<span className="chip">2초 POLLING</span>}>
            <TagTrendGrid history={history} />
          </Panel>
          <Panel title={`${profile.display_name} · Live vision`} icon="layers" right={<span className="chip">INPUT + DEVIATION</span>}>
            <VisionViewer history={history} />
          </Panel>
        </>
      )}

      {section === "equipment" && (
        <>
          <div className="fab-metrics">
            <MetricCard label="Equipment" value={latest?.equipment_id || "—"} detail={latest?.lot_id || "waiting"} />
            <MetricCard label="Latest Wafer" value={latest?.wafer_id || "—"} detail={latest?.observed_at || "waiting"} tone="low" />
            <MetricCard label="TS State" value={latestTs?.flag ? "ANOMALY" : latestTs ? "NORMAL" : "—"} detail={latestTs?.injected_anomaly || "no injected anomaly"} tone={latestTs?.flag ? "high" : "low"} />
            <MetricCard label="Vision State" value={latestVision?.flag ? "ANOMALY" : latestVision ? "NORMAL" : "—"} detail={latestVision?.injected_anomaly || "no injected defect"} tone={latestVision?.flag ? "high" : "low"} />
          </div>
          <Panel title="Current telemetry values" icon="cpu" right={<span className="chip">RAW SYNTHETIC</span>}>
            <div className="process-profile-grid">
              {Object.entries(latestTs?.payload || {}).map(([tag, value]) => <div key={tag}><span>{TAG_LABELS[tag] || tag}</span><strong className="mono">{formatValue(value)}</strong><small>{tag}</small></div>)}
              {!latestTs && <EmptyState processId={processId} />}
            </div>
          </Panel>
        </>
      )}

      {section === "trend" && (
        <>
          <div className="fab-metrics">
            <MetricCard label="TS Score" value={formatScore(latestTs?.score)} detail={`threshold ${formatScore(latestTs?.threshold)}`} tone={latestTs?.flag ? "high" : "low"} />
            <MetricCard label="Vision Score" value={formatScore(latestVision?.score)} detail={`threshold ${formatScore(latestVision?.threshold)}`} tone={latestVision?.flag ? "high" : "med"} />
            <MetricCard label="Runtime Precision" value={live.metrics?.precision == null ? "—" : Number(live.metrics.precision).toFixed(3)} detail="synthetic injected GT" />
            <MetricCard label="Runtime Recall" value={live.metrics?.recall == null ? "—" : Number(live.metrics.recall).toFixed(3)} detail={`F2 ${live.metrics?.f2 == null ? "—" : Number(live.metrics.f2).toFixed(3)}`} tone="high" />
          </div>
          <Panel title={`${profile.display_name} · Tag trend`} icon="activity" right={<span className="chip">RAW VALUES</span>}>
            <TagTrendGrid history={history} />
          </Panel>
        </>
      )}

      {section === "anomaly" && (
        <>
          <Panel title={`${profile.display_name} · Vision inspection`} icon="layers" right={<span className="chip">LATEST INPUT</span>}>
            <VisionViewer history={history} />
          </Panel>
          <Panel title={`${profile.display_name} multimodal anomaly history`} icon="alert" right={<span className="chip">POSTGRESQL EVENTS</span>}>
            {events.length ? <EventTable events={events} /> : <div className="process-list-empty">현재 탐지된 anomaly event가 없습니다. 정상 데이터는 위 Live 화면에서 계속 확인할 수 있습니다.</div>}
          </Panel>
        </>
      )}

      {section === "model" && (
        <>
          <div className="fab-metrics">
            <MetricCard label="Time-series Production" value={latestTs?.model || "—"} detail="actual inference artifact" />
            <MetricCard label="Vision Production" value={latestVision?.model || "—"} detail="actual inference artifact" tone="med" />
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

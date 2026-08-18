import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Icon, Panel } from "../lib";
import { buildQuery } from "../fabApi";
import { useUi } from "../UiContext";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const STEP_BY_PROCESS = {
  photo: "Lithography",
  deposition: "Deposition",
  cmp: "CMP",
};

const TAG_LABELS = {
  exposure_dose: { ko: "노광량", en: "Exposure Dose" },
  focus_offset: { ko: "포커스 오프셋", en: "Focus Offset" },
  track_temperature: { ko: "트랙 온도", en: "Track Temperature" },
  developer_temperature: { ko: "현상 온도", en: "Developer Temperature" },
  overlay_nm: { ko: "오버레이", en: "Overlay" },
  chamber_pressure: { ko: "챔버 압력", en: "Chamber Pressure" },
  substrate_temperature: { ko: "기판 온도", en: "Substrate Temperature" },
  precursor_flow: { ko: "전구체 유량", en: "Precursor Flow" },
  carrier_gas_flow: { ko: "캐리어 가스 유량", en: "Carrier Gas Flow" },
  rf_power: { ko: "RF 파워", en: "RF Power" },
  deposition_time: { ko: "증착 시간", en: "Deposition Time" },
  down_force: { ko: "가압력", en: "Down Force" },
  platen_speed: { ko: "플래튼 속도", en: "Platen Speed" },
  carrier_speed: { ko: "캐리어 속도", en: "Carrier Speed" },
  slurry_flow: { ko: "슬러리 유량", en: "Slurry Flow" },
  motor_current: { ko: "모터 전류", en: "Motor Current" },
};

function assetUrl(value) {
  if (!value) return "";
  if (/^https?:\/\//.test(value)) return value;
  return `${API_BASE}${value.startsWith("/") ? value : `/${value}`}`;
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

function SourceNotice({ processId }) {
  const { text } = useUi();
  return (
    <div className="source-notice">
      <Icon name="alert" size={13} />
      {text(
        `${processId.toUpperCase()} synthetic 멀티모달 runtime · 생성 범위/결함/성능은 실제 Fab calibration 값이 아닙니다.`,
        `${processId.toUpperCase()} synthetic multimodal runtime · Generated ranges, defects, and metrics are not Fab-calibrated values.`,
      )}
    </div>
  );
}

function EmptyState({ processId, error }) {
  const { text } = useUi();
  return (
    <div className="panel process-empty">
      <Icon name="activity" size={22} />
      <strong>{error || text(`${processId} runtime 데이터를 기다리고 있습니다.`, `Waiting for ${processId} runtime data.`)}</strong>
      <code>python scripts/run_process_stream.py --process {processId} --modality both --samples 300 --interval 1</code>
    </div>
  );
}

function EventTable({ events }) {
  const { text } = useUi();
  return (
    <div className="chamber-table-wrap">
      <table className="chamber-table">
        <thead>
          <tr>
            <th>{text("시간", "Time")}</th>
            <th>{text("설비", "Equipment")}</th>
            <th>{text("모달리티", "Modality")}</th>
            <th>{text("신호", "Signal")}</th>
            <th>{text("점수 / 임계값", "Score / Threshold")}</th>
            <th>{text("관련 태그", "Related tags")}</th>
            <th>{text("모델", "Model")}</th>
          </tr>
        </thead>
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
  const { language } = useUi();
  const series = useMemo(() => {
    const rows = history
      .filter(item => item.timeseries?.payload)
      .map(item => ({
        index: item.index,
        time: item.observed_at?.slice(11, 19),
        phase: item.timeseries?.phase,
        flag: Boolean(item.timeseries?.flag),
        ...item.timeseries.payload,
      }));
    const latest = rows.at(-1) || {};
    const keys = Object.keys(latest).filter(key => !["index", "time", "phase", "flag"].includes(key));
    return { rows, keys };
  }, [history]);

  if (!series.rows.length) return <div className="process-list-empty">{language === "en" ? "Waiting for time-series stream." : "시계열 stream을 기다리고 있습니다."}</div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))", gap: 12 }}>
      {series.keys.map((tag, idx) => {
        const latest = series.rows.at(-1)?.[tag];
        const label = TAG_LABELS[tag]?.[language] || tag;
        return (
          <div key={tag} className="panel" style={{ padding: 12, minHeight: 190 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline", marginBottom: 8 }}>
              <strong>{label}</strong>
              <span className="mono">{formatValue(latest)}</span>
            </div>
            <div style={{ height: 145 }}>
              <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 260, height: 145 }}>
                <LineChart data={series.rows} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} minTickGap={25} />
                  <YAxis domain={["auto", "auto"]} tick={{ fontSize: 9, fill: "var(--text-3)" }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: "var(--panel)", borderColor: "var(--border)", color: "var(--text)" }} formatter={value => [formatValue(value), label]} />
                  <Line type="monotone" dataKey={tag} name={label} stroke={idx % 2 ? "var(--chart-b)" : "var(--chart-a)"} strokeWidth={1.8} dot={false} isAnimationActive={false} />
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
  const { text } = useUi();
  const visionRows = useMemo(() => history.filter(item => item.vision?.image_url), [history]);
  const latest = visionRows.at(-1);
  if (!latest) return <div className="process-list-empty">{text("Vision stream을 기다리고 있습니다.", "Waiting for vision stream.")}</div>;
  const vision = latest.vision;
  const recent = visionRows.slice(-8).reverse();
  const margin = Number(vision.margin ?? (vision.score - vision.threshold));

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 1.1fr) minmax(280px, 1fr)", gap: 16 }}>
      <div>
        <div style={{ display: "grid", gridTemplateColumns: vision.deviation_url ? "1fr 1fr" : "1fr", gap: 10 }}>
          <figure style={{ margin: 0 }}>
            <img src={assetUrl(vision.image_url)} alt={`${latest.wafer_id} synthetic wafer`} style={{ width: "100%", borderRadius: 10, display: "block", background: "var(--image-bg)" }} />
            <figcaption style={{ fontSize: 11, marginTop: 6, color: "var(--text-3)" }}>{text("Synthetic Vision 입력", "Synthetic Vision Input")}</figcaption>
          </figure>
          {vision.deviation_url && (
            <figure style={{ margin: 0 }}>
              <img src={assetUrl(vision.deviation_url)} alt={`${latest.wafer_id} deviation map`} style={{ width: "100%", borderRadius: 10, display: "block", background: "var(--image-bg)" }} />
              <figcaption style={{ fontSize: 11, marginTop: 6, color: "var(--text-3)" }}>{text("편차 맵 · 모델 localization 아님", "Deviation Map · Not model localization")}</figcaption>
            </figure>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, marginTop: 10 }}>
          {recent.map(item => (
            <div key={`${item.index}-${item.wafer_id}`} style={{ opacity: item.vision.flag ? 1 : 0.72 }}>
              <img src={assetUrl(item.vision.image_url)} alt={item.wafer_id} style={{ width: "100%", display: "block", borderRadius: 6, background: "var(--image-bg)" }} />
              <small className="mono">{item.wafer_id}{item.vision.flag ? ` · ${text("이상", "ALERT")}` : ""}</small>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="chamber-process-grid">
          <div><span>{text("웨이퍼", "Wafer")}</span><strong className="mono">{latest.wafer_id}</strong><em>{latest.observed_at?.slice(11, 19)}</em></div>
          <div><span>{text("탐지", "Detection")}</span><strong>{vision.flag ? text("이상", "ANOMALY") : text("정상", "NORMAL")}</strong><em>{text("모델 판정", "model decision")}</em></div>
          <div><span>{text("점수", "Score")}</span><strong className="mono">{formatScore(vision.score)}</strong><em>{text("임계값", "threshold")} {formatScore(vision.threshold)}</em></div>
          <div><span>{text("마진", "Margin")}</span><strong className="mono" style={{ color: margin >= 0 ? "var(--high)" : "var(--low)" }}>{formatScore(margin)}</strong><em>{text("0 이상이면 이상", "anomaly when ≥ 0")}</em></div>
          <div><span>Process Run</span><strong className="mono">{latest.process_run_id || "—"}</strong><em>{text("FAB identity", "FAB identity")}</em></div>
          <div><span>{text("모델", "Model")}</span><strong className="mono">{vision.model || "—"}</strong><em>{text("Production artifact", "Production artifact")}</em></div>
          <div><span>{text("RCA 태그", "RCA Tags")}</span><strong>{vision.related_tags?.length || 0}</strong><em>{vision.related_tags?.join(", ") || "—"}</em></div>
        </div>
        <div className="source-notice" style={{ marginTop: 12 }}>
          <Icon name="alert" size={13} />
          {text(
            "현재 generic Vision 모델은 이미지 feature 기반 anomaly model입니다. 편차 맵은 입력의 공간적 편차를 보여주는 진단용이며 모델 heatmap으로 해석하면 안 됩니다.",
            "The current generic Vision model uses image-derived features. The deviation map is a visual diagnostic of input variation, not a model attribution heatmap.",
          )}
        </div>
      </div>
    </div>
  );
}

export default function GenericProcessMonitoring({ profile, section = "overview", filters }) {
  const { text } = useUi();
  const processId = profile.process_id;
  const processStep = STEP_BY_PROCESS[processId] || profile.display_name;
  const [events, setEvents] = useState([]);
  const [live, setLive] = useState({ history: [], metrics: {} });
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const eventQuery = buildQuery({
        process_step: processStep,
        equipment_id: filters?.equipment,
        recipe_id: filters?.recipe,
        limit: 160,
      });
      const [eventResponse, liveResponse] = await Promise.all([
        fetch(`${API_BASE}/api/v1/process/events${eventQuery}`, { cache: "no-store" }),
        fetch(`${API_BASE}/outputs/process_runtime/${processId}/live.json?t=${Date.now()}`, { cache: "no-store" }),
      ]);
      if (!eventResponse.ok) throw new Error(text(`${profile.display_name} process event API를 확인해 주세요.`, `Check the ${profile.display_name} process event API.`));
      const body = await eventResponse.json();
      setEvents(body.filter(item => item.source === "process_multimodal_synthetic_runtime"));
      if (liveResponse.ok) setLive(await liveResponse.json());
      setError("");
    } catch (nextError) {
      setError(nextError.message || text(`${profile.display_name} runtime 데이터를 불러오지 못했습니다.`, `Could not load ${profile.display_name} runtime data.`));
    }
  }, [filters?.equipment, filters?.recipe, processId, processStep, profile.display_name, text]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  const history = (live.history || []).filter(item => {
    if (filters?.equipment && filters.equipment !== "all" && item.equipment_id !== filters.equipment) return false;
    if (filters?.unit && filters.unit !== "all" && item.unit_id !== filters.unit) return false;
    if (filters?.recipe && filters.recipe !== "all" && item.recipe_id !== filters.recipe) return false;
    return true;
  });
  const latest = history.at(-1);
  const latestTs = [...history].reverse().find(item => item.timeseries)?.timeseries;
  const latestVision = [...history].reverse().find(item => item.vision)?.vision;
  const connected = history.length > 0;
  const tsMargin = latestTs ? Number(latestTs.margin ?? (latestTs.score - latestTs.threshold)) : null;
  const visionMargin = latestVision ? Number(latestVision.margin ?? (latestVision.score - latestVision.threshold)) : null;

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
              <h2>{text(`${profile.display_name} 공정의 원본 synthetic 데이터와 탐지 결과를 같이 봅니다.`, `Monitor raw synthetic ${profile.display_name} data and detection results together.`)}</h2>
              <p>{text("공정 phase·Tag 연속성·Tag 상관관계를 포함한 stream을 실제 artifact로 추론하고 PostgreSQL에 저장합니다.", "The stream includes process phases, temporal continuity, and tag relationships, runs through real model artifacts, and is persisted to PostgreSQL.")}</p>
            </div>
            <span className={`chamber-status ${connected ? "chamber-status-low" : "chamber-status-med"}`}><span />{connected ? text("연결됨", "CONNECTED") : text("대기", "WAITING")}</span>
          </section>
          <div className="fab-metrics">
            <MetricCard label={text("실시간 샘플", "Live Samples")} value={history.length} unit={text("행", "rows")} detail={latest?.equipment_id || text("stream 대기", "stream waiting")} tone="low" />
            <MetricCard label={text("시계열 모델", "Time-series Model")} value={latestTs?.model || "—"} detail={latestTs ? `${latestTs.flag ? text("이상", "anomaly") : text("정상", "normal")} · margin ${formatScore(tsMargin)}` : text("stream 없음", "no stream")} />
            <MetricCard label={text("Vision 모델", "Vision Model")} value={latestVision?.model || "—"} detail={latestVision ? `${latestVision.flag ? text("이상", "anomaly") : text("정상", "normal")} · margin ${formatScore(visionMargin)}` : text("stream 없음", "no stream")} tone="med" />
            <MetricCard label={text("탐지 이벤트", "Detected Events")} value={events.length} unit={text("건", "events")} detail={`${stats.timeSeries} TS · ${stats.vision} Vision`} tone="high" />
          </div>
          <Panel title={text(`${profile.display_name} · 실시간 태그 값`, `${profile.display_name} · Live tag values`)} icon="pulse" right={<span className="chip">2s POLLING</span>}>
            <TagTrendGrid history={history} />
          </Panel>
          <Panel title={text(`${profile.display_name} · 실시간 Vision`, `${profile.display_name} · Live Vision`)} icon="layers" right={<span className="chip">INPUT + DEVIATION</span>}>
            <VisionViewer history={history} />
          </Panel>
        </>
      )}

      {section === "equipment" && (
        <>
          <div className="fab-metrics">
            <MetricCard label={text("설비", "Equipment")} value={latest?.equipment_id || "—"} detail={latest?.lot_id || text("대기", "waiting")} />
            <MetricCard label={text("최신 웨이퍼", "Latest Wafer")} value={latest?.wafer_id || "—"} detail={latest?.observed_at || text("대기", "waiting")} tone="low" />
            <MetricCard label={text("공정 Phase", "Process Phase")} value={latestTs?.phase || "—"} detail={latestTs?.phase_progress == null ? "—" : `${Math.round(Number(latestTs.phase_progress) * 100)}%`} />
            <MetricCard label={text("TS 상태", "TS State")} value={latestTs?.flag ? text("이상", "ANOMALY") : latestTs ? text("정상", "NORMAL") : "—"} detail={`${text("판정 마진", "decision margin")} ${formatScore(tsMargin)}`} tone={latestTs?.flag ? "high" : "low"} />
            <MetricCard label={text("Vision 상태", "Vision State")} value={latestVision?.flag ? text("이상", "ANOMALY") : latestVision ? text("정상", "NORMAL") : "—"} detail={`${text("판정 마진", "decision margin")} ${formatScore(visionMargin)}`} tone={latestVision?.flag ? "high" : "low"} />
          </div>
          <Panel title={text("현재 telemetry 값", "Current telemetry values")} icon="cpu" right={<span className="chip">RAW SYNTHETIC</span>}>
            <div className="process-profile-grid">
              {Object.entries(latestTs?.payload || {}).map(([tag, value]) => <div key={tag}><span>{TAG_LABELS[tag]?.ko || tag}</span><strong className="mono">{formatValue(value)}</strong><small>{tag}</small></div>)}
              {!latestTs && <EmptyState processId={processId} />}
            </div>
          </Panel>
        </>
      )}

      {section === "trend" && (
        <>
          <div className="fab-metrics">
            <MetricCard label={text("TS 마진", "TS Margin")} value={formatScore(tsMargin)} detail={`${text("점수", "score")} ${formatScore(latestTs?.score)} · ${text("임계값", "threshold")} ${formatScore(latestTs?.threshold)}`} tone={tsMargin != null && tsMargin >= 0 ? "high" : "low"} />
            <MetricCard label={text("Vision 마진", "Vision Margin")} value={formatScore(visionMargin)} detail={`${text("점수", "score")} ${formatScore(latestVision?.score)} · ${text("임계값", "threshold")} ${formatScore(latestVision?.threshold)}`} tone={visionMargin != null && visionMargin >= 0 ? "high" : "med"} />
            <MetricCard label={text("Runtime 정밀도", "Runtime Precision")} value={live.metrics?.precision == null ? "—" : Number(live.metrics.precision).toFixed(3)} detail={text("synthetic 주입 GT", "synthetic injected GT")} />
            <MetricCard label={text("Runtime 재현율", "Runtime Recall")} value={live.metrics?.recall == null ? "—" : Number(live.metrics.recall).toFixed(3)} detail={`F2 ${live.metrics?.f2 == null ? "—" : Number(live.metrics.f2).toFixed(3)}`} tone="high" />
          </div>
          <div className="source-notice">
            <Icon name="activity" size={13} />{text("Anomaly Margin = Score - Threshold. 0 이상이면 이상, 0 미만이면 정상입니다. Score 자체가 음수여도 정상입니다.", "Anomaly Margin = Score - Threshold. Margin ≥ 0 is anomalous and Margin < 0 is normal. A negative raw score is valid.")}
          </div>
          <Panel title={text(`${profile.display_name} · 태그 추세`, `${profile.display_name} · Tag trend`)} icon="activity" right={<span className="chip">RAW VALUES</span>}>
            <TagTrendGrid history={history} />
          </Panel>
        </>
      )}

      {section === "anomaly" && (
        <>
          <Panel title={text(`${profile.display_name} · Vision 검사`, `${profile.display_name} · Vision inspection`)} icon="layers" right={<span className="chip">LATEST INPUT</span>}>
            <VisionViewer history={history} />
          </Panel>
          <Panel title={text(`${profile.display_name} 멀티모달 이상 이력`, `${profile.display_name} multimodal anomaly history`)} icon="alert" right={<span className="chip">POSTGRESQL EVENTS</span>}>
            {events.length ? <EventTable events={events} /> : <div className="process-list-empty">{text("현재 탐지된 anomaly event가 없습니다. 정상 데이터는 Live 화면에서 계속 확인할 수 있습니다.", "No anomaly events are currently detected. Normal data remains visible in the Live view.")}</div>}
          </Panel>
        </>
      )}

      {section === "model" && (
        <>
          <div className="fab-metrics">
            <MetricCard label={text("시계열 Production", "Time-series Production")} value={latestTs?.model || "—"} detail={text("실제 inference artifact", "actual inference artifact")} />
            <MetricCard label={text("Vision Production", "Vision Production")} value={latestVision?.model || "—"} detail={text("실제 inference artifact", "actual inference artifact")} tone="med" />
            <MetricCard label={text("선택 지표", "Selection Metric")} value="F2" detail={text("Recall 가중 후보 선택", "Recall-weighted candidate selection")} tone="high" />
            <MetricCard label={text("Lifecycle", "Lifecycle")} value="Staging → Prod" detail={text("승격 gate + rollback", "guarded promotion + rollback")} tone="low" />
          </div>
          <Panel title={text("모델 lifecycle 명령", "Model lifecycle commands")} icon="box" right={<span className="chip">REAL ARTIFACT</span>}>
            <div className="process-empty">
              <strong>{text("현재 generic runtime 모델 작업은 CLI로 실행합니다.", "Generic runtime model operations currently run through the CLI.")}</strong>
              <code>python scripts/manage_process_models.py health --process {processId} --modality timeseries</code>
              <code>python scripts/manage_process_models.py retrain --process {processId} --modality timeseries --force</code>
              <code>python scripts/manage_process_models.py health --process {processId} --modality vision</code>
              <small>{text("승격 후 다음 추론부터 새 joblib Production artifact를 사용합니다. 실제 Fab 데이터에서는 synthetic GT 기준을 validation/drift 기준으로 교체해야 합니다.", "After promotion, the next inference uses the new joblib Production artifact. Real Fab data should replace synthetic-GT health policy with validation/drift evidence.")}</small>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

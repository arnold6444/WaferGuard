import React, { useCallback, useEffect, useMemo, useState } from "react";

import { fetchJson, normalizeEquipment } from "./fabApi";
import { Icon, Panel } from "./lib";
import { useUi } from "./UiContext";

const SOURCE_LABEL = {
  runtime_inspection_db: "Runtime DB",
  synthetic_runtime: "Synthetic runtime",
  proxy_runtime: "Proxy runtime",
  demo_profile: "Demo profile",
  hybrid: "Hybrid",
  fab_runtime: "FAB v2 Runtime",
  fab_v2_runtime: "FAB v2 Runtime",
  fab_v2_persistence: "FAB v2 DB",
  mqtt_runtime: "MQTT Runtime",
};
const CONNECTED_ORDER = ["photo", "etch", "deposition", "cmp"];
const PROFILE_ORDER = [...CONNECTED_ORDER, "oxidation", "implant", "metal", "inspection"];
const FALLBACK_OVERVIEW = {
  metrics: {
    active_lots: { value: "—", data_source: "demo_profile" },
    running_equipment: { value: "—", data_source: "demo_profile" },
    warnings: { value: "—", data_source: "demo_profile" },
    critical: { value: "—", data_source: "demo_profile" },
  },
  process_status: [
    ["photo", "Photo"], ["etch", "Etch"], ["deposition", "Deposition"], ["cmp", "CMP"],
    ["oxidation", "Oxidation"], ["implant", "Implant"], ["metal", "Metal"], ["inspection", "Inspection"],
  ].map(([process_id, display_name]) => ({ process_id, display_name, status: "offline", data_source: "demo_profile" })),
  recent_alerts: [],
  wafer_quality: { status: "offline", latest_wafer: "—", data_source: "demo_profile" },
  disclaimer: "FAB API is unavailable. Showing the preserved demo shell without live operational claims.",
};

function statusTone(value) {
  const status = String(value || "offline").toLowerCase();
  if (["alarm", "critical", "failed"].includes(status)) return "critical";
  if (["warning", "anomaly", "degraded"].includes(status)) return "warning";
  if (["running", "normal", "idle", "ready"].includes(status)) return "normal";
  return "offline";
}

function operationalMetrics(payload) {
  const metrics = payload?.metrics || payload || {};
  const mqtt = metrics.mqtt || {};
  const database = metrics.database || metrics.db || {};
  const latency = metrics.latency || {};
  const rows = [
    ["MQTT", metrics.mqtt_messages_per_second ?? metrics.mqtt_rate ?? mqtt.message_rate ?? mqtt.rate, "msg/s"],
    ["Reconnect", metrics.mqtt_reconnect_count ?? metrics.mqtt_reconnects ?? mqtt.reconnects, "count"],
    ["DB errors", metrics.db_error_count ?? metrics.db_errors ?? database.errors ?? database.write_errors, "count"],
    ["Ingestion", metrics.ingestion_lag_seconds ?? metrics.ingestion_latency_ms ?? latency.ingestion_ms, metrics.ingestion_lag_seconds != null ? "s" : "ms"],
    ["Detection", metrics.detection_latency_ms ?? latency.detection_ms, "ms"],
    ["Anomaly rate", metrics.anomaly_rate == null ? null : (Number(metrics.anomaly_rate) <= 1 ? Number(metrics.anomaly_rate) * 100 : metrics.anomaly_rate), "%"],
    ["Latest", metrics.latest_telemetry_timestamp?.slice(11, 19), "UTC"],
    ["Models", Array.isArray(metrics.model_versions) ? metrics.model_versions.length : null, "active"],
  ];
  return rows.filter(([, value]) => value != null);
}

function SourceTag({ value }) {
  return <span className={`source-tag source-${value === "demo_profile" ? "demo" : "runtime"}`}>{SOURCE_LABEL[value] || value}</span>;
}

function OverviewMetric({ label, metric, icon }) {
  return (
    <div className="panel fab-overview-metric">
      <span className="fab-overview-icon"><Icon name={icon} size={17} /></span>
      <div><span>{label}</span><strong className="mono">{metric?.value ?? "—"}</strong><SourceTag value={metric?.data_source || "demo_profile"} /></div>
    </div>
  );
}

export default function FabOverview({ onNavigate }) {
  const { text } = useUi();
  const [overview, setOverview] = useState(null);
  const [equipment, setEquipment] = useState([]);
  const [fabMetrics, setFabMetrics] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [overviewResult, equipmentResult, metricsResult] = await Promise.allSettled([
      fetchJson("/api/v1/fab/overview"),
      fetchJson("/api/v1/fab/equipment"),
      fetchJson("/api/v1/fab/metrics"),
    ]);
    if (overviewResult.status === "fulfilled") {
      setOverview(overviewResult.value);
      setError("");
    } else {
      setOverview(current => current || FALLBACK_OVERVIEW);
      setError(text("FAB v2 API에 연결할 수 없어 보존된 demo shell을 표시합니다.", "FAB v2 API is unavailable; showing the preserved demo shell."));
    }
    if (equipmentResult.status === "fulfilled") setEquipment(normalizeEquipment(equipmentResult.value));
    if (metricsResult.status === "fulfilled") setFabMetrics(metricsResult.value);
  }, [text]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const processStatus = useMemo(() => [...(overview?.process_status || [])].sort((left, right) => {
    const leftOrder = PROFILE_ORDER.indexOf(left.process_id);
    const rightOrder = PROFILE_ORDER.indexOf(right.process_id);
    return (leftOrder < 0 ? 999 : leftOrder) - (rightOrder < 0 ? 999 : rightOrder);
  }), [overview]);
  const connectedRuntimeCount = useMemo(() => new Set(
    equipment
      .filter(item => item.connected !== false)
      .map(item => item.process_id)
      .filter(processId => CONNECTED_ORDER.includes(processId)),
  ).size, [equipment]);
  const metricRows = useMemo(() => operationalMetrics(fabMetrics), [fabMetrics]);

  if (!overview) return <div className="panel process-empty"><Icon name="gauge" size={22} /><strong>{text("Fab 상태를 불러오는 중입니다…", "Loading Fab status…")}</strong></div>;

  return (
    <div className="fab-overview-page">
      <section className="panel fab-overview-hero">
        <div>
          <div className="chamber-eyebrow"><span />FAB QUALITY OPS · DEMO CONTROL PLANE</div>
          <h2>{text("공정 설비와 웨이퍼 품질 이상을 한 화면에서 연결합니다.", "Connect process equipment and wafer quality anomalies in one operational view.")}</h2>
          <p>{overview.disclaimer}</p>
        </div>
        <div className="fab-overview-health"><span className={`status-dot status-${overview.wafer_quality.status}`} /><div><strong>{text("웨이퍼 품질", "Wafer Quality")}</strong><span>{overview.wafer_quality.latest_wafer}</span><SourceTag value={overview.wafer_quality.data_source} /></div></div>
      </section>

      {error && <div className="source-notice is-demo"><Icon name="alert" size={13} />{error}<button type="button" className="btn btn-sm" onClick={load}>{text("다시 시도", "Retry")}</button></div>}

      <div className="fab-overview-metrics">
        <OverviewMetric label={text("활성 Lot", "Active Lots")} metric={overview.metrics.active_lots} icon="layers" />
        <OverviewMetric label={text("가동 설비", "Running Equipment")} metric={overview.metrics.running_equipment} icon="cpu" />
        <OverviewMetric label={text("경고", "Warnings")} metric={overview.metrics.warnings} icon="alert" />
        <OverviewMetric label={text("위험", "Critical")} metric={overview.metrics.critical} icon="pulse" />
      </div>

      <div className="fab-overview-grid">
        <Panel title={text("공정 상태", "Process Status")} icon="activity" right={<span className="chip">{connectedRuntimeCount} CONNECTED · {processStatus.length} PROFILES</span>}>
          <div className="fab-process-list">
            {processStatus.map(process => (
              <button key={process.process_id} type="button" className="focusable" onClick={() => onNavigate?.({ id: "process", processId: process.process_id, tab: process.process_id === "etch" ? "anomaly" : "overview" })}>
                <span className={`status-dot status-${statusTone(process.status)}`} />
                <strong>{process.display_name}</strong>
                <span className={`status-label status-${statusTone(process.status)}`}>{String(process.status || "offline").toUpperCase()}</span>
                <SourceTag value={process.data_source} />
                <Icon name="chevR" size={13} />
              </button>
            ))}
          </div>
        </Panel>

        <Panel title={text("최근 경고", "Recent Alerts")} icon="alert" right={<span className="chip">TEMPORAL CANDIDATES</span>}>
          <div className="fab-alert-list">
            {overview.recent_alerts.map((alert, index) => (
              <button key={alert.id || `${alert.entity_id}-${alert.observed_at || "demo"}-${index}`} type="button" className="focusable" onClick={() => onNavigate?.(alert.process_id === "inspection" ? { id: "quality", tab: "overview" } : { id: "process", processId: alert.process_id || "etch", tab: "anomaly" })}>
                <time className="mono">{alert.observed_at?.slice(11, 19) || `DEMO-${String(index + 1).padStart(2, "0")}`}</time>
                <span className={`status-dot status-${alert.severity}`} />
                <strong className="mono">{alert.entity_id}</strong>
                <span>{alert.label}</span>
                <SourceTag value={alert.data_source} />
              </button>
            ))}
          </div>
        </Panel>
      </div>

      {equipment.length > 0 && (
        <Panel title={text("FAB 장비 / Unit 상태", "FAB Equipment / Unit Status")} icon="cpu" right={<span className="chip">{equipment.length} UNIT CONTEXTS</span>}>
          <div className="fab-equipment-grid">
            {equipment.slice(0, 16).map((item, index) => (
              <button key={`${item.equipment_id}-${item.unit_id || index}`} type="button" className="focusable" onClick={() => onNavigate?.({ id: "process", processId: item.process_id || "etch", tab: "equipment", equipmentId: item.equipment_id, unitId: item.unit_id, recipeId: item.recipe_id })}>
                <span className={`status-dot status-${statusTone(item.machine_state)}`} />
                <div><strong className="mono">{item.equipment_id || "—"}</strong><small>{item.process_id || "process"} · {item.unit_id || "unit —"}</small></div>
                <span>{String(item.machine_state || "unknown").toUpperCase()}</span>
                <small className="mono">{item.recipe_id || "recipe —"}</small>
                <Icon name="chevR" size={13} />
              </button>
            ))}
          </div>
        </Panel>
      )}

      {metricRows.length > 0 && (
        <Panel title={text("수집 / 탐지 운영 지표", "Ingestion / Detection Operations")} icon="gauge" right={<span className="chip">FAB METRICS</span>}>
          <div className="fab-operational-metrics">
            {metricRows.map(([label, value, unit]) => <div key={label}><span>{label}</span><strong className="mono">{typeof value === "number" ? Number(value).toFixed(value % 1 ? 2 : 0) : value}</strong><small>{unit}</small></div>)}
          </div>
        </Panel>
      )}

      <section className="panel fab-flow-card">
        <div><span className="label-cap">{text("권장 조사 흐름", "Recommended Investigation Flow")}</span><strong>Photo → Etch → Deposition → CMP → Inspection → Candidate RCA</strong></div>
        <button type="button" className="btn btn-accent" onClick={() => onNavigate?.({ id: "process", processId: "etch", tab: "anomaly" })}><Icon name="activity" size={14} />{text("Etch 이상 확인", "Open Etch anomalies")}</button>
      </section>
    </div>
  );
}

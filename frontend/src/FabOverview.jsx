import React, { useCallback, useEffect, useState } from "react";

import { Icon, Panel } from "./lib";
import { useUi } from "./UiContext";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const SOURCE_LABEL = {
  runtime_inspection_db: "Runtime DB",
  synthetic_runtime: "Synthetic runtime",
  proxy_runtime: "Proxy runtime",
  demo_profile: "Demo profile",
  hybrid: "Hybrid",
};

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
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/fab/overview`);
      if (!response.ok) throw new Error(text("Fab overview API를 확인해 주세요.", "Check the Fab overview API."));
      setOverview(await response.json());
      setError("");
    } catch (nextError) {
      setError(nextError.message || text("Fab overview를 불러오지 못했습니다.", "Could not load the Fab overview."));
    }
  }, [text]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  if (!overview) return <div className="panel process-empty"><Icon name="gauge" size={22} /><strong>{error || text("Fab 상태를 불러오는 중입니다…", "Loading Fab status…")}</strong><button type="button" className="btn" onClick={load}>{text("다시 시도", "Retry")}</button></div>;

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

      <div className="fab-overview-metrics">
        <OverviewMetric label={text("활성 Lot", "Active Lots")} metric={overview.metrics.active_lots} icon="layers" />
        <OverviewMetric label={text("가동 설비", "Running Equipment")} metric={overview.metrics.running_equipment} icon="cpu" />
        <OverviewMetric label={text("경고", "Warnings")} metric={overview.metrics.warnings} icon="alert" />
        <OverviewMetric label={text("위험", "Critical")} metric={overview.metrics.critical} icon="pulse" />
      </div>

      <div className="fab-overview-grid">
        <Panel title={text("공정 상태", "Process Status")} icon="activity" right={<span className="chip">8 PROFILES</span>}>
          <div className="fab-process-list">
            {overview.process_status.map(process => (
              <button key={process.process_id} type="button" className="focusable" onClick={() => onNavigate?.({ id: "process", processId: process.process_id, tab: process.process_id === "etch" ? "anomaly" : "overview" })}>
                <span className={`status-dot status-${process.status}`} />
                <strong>{process.display_name}</strong>
                <span className={`status-label status-${process.status}`}>{process.status.toUpperCase()}</span>
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

      <section className="panel fab-flow-card">
        <div><span className="label-cap">{text("권장 조사 흐름", "Recommended Investigation Flow")}</span><strong>Etch warning → related Lot → Wafer trend → evidence → Agent review</strong></div>
        <button type="button" className="btn btn-accent" onClick={() => onNavigate?.({ id: "process", processId: "etch", tab: "anomaly" })}><Icon name="activity" size={14} />{text("Etch 이상 확인", "Open Etch anomalies")}</button>
      </section>
    </div>
  );
}
import React, { useEffect, useMemo, useState } from "react";

import { Icon, Panel } from "../lib";
import { buildQuery, fetchJson, rowsFrom } from "../fabApi";
import { useUi } from "../UiContext";

function formatNumber(value, digits = 3) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function normalizeResults(payload) {
  const rows = rowsFrom(payload, ["modality_results", "results", "detections", "inference_results"]);
  if (rows.length) return rows;
  const mapped = payload?.latest_results || payload?.modalities;
  return mapped && !Array.isArray(mapped) && typeof mapped === "object" ? Object.values(mapped) : [];
}

function normalizeLive(payload) {
  const samples = rowsFrom(payload, ["telemetry", "samples", "history", "items"]);
  const latest = payload?.latest || payload?.current || payload?.latest_telemetry || samples.at(-1) || {};
  const identity = payload?.identity || latest.identity || latest;
  const rawTags = latest.payload || latest.tags || latest.values || latest.telemetry || payload?.raw_tags || {};
  const events = rowsFrom(payload, ["events", "recent_events", "anomalies"]);
  const phaseBoundaries = rowsFrom(payload, ["phase_boundaries"]).map(item => ({
    ...item,
    event_type: `${item.phase || "phase"}_boundary`,
    severity: "info",
  }));
  return {
    identity,
    latest,
    samples,
    rawTags: rawTags && !Array.isArray(rawTags) && typeof rawTags === "object" ? rawTags : {},
    results: normalizeResults(payload),
    events: [...events, ...phaseBoundaries],
  };
}

function RuntimeIdentity({ label, value, detail }) {
  return (
    <div>
      <span>{label}</span>
      <strong className="mono">{value || "—"}</strong>
      <small>{detail || "FAB v2"}</small>
    </div>
  );
}

export default function FabLivePanel({ processId, filters }) {
  const { text } = useUi();
  const [payload, setPayload] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let controller;
    const load = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const query = buildQuery({
          equipment_id: filters?.equipment,
          unit_id: filters?.unit,
          recipe_id: filters?.recipe,
          limit: 180,
        });
        const body = await fetchJson(`/api/v1/process/${encodeURIComponent(processId)}/live${query}`, { signal: controller.signal });
        if (!cancelled) setPayload(body);
      } catch (error) {
        if (!cancelled && error.name !== "AbortError") setPayload(null);
      }
    };
    load();
    const timer = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [filters?.equipment, filters?.recipe, filters?.unit, processId]);

  const live = useMemo(() => payload ? normalizeLive(payload) : null, [payload]);
  if (!live) return null;

  const { identity, latest, rawTags, results, events, samples } = live;
  const machineState = identity.machine_state || latest.machine_state || payload.machine_state;
  const phase = identity.phase || latest.phase || payload.phase;
  const cycle = identity.cycle_index_since_maintenance ?? latest.cycle_index_since_maintenance ?? identity.cycle_index ?? latest.cycle_index ?? identity.cycle_id ?? latest.cycle_id ?? payload.cycle;
  const phaseProgress = latest.phase_progress ?? payload.phase_progress;
  const tags = Object.entries(rawTags).filter(([, value]) => typeof value !== "object").slice(0, 12);

  return (
    <section className="fab-live-context" aria-label={text("FAB v2 실시간 공정 컨텍스트", "FAB v2 live process context")}>
      <div className="source-notice">
        <Icon name="activity" size={13} />
        {text("FAB v2 identity 기반 live endpoint · 기존 runtime 화면은 아래에 그대로 유지됩니다.", "FAB v2 identity live endpoint · The existing runtime view remains available below.")}
      </div>
      <Panel title={text("현재 공정 Run", "Current Process Run")} icon="cpu" right={<span className="chip">{samples.length} SAMPLES · 2s</span>}>
        <div className="fab-runtime-identity">
          <RuntimeIdentity label="Process Run" value={identity.process_run_id || latest.process_run_id || payload.process_run_id} />
          <RuntimeIdentity label={text("설비 / Unit", "Equipment / Unit")} value={identity.equipment_id || latest.equipment_id} detail={identity.unit_id || latest.unit_id || "—"} />
          <RuntimeIdentity label="Recipe" value={identity.recipe_id || latest.recipe_id} detail={identity.wafer_id || latest.wafer_id} />
          <RuntimeIdentity label={text("장비 상태", "Machine State")} value={machineState ? String(machineState).toUpperCase() : "—"} detail={`cycle ${cycle ?? "—"}`} />
          <RuntimeIdentity label="Phase" value={phase} detail={phaseProgress == null ? "—" : `${Math.round(Number(phaseProgress) * 100)}%`} />
        </div>
      </Panel>

      {(tags.length > 0 || results.length > 0 || events.length > 0) && (
        <div className="fab-live-evidence-grid">
          <Panel title={text("원본 태그", "Raw Tags")} icon="pulse" right={<span className="chip">ALLOWLISTED TELEMETRY</span>}>
            <div className="fab-raw-tag-grid">
              {tags.map(([tag, value]) => <div key={tag}><span>{tag}</span><strong className="mono">{formatNumber(value)}</strong></div>)}
              {!tags.length && <div className="process-list-empty">—</div>}
            </div>
          </Panel>
          <Panel title={text("모달리티 판정", "Modality Results")} icon="alert" right={<span className="chip">MARGIN</span>}>
            <div className="fab-result-list">
              {results.slice(0, 6).map((result, index) => {
                const margin = result.margin ?? (result.raw_score != null && result.threshold != null ? Number(result.raw_score) - Number(result.threshold) : null);
                return (
                  <div key={`${result.modality || "result"}-${result.observed_at || index}`}>
                    <span className={`status-dot status-${result.is_anomaly ? "warning" : "normal"}`} />
                    <strong>{result.modality || "result"}</strong>
                    <span className="mono">raw {formatNumber(result.raw_score ?? result.score)}</span>
                    <span className="mono">margin {formatNumber(margin)}</span>
                    <small className="mono">{result.model_version || result.model || "—"}</small>
                  </div>
                );
              })}
              {!results.length && <div className="process-list-empty">—</div>}
            </div>
          </Panel>
        </div>
      )}

      {events.length > 0 && (
        <Panel title={text("현재 Run 이벤트", "Current Run Events")} icon="history" right={<span className="chip">{events.length}</span>}>
          <div className="process-event-list">
            {events.slice(0, 8).map((event, index) => (
              <div key={event.id || event.message_id || `${event.observed_at}-${index}`}>
                <span className={`status-dot status-${event.severity === "critical" ? "critical" : event.severity === "info" ? "normal" : "warning"}`} />
                <time className="mono">{event.observed_at?.slice(11, 19) || "—"}</time>
                <strong className="mono">{event.equipment_id || identity.equipment_id || "—"}</strong>
                <span>{String(event.event_type || event.label || "anomaly candidate").replaceAll("_", " ")}</span>
                <small>{event.modality || event.source || "FAB v2"}</small>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </section>
  );
}

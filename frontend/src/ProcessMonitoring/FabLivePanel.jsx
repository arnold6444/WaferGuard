import React, { useEffect, useMemo, useState } from "react";

import { Icon, Panel } from "../lib";
import { API_BASE, buildQuery, fetchJson, rowsFrom } from "../fabApi";
import { useUi } from "../UiContext";

const COMPLETE_RUN_STATES = new Set(["complete", "completed", "processed", "finished", "done"]);

const TAG_CONTEXT = {
  exposure_dose: { ko: "노광량", en: "Exposure dose", sourceKo: "Scanner dose monitor", sourceEn: "Scanner dose monitor" },
  focus_offset: { ko: "포커스 오프셋", en: "Focus offset", sourceKo: "Focus / leveling sensor", sourceEn: "Focus / leveling sensor" },
  track_temperature: { ko: "트랙 온도", en: "Track temperature", sourceKo: "Coater/developer track sensor", sourceEn: "Coater/developer track sensor" },
  developer_temperature: { ko: "현상 온도", en: "Developer temperature", sourceKo: "Developer bath sensor", sourceEn: "Developer bath sensor" },
  overlay_nm: { ko: "오버레이", en: "Overlay", sourceKo: "Alignment / overlay channel", sourceEn: "Alignment / overlay channel" },
  chamber_pressure: { ko: "챔버 압력", en: "Chamber pressure", sourceKo: "압력계 채널", sourceEn: "Pressure gauge channel" },
  source_rf_power: { ko: "Source RF 파워", en: "Source RF power", sourceKo: "RF generator telemetry", sourceEn: "RF generator telemetry" },
  bias_rf_power: { ko: "Bias RF 파워", en: "Bias RF power", sourceKo: "Bias RF generator telemetry", sourceEn: "Bias RF generator telemetry" },
  total_gas_flow: { ko: "총 가스 유량", en: "Total gas flow", sourceKo: "MFC 합산 채널", sourceEn: "MFC aggregate channel" },
  chamber_temperature: { ko: "챔버 온도", en: "Chamber temperature", sourceKo: "Chamber temperature sensor", sourceEn: "Chamber temperature sensor" },
  esc_temperature: { ko: "ESC 온도", en: "ESC temperature", sourceKo: "Electrostatic chuck sensor", sourceEn: "Electrostatic chuck sensor" },
  resistance: { ko: "챔버 저항", en: "Chamber resistance", sourceKo: "Plasma impedance proxy", sourceEn: "Plasma impedance proxy" },
  substrate_temperature: { ko: "기판 온도", en: "Substrate temperature", sourceKo: "Susceptor / pyrometer channel", sourceEn: "Susceptor / pyrometer channel" },
  precursor_flow: { ko: "전구체 유량", en: "Precursor flow", sourceKo: "Precursor MFC", sourceEn: "Precursor MFC" },
  carrier_gas_flow: { ko: "캐리어 가스 유량", en: "Carrier gas flow", sourceKo: "Carrier-gas MFC", sourceEn: "Carrier-gas MFC" },
  rf_power: { ko: "RF 파워", en: "RF power", sourceKo: "RF generator telemetry", sourceEn: "RF generator telemetry" },
  deposition_time: { ko: "증착 시간", en: "Deposition time", sourceKo: "Recipe timer", sourceEn: "Recipe timer" },
  down_force: { ko: "가압력", en: "Down force", sourceKo: "Carrier-head force channel", sourceEn: "Carrier-head force channel" },
  platen_speed: { ko: "플래튼 속도", en: "Platen speed", sourceKo: "Platen encoder", sourceEn: "Platen encoder" },
  carrier_speed: { ko: "캐리어 속도", en: "Carrier speed", sourceKo: "Carrier-head encoder", sourceEn: "Carrier-head encoder" },
  slurry_flow: { ko: "슬러리 유량", en: "Slurry flow", sourceKo: "Slurry flow controller", sourceEn: "Slurry flow controller" },
  motor_current: { ko: "모터 전류", en: "Motor current", sourceKo: "Platen motor drive", sourceEn: "Platen motor drive" },
};

const METROLOGY_CONTEXT = {
  critical_dimension_nm: { ko: "Critical Dimension / 선폭", en: "Critical Dimension / line width", instrumentKo: "CD-SEM 또는 OCD", instrumentEn: "CD-SEM or OCD" },
  pattern_linewidth_nm: { ko: "패턴 선폭", en: "Pattern line width", instrumentKo: "CD-SEM", instrumentEn: "CD-SEM" },
  etch_depth_nm: { ko: "식각 깊이", en: "Etch depth", instrumentKo: "단면 SEM 또는 profilometer", instrumentEn: "Cross-section SEM or profilometer" },
  sidewall_angle_deg: { ko: "측벽 각도", en: "Sidewall angle", instrumentKo: "단면 SEM", instrumentEn: "Cross-section SEM" },
  overlay_nm: { ko: "Overlay", en: "Overlay", instrumentKo: "Overlay metrology", instrumentEn: "Overlay metrology" },
  film_thickness_nm: { ko: "막 두께", en: "Film thickness", instrumentKo: "Ellipsometer / reflectometer", instrumentEn: "Ellipsometer / reflectometer" },
  uniformity_percent: { ko: "막 균일도", en: "Film uniformity", instrumentKo: "Wafer map metrology", instrumentEn: "Wafer map metrology" },
  remaining_film_nm: { ko: "잔여 막 두께", en: "Remaining film", instrumentKo: "Thickness mapping", instrumentEn: "Thickness mapping" },
  within_wafer_nonuniformity_percent: { ko: "Wafer 내 비균일도", en: "Within-wafer non-uniformity", instrumentKo: "Wafer map metrology", instrumentEn: "Wafer map metrology" },
};

function formatNumber(value, digits = 3) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function asList(value) {
  if (Array.isArray(value)) return value;
  if (value == null || value === "") return [];
  return [value];
}

function readable(value, fallback = "—") {
  return value == null || value === "" ? fallback : String(value).replaceAll("_", " ");
}

function assetUrl(value) {
  if (!value) return "";
  if (/^https?:\/\//.test(value)) return value;
  const path = String(value).replace(/^\/+/, "");
  return `${API_BASE}/${path.startsWith("outputs/") ? path : `outputs/${path}`}`;
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
  const rawTags = latest.tags || latest.payload || latest.values || latest.telemetry || payload?.raw_tags || {};
  const events = rowsFrom(payload, ["events", "recent_events", "anomalies"]);
  const phaseBoundaries = rowsFrom(payload, ["phase_boundaries"]).map(item => ({
    ...item,
    event_type: `${item.phase || "phase"}_boundary`,
    severity: "info",
  }));
  return {
    connected: Boolean(payload?.connected),
    runtimeSupported: Boolean(payload?.runtime_supported || payload?.connected),
    equipment: rowsFrom(payload, ["equipment", "equipment_units", "units"]),
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
      <strong className="mono" title={value || undefined}>{value || "—"}</strong>
      <small>{detail || "FAB v2"}</small>
    </div>
  );
}

function RunEmptyState({ connected, error }) {
  const { text } = useUi();
  return (
    <div className="fab-run-empty-state">
      <Icon name={error ? "alert" : "activity"} size={19} />
      <div>
        <strong>{error || (connected ? text("선택한 조건에 저장된 FAB v2 Run이 없습니다.", "No FAB v2 run is stored for the selected context.") : text("이 공정의 FAB v2 runtime 데이터가 아직 연결되지 않았습니다.", "FAB v2 runtime data is not connected for this process yet."))}</strong>
        <span>{text("아래 legacy 화면의 데이터는 별도 runtime이므로 FAB v2 Process Run ID로 표시되지 않습니다.", "The legacy view below is a separate runtime and therefore has no FAB v2 Process Run ID.")}</span>
      </div>
    </div>
  );
}

function EquipmentTagPanel({ processId, tags, evidenceTags, sensorCatalog = {}, contextLabel, hasRun }) {
  const { language, text } = useUi();
  const tagValues = new Map(tags);
  const tagNames = [...new Set([...Object.keys(sensorCatalog || {}), ...tags.map(([tag]) => tag)])];
  return (
    <Panel
      title={hasRun ? text("장비 Sensor Tag", "Equipment Sensor Tags") : text("구성된 장비 Sensor Tag", "Configured Equipment Sensor Tags")}
      icon="pulse"
      right={<span className="chip">{contextLabel || processId.toUpperCase()} · {hasRun ? "SYNTHETIC SIGNALS" : "NO LIVE VALUE"}</span>}
    >
      <div className="fab-equipment-tag-grid">
        {tagNames.map(tag => {
          const value = tagValues.get(tag);
          const context = TAG_CONTEXT[tag] || {};
          const configured = sensorCatalog?.[tag] || {};
          const related = evidenceTags.has(tag);
          const hasValue = value != null && value !== "";
          const configuredSource = [configured.sensor_type, configured.role].filter(Boolean).map(item => readable(item)).join(" · ");
          const unit = configured.unit ? ` ${configured.unit}` : "";
          return (
            <div key={tag} className={related ? "is-evidence" : ""}>
              <span className={`status-dot status-${related ? "warning" : hasValue ? "normal" : "offline"}`} />
              <div>
                <strong>{configured.display_name || (language === "en" ? context.en || tag : context.ko || tag)}</strong>
                <small className="mono">{tag}</small>
              </div>
              <b className="mono">{formatNumber(value)}{unit}</b>
              <em>{configuredSource || (language === "en" ? context.sourceEn || "Equipment telemetry channel" : context.sourceKo || "장비 telemetry 채널")}</em>
            </div>
          );
        })}
        {!tagNames.length && <div className="process-list-empty">{text("이 공정의 sensor tag 정의가 없습니다.", "No sensor tag definition is available for this process.")}</div>}
      </div>
      <div className="fab-evidence-legend">
        <span><i className="status-dot status-warning" />{text("탐지/RCA 관련 태그 후보", "Detection/RCA related tag candidate")}</span>
        <span>{hasRun ? text("계측 채널명은 실제 장비 맥락을 설명하며 값은 synthetic proxy입니다.", "Channel names explain the real-equipment context; values are synthetic proxies.") : text("구성 정의만 표시 중이며 실시간 측정값은 아직 없습니다.", "Showing configured definitions only; no live measurement values exist yet.")}</span>
      </div>
    </Panel>
  );
}

function ConfiguredMeasurementPlan({ definition }) {
  const { text } = useUi();
  const metrology = definition?.metrology_plan || [];
  const inspection = definition?.inspection_plan || {};
  if (!metrology.length && !Object.keys(inspection).length) return null;
  return (
    <Panel title={text("구성된 후공정 계측 계획", "Configured Post-process Measurement Plan")} icon="layers" right={<span className="chip">PLAN · NO RESULT</span>}>
      <div className="fab-config-plan-list">
        {metrology.map(item => (
          <div key={item.metric_id}>
            <span className="status-dot status-offline" />
            <div><strong>{item.display_name || readable(item.metric_id)}</strong><small className="mono">{item.metric_id}</small></div>
            <b>{readable(item.instrument_class)}</b>
            <em>{readable(item.modality)} · {readable(item.sampling_level)}</em>
          </div>
        ))}
        {Object.keys(inspection).length > 0 && (
          <div>
            <span className="status-dot status-offline" />
            <div><strong>{inspection.display_name || text("검사 Asset", "Inspection Asset")}</strong><small className="mono">inspection</small></div>
            <b>{readable(inspection.instrument_class)}</b>
            <em>{readable(inspection.inspection_modality)} · {readable(inspection.sampling_level)}</em>
          </div>
        )}
      </div>
      <div className="fab-context-disclaimer">{definition.disclaimer || text("Synthetic 구성 계획이며 실제 계측 결과가 아닙니다.", "Synthetic configuration plan; these are not measured results.")}</div>
    </Panel>
  );
}

function metricDisplay(metric, payload, language) {
  const raw = payload?.metrics?.[metric];
  const embedded = raw && typeof raw === "object" ? raw : {};
  const measurement = rowsFrom(payload, ["measurements"]).find(item => (item.metric_id || item.metric || item.name) === metric) || {};
  const targetMetadata = payload?.quality_targets?.[metric] || payload?.quality_target?.[metric] || {};
  const metadata = payload?.measurement_context?.[metric]
    || payload?.metrology_context?.measurements?.[metric]
    || payload?.metadata?.measurement_context?.[metric]
    || payload?.metadata?.metrology_context?.measurements?.[metric]
    || payload?.metadata?.measurements?.[metric]
    || {};
  const fallback = METROLOGY_CONTEXT[metric] || {};
  return {
    value: measurement.value ?? measurement.measured_value ?? embedded.value ?? embedded.measured_value ?? raw,
    unit: measurement.unit || embedded.unit || metadata.unit || targetMetadata.unit || (metric.endsWith("_nm") ? "nm" : metric.endsWith("_deg") ? "deg" : metric.endsWith("_percent") ? "%" : ""),
    label: language === "en"
      ? measurement.display_name_en || measurement.display_name || metadata.label_en || embedded.label_en || targetMetadata.display_name || fallback.en || metric
      : measurement.display_name_ko || measurement.display_name || metadata.label_ko || embedded.label_ko || targetMetadata.display_name || fallback.ko || metric,
    instrument: language === "en"
      ? measurement.instrument_class || measurement.instrument_type || metadata.instrument_class || metadata.instrument_type || embedded.instrument_class || embedded.instrument_type || targetMetadata.instrument_class || fallback.instrumentEn || "Metrology equipment"
      : measurement.instrument_class_ko || measurement.instrument_class || measurement.instrument_type_ko || measurement.instrument_type || metadata.instrument_class_ko || metadata.instrument_class || metadata.instrument_type_ko || metadata.instrument_type || embedded.instrument_class || embedded.instrument_type || targetMetadata.instrument_class || fallback.instrumentKo || "계측 장비",
    modality: measurement.modality || metadata.modality || targetMetadata.modality,
    method: measurement.method || metadata.method || targetMetadata.method,
    sampling: measurement.sampling_level || metadata.sampling_level || targetMetadata.sampling_level || payload?.sampling_level,
  };
}

function MetrologyEvidence({ detail }) {
  const { language, text } = useUi();
  const metrologyRows = rowsFrom(detail, ["metrology", "metrology_results"]);
  const inspections = rowsFrom(detail, ["inspections", "inspection_assets", "vision"]);
  const metrology = metrologyRows.at(-1);
  const inspection = inspections.at(-1);
  const metrics = Object.keys(metrology?.metrics || {});
  const metrologyContext = metrology?.metrology_context || metrology?.metadata?.metrology_context || {};
  const metrologyScope = metrologyContext.measurement_scope || asList(metrologyContext.modalities).join(" / ") || "MEASUREMENT EVIDENCE";
  const inspectionMetadata = inspection?.metadata || {};
  const inspectionContext = inspection?.inspection_context || inspectionMetadata.inspection_context || {};
  const inspectionModality = inspection?.inspection_modality
    || inspection?.imaging_modality
    || inspection?.inspection_type
    || inspectionMetadata.inspection_modality
    || inspectionMetadata.imaging_modality
    || inspectionMetadata.inspection_type
    || inspectionContext.inspection_modality
    || inspectionContext.imaging_modality
    || inspectionContext.inspection_type;
  const inspectionInstrument = inspection?.instrument_class
    || inspection?.instrument_type
    || inspectionMetadata.instrument_class
    || inspectionMetadata.instrument_type
    || inspectionContext.instrument_class
    || inspectionContext.instrument_type
    || text("Synthetic 검사 장비", "Synthetic inspection equipment");
  const imageType = inspection?.image_type || inspectionMetadata.image_type || inspectionContext.image_type;
  const samplingLevel = inspection?.sampling_level || inspectionMetadata.sampling_level || inspectionContext.sampling_level;
  const inspectionType = readable(inspectionModality, text("Synthetic 검사 이미지", "Synthetic inspection image"));
  const image = assetUrl(inspection?.image_url || inspection?.image_key);

  return (
    <div className="fab-postprocess-grid">
      <Panel title={text("공정 후 계측 결과", "Post-process Metrology")} icon="activity" right={<span className="chip">{readable(metrologyScope)}</span>}>
        {metrology && metrics.length ? (
          <div className="fab-metrology-list">
            {metrics.map(metric => {
              const display = metricDisplay(metric, metrology, language);
              const target = metrology.quality_targets?.[metric] || metrology.quality_target?.[metric] || {};
              const numeric = Number(display.value);
              const outside = Number.isFinite(numeric) && ((target.lower != null && numeric < Number(target.lower)) || (target.upper != null && numeric > Number(target.upper)));
              return (
                <div key={metric} className={outside ? "is-warning" : ""}>
                  <span className={`status-dot status-${outside ? "warning" : "normal"}`} />
                  <div><strong>{display.label}</strong><small className="mono">{metric}</small></div>
                  <b className="mono">{formatNumber(display.value)} {display.unit}</b>
                  <em title={readable(display.method)}>{readable(display.instrument)}{display.modality ? ` · ${readable(display.modality)}` : ""}</em>
                  <small className="mono">{target.lower != null && target.upper != null ? `${formatNumber(target.lower)} – ${formatNumber(target.upper)} ${display.unit}` : text("Proxy spec 미정", "No proxy spec")}{display.sampling ? ` · ${readable(display.sampling)}` : ""}</small>
                </div>
              );
            })}
          </div>
        ) : <div className="process-list-empty">{text("Run 완료 후 metrology 결과를 기다리고 있습니다.", "Waiting for metrology evidence after run completion.")}</div>}
        {metrologyContext.disclaimer && <div className="fab-context-disclaimer">{metrologyContext.disclaimer}</div>}
      </Panel>

      <Panel title={text("검사 Asset", "Inspection Asset")} icon="layers" right={<span className="chip">{inspectionType}</span>}>
        {inspection ? (
          <div className="fab-inspection-asset">
            {image ? <img src={image} alt={`${inspectionType} · ${detail?.wafer_id || "wafer"}`} /> : <div className="fab-inspection-placeholder"><Icon name="layers" size={22} /></div>}
            <div>
              <strong>{readable(inspectionInstrument)}</strong>
              <span>{inspectionType}{imageType ? ` · ${readable(imageType)}` : ""}{samplingLevel ? ` · ${readable(samplingLevel)}` : ""}</span>
              <span className="mono">{inspection.image_key || inspection.image_url || "—"}</span>
              <small>{inspectionContext.disclaimer || text("Wafer map, CD-SEM, 단면 SEM 등 asset 종류를 metadata로 구분합니다.", "Asset metadata distinguishes wafer maps, CD-SEM, cross-section SEM, and other acquisition types.")}</small>
              <small className="mono">{formatTimestamp(inspection.available_at || inspection.observed_at)}</small>
            </div>
          </div>
        ) : <div className="process-list-empty">{text("Run 완료 후 inspection asset을 기다리고 있습니다.", "Waiting for an inspection asset after run completion.")}</div>}
      </Panel>
    </div>
  );
}

function RcaCandidates({ rca }) {
  const { text } = useUi();
  const candidates = rowsFrom(rca, ["candidate_causes", "candidates", "ranked_candidates"]);
  if (!candidates.length) return null;
  return (
    <Panel title={text("장비 원인 후보", "Equipment Cause Candidates")} icon="alert" right={<span className="chip">CANDIDATE · REVIEW REQUIRED</span>}>
      <div className="fab-rca-candidate-list">
        {candidates.slice(0, 3).map((candidate, index) => {
          const evidence = asList(Array.isArray(candidate.evidence) ? candidate.evidence : candidate.evidence?.tags || candidate.related_tags);
          return (
            <div key={candidate.id || candidate.candidate || candidate.cause || index}>
              <span className="mono">#{index + 1}</span>
              <div>
                <strong>{candidate.candidate || candidate.cause || candidate.label || text("원인 후보", "Cause candidate")}</strong>
                <small>{evidence.length ? evidence.join(" · ") : text("근거 태그 확인 필요", "Evidence tags pending")}</small>
              </div>
              <b className="mono">{candidate.score == null ? "—" : formatNumber(candidate.score)}</b>
            </div>
          );
        })}
      </div>
      <div className="source-notice is-review"><Icon name="alert" size={13} />{text("이 결과는 Candidate root cause입니다. 담당자 review가 쌓이기 전에는 확정 원인이나 자동 조치로 사용하지 않습니다.", "These are Candidate root causes. They are not confirmed causes or automatic actions until operator review evidence accumulates.")}</div>
    </Panel>
  );
}

export default function FabLivePanel({ processId, filters, definition }) {
  const { text } = useUi();
  const [payload, setPayload] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let controller;
    setPayload(null);
    setLoadError(false);
    const load = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const query = buildQuery({ equipment_id: filters?.equipment, unit_id: filters?.unit, recipe_id: filters?.recipe, limit: 180 });
        const body = await fetchJson(`/api/v1/process/${encodeURIComponent(processId)}/live${query}`, { signal: controller.signal });
        if (!cancelled) {
          setPayload(body);
          setLoadError(false);
        }
      } catch (error) {
        if (!cancelled && error.name !== "AbortError") setLoadError(true);
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
  const currentRunId = live?.identity?.process_run_id || live?.latest?.process_run_id || payload?.process_run?.process_run_id || payload?.process_run_id || null;

  useEffect(() => {
    let cancelled = false;
    let controller;
    setRunDetail(null);
    if (!currentRunId) return undefined;
    const load = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const detail = await fetchJson(`/api/v1/fab/process-runs/${encodeURIComponent(currentRunId)}?telemetry_limit=180`, { signal: controller.signal });
        if (!cancelled) setRunDetail(detail);
      } catch (error) {
        if (!cancelled && error.name !== "AbortError" && error.status !== 404) setRunDetail(null);
      }
    };
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      cancelled = true;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [currentRunId]);

  const identity = live?.identity || {};
  const latest = live?.latest || {};
  const rawTags = live?.rawTags || {};
  const results = live?.results || [];
  const events = live?.events || [];
  const samples = live?.samples || [];
  const equipment = live?.equipment || [];
  const selectedEquipmentId = identity.equipment_id || (filters?.equipment !== "all" ? filters?.equipment : null);
  const selectedUnitId = identity.unit_id || (filters?.unit !== "all" ? filters?.unit : null);
  const selectedUnit = equipment.find(item => ((!selectedEquipmentId || item.equipment_id === selectedEquipmentId) && (!selectedUnitId || item.unit_id === selectedUnitId))) || equipment[0] || {};
  const machineState = selectedUnit.machine_state || identity.machine_state || latest.machine_state || payload?.machine_state;
  const phase = selectedUnit.phase || identity.phase || latest.phase || payload?.phase;
  const cycle = identity.cycle_index_since_maintenance ?? latest.cycle_index_since_maintenance ?? identity.cycle_index ?? latest.cycle_index ?? identity.cycle_id ?? latest.cycle_id ?? payload?.cycle;
  const phaseProgress = latest.phase_progress ?? identity.phase_progress ?? payload?.phase_progress;
  const tags = Object.entries(rawTags).filter(([, value]) => typeof value !== "object").slice(0, 24);
  const processRun = runDetail || payload?.process_run || {};
  const effectiveDetail = runDetail || (currentRunId ? payload : null);
  const effectiveRca = effectiveDetail?.rca || payload?.rca;
  const runStatus = String(processRun.status || "").toLowerCase();
  const authoritativeRunState = String(payload?.run_state || "").toLowerCase();
  const hasAuthoritativeRunState = ["running", "latest_completed", "latest_stored", "no_run"].includes(authoritativeRunState);
  const hasReceivingFlag = typeof payload?.receiving === "boolean";
  const completed = Boolean(currentRunId) && (
    authoritativeRunState === "latest_completed"
    || (!hasAuthoritativeRunState && (Boolean(processRun.completed_at) || COMPLETE_RUN_STATES.has(runStatus)))
  );
  const running = Boolean(currentRunId) && (
    hasAuthoritativeRunState
      ? authoritativeRunState === "running"
      : hasReceivingFlag
        ? payload.receiving
        : !completed && (runStatus === "running" || String(machineState || "").toUpperCase() === "RUNNING")
  );
  const state = !currentRunId ? (live?.runtimeSupported ? "ready" : "offline") : running ? "live" : completed ? "completed" : "stored";
  const stateLabel = {
    live: text("LIVE · RUNNING", "LIVE · RUNNING"),
    completed: text("IDLE · 최근 완료 Run", "IDLE · LATEST COMPLETED"),
    stored: text("최근 저장 Run", "LATEST STORED RUN"),
    ready: text("Runtime 준비 · Run 없음", "RUNTIME READY · NO RUN"),
    offline: text("연결 안 됨", "NOT CONNECTED"),
  }[state];
  const unitSensorCatalog = selectedUnit.sensor_tags || selectedUnit.equipment_context?.sensor_tags || {};
  const sensorCatalog = Object.keys(unitSensorCatalog).length ? unitSensorCatalog : definition?.sensor_tags || {};
  const equipmentContextLabel = selectedUnit.equipment_context?.equipment_display_name
    || selectedUnit.equipment_id
    || definition?.equipment_class?.display_name
    || processId.toUpperCase();
  const evidenceTags = new Set([...results.flatMap(result => asList(result.related_tags)), ...asList(effectiveRca?.evidence?.tags)]);

  return (
    <section className="fab-live-context" aria-label={text("FAB v2 공정 컨텍스트", "FAB v2 process context")}>
      <div className="source-notice">
        <Icon name="activity" size={13} />
        {text("FAB v2 DB snapshot을 2초마다 조회합니다. Run이 끝나면 실시간 표시 대신 최근 완료 Run과 후공정 계측 근거를 유지합니다.", "The FAB v2 DB snapshot is polled every 2 seconds. After a run ends, the latest completed run and post-process evidence remain visible instead of being labelled live.")}
      </div>
      <Panel
        title={state === "live" ? text("현재 공정 Run", "Current Process Run") : state === "completed" || state === "stored" ? text("최근 공정 Run", "Latest Process Run") : text("공정 Run 상태", "Process Run Status")}
        icon="cpu"
        right={<span className={`chip fab-runtime-state state-${state}`}><span className={`status-dot status-${state === "live" ? "normal" : state === "offline" ? "offline" : "warning"}`} />{stateLabel} · {samples.length} SAMPLES</span>}
      >
        {currentRunId ? (
          <div className="fab-runtime-identity">
            <RuntimeIdentity label="Process Run" value={currentRunId} detail={formatTimestamp(latest.observed_at)} />
            <RuntimeIdentity label={text("설비 / Unit", "Equipment / Unit")} value={identity.equipment_id || latest.equipment_id} detail={identity.unit_id || latest.unit_id || "—"} />
            <RuntimeIdentity label="Recipe / Wafer" value={identity.recipe_id || latest.recipe_id} detail={identity.wafer_id || latest.wafer_id || processRun.wafer_id} />
            <RuntimeIdentity label={text("장비 / Run 상태", "Machine / Run State")} value={machineState ? String(machineState).toUpperCase() : stateLabel} detail={`${String(processRun.status || state).toUpperCase()} · cycle ${cycle ?? "—"}`} />
            <RuntimeIdentity label="Phase" value={phase} detail={phaseProgress == null ? (processRun.completed_at ? formatTimestamp(processRun.completed_at) : "—") : `${Math.round(Number(phaseProgress) * 100)}%`} />
          </div>
        ) : <RunEmptyState connected={live?.runtimeSupported} error={!payload && loadError ? text("FAB v2 live API에 연결할 수 없습니다.", "Could not connect to the FAB v2 live API.") : ""} />}
      </Panel>

      {(currentRunId || Object.keys(sensorCatalog).length > 0) && (
        <EquipmentTagPanel
          processId={processId}
          tags={tags}
          evidenceTags={evidenceTags}
          sensorCatalog={sensorCatalog}
          contextLabel={equipmentContextLabel}
          hasRun={Boolean(currentRunId && tags.length)}
        />
      )}

      {!currentRunId && definition && <ConfiguredMeasurementPlan definition={definition} />}

      {currentRunId && results.length > 0 && (
        <Panel title={text("모달리티 판정", "Modality Results")} icon="alert" right={<span className="chip">MARGIN = SCORE − THRESHOLD</span>}>
          <div className="fab-result-list">
            {results.slice(0, 8).map((result, index) => {
              const margin = result.margin ?? (result.raw_score != null && result.threshold != null ? Number(result.raw_score) - Number(result.threshold) : null);
              return (
                <div key={`${result.modality || "result"}-${result.observed_at || index}`}>
                  <span className={`status-dot status-${result.is_anomaly ? "warning" : "normal"}`} />
                  <strong>{result.modality || "result"}</strong>
                  <span className="mono">raw {formatNumber(result.raw_score ?? result.score)}</span>
                  <span className="mono">margin {formatNumber(margin)}</span>
                  <small className="mono">{asList(result.related_tags).join(", ") || result.model_version || result.model || "—"}</small>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      {currentRunId && effectiveDetail && <MetrologyEvidence detail={effectiveDetail} />}
      {currentRunId && effectiveRca && <RcaCandidates rca={effectiveRca} />}

      {currentRunId && events.length > 0 && (
        <Panel title={state === "live" ? text("현재 Run 이벤트", "Current Run Events") : text("최근 Run 이벤트", "Latest Run Events")} icon="history" right={<span className="chip">{events.length}</span>}>
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

import React, { useEffect, useMemo, useState } from "react";

import { Icon, Panel, SubTabs } from "../lib";
import { fetchJson, normalizeEquipment } from "../fabApi";
import { useUi } from "../UiContext";
import EtchMonitoring from "./EtchMonitoring";
import FabLivePanel from "./FabLivePanel";
import GenericProcessMonitoring from "./GenericProcessMonitoring";

const TAB_DEFS = [
  { id: "overview", ko: "개요", en: "Overview", icon: "gauge" },
  { id: "equipment", ko: "설비", en: "Equipment", icon: "cpu" },
  { id: "trend", ko: "공정 추세", en: "Process Trend", icon: "pulse" },
  { id: "anomaly", ko: "이상", en: "Anomaly", icon: "alert" },
  { id: "model", ko: "모델", en: "Model", icon: "box" },
];
const MULTIMODAL_RUNTIME = new Set(["photo", "deposition", "cmp", "cleaning"]);
const FAB_V2_PROCESSES = ["photo", "etch", "deposition", "cmp", "cleaning"];
const FAB_V2_RUNTIME = new Set(FAB_V2_PROCESSES);
const DEFAULT_PROFILES = [
  ["oxidation", "Oxidation", "Furnace"],
  ["photo", "Photo", "Scanner / Track"],
  ["etch", "Etch", "Plasma Etcher"],
  ["deposition", "Deposition", "CVD / PVD (Thin Film)"],
  ["implant", "Implant", "Ion Implanter"],
  ["metal", "Metal", "Metal Deposition"],
  ["cmp", "CMP", "Polisher (C&C)"],
  ["cleaning", "Cleaning", "Single-wafer Wet Cleaner (C&C)"],
  ["inspection", "Inspection", "Wafer Inspection"],
].map(([process_id, display_name, equipment_type]) => ({ process_id, display_name, equipment_type, parameters: [] }));

function FilterSelect({ label, value, values, onChange, allLabel }) {
  return (
    <label>
      {label}
      <select value={value} onChange={event => onChange(event.target.value)}>
        <option value="all">{allLabel}</option>
        {values.map(item => <option key={item} value={item}>{item}</option>)}
      </select>
    </label>
  );
}

function DisconnectedProcess({ profile, section }) {
  const { text } = useUi();
  const params = profile.parameters || [];
  return (
    <div className="process-section">
      <div className="source-notice is-demo"><Icon name="alert" size={13} />{text("확장 프로필 · 현재 FAB v2 runtime에 연결되지 않았습니다.", "Extension profile · Not connected to the FAB v2 runtime.")}</div>
      <section className="panel process-hero is-demo">
        <div>
          <div className="chamber-eyebrow"><span />{profile.display_name.toUpperCase()} · {text("확장 프로필", "EXTENSION PROFILE")}</div>
          <h2>{profile.equipment_type}</h2>
          <p>{text("동일 UI가 parameter metadata를 바꾸어 확장되는 구조입니다. 현재 detector/model은 연결하지 않았습니다.", "The same UI can extend through parameter metadata. No detector/model is connected for this profile yet.")}</p>
        </div>
        <span className="chip">{section.toUpperCase()} · {text("연결 안 됨", "NOT CONNECTED")}</span>
      </section>
      <Panel title={text(`${profile.display_name} 파라미터 프로필`, `${profile.display_name} parameter profile`)} icon="layers" right={<span className="chip">{text("연결 전 메타데이터", "UNCONNECTED METADATA")}</span>}>
        <div className="process-profile-grid">
          {params.map(item => <div key={item.id}><span>{item.label}</span><strong className="mono">— {item.unit}</strong><small>{item.normal_range}</small></div>)}
        </div>
      </Panel>
    </div>
  );
}

export default function ProcessMonitoringView({ target }) {
  const { language, text } = useUi();
  const [profiles, setProfiles] = useState(DEFAULT_PROFILES);
  const [equipment, setEquipment] = useState([]);
  const [definitions, setDefinitions] = useState([]);
  const [runtimeByProcess, setRuntimeByProcess] = useState({});
  const [processId, setProcessId] = useState(target?.processId || "etch");
  const [tab, setTab] = useState(target?.tab || "overview");
  const [filters, setFilters] = useState({ equipment: target?.equipmentId || "all", unit: target?.unitId || "all", recipe: target?.recipeId || "all" });

  useEffect(() => {
    let cancelled = false;
    const loadInventory = () => Promise.allSettled([
        fetchJson("/api/v1/process/profiles"),
        fetchJson("/api/v1/fab/equipment"),
        fetchJson("/api/v1/fab/process-definitions"),
        ...FAB_V2_PROCESSES.map(id => fetchJson(`/api/v1/process/${id}/live?limit=1`)),
      ]).then(([profileResult, equipmentResult, definitionResult, ...runtimeResults]) => {
        if (cancelled) return;
        if (profileResult.status === "fulfilled" && Array.isArray(profileResult.value) && profileResult.value.length) setProfiles(profileResult.value);
        if (equipmentResult.status === "fulfilled") setEquipment(normalizeEquipment(equipmentResult.value));
        if (definitionResult.status === "fulfilled" && Array.isArray(definitionResult.value)) setDefinitions(definitionResult.value);
        setRuntimeByProcess(Object.fromEntries(FAB_V2_PROCESSES.map((id, index) => {
          const result = runtimeResults[index];
          if (result?.status !== "fulfilled") return [id, { apiAvailable: false, connected: false, hasData: false }];
          const value = result.value || {};
          const rows = Array.isArray(value.telemetry) ? value.telemetry : [];
          return [id, {
            apiAvailable: true,
            connected: Boolean(value.connected),
            runtimeSupported: Boolean(value.runtime_supported),
            hasData: value.has_runtime_data ?? Boolean(value.process_run || value.current || rows.length),
            current: value.current || rows.at(-1) || null,
            runState: value.run_state || null,
          }];
        })));
      });
    loadInventory();
    const timer = window.setInterval(loadInventory, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (target?.processId) {
      setProcessId(target.processId);
      setFilters({ equipment: target.equipmentId || "all", unit: target.unitId || "all", recipe: target.recipeId || "all" });
    }
    if (target?.tab) setTab(target.tab);
  }, [target]);

  const selected = useMemo(() => profiles.find(item => item.process_id === processId), [profiles, processId]);
  const selectedDefinition = useMemo(() => definitions.find(item => item.process_id === processId), [definitions, processId]);
  const isRuntime = FAB_V2_RUNTIME.has(processId);
  const tabs = useMemo(() => TAB_DEFS.map(item => ({ ...item, label: language === "en" ? item.en : item.ko, en: null })), [language]);
  const processEquipment = useMemo(() => equipment.filter(item => item.process_id === processId), [equipment, processId]);
  const filterValues = useMemo(() => {
    const forEquipment = filters.equipment === "all" ? processEquipment : processEquipment.filter(item => item.equipment_id === filters.equipment);
    const forUnit = filters.unit === "all" ? forEquipment : forEquipment.filter(item => item.unit_id === filters.unit);
    const configuredRecipes = (selectedDefinition?.recipes || []).map(item => item.recipe_id).filter(Boolean);
    return {
      equipment: [...new Set(processEquipment.map(item => item.equipment_id).filter(Boolean))],
      unit: [...new Set(forEquipment.map(item => item.unit_id).filter(Boolean))],
      recipe: [...new Set([...forUnit.map(item => item.recipe_id).filter(Boolean), ...configuredRecipes])],
    };
  }, [filters.equipment, filters.unit, processEquipment, selectedDefinition]);

  function updateFilter(key, value) {
    setFilters(current => {
      if (key === "equipment") return { equipment: value, unit: "all", recipe: "all" };
      if (key === "unit") return { ...current, unit: value, recipe: "all" };
      return { ...current, [key]: value };
    });
  }

  function chooseProcess(nextProcessId) {
    setProcessId(nextProcessId);
    setFilters({ equipment: "all", unit: "all", recipe: "all" });
  }

  return (
    <div>
      <div className="process-selector" role="list" aria-label={text("공정 선택", "Process selector")}>
        {profiles.map(profile => {
          const isFabV2 = FAB_V2_RUNTIME.has(profile.process_id);
          const runtime = runtimeByProcess[profile.process_id];
          const units = equipment.filter(item => item.process_id === profile.process_id);
          const authoritativeRunState = String(runtime?.runState || "").toLowerCase();
          const running = authoritativeRunState
            ? authoritativeRunState === "running"
            : units.some(item => String(item.machine_state || "").toUpperCase() === "RUNNING");
          const state = !isFabV2
            ? "offline"
            : !runtime?.apiAvailable
              ? "unavailable"
              : running
                ? "live"
                : runtime.hasData
                  ? "stored"
                  : "ready";
          const stateLabel = {
            live: text("LIVE", "LIVE"),
            stored: text("최근 Run 저장됨", "LATEST STORED"),
            ready: text("Runtime 준비", "RUNTIME READY"),
            unavailable: text("API 확인 필요", "API UNAVAILABLE"),
            offline: text("연결 안 됨", "NOT CONNECTED"),
          }[state];
          return (
            <button
              type="button"
              key={profile.process_id}
              className={`focusable ${processId === profile.process_id ? "is-active" : ""}`}
              data-runtime-state={state}
              title={isFabV2 ? text(`${profile.display_name} FAB v2 runtime · ${stateLabel}`, `${profile.display_name} FAB v2 runtime · ${stateLabel}`) : stateLabel}
              onClick={() => chooseProcess(profile.process_id)}
            >
              <span className={`status-dot ${state === "live" ? "status-normal" : state === "stored" ? "status-warning" : "status-offline"}`} />
              <span>{profile.display_name}</span>
              <small>{stateLabel}</small>
            </button>
          );
        })}
        {!profiles.length && <span className="chip">{text("공정 프로필을 불러오는 중…", "Loading process profiles…")}</span>}
      </div>
      {isRuntime && (
        <div className="process-context-filters panel" aria-label={text("공정 컨텍스트 필터", "Process context filters")}>
          <FilterSelect label={text("설비", "Equipment")} value={filters.equipment} values={filterValues.equipment} onChange={value => updateFilter("equipment", value)} allLabel={text("전체 설비", "All equipment")} />
          <FilterSelect label="Unit" value={filters.unit} values={filterValues.unit} onChange={value => updateFilter("unit", value)} allLabel={text("전체 Unit", "All units")} />
          <FilterSelect label="Recipe" value={filters.recipe} values={filterValues.recipe} onChange={value => updateFilter("recipe", value)} allLabel={text("전체 Recipe", "All recipes")} />
          <span className="process-context-note">{processEquipment.length ? text(`${processEquipment.length}개 FAB Unit context`, `${processEquipment.length} FAB unit contexts`) : text("v2 inventory 대기 · legacy 화면 유지", "Waiting for v2 inventory · legacy view preserved")}</span>
        </div>
      )}
      <SubTabs tabs={tabs} active={tab} onChange={setTab} />
      {isRuntime ? <FabLivePanel processId={processId} filters={filters} definition={selectedDefinition} /> : null}
      {processId === "etch" ? <EtchMonitoring section={tab} filters={filters} onEquipmentSelect={value => updateFilter("equipment", value)} /> : null}
      {selected && MULTIMODAL_RUNTIME.has(processId) ? <GenericProcessMonitoring profile={selected} section={tab} filters={filters} /> : null}
      {selected && !isRuntime ? <DisconnectedProcess profile={selected} section={tab} /> : null}
    </div>
  );
}

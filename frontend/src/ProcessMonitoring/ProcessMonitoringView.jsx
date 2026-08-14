import React, { useEffect, useMemo, useState } from "react";

import { Icon, Panel, SubTabs } from "../lib";
import { useUi } from "../UiContext";
import EtchMonitoring from "./EtchMonitoring";
import GenericProcessMonitoring from "./GenericProcessMonitoring";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const TAB_DEFS = [
  { id: "overview", ko: "개요", en: "Overview", icon: "gauge" },
  { id: "equipment", ko: "설비", en: "Equipment", icon: "cpu" },
  { id: "trend", ko: "공정 추세", en: "Process Trend", icon: "pulse" },
  { id: "anomaly", ko: "이상", en: "Anomaly", icon: "alert" },
  { id: "model", ko: "모델", en: "Model", icon: "box" },
];
const MULTIMODAL_RUNTIME = new Set(["photo", "deposition", "cmp"]);

function DemoProcess({ profile, section }) {
  const { text } = useUi();
  const params = profile.parameters || [];
  return (
    <div className="process-section">
      <div className="source-notice is-demo"><Icon name="alert" size={13} />{text("데모 프로필 · 연결 안 됨 · 실제 Fab telemetry가 아닙니다.", "Demo profile · Not connected · Not real Fab telemetry.")}</div>
      <section className="panel process-hero is-demo">
        <div>
          <div className="chamber-eyebrow"><span />{profile.display_name.toUpperCase()} · {text("확장 프로필", "EXTENSION PROFILE")}</div>
          <h2>{profile.equipment_type}</h2>
          <p>{text("동일 UI가 parameter metadata를 바꾸어 확장되는 구조입니다. 현재 detector/model은 연결하지 않았습니다.", "The same UI can extend through parameter metadata. No detector/model is connected for this profile yet.")}</p>
        </div>
        <span className="chip">{section.toUpperCase()} · {text("연결 안 됨", "NOT CONNECTED")}</span>
      </section>
      <Panel title={text(`${profile.display_name} 파라미터 프로필`, `${profile.display_name} parameter profile`)} icon="layers" right={<span className="chip">{text("데모 메타데이터", "DEMO METADATA")}</span>}>
        <div className="process-profile-grid">
          {params.map(item => <div key={item.id}><span>{item.label}</span><strong className="mono">— {item.unit}</strong><small>{item.normal_range}</small></div>)}
        </div>
      </Panel>
    </div>
  );
}

export default function ProcessMonitoringView({ target }) {
  const { language, text } = useUi();
  const [profiles, setProfiles] = useState([]);
  const [processId, setProcessId] = useState(target?.processId || "etch");
  const [tab, setTab] = useState(target?.tab || "overview");

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/process/profiles`).then(response => response.ok ? response.json() : []).then(setProfiles).catch(() => setProfiles([]));
  }, []);
  useEffect(() => {
    if (target?.processId) setProcessId(target.processId);
    if (target?.tab) setTab(target.tab);
  }, [target]);

  const selected = useMemo(() => profiles.find(item => item.process_id === processId), [profiles, processId]);
  const isRuntime = processId === "etch" || MULTIMODAL_RUNTIME.has(processId);
  const tabs = useMemo(() => TAB_DEFS.map(item => ({ ...item, label: language === "en" ? item.en : item.ko, en: null })), [language]);

  return (
    <div>
      <div className="process-selector" role="list" aria-label={text("공정 선택", "Process selector")}>
        {profiles.map(profile => {
          const connected = profile.process_id === "etch" || MULTIMODAL_RUNTIME.has(profile.process_id);
          return (
            <button type="button" key={profile.process_id} className={`focusable ${processId === profile.process_id ? "is-active" : ""}`} onClick={() => setProcessId(profile.process_id)}>
              <span className={`status-dot ${connected ? "status-normal" : "status-offline"}`} />
              <span>{profile.display_name}</span>
              <small>{connected ? text("Synthetic Runtime", "Synthetic Runtime") : text("데모", "Demo")}</small>
            </button>
          );
        })}
        {!profiles.length && <span className="chip">{text("공정 프로필을 불러오는 중…", "Loading process profiles…")}</span>}
      </div>
      <SubTabs tabs={tabs} active={tab} onChange={setTab} />
      {processId === "etch" ? <EtchMonitoring section={tab} /> : null}
      {selected && MULTIMODAL_RUNTIME.has(processId) ? <GenericProcessMonitoring profile={selected} section={tab} /> : null}
      {selected && !isRuntime ? <DemoProcess profile={selected} section={tab} /> : null}
    </div>
  );
}
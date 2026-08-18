import React, { useMemo, useState } from "react";
import { SubTabs } from "./lib";
import { useUi } from "./UiContext";
import MlopsView from "./MlopsView";
import MlopsAgentView from "./MlopsAgentView";
import EtchMonitoring from "./ProcessMonitoring/EtchMonitoring";

const SUB_DEFS = [
  { id: "chamber", ko: "Chamber 모델 운영", en: "Live Model Lifecycle", icon: "activity" },
  { id: "console", ko: "Legacy 모델 데모", en: "Legacy Registry Demo", icon: "box" },
  { id: "agent", ko: "Legacy MLOps Agent", en: "Legacy Agent Demo", icon: "bot" },
];

export default function MlopsWorkspace() {
  const { language, text } = useUi();
  const [sub, setSub] = useState("chamber");
  const tabs = useMemo(() => SUB_DEFS.map(item => ({ ...item, label: language === "en" ? item.en : item.ko, en: null })), [language]);
  return (
    <div>
      <SubTabs tabs={tabs} active={sub} onChange={setSub} />
      {sub === "chamber" && <EtchMonitoring section="model" />}
      {sub === "console" && <><div className="source-notice">{text("Legacy / Demo · 실제 Chamber registry와 분리된 기존 wafer 모델 시뮬레이션입니다.", "Legacy / Demo · Existing wafer-model simulation, separate from the live Chamber registry.")}</div><MlopsView /></>}
      {sub === "agent" && <><div className="source-notice">{text("Legacy / Demo · 기존 fleet-level 시뮬레이션 Agent입니다.", "Legacy / Demo · Existing fleet-level simulation agent.")}</div><MlopsAgentView /></>}
    </div>
  );
}
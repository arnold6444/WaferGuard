import React, { useState } from "react";
import { SubTabs } from "./lib";
import MlopsView from "./MlopsView";
import MlopsAgentView from "./MlopsAgentView";
import EtchMonitoring from "./ProcessMonitoring/EtchMonitoring";

const SUBS = [
  { id: "chamber", label: "Chamber 모델 운영", en: "Live Model Lifecycle", icon: "activity" },
  { id: "console", label: "Legacy 모델 데모", en: "Legacy Registry Demo", icon: "box" },
  { id: "agent",   label: "Legacy MLOps Agent", en: "Legacy Agent Demo", icon: "bot" },
];

// MLOps tab = the model lifecycle: the rule-based registry/drift console, and
// the fleet-level MLOps agent that triages performance and recommends retraining.
export default function MlopsWorkspace() {
  const [sub, setSub] = useState("chamber");
  return (
    <div>
      <SubTabs tabs={SUBS} active={sub} onChange={setSub} />
      {sub === "chamber" && <EtchMonitoring section="model" />}
      {sub === "console" && <><div className="source-notice">Legacy / Demo · 실제 Chamber registry와 분리된 기존 wafer 모델 시뮬레이션입니다.</div><MlopsView /></>}
      {sub === "agent" && <><div className="source-notice">Legacy / Demo · 기존 fleet-level 시뮬레이션 Agent입니다.</div><MlopsAgentView /></>}
    </div>
  );
}

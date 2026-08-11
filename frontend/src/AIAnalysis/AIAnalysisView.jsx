import React from "react";

import AgentView from "../AgentView";
import { Icon } from "../lib";

const STEPS = ["Current Incident", "Root Cause Candidates", "Similar Cases", "Recommended Actions", "Agent Trace"];

export default function AIAnalysisView({ focusId, onFocusHandled }) {
  return (
    <div className="ai-analysis-page">
      <section className="panel ai-analysis-hero">
        <div><div className="chamber-eyebrow"><span />INSPECTION AGENT · ENGINEER REVIEW</div><h2>Wafer, Metrology, Vision, Lot, 공정 이상 후보를 함께 검토합니다.</h2><p>공정 event는 검사 이전 30분의 시간적 연관 후보이며 물리적 원인 확정이 아닙니다.</p></div>
        <span className="chip"><Icon name="bot" size={12} />HUMAN IN THE LOOP</span>
      </section>
      <div className="ai-analysis-steps">{STEPS.map((step, index) => <div key={step}><span className="mono">0{index + 1}</span><strong>{step}</strong></div>)}</div>
      <AgentView focusId={focusId} onFocusHandled={onFocusHandled} />
    </div>
  );
}

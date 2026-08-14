import React from "react";

import AgentView from "../AgentView";
import { Icon } from "../lib";
import { useUi } from "../UiContext";

const STEP_DEFS = [
  { ko: "현재 Incident", en: "Current Incident" },
  { ko: "원인 후보", en: "Root Cause Candidates" },
  { ko: "유사 사례", en: "Similar Cases" },
  { ko: "권장 조치", en: "Recommended Actions" },
  { ko: "Agent 추적", en: "Agent Trace" },
];

export default function AIAnalysisView({ focusId, onFocusHandled }) {
  const { text } = useUi();
  return (
    <div className="ai-analysis-page">
      <section className="panel ai-analysis-hero">
        <div>
          <div className="chamber-eyebrow"><span />INSPECTION AGENT · ENGINEER REVIEW</div>
          <h2>{text("웨이퍼, Metrology, Vision, Lot, 공정 이상 후보를 함께 검토합니다.", "Review wafer, metrology, vision, Lot, and process anomaly evidence together.")}</h2>
          <p>{text("공정 event는 검사 이전 30분의 시간적 연관 후보이며 물리적 원인 확정이 아닙니다.", "Process events are temporal candidates from the 30 minutes before inspection, not confirmed physical causes.")}</p>
        </div>
        <span className="chip"><Icon name="bot" size={12} />HUMAN IN THE LOOP</span>
      </section>
      <div className="ai-analysis-steps">{STEP_DEFS.map((step, index) => <div key={step.en}><span className="mono">0{index + 1}</span><strong>{text(step.ko, step.en)}</strong></div>)}</div>
      <AgentView focusId={focusId} onFocusHandled={onFocusHandled} />
    </div>
  );
}
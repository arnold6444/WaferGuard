import React, { useEffect, useState } from "react";

import AgentView from "../AgentView";
import { fetchJson, rowsFrom } from "../fabApi";
import { Icon, Panel } from "../lib";
import { useUi } from "../UiContext";

const STEP_DEFS = [
  { ko: "현재 Incident", en: "Current Incident" },
  { ko: "원인 후보", en: "Root Cause Candidates" },
  { ko: "유사 사례", en: "Similar Cases" },
  { ko: "권장 조치", en: "Recommended Actions" },
  { ko: "Agent 추적", en: "Agent Trace" },
];

function candidateRows(payload) {
  const source = payload?.rca || payload?.result || payload;
  const rows = rowsFrom(source, ["candidate_causes", "candidates", "root_cause_candidates", "ranked_candidates", "items"]);
  return rows.map((candidate, index) => {
    const evidence = candidate.evidence ?? candidate.related_tags ?? candidate.supporting_evidence ?? [];
    return {
      label: candidate.label || candidate.candidate || candidate.cause || candidate.name || candidate.category || `Candidate ${index + 1}`,
      score: candidate.score ?? candidate.confidence ?? candidate.rank_score,
      evidence: Array.isArray(evidence) ? evidence : [evidence],
      explanation: candidate.explanation || candidate.rationale || candidate.summary || "",
    };
  });
}

function evidenceLabel(value) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return String(value ?? "");
  return value.label || value.signal || value.tag || value.description || value.type || "evidence";
}

function LocalAnalysisPanel() {
  const { text } = useUi();
  const [state, setState] = useState({ loading: true, payload: null, error: "" });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    fetchJson("/api/v1/fab/analysis/latest", { signal: controller.signal })
      .then(payload => { if (!cancelled) setState({ loading: false, payload, error: "" }); })
      .catch(error => {
        if (!cancelled && error.name !== "AbortError") {
          setState({ loading: false, payload: null, error: text("로컬 분석 결과를 불러오지 못했습니다.", "Could not load local analysis results.") });
        }
      });
    return () => { cancelled = true; controller.abort(); };
  }, [text]);

  const payload = state.payload || {};
  const ready = payload.status === "ready";
  const features = Array.isArray(payload.feature_importance) ? payload.feature_importance.slice(0, 8) : [];
  const correlations = Array.isArray(payload.top_correlations) ? payload.top_correlations.slice(0, 5) : [];
  const maximum = Math.max(0.000001, ...features.map(item => Number(item.importance) || 0));
  const metrics = payload.model?.metrics || {};

  return (
    <Panel title={text("로컬 데이터 분석", "Local Data Analysis")} icon="database" right={<span className="chip">NOTEBOOK / PY</span>}>
      {state.loading && <div className="process-list-empty">{text("분석 결과를 확인하는 중…", "Checking analysis output…")}</div>}
      {state.error && <div className="source-notice is-demo"><Icon name="alert" size={13} />{state.error}</div>}
      {!state.loading && !state.error && !ready && (
        <div className="source-notice is-demo"><Icon name="info" size={13} />{text("아직 결과가 없습니다. notebooks/fab_local_analysis.ipynb 또는 run_fab_analysis.py를 실행하세요.", "No result yet. Run notebooks/fab_local_analysis.ipynb or run_fab_analysis.py.")}</div>
      )}
      {ready && (
        <div className="local-analysis-content">
          <div className="rca-summary-row">
            <div><span>{text("공정", "Process")}</span><strong>{String(payload.process_id || "—").toUpperCase()}</strong></div>
            <div><span>{text("행 / 열", "Rows / Columns")}</span><strong className="mono">{payload.dataset?.rows ?? 0} / {payload.dataset?.columns ?? 0}</strong></div>
            <div><span>{text("결측 / 중복", "Missing / Duplicates")}</span><strong className="mono">{payload.dataset?.missing_cells ?? 0} / {payload.dataset?.duplicate_rows ?? 0}</strong></div>
            <div><span>{text("분석 방식", "Analysis Mode")}</span><strong>{String(metrics.mode || "—").replaceAll("_", " ")}</strong></div>
          </div>
          <div className="analysis-grid">
            <section>
              <h4>Feature Importance</h4>
              {features.map(item => (
                <div className="analysis-bar-row" key={item.feature}>
                  <span title={item.feature}>{item.feature}</span>
                  <div><i style={{ width: `${Math.max(2, (Number(item.importance) / maximum) * 100)}%` }} /></div>
                  <strong className="mono">{Number(item.importance).toFixed(3)}</strong>
                </div>
              ))}
              {!features.length && <div className="process-list-empty">{text("표시할 중요도가 없습니다.", "No importance values available.")}</div>}
            </section>
            <section>
              <h4>{text("강한 상관관계", "Top Correlations")}</h4>
              <div className="analysis-correlation-list">
                {correlations.map(item => (
                  <div key={`${item.left}-${item.right}`}><span>{item.left} ↔ {item.right}</span><strong className="mono">{Number(item.correlation).toFixed(3)}</strong></div>
                ))}
                {!correlations.length && <div className="process-list-empty">{text("표시할 상관관계가 없습니다.", "No correlations available.")}</div>}
              </div>
            </section>
          </div>
          <div className="source-notice"><Icon name="info" size={13} />{text("원본 데이터는 로컬 data/input에만 있고, 화면은 data/output/dashboard_summary.json의 요약만 읽습니다.", "Raw data stays in local data/input; the UI reads only data/output/dashboard_summary.json.")}</div>
        </div>
      )}
    </Panel>
  );
}

function RcaPanel({ processRunId }) {
  const { text } = useUi();
  const [state, setState] = useState({ loading: true, payload: null, error: "" });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setState({ loading: true, payload: null, error: "" });
    fetchJson(`/api/v1/fab/rca/${encodeURIComponent(processRunId)}`, { signal: controller.signal })
      .then(payload => { if (!cancelled) setState({ loading: false, payload, error: "" }); })
      .catch(error => {
        if (!cancelled && error.name !== "AbortError") setState({ loading: false, payload: null, error: text("RCA 결과를 아직 사용할 수 없습니다.", "RCA result is not available yet.") });
      });
    return () => { cancelled = true; controller.abort(); };
  }, [processRunId, text]);

  const candidates = candidateRows(state.payload);
  const source = state.payload?.rca || state.payload?.result || state.payload || {};
  return (
    <Panel title={text("Evidence-ranked Candidate RCA", "Evidence-ranked Candidate RCA")} icon="activity" right={<span className="chip">{processRunId}</span>}>
      {state.loading && <div className="process-list-empty">{text("RCA 근거를 불러오는 중…", "Loading RCA evidence…")}</div>}
      {state.error && <div className="source-notice is-demo"><Icon name="alert" size={13} />{state.error}</div>}
      {!state.loading && !state.error && (
        <div className="rca-panel-content">
          <div className="source-notice"><Icon name="alert" size={13} />{text("모든 항목은 확정 원인이 아닌 Candidate root cause입니다. 주입 fault/GT는 이 화면에서 사용하거나 표시하지 않습니다.", "Every item is a Candidate root cause, not a confirmed cause. Injected faults and GT are neither used nor shown here.")}</div>
          <div className="rca-summary-row">
            <div><span>Process Run</span><strong className="mono">{source.process_run_id || processRunId}</strong></div>
            <div><span>{text("융합 Risk", "Fused Risk")}</span><strong className="mono">{(source.fused_risk ?? source.overall_risk) == null ? "—" : Number(source.fused_risk ?? source.overall_risk).toFixed(3)}</strong></div>
            <div><span>{text("모델", "Model")}</span><strong className="mono">{source.model_version || source.rca_version || source.model_or_rule_version || "rule-evidence"}</strong></div>
          </div>
          <div className="rca-candidate-list">
            {candidates.map((candidate, index) => (
              <article key={`${candidate.label}-${index}`}>
                <span className="mono">#{index + 1}</span>
                <div>
                  <strong>Candidate root cause · {String(candidate.label).replaceAll("_", " ")}</strong>
                  {candidate.explanation && <p>{candidate.explanation}</p>}
                  <div>{candidate.evidence.slice(0, 8).map((evidence, evidenceIndex) => <span className="chip" key={`${evidenceLabel(evidence)}-${evidenceIndex}`}>{evidenceLabel(evidence)}</span>)}</div>
                </div>
                <em className="mono">{candidate.score == null ? "—" : Number(candidate.score).toFixed(3)}</em>
              </article>
            ))}
            {!candidates.length && <div className="process-list-empty">{text("순위화된 후보가 없습니다.", "No ranked candidates are available.")}</div>}
          </div>
        </div>
      )}
    </Panel>
  );
}

export default function AIAnalysisView({ target, focusId, onFocusHandled }) {
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
      <LocalAnalysisPanel />
      {target?.processRunId && <RcaPanel processRunId={target.processRunId} />}
      <AgentView focusId={focusId} onFocusHandled={onFocusHandled} />
    </div>
  );
}

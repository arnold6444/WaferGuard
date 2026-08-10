import React, { useEffect, useRef, useState } from "react";
import { Icon, Panel, Metric, RiskBadge, RiskGauge, Modal, Markdown, ToolCalls, TOOL_META } from "./lib";
import { useStream } from "./SettingsContext";
import { useDefectChat } from "./DefectChatContext";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const MODE_CHIP = {
  llm:       { color: "var(--accent)", label: "LLM (GPT-4o-mini)" },
  stub:      { color: "var(--med)",    label: "룰 기반 폴백" },
  rule_only: { color: "var(--text-3)", label: "Agent 미참여" },
  pending:   { color: "var(--med)",    label: "AI 분석 중…" },
  error:     { color: "var(--high)",   label: "Agent 오류" },
};

const DECISION_LABEL = {
  approved:     { color: "var(--low)",    label: "조치 완료" },
  needs_review: { color: "var(--med)",    label: "추가 리뷰" },
  false_alarm:  { color: "var(--text-3)", label: "오탐 처리" },
};

const CAUSE_CONF = [0.42, 0.27, 0.15, 0.10];

// queue status filters — "조치 대기"는 아직 판단이 없는 케이스
const QUEUE_FILTERS = [
  { id: "all",          label: "전체" },
  { id: "pending",      label: "조치 대기" },
  { id: "needs_review", label: "추가 리뷰" },
  { id: "approved",     label: "조치 완료" },
  { id: "false_alarm",  label: "오탐" },
];
const matchesFilter = (row, f) =>
  f === "all" ? true : f === "pending" ? !row.engineer_decision : row.engineer_decision === f;

/* ---------- queue ---------- */

function QueueRow({ row, active, onSelect }) {
  const decided = !!row.engineer_decision;
  const d = DECISION_LABEL[row.engineer_decision];
  return (
    <button onClick={() => onSelect(row.id)} className="focusable"
      style={{
        display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left",
        padding: "8px 10px", borderRadius: 7, cursor: "pointer", font: "inherit",
        border: `1px solid ${active ? "var(--accent-line)" : "transparent"}`,
        background: active ? "var(--accent-dim)" : "transparent",
        opacity: decided && !active ? .55 : 1,
      }}>
      <RiskBadge level={row.risk_level} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
          <span className="mono" style={{ fontSize: 11.5, color: "var(--text)", fontWeight: 600 }}>{row.wafer_id}</span>
          <span style={{ fontSize: 11, color: "var(--text-2)" }}>{row.defect_type}</span>
        </div>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--text-3)", marginTop: 2 }}>
          score {(row.risk_score ?? 0).toFixed(2)} · {(row.created_at || "").slice(5, 19).replace("T", " ")}
        </div>
      </div>
      {decided
        ? <span className="chip" style={{ fontSize: 9, color: d?.color, borderColor: d?.color }}>{d?.label || row.engineer_decision}</span>
        : <span className="chip" style={{ fontSize: 9, color: "var(--med)", borderColor: "var(--med)" }}>조치 대기</span>}
    </button>
  );
}


/* ---------- evidence popover ---------- */

function EvidenceBlock({ source }) {
  if (!source) return null;
  if (source.type === "metrology") {
    const h = source.hit;
    return (
      <div className="panel-inset" style={{ padding: "9px 11px", marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span className="chip" style={{ fontSize: 8.5, color: h.severity === "Critical" ? "var(--high)" : "var(--med)", borderColor: h.severity === "Critical" ? "var(--high)" : "var(--med)" }}>
            계측 룰 · {h.severity}
          </span>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text)" }}>{h.signal}</span>
        </div>
        <div className="mono" style={{ fontSize: 10.5, color: "var(--text-2)" }}>{h.evidence}</div>
        <div style={{ fontSize: 10.5, color: "var(--text-3)" }}>담당: {h.owner} · risk Δ +{h.risk_delta}</div>
      </div>
    );
  }
  const c = source.case;
  if (!c) {
    return (
      <div className="panel-inset" style={{ padding: "9px 11px", marginTop: 6, fontSize: 11, color: "var(--text-3)" }}>
        연결된 RAG 사례가 없습니다.
      </div>
    );
  }
  return (
    <div className="panel-inset" style={{ padding: "9px 11px", marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span className="chip" style={{ fontSize: 8.5, color: "var(--accent)", borderColor: "var(--accent-line)" }}>RAG 유사 사례</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", flex: 1, minWidth: 0 }}>{c.title}</span>
        {c.similarity != null && (
          <span className="mono" style={{ fontSize: 9.5, color: "var(--accent)", fontWeight: 600 }}>유사도 {Math.round(c.similarity * 100)}%</span>
        )}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-2)", lineHeight: 1.5 }}>{c.summary}</div>
      <div style={{ fontSize: 10.5, color: "var(--text-3)" }}>당시 조치: {c.action}</div>
      <div className="mono" style={{ fontSize: 9.5, color: "var(--text-3)" }}>
        {[c.case_id, c.equipment, c.date].filter(Boolean).join(" · ")}
        {c.line_context && (c.case_id || c.equipment || c.date ? " · " : "") + c.line_context}
      </div>
    </div>
  );
}

function EvidenceTrigger({ source, onOpen }) {
  const tag = source?.type === "metrology" ? "계측 근거" : "RAG 근거";
  return (
    <button onClick={onOpen} className="focusable" title={`${tag} 보기`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 3, cursor: "pointer", flex: "none",
        background: "transparent", border: "1px solid var(--border-soft)", borderRadius: 6,
        padding: "2px 6px", color: "var(--text-3)", font: "inherit", fontSize: 9.5,
      }}>
      <Icon name="history" size={10} />근거
    </button>
  );
}

/* ---------- evidence modal ---------- */

function EvidenceModal({ entry, cases, onClose }) {
  if (!entry) return null;
  const isAction = entry.kind === "action";
  const source = entry.source;
  const metrology = source?.type === "metrology" ? source : null;
  // pull the retrieved RAG cases that this recommendation was reasoned from
  const ragCases = (cases || []).filter(Boolean);
  const primaryTitle = source?.case?.title;

  const reasoning = isAction
    ? (metrology
        ? `이 액션은 계측 룰 위반에서 직접 도출되었습니다. 동일한 신호가 잡혔던 과거 대응 사례를 함께 참고해 다음 조치를 권장합니다.`
        : `에이전트가 과거 결함 대응 이력에서 유사 상황을 검색했고, 그때 효과가 있었던 조치를 현재 케이스에 맞춰 권장 액션으로 제안했습니다.`)
    : `에이전트가 과거 결함 대응 이력에서 유사 사례를 검색(RAG)해, 그 패턴·조치 내역을 현재 웨이퍼 상태와 대조했습니다. 그 결과 "${entry.label}"을(를) 가장 가능성 높은 원인으로 ${Math.round((entry.conf ?? 0) * 100)}% 확신도로 추정했습니다.`;

  return (
    <Modal open onClose={onClose} icon="history"
      title={isAction ? "권장 액션 근거" : "추정 원인 근거"}
      right={<span className="chip" style={{ fontSize: 9, color: "var(--accent)", borderColor: "var(--accent-line)" }}>
        {metrology ? "계측 룰 + RAG" : "RAG 검색 근거"}
      </span>}>
      {/* the claim being explained */}
      <div className="panel-inset" style={{ padding: "10px 12px", display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 650, color: "var(--text)", flex: 1, minWidth: 0 }}>{entry.label}</span>
        {!isAction && entry.conf != null && (
          <span className="mono" style={{ fontSize: 13, color: "var(--accent)", fontWeight: 600 }}>{Math.round(entry.conf * 100)}%</span>
        )}
      </div>

      {/* reasoning narrative */}
      <div style={{ fontSize: 12, color: "var(--text-2)", lineHeight: 1.7, margin: "12px 2px 6px" }}>
        {reasoning}
      </div>

      {/* metrology rule (only for rule-derived actions) */}
      {metrology && <EvidenceBlock source={metrology} />}

      {/* the past RAG cases that grounded the reasoning */}
      <div className="label-cap" style={{ margin: "16px 0 6px" }}>
        근거가 된 과거 사례 · RAG Top-{ragCases.length || 0}
      </div>
      {ragCases.length === 0 && (
        <div className="panel-inset" style={{ padding: "9px 11px", fontSize: 11, color: "var(--text-3)" }}>
          연결된 과거 RAG 사례가 없습니다.
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {ragCases.map((c, i) => {
          const isPrimary = primaryTitle && c.title === primaryTitle;
          return (
            <div key={i} style={{ position: "relative" }}>
              {isPrimary && (
                <span className="chip" style={{
                  position: "absolute", top: -7, right: 8, zIndex: 1, fontSize: 8,
                  color: "var(--accent)", borderColor: "var(--accent-line)", background: "var(--panel)",
                }}>주요 매칭</span>
              )}
              <div style={{ outline: isPrimary ? "1px solid var(--accent-line)" : "none", borderRadius: 8 }}>
                <EvidenceBlock source={{ type: "rag", case: c }} />
              </div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

/* ---------- AI recommendation ---------- */

function CauseCard({ idx, cause, onEvidence }) {
  return (
    <div className="panel-inset" style={{ padding: "6px 9px", display: "flex", flexDirection: "column", gap: 5 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span className="mono" style={{
          fontSize: 9, fontWeight: 700, color: "var(--accent)", background: "var(--accent-dim)",
          borderRadius: 4, padding: "1px 5px", flex: "none",
        }}>{idx + 1}</span>
        <span style={{ fontSize: 11.5, color: "var(--text)", fontWeight: 600, flex: 1, minWidth: 0 }}>{cause.label}</span>
        <span className="mono" style={{ fontSize: 11, color: "var(--accent)", fontWeight: 600 }}>{(cause.conf * 100).toFixed(0)}%</span>
        <EvidenceTrigger source={cause.source} onOpen={() => onEvidence({ kind: "cause", label: cause.label, conf: cause.conf, source: cause.source })} />
      </div>
      <div style={{ height: 3, background: "var(--panel-2)", borderRadius: 99, overflow: "hidden" }}>
        <div style={{ width: `${cause.conf * 100}%`, height: "100%", background: "var(--accent)", opacity: .7, borderRadius: 99 }} />
      </div>
    </div>
  );
}

/* ---------- multi-agent handoff: visualize escalate_to_mlops in the trace ---------- */

function DelegationFlow({ calls }) {
  const escs = (calls || []).filter(c => c.name === "escalate_to_mlops" && c.result && typeof c.result === "object");
  if (escs.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {escs.map((c, i) => {
        const r = c.result || {};
        const reason = r.reason || c.args?.reason || "";
        const tools = r.mlops_tools_used || [];
        const decision = (r.mlops_decision || "").trim();
        return (
          <div key={i} className="panel-inset" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 9, border: "1px solid var(--accent-line)" }}>
            {/* handoff header: 검사 에이전트 → MLOps 에이전트 */}
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
              <span className="chip" style={{ fontSize: 8.5, color: "var(--accent)", borderColor: "var(--accent-line)" }}>멀티에이전트 위임</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 600, color: "var(--text)" }}>
                <Icon name="bot" size={12} style={{ color: "var(--accent)" }} />검사 에이전트
                <Icon name="handoff" size={14} style={{ color: "var(--text-3)" }} />
                <Icon name="box" size={12} style={{ color: "var(--med)" }} />MLOps 에이전트
              </span>
            </div>
            {reason && <div style={{ fontSize: 11, color: "var(--text-2)", lineHeight: 1.5 }}>위임 사유: {reason}</div>}
            {tools.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, alignItems: "center" }}>
                <span className="label-cap">MLOps가 조회한 도구</span>
                {tools.map((t, j) => {
                  const meta = TOOL_META[t] || { icon: "cpu", label: t };
                  return (
                    <span key={j} className="chip" style={{ fontSize: 9, color: "var(--text-2)" }}>
                      <Icon name={meta.icon} size={10} />{meta.label}
                    </span>
                  );
                })}
              </div>
            )}
            {decision && (
              <div>
                <div className="label-cap" style={{ marginBottom: 4 }}>MLOps 에이전트 판단</div>
                <div style={{ fontSize: 11.5, color: "var(--text)", lineHeight: 1.6, background: "var(--panel-2)", borderRadius: 7, padding: "8px 10px" }}>
                  <Markdown text={decision} />
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ---------- agent analysis bubble (the agent's core judgment, in the chat) ----------
   Renders only the essentials — probable causes (%) and recommended next actions —
   as the opening assistant message. Each item is clickable to inspect the evidence
   it was reasoned from, and the full tool-by-tool reasoning sits behind a toggle. */

function AgentAnalysisBubble({
  causes, actions, decided, busy, onExecute, onEvidence,
  reviewResult, reviewNote, agentRun, llmOn, traceLoading, mode,
}) {
  const [showReasoning, setShowReasoning] = useState(false);
  const hasReasoning = llmOn && (agentRun.displayTools.length > 0 || agentRun.displayText || agentRun.running);
  // hold the causes/actions back until the agent's output has actually arrived,
  // so they don't pop up before the LLM has reasoned. Reveal once: there's agent
  // output, the case is already decided, or we've stopped waiting (poll gave up).
  const analysisReady = decided || !!agentRun.displayText || (!traceLoading && !agentRun.running);

  return (
    <div style={{
      alignSelf: "stretch", background: "var(--panel-2)", border: "1px solid var(--border-soft)",
      borderRadius: 12, padding: "11px 13px", display: "flex", flexDirection: "column", gap: 11,
    }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <Icon name="bot" size={14} style={{ color: "var(--accent)" }} />
        <span style={{ fontSize: 12, fontWeight: 650, color: "var(--text)" }}>에이전트 판단</span>
        <span className="chip" style={{ fontSize: 8.5, color: mode.color, borderColor: mode.color }}>{mode.label}</span>
        <button onClick={agentRun.run} disabled={agentRun.running || !llmOn} className="btn btn-ghost"
          title="에이전트 다시 실행" style={{ marginLeft: "auto", padding: "2px 8px", fontSize: 10, gap: 5 }}>
          <Icon name="refresh" size={11} style={agentRun.running ? { animation: "spin 1s linear infinite" } : undefined} />
          {agentRun.runLabel}
        </button>
      </div>

      {!analysisReady ? (
        /* waiting for the agent's LLM output — show progress, not premature results */
        <div className="panel-inset" style={{ padding: "16px 12px", display: "flex", flexDirection: "column", gap: 9, alignItems: "center", textAlign: "center" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--text-2)", fontSize: 12 }}>
            <Icon name="refresh" size={13} style={{ animation: "spin 1s linear infinite" }} />
            에이전트가 증거를 분석하는 중입니다…
          </span>
          <span style={{ fontSize: 10.5, color: "var(--text-3)", lineHeight: 1.5 }}>
            분석이 끝나면 추정 원인과 권장 다음 액션이 표시됩니다.
          </span>
          {agentRun.displayTools.length > 0 && (
            <div style={{ width: "100%", marginTop: 2 }}><ToolCalls calls={agentRun.displayTools} /></div>
          )}
        </div>
      ) : (
        <>
          {/* 추정 원인 — % UI, click 근거 to inspect */}
          <div>
            <div className="label-cap" style={{ marginBottom: 6 }}>추정 원인 · Probable Causes</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {causes.map((c, i) => <CauseCard key={i} idx={i} cause={c} onEvidence={onEvidence} />)}
              {causes.length === 0 && <span style={{ fontSize: 11, color: "var(--text-3)" }}>추정 원인이 아직 없습니다.</span>}
            </div>
          </div>

          {/* 권장 다음 액션 — select / write your own → record + RAG write-back */}
          <div>
            <div className="label-cap" style={{ marginBottom: 6 }}>권장 다음 액션 · Next Actions</div>
            <ActionSelector actions={actions} decided={decided} busy={busy}
              onExecute={onExecute} onEvidence={onEvidence}
              reviewResult={reviewResult} reviewNote={reviewNote} />
          </div>

          {/* multi-agent handoff (검사 → MLOps), surfaced when the agent escalated */}
          <DelegationFlow calls={agentRun.displayTools} />
        </>
      )}

      {/* full reasoning — collapsed by default, click to verify how the agent judged */}
      {hasReasoning && (
        <div style={{ borderTop: "1px solid var(--border-soft)", paddingTop: 8 }}>
          <button onClick={() => setShowReasoning(s => !s)} className="focusable"
            style={{
              display: "flex", alignItems: "center", gap: 5, background: "transparent", border: "none",
              cursor: "pointer", font: "inherit", fontSize: 10.5, color: "var(--text-3)", padding: 0,
            }}>
            <Icon name={showReasoning ? "chevD" : "chevR"} size={11} />
            판단 근거 · 분석 과정{agentRun.running ? " (실행 중…)" : ""}
          </button>
          {showReasoning && (
            <div style={{ marginTop: 8 }}>
              <AgentRunBody state={agentRun} llmOn={llmOn} traceLoading={traceLoading} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- agent chat (the single unified surface) ----------
   The agent's judgment is the opening message; the operator converses below it. */

function AgentChat({
  inspectionId, llmOn, causes, actions, decided, busy,
  onExecute, onEvidence, reviewResult, reviewNote, agentRun, traceLoading, mode,
}) {
  // chat history lives in a root-level store keyed by inspection id, so it
  // survives navigating to another tab and back
  const { getChat, setChat } = useDefectChat();
  const msgs = getChat(inspectionId);
  const setMsgs = (updater) => setChat(inspectionId, updater);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => { setInput(""); }, [inspectionId]);
  // keep the analysis card in view on open; only follow the conversation once it starts
  useEffect(() => {
    if (msgs.length > 0 && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, sending]);

  // update the last (assistant) message immutably
  const patchLast = (fn) => setMsgs(m => {
    if (!m.length) return m;
    const copy = m.slice();
    copy[copy.length - 1] = fn(copy[copy.length - 1]);
    return copy;
  });

  function applyEvent(ev) {
    if (ev.type === "tool_call") {
      patchLast(a => ({ ...a, tools: [...(a.tools || []), { name: ev.name, args: ev.args, status: "running" }] }));
    } else if (ev.type === "tool_result") {
      patchLast(a => {
        const tools = (a.tools || []).slice();
        for (let i = tools.length - 1; i >= 0; i--) {
          if (tools[i].status === "running") { tools[i] = { ...tools[i], status: "done", summary: ev.summary, result: ev.result }; break; }
        }
        return { ...a, tools };
      });
    } else if (ev.type === "token") {
      patchLast(a => ({ ...a, content: (a.content || "") + ev.text }));
    } else if (ev.type === "done") {
      patchLast(a => ({ ...a, content: ev.reply || a.content || "응답 없음", streaming: false }));
    }
  }

  // ground the chat in exactly what the engineer is looking at — the agent's
  // analysis of THIS defect, plus the on-screen causes / actions
  function buildExtraContext() {
    const lines = [];
    const analysis = (agentRun?.displayText || "").trim();
    if (analysis) {
      lines.push("에이전트가 분석한 현재 결함 판단 (이 검사에 대한 결론):");
      lines.push(analysis);
    }
    if (causes?.length) {
      lines.push("화면에 표시된 추정 원인 (확신도순):");
      causes.forEach((c, i) => lines.push(`${i + 1}. ${c.label} (${Math.round((c.conf ?? 0) * 100)}%)`));
    }
    if (actions?.length) {
      lines.push("화면에 표시된 권장 액션:");
      actions.forEach((a, i) => lines.push(`${i + 1}. ${a.label}`));
    }
    return lines.join("\n");
  }

  async function send(text) {
    const q = (text ?? input).trim();
    if (!q || sending || !inspectionId || !llmOn) return;
    const history = msgs.map(m => ({ role: m.role, content: m.content }));
    setMsgs(m => [...m,
      { role: "user", content: q },
      { role: "assistant", content: "", tools: [], streaming: true },
    ]);
    setInput("");
    setSending(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/inspect/${inspectionId}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: q, history, use_llm: llmOn, extra_context: buildExtraContext() }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const blocks = buf.split("\n\n");
        buf = blocks.pop() || "";
        for (const block of blocks) {
          const line = block.split("\n").find(l => l.startsWith("data:"));
          if (!line) continue;
          try { applyEvent(JSON.parse(line.slice(5).trim())); } catch { /* skip partial */ }
        }
      }
    } catch (e) {
      patchLast(a => ({ ...a, content: `오류: 응답을 받지 못했습니다 (${e.message})`, streaming: false }));
    } finally {
      patchLast(a => ({ ...a, streaming: false }));
      setSending(false);
    }
  }

  const SUGGESTIONS = ["그래서 뭐부터 해야 돼?", "이 설비에서 최근에도 이랬어?", "계속 밀리는 추세야?"];

  return (
    <div>
      <div ref={scrollRef} style={{ maxHeight: 560, minHeight: 320, overflowY: "auto", padding: "12px 14px", display: "flex", flexDirection: "column", gap: 9 }}>
        {/* opening message: the agent's core judgment */}
        <AgentAnalysisBubble
          causes={causes} actions={actions} decided={decided} busy={busy}
          onExecute={onExecute} onEvidence={onEvidence}
          reviewResult={reviewResult} reviewNote={reviewNote}
          agentRun={agentRun} llmOn={llmOn} traceLoading={traceLoading} mode={mode} />

        {/* quick follow-up prompts, shown until the conversation starts */}
        {msgs.length === 0 && llmOn && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "2px 1px" }}>
            {SUGGESTIONS.map(s => (
              <button key={s} onClick={() => send(s)} disabled={sending} className="focusable"
                style={{
                  fontSize: 10.5, padding: "4px 9px", borderRadius: 99, cursor: "pointer", font: "inherit",
                  border: "1px solid var(--border-soft)", background: "var(--panel)", color: "var(--text-2)",
                }}>
                {s}
              </button>
            ))}
          </div>
        )}
        {msgs.length === 0 && !llmOn && (
          <div style={{ fontSize: 11, color: "var(--text-3)", lineHeight: 1.6, padding: "2px 1px" }}>
            설정 탭의 'Agent LLM 분석'을 켜면 이 판단에 대해 추가 질문을 할 수 있습니다.
          </div>
        )}

        {msgs.map((m, i) => {
          const isUser = m.role === "user";
          const hasTools = !isUser && m.tools && m.tools.length > 0;
          const showCursor = !isUser && m.streaming && (m.content || "").length > 0;
          const thinking = !isUser && m.streaming && !hasTools && !(m.content || "").length;
          return (
            <div key={i} style={{
              alignSelf: isUser ? "flex-end" : "flex-start",
              maxWidth: "82%",
              background: isUser ? "var(--accent-dim)" : "var(--panel-2)",
              border: `1px solid ${isUser ? "var(--accent-line)" : "var(--border-soft)"}`,
              borderRadius: 10, padding: "8px 11px",
              fontSize: 12, lineHeight: 1.6, color: "var(--text)",
              whiteSpace: isUser ? "pre-wrap" : "normal",
            }}>
              {hasTools && <ToolCalls calls={m.tools} />}
              {thinking ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--text-3)" }}>
                  <Icon name="refresh" size={12} style={{ animation: "spin 1s linear infinite" }} />분석 중…
                </span>
              ) : isUser ? (
                m.content
              ) : (
                <>{m.content && <Markdown text={m.content} />}{showCursor && <span className="blink-cursor">▍</span>}</>
              )}
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 8, padding: "10px 12px", borderTop: "1px solid var(--border)" }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) send(); }}
          placeholder={llmOn ? "이 판단에 대해 질문하거나 다른 조치를 물어보세요…" : "LLM이 꺼져 있어 채팅 불가"}
          disabled={!llmOn}
          style={{
            flex: 1, background: "var(--panel-2)", border: "1px solid var(--border-strong)",
            borderRadius: 7, padding: "8px 11px", color: "var(--text)", font: "inherit", fontSize: 12,
            opacity: llmOn ? 1 : .55,
          }} />
        <button className="btn btn-accent" onClick={() => send()} disabled={sending || !input.trim() || !llmOn}>
          <Icon name="send" size={13} />전송
        </button>
      </div>
    </div>
  );
}

/* ---------- live agent run (B-7): stream the flagship inspection agent ----------
   Split into a hook (state + streaming) and a presentational body so the run can
   live as the first section of the unified Agent panel, with its re-run control
   hoisted into that panel's header. */

function useAgentRun({ inspectionId, llmOn, trace }) {
  const [tools, setTools] = useState([]);
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [ran, setRan] = useState(false);

  // reset when switching cases — show the polled background trace until re-run
  useEffect(() => { setRan(false); setTools([]); setText(""); setRunning(false); }, [inspectionId]);

  const traceTools = (trace?.tool_calls || []).map(tc => ({
    name: tc.name, args: tc.args, status: "done", summary: "", result: tc.result,
  }));
  const displayTools = ran ? tools : traceTools;
  const displayText = ran ? text : (trace?.final_action || "");
  const hasTrace = !!trace?.final_action;

  async function run() {
    if (running || !inspectionId || !llmOn) return;
    setRan(true); setRunning(true); setTools([]); setText("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/inspect/${inspectionId}/agent/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ use_llm: llmOn }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const blocks = buf.split("\n\n");
        buf = blocks.pop() || "";
        for (const block of blocks) {
          const line = block.split("\n").find(l => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (ev.type === "tool_call") {
            setTools(t => [...t, { name: ev.name, args: ev.args, status: "running" }]);
          } else if (ev.type === "tool_result") {
            setTools(t => {
              const c = t.slice();
              for (let i = c.length - 1; i >= 0; i--) {
                if (c[i].status === "running") { c[i] = { ...c[i], status: "done", summary: ev.summary, result: ev.result }; break; }
              }
              return c;
            });
          } else if (ev.type === "token") {
            setText(s => s + ev.text);
          } else if (ev.type === "done") {
            setText(s => ev.final_action || s);
          }
        }
      }
    } catch (e) {
      setText(`오류: 실행 실패 (${e.message})`);
    } finally {
      setRunning(false);
    }
  }

  const runLabel = running ? "실행 중…" : (hasTrace || ran ? "다시 실행" : "라이브 실행");
  return { displayTools, displayText, running, ran, hasTrace, run, runLabel };
}

function AgentRunBody({ state, llmOn, traceLoading }) {
  const { displayTools, displayText, running } = state;
  if (!llmOn) {
    return (
      <div style={{ fontSize: 11.5, color: "var(--text-3)", lineHeight: 1.7, padding: "4px 2px" }}>
        설정에서 'Agent LLM 분석'을 켜면 에이전트의 판단 과정(증거 해석 → 도구 호출 → 최종 판단)을 실시간으로 볼 수 있습니다.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {displayTools.length > 0 && <ToolCalls calls={displayTools} />}
      {running && displayTools.length === 0 && !displayText && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--text-3)", fontSize: 11.5 }}>
          <Icon name="refresh" size={12} style={{ animation: "spin 1s linear infinite" }} />증거 분석 중…
        </span>
      )}
      {displayText
        ? <div style={{ fontSize: 12, lineHeight: 1.65, color: "var(--text)" }}>
            <Markdown text={displayText} />{running && <span className="blink-cursor">▍</span>}
          </div>
        : !running && (
          <div style={{ fontSize: 11.5, color: "var(--text-3)", lineHeight: 1.7, padding: "4px 2px" }}>
            {traceLoading
              ? "백그라운드 에이전트 판단을 불러오는 중…"
              : "‘라이브 실행’을 누르면 에이전트가 증거를 해석하고 도구를 호출하는 과정을 실시간으로 보여줍니다."}
          </div>
        )}
    </div>
  );
}

/* ---------- response action: select a recommended action (or write your own),
   record the decision, and confirm the case was written back into the RAG corpus ---------- */

const DECISION_OPTIONS = [
  { id: "approved",     label: "조치 완료", color: "var(--low)" },
  { id: "false_alarm",  label: "오탐 처리", color: "var(--text-3)" },
  { id: "needs_review", label: "추가 리뷰", color: "var(--med)" },
];

function RadioDot({ on }) {
  return (
    <span style={{
      width: 13, height: 13, borderRadius: 99, flex: "none",
      border: `2px solid ${on ? "var(--accent)" : "var(--border-strong)"}`,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
    }}>
      {on && <span style={{ width: 6, height: 6, borderRadius: 99, background: "var(--accent)" }} />}
    </span>
  );
}

function RagWriteResult({ reviewResult }) {
  const ks = reviewResult?.knowledge_saved;
  if (!ks) return null;
  if (ks.skipped) {
    return (
      <div className="panel-inset" style={{ padding: "8px 11px", fontSize: 10.5, color: "var(--text-3)", lineHeight: 1.5 }}>
        '추가 리뷰'는 확정된 결론이 아니므로 RAG 지식베이스에는 기록하지 않았습니다.
      </div>
    );
  }
  if (!ks.ok) return null;
  return (
    <div className="panel-inset fade-in" style={{ padding: "9px 11px", display: "flex", flexDirection: "column", gap: 4, border: "1px solid var(--accent-line)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <span className="chip" style={{ fontSize: 8.5, color: "var(--accent)", borderColor: "var(--accent-line)" }}>RAG 지식베이스 기록됨</span>
        {ks.embedded && <span className="chip" style={{ fontSize: 8.5, color: "var(--low)", borderColor: "var(--low)" }}>임베딩 완료</span>}
        {ks.doc_id && <span className="mono" style={{ fontSize: 9, color: "var(--text-3)", marginLeft: "auto" }}>{ks.doc_id}</span>}
      </div>
      {ks.content && <div style={{ fontSize: 11, color: "var(--text-2)", lineHeight: 1.5 }}>{ks.content}</div>}
      <div style={{ fontSize: 9.5, color: "var(--text-3)" }}>이후 유사 검사의 RAG 검색에서 이 사례가 함께 검색됩니다.</div>
    </div>
  );
}

function ActionSelector({ actions, decided, busy, onExecute, onEvidence, reviewResult, reviewNote }) {
  const [sel, setSel] = useState(0);        // index into actions, or "custom"
  const [custom, setCustom] = useState("");
  const [decision, setDecision] = useState("approved");

  // already recorded — show the decision + what was written to the RAG corpus
  if (decided) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="panel-inset" style={{ padding: "9px 11px", display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="check" size={14} style={{ color: "var(--low)" }} />
          <span style={{ fontSize: 11.5, color: "var(--text)" }}>조치가 기록되었습니다.</span>
        </div>
        {reviewNote && <div style={{ fontSize: 11, color: "var(--text-2)", padding: "0 2px", lineHeight: 1.5 }}>기록된 조치: {reviewNote}</div>}
        <RagWriteResult reviewResult={reviewResult} />
      </div>
    );
  }

  const isCustom = sel === "custom";
  const chosenLabel = isCustom ? custom.trim() : (actions[sel]?.label || "");
  const canRun = !busy && chosenLabel.length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {actions.map((a, i) => (
          <div key={i}
            style={{
              display: "flex", alignItems: "center", gap: 4, width: "100%",
              padding: "4px 5px 4px 0", borderRadius: 7,
              border: `1px solid ${sel === i ? "var(--accent-line)" : "var(--border-soft)"}`,
              background: sel === i ? "var(--accent-dim)" : "var(--panel-2)",
            }}>
            <button type="button" onClick={() => setSel(i)} className="focusable"
              style={{
                display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0, textAlign: "left",
                padding: "3px 4px 3px 9px", cursor: "pointer", font: "inherit",
                border: 0, background: "transparent",
              }}>
              <RadioDot on={sel === i} />
              {i === 0 && <span className="chip" style={{ fontSize: 8.5, color: "var(--accent)", borderColor: "var(--accent-line)", flex: "none" }}>권장</span>}
              <span style={{ flex: 1, minWidth: 0, fontSize: 11.5, color: "var(--text)", lineHeight: 1.45 }}>{a.label}</span>
            </button>
            <EvidenceTrigger source={a.source} onOpen={() => onEvidence({ kind: "action", label: a.label, source: a.source })} />
          </div>
        ))}
        <button onClick={() => setSel("custom")} className="focusable"
          style={{
            display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left",
            padding: "7px 9px", borderRadius: 7, cursor: "pointer", font: "inherit",
            border: `1px solid ${isCustom ? "var(--accent-line)" : "var(--border-soft)"}`,
            background: isCustom ? "var(--accent-dim)" : "var(--panel-2)",
          }}>
          <RadioDot on={isCustom} />
          <span style={{ fontSize: 11.5, color: "var(--text)" }}>직접 입력</span>
        </button>
        {isCustom && (
          <input autoFocus value={custom} onChange={e => setCustom(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing && canRun) onExecute({ label: chosenLabel, decision }); }}
            placeholder="조치 내용을 입력하세요 (지식베이스에 그대로 기록됩니다)"
            style={{
              background: "var(--panel-2)", border: "1px solid var(--border-strong)", borderRadius: 7,
              padding: "8px 11px", color: "var(--text)", font: "inherit", fontSize: 11.5,
            }} />
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
        <span className="label-cap">처리 구분</span>
        {DECISION_OPTIONS.map(o => (
          <button key={o.id} onClick={() => setDecision(o.id)} className="focusable"
            style={{
              padding: "3px 9px", fontSize: 10.5, borderRadius: 99, cursor: "pointer", font: "inherit",
              border: `1px solid ${decision === o.id ? o.color : "var(--border-soft)"}`,
              background: decision === o.id ? "var(--accent-dim)" : "transparent",
              color: decision === o.id ? o.color : "var(--text-3)", fontWeight: decision === o.id ? 600 : 500,
            }}>
            {o.label}
          </button>
        ))}
        <button className="btn btn-accent" disabled={!canRun}
          onClick={() => onExecute({ label: chosenLabel, decision })}
          style={{ marginLeft: "auto", padding: "5px 12px", fontSize: 11, opacity: canRun ? 1 : .5 }}>
          {busy
            ? <Icon name="refresh" size={12} style={{ animation: "spin 1s linear infinite" }} />
            : <Icon name="check" size={12} />}
          기록 & RAG 반영
        </button>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-3)", lineHeight: 1.5 }}>
        '조치 완료' 또는 '오탐 처리'로 기록하면 이 사례가 RAG 지식베이스에 저장되어 이후 유사 검사 검색에 활용됩니다.
      </div>
    </div>
  );
}

/* ---------- main view ---------- */

export default function AgentView({ focusId, onFocusHandled }) {
  const { settings, riskQueue, setRiskQueue } = useStream();
  // the queue is the live, session-scoped list of Medium/High inspections — fed
  // by the stream (SettingsContext), not bulk-fetched from the DB. Empty on refresh.
  const rows = riskQueue;
  const [selectedId, setSelectedId] = useState(null);
  const [listOpen, setListOpen] = useState(true);
  const [trace, setTrace] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState(null);
  const [queueFilter, setQueueFilter] = useState("all");
  // result of the last review POST — carries knowledge_saved so we can confirm
  // the case was written into the RAG corpus
  const [reviewResult, setReviewResult] = useState(null);

  // picking a case collapses the left list; the collapsed bar re-expands it
  function selectRow(id) {
    setSelectedId(id);
    setListOpen(false);
  }

  // toast "분석 보러 가기" hands us the case to open directly
  useEffect(() => {
    if (!focusId) return;
    if (rows.some(r => r.id === focusId)) {
      setSelectedId(focusId);
      setListOpen(false);
      onFocusHandled && onFocusHandled();
    }
  }, [focusId, rows, onFocusHandled]);

  // the agent runs in the background after an inspection, so the trace may
  // land a few seconds later — poll until it appears (or give up quietly)
  useEffect(() => {
    setTrace(null);
    setEvidence(null);
    setReviewResult(null);
    if (!selectedId) return;
    let cancelled = false;
    let attempts = 0;
    setTraceLoading(true);
    async function poll() {
      try {
        const res = await fetch(`${API_BASE}/api/v1/inspect/${selectedId}/trace`);
        if (cancelled) return;
        if (res.ok) {
          setTrace(await res.json());
          setTraceLoading(false);
          return;
        }
      } catch { /* retry */ }
      attempts += 1;
      if (!cancelled && attempts < 8) setTimeout(poll, 3000);
      else if (!cancelled) setTraceLoading(false);
    }
    poll();
    return () => { cancelled = true; };
  }, [selectedId]);

  async function executeAction({ label, decision }) {
    if (!selectedId || busy || !label) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/review/${selectedId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: decision || "approved", reviewer: "operator", note: label }),
      });
      if (res.ok) {
        const data = await res.json();
        setReviewResult(data);
        // reflect the decision on the queued row in place (no bulk refetch)
        setRiskQueue(q => q.map(r => (r.id === selectedId ? { ...r, ...data } : r)));
      }
    } finally {
      setBusy(false);
    }
  }

  const selected = rows.find(r => r.id === selectedId) || null;
  const decided = !!selected?.engineer_decision;
  const pending = rows.filter(r => !r.engineer_decision).length;
  const visibleRows = rows.filter(r => matchesFilter(r, queueFilter));
  const mode = trace
    ? (MODE_CHIP[trace.agent_mode] || MODE_CHIP.rule_only)
    : (traceLoading ? MODE_CHIP.pending : MODE_CHIP.rule_only);

  // AI recommendation data with evidence sources
  const card = selected?.action_card || {};
  const cases = selected?.cases || [];
  const ruleHits = card.metrology_rule_hits || [];
  const causes = (card.possible_causes || []).map((label, i) => ({
    label,
    conf: CAUSE_CONF[i] ?? 0.08,
    source: { type: "rag", case: cases[i % Math.max(cases.length, 1)] || cases[0] || null },
  }));
  const actions = (card.next_actions || []).map(label => {
    const hit = ruleHits.find(h => h.action === label);
    return { label, source: hit ? { type: "metrology", hit } : { type: "rag", case: cases[0] || null } };
  });
  const met = selected?.metrology || {};
  const riskScore = Math.round((selected?.risk_score ?? 0) * 100);
  const dieFail = Math.round((selected?.hotspot_ratio || 0) * 612);

  const showList = listOpen || !selected;

  // unified agent run (record + live re-run) — its control sits in the panel header
  const agentRun = useAgentRun({ inspectionId: selected?.id, llmOn: settings.useLlm, trace });

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 12, alignItems: "start" }}>
      {/* left column — queue (collapsible) + overview below it */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
        <Panel title="리스크 큐 · Medium / High" icon="alert" dense pad={6}
          right={<span className="chip" style={{ fontSize: 9.5, color: pending ? "var(--med)" : "var(--low)", borderColor: pending ? "var(--med)" : "var(--low)" }}>대기 {pending}</span>}>
          <div style={{ display: "grid", gridTemplateRows: showList ? "1fr" : "0fr", transition: "grid-template-rows .45s ease" }}>
            <div style={{ overflow: "hidden" }}>
              {/* status filters */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, padding: "2px 2px 8px" }}>
                {QUEUE_FILTERS.map(f => {
                  const n = rows.filter(r => matchesFilter(r, f.id)).length;
                  const on = queueFilter === f.id;
                  return (
                    <button key={f.id} onClick={() => setQueueFilter(f.id)} className="focusable"
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 99,
                        cursor: "pointer", font: "inherit", fontSize: 10,
                        border: `1px solid ${on ? "var(--accent-line)" : "var(--border-soft)"}`,
                        background: on ? "var(--accent-dim)" : "transparent",
                        color: on ? "var(--accent)" : "var(--text-3)", fontWeight: on ? 650 : 500,
                      }}>
                      {f.label}
                      <span className="mono" style={{ fontSize: 9, opacity: .8 }}>{n}</span>
                    </button>
                  );
                })}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 452, overflowY: "auto" }}>
                {visibleRows.length === 0 && (
                  <div style={{ padding: "26px 12px", textAlign: "center", fontSize: 11.5, color: "var(--text-3)" }}>
                    {rows.length === 0
                      ? "Medium/High 리스크 검사가 아직 없습니다."
                      : "이 필터에 해당하는 검사가 없습니다."}
                  </div>
                )}
                {visibleRows.map(r => (
                  <QueueRow key={r.id} row={r} active={r.id === selectedId} onSelect={selectRow} />
                ))}
              </div>
            </div>
          </div>
          {!showList && (
            <button onClick={() => setListOpen(true)} className="focusable fade-in" title="리스크 큐 펼치기"
              style={{
                display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left",
                padding: "7px 10px", borderRadius: 7, cursor: "pointer", font: "inherit",
                border: "1px solid var(--border-soft)", background: "var(--panel-2)",
              }}>
              <RiskBadge level={selected.risk_level} />
              <span className="mono" style={{ fontSize: 11.5, color: "var(--text)", fontWeight: 600 }}>{selected.wafer_id}</span>
              <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 5, fontSize: 10.5, color: "var(--text-3)" }}>
                다른 케이스 <Icon name="chevD" size={12} />
              </span>
            </button>
          )}
        </Panel>

        {/* basic info — appears under the collapsed queue */}
        {selected && (
          <div key={`ov-${selected.id}`} className="fade-in">
              <Panel title="검사 개요" icon="layers" dense
                right={decided && (
                  <span className="chip" style={{ fontSize: 9.5, color: DECISION_LABEL[selected.engineer_decision]?.color, borderColor: DECISION_LABEL[selected.engineer_decision]?.color }}>
                    {DECISION_LABEL[selected.engineer_decision]?.label || selected.engineer_decision}
                  </span>
                )}>
                <div style={{ display: "flex", flexDirection: "column", gap: 14, alignItems: "center" }}>
                  <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
                    {selected.image_url && (
                      <figure style={{ margin: 0, textAlign: "center" }}>
                        <img src={`${API_BASE}${selected.image_url}`} alt="wafer map"
                          style={{ width: 108, height: 108, objectFit: "contain", borderRadius: 8, background: "var(--panel-2)" }} />
                        <figcaption style={{ fontSize: 9.5, color: "var(--text-3)", marginTop: 4 }}>Wafer Map</figcaption>
                      </figure>
                    )}
                    {selected.overlay_url && (
                      <figure style={{ margin: 0, textAlign: "center" }}>
                        <img src={`${API_BASE}${selected.overlay_url}`} alt="grad-cam"
                          style={{ width: 108, height: 108, objectFit: "contain", borderRadius: 8, background: "#0b1a2e" }} />
                        <figcaption style={{ fontSize: 9.5, color: "var(--text-3)", marginTop: 4 }}>Grad-CAM</figcaption>
                      </figure>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
                    <RiskGauge score={riskScore} level={selected.risk_level} size={100} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
                      <RiskBadge level={selected.risk_level} size="lg" />
                      <span className="chip">{selected.defect_type}</span>
                      <span className="chip" style={{ color: mode.color, borderColor: mode.color, fontSize: 9 }}>
                        <Icon name="bot" size={10} /> {mode.label}
                      </span>
                    </div>
                  </div>
                  <div className="divider" style={{ width: "100%", margin: 0 }} />
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 18px", width: "100%" }}>
                    <Metric label="신뢰도" value={((selected.confidence || 0) * 100).toFixed(1)} unit="%" />
                    <Metric label="영향 다이" value={dieFail} unit="/ 612" accent={selected.risk_level === "High" ? "var(--high)" : "var(--text)"} />
                    <Metric label="설비" value={selected.equipment_id} mono={false} sub={selected.process_step || ""} />
                    <Metric label="Lot · Wafer" value={selected.wafer_id} mono={false} sub={selected.lot_id || ""} />
                  </div>
                  <div className="mono" style={{ fontSize: 9.5, color: "var(--text-3)", lineHeight: 1.6, width: "100%" }}>
                    {selected.id}<br />
                    {(selected.created_at || "").slice(0, 19).replace("T", " ")}<br />
                    CD {met.cd_nm}nm · OVL {met.overlay_nm}nm · THK {met.film_thickness_nm}nm
                  </div>
                </div>
              </Panel>
          </div>
        )}
      </div>

      {/* right column — prompt or AI recommendation + agent decision */}
      {!selected ? (
        <Panel dense>
          <div style={{ minHeight: 340, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 11, textAlign: "center" }}>
            <Icon name="bot" size={30} style={{ color: "var(--accent)" }} />
            <div style={{ fontSize: 14.5, fontWeight: 650, color: "var(--text)" }}>분석할 케이스를 선택하세요</div>
            <div style={{ fontSize: 12, color: "var(--text-3)", lineHeight: 1.7 }}>
              왼쪽 리스크 큐에서 검사를 선택하면<br />
              에이전트 실행 기록, 추정 원인, 권장·선택 조치, 결함 Q&A가 한 화면에 표시됩니다.
            </div>
            {pending > 0 && (
              <span className="chip" style={{ color: "var(--med)", borderColor: "var(--med)", marginTop: 4 }}>
                조치 대기 {pending}건
              </span>
            )}
          </div>
        </Panel>
      ) : (
      <div key={selected.id} className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
              {/* One agent surface: its core judgment is the opening chat message,
                  each item clickable to its evidence, conversation continues below. */}
              <Panel title="검사 에이전트 · Inspection Agent" icon="bot" dense pad={0}
                right={<span className="chip" style={{ fontSize: 9, color: "var(--accent)", borderColor: "var(--accent-line)" }}>근거: RAG + 계측 룰</span>}>
                <AgentChat
                  inspectionId={selected.id}
                  llmOn={settings.useLlm}
                  causes={causes}
                  actions={actions}
                  decided={decided}
                  busy={busy}
                  onExecute={executeAction}
                  onEvidence={setEvidence}
                  reviewResult={reviewResult}
                  reviewNote={selected.review_note || DECISION_LABEL[selected.engineer_decision]?.label}
                  agentRun={agentRun}
                  traceLoading={traceLoading}
                  mode={mode}
                />
              </Panel>
      </div>
      )}

      {evidence && <EvidenceModal entry={evidence} cases={cases} onClose={() => setEvidence(null)} />}
    </div>
  );
}

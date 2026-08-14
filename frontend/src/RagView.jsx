import React, { useCallback, useEffect, useState } from "react";
import { Panel, Icon } from "./lib";
import { useUi } from "./UiContext";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const DEFECT_LABELS = { Center: "Center", Donut: "Donut", "Edge-Loc": "Edge-Loc", "Edge-Ring": "Edge-Ring", Loc: "Loc", Random: "Random", Scratch: "Scratch", "Near-full": "Near-full", None: "None" };
const SOURCE_META = {
  engineer_chat: { ko: "엔지니어 학습", en: "Engineer Learned", learned: true },
  engineer_confirmed: { ko: "엔지니어 확정", en: "Engineer Confirmed", learned: true },
  case_library: { ko: "표준 사례", en: "Case Library", learned: false },
  tech_guide: { ko: "기술 가이드", en: "Technical Guide", learned: false },
  sop: { ko: "SOP", en: "SOP", learned: false },
  near_miss: { ko: "Near-miss", en: "Near-miss", learned: false },
  eval_set: { ko: "평가셋", en: "Evaluation Set", learned: false },
  rag_documents: { ko: "지식베이스", en: "Knowledge Base", learned: false },
};

function SourceBadge({ source }) {
  const { language } = useUi();
  const meta = SOURCE_META[source] || { ko: source || "지식베이스", en: source || "Knowledge Base", learned: false };
  return (
    <span className="chip" style={{ fontSize: 9, fontWeight: 600, color: meta.learned ? "var(--low)" : "var(--text-3)", borderColor: meta.learned ? "var(--low)" : "var(--border)" }}>
      {meta.learned && <Icon name="check" size={9} style={{ marginRight: 3 }} />}{meta[language]}
    </span>
  );
}

function CaseCard({ c }) {
  const { text } = useUi();
  const sim = Math.round((c.similarity || 0) * 100);
  return (
    <div className="panel-inset" style={{ padding: 13, display: "flex", flexDirection: "column", gap: 9 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.case_id || "—"}</span>
        <span style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}><span className="mono" style={{ fontSize: 14, fontWeight: 600, color: "var(--accent)" }}>{sim}%</span><span className="label-cap" style={{ fontSize: 8.5 }}>{text("유사", "similar")}</span></span>
      </div>
      <div style={{ height: 3, background: "var(--panel-3)", borderRadius: 99 }}><div style={{ width: `${sim}%`, height: "100%", background: "var(--accent)", borderRadius: 99 }} /></div>
      <div style={{ fontSize: 12.5, color: "var(--text)", fontWeight: 600, lineHeight: 1.4 }}>{c.title}</div>
      {c.summary && <div style={{ fontSize: 11, color: "var(--text-2)", lineHeight: 1.5 }}>{c.summary}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {c.equipment && <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{c.equipment}</span>}
        {c.date && <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>· {c.date}</span>}
        <SourceBadge source={c.source} />
      </div>
      {c.action && <><div className="divider" style={{ margin: "2px 0" }} /><div><div className="label-cap" style={{ fontSize: 8.5, marginBottom: 3 }}>{text("권장 조치", "Recommended Action")}</div><div style={{ fontSize: 11.5, color: "var(--text-2)", lineHeight: 1.45 }}>{c.action}</div></div></>}
    </div>
  );
}

export default function RagView() {
  const { text } = useUi();
  const [stats, setStats] = useState(null);
  const [defectType, setDefectType] = useState("Edge-Ring");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState(null);

  const runSearch = useCallback(async (dt, q) => {
    setSearching(true);
    try {
      const url = `${API_BASE}/api/v1/rag/search?defect_type=${encodeURIComponent(dt || "")}&q=${encodeURIComponent(q || "")}&k=6`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResult(await res.json());
      setError(null);
    } catch (e) {
      setError(text(`케이스 검색 실패: ${e.message}`, `Case search failed: ${e.message}`));
    } finally { setSearching(false); }
  }, [text]);

  useEffect(() => {
    (async () => {
      try {
        const si = await fetch(`${API_BASE}/api/v1/rag/index`).then(r => r.json());
        setStats(si);
        const types = si?.defect_types || [];
        const initial = types.includes("Edge-Ring") ? "Edge-Ring" : (types[0] || null);
        setDefectType(initial);
        runSearch(initial, "");
      } catch (e) {
        setError(text(`RAG 인덱스 조회 실패: ${e.message}`, `Failed to load RAG index: ${e.message}`));
      }
    })();
  }, [runSearch, text]);

  function selectType(dt) {
    const next = dt === defectType ? null : dt;
    setDefectType(next);
    runSearch(next, query);
  }
  function submitSearch(e) { e?.preventDefault(); runSearch(defectType, query); }

  const types = stats?.defect_types || [];
  const byType = stats?.by_type || {};

  return (
    <Panel title={text("유사 과거 케이스 검색 (RAG)", "Similar Historical Case Search (RAG)")} icon="search" dense right={result && <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{result.source === "vector" ? text("벡터", "Vector") : text("라이브러리", "Library")} · {result.count} {text("건", "results")}</span>}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
        <button onClick={() => selectType(null)} className="focusable" style={{ cursor: "pointer", font: "inherit", padding: "5px 10px", borderRadius: 7, fontSize: 11, fontWeight: !defectType ? 600 : 500, border: "1px solid " + (!defectType ? "var(--accent)" : "var(--border)"), background: !defectType ? "var(--accent-dim)" : "transparent", color: !defectType ? "var(--accent)" : "var(--text-2)" }}>{text("전체", "All")}</button>
        {types.map(dt => {
          const on = dt === defectType;
          const n = byType[dt];
          return <button key={dt} onClick={() => selectType(dt)} className="focusable" title={on ? text("다시 누르면 필터 해제", "Click again to clear filter") : undefined} style={{ display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer", font: "inherit", padding: "5px 10px", borderRadius: 7, fontSize: 11, fontWeight: on ? 600 : 500, border: "1px solid " + (on ? "var(--accent)" : "var(--border)"), background: on ? "var(--accent-dim)" : "transparent", color: on ? "var(--accent)" : "var(--text-2)" }}>{DEFECT_LABELS[dt] || dt}{typeof n === "number" && <span className="mono" style={{ fontSize: 9.5, opacity: 0.7 }}>{n}</span>}{on && <Icon name="x" size={10} style={{ opacity: 0.8 }} />}</button>;
        })}
      </div>

      <form onSubmit={submitSearch} style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <div style={{ position: "relative", flex: 1 }}>
          <Icon name="search" size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-3)" }} />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder={text("키워드로 케이스 좁히기 (예: EBR nozzle, 슬러리 유량, 파티클)", "Narrow cases by keyword (e.g. EBR nozzle, slurry flow, particle)")} style={{ width: "100%", boxSizing: "border-box", font: "inherit", fontSize: 12, padding: "8px 10px 8px 30px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--panel-2)", color: "var(--text)" }} />
        </div>
        <button type="submit" className="btn" disabled={searching} style={{ padding: "0 16px", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap" }}>{searching ? text("검색 중…", "Searching…") : text("검색", "Search")}</button>
      </form>

      {error && <div style={{ fontSize: 12, color: "var(--high)", marginBottom: 10 }}>{error}</div>}
      {result && result.cases.length > 0 ? <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>{result.cases.map((c, i) => <CaseCard key={c.case_id || i} c={c} />)}</div> : (!searching && !error && <div style={{ padding: "22px 0", textAlign: "center", color: "var(--text-3)", fontSize: 12 }}>{text("검색 결과가 없습니다.", "No search results.")}</div>)}
    </Panel>
  );
}
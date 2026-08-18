import React, { useCallback, useEffect, useState } from "react";
import { Icon, Panel } from "./lib";
import { useUi } from "./UiContext";
import RagView from "./RagView";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const TABLE_LABELS = {
  inspections: { ko: "검사 이력", en: "Inspections" },
  lots: { ko: "Lot 운영 원장", en: "Lot Registry" },
  model_registry: { ko: "모델 레지스트리", en: "Model Registry" },
  drift_events: { ko: "드리프트 이벤트", en: "Drift Events" },
  retraining_jobs: { ko: "재학습 작업", en: "Retraining Jobs" },
  alerts: { ko: "알림", en: "Alerts" },
  process_events: { ko: "공정 이벤트", en: "Process Events" },
  handoff_reports: { ko: "인수인계 리포트", en: "Handoff Reports" },
  agent_traces: { ko: "Agent 추적", en: "Agent Traces" },
  pending_approvals: { ko: "승인 대기", en: "Pending Approvals" },
  rag_documents: { ko: "RAG 문서", en: "RAG Documents" },
  chamber_telemetry: { ko: "Chamber Telemetry", en: "Chamber Telemetry" },
  chamber_predictions: { ko: "Chamber Predictions", en: "Chamber Predictions" },
  anomaly_detections: { ko: "Anomaly Detector 결과", en: "Anomaly Detector Results" },
  chamber_model_registry: { ko: "Chamber Model Registry", en: "Chamber Model Registry" },
};

function formatBytes(n) {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function CellValue({ value }) {
  if (value === null || value === undefined) return <span style={{ color: "var(--text-3)" }}>—</span>;
  const s = String(value);
  const truncated = s.length > 90 ? s.slice(0, 90) + "…" : s;
  return <span title={s.length > 90 ? s : undefined}>{truncated}</span>;
}

const PAGE_SIZE = 25;

export default function DatabaseView() {
  const { language, text } = useUi();
  const [overview, setOverview] = useState(null);
  const [selected, setSelected] = useState("inspections");
  const [data, setData] = useState(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadOverview = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/db/overview`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOverview(await res.json());
      setError(null);
    } catch (e) {
      setError(text(`DB 개요 조회 실패: ${e.message}`, `Failed to load DB overview: ${e.message}`));
    }
  }, [text]);

  const loadTable = useCallback(async (table, off) => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/db/tables/${table}?limit=${PAGE_SIZE}&offset=${off}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(text(`테이블 조회 실패: ${e.message}`, `Failed to load table: ${e.message}`));
    } finally {
      setLoading(false);
    }
  }, [text]);

  useEffect(() => { loadOverview(); }, [loadOverview]);
  useEffect(() => { loadTable(selected, offset); }, [selected, offset, loadTable]);

  function selectTable(name) {
    setSelected(name);
    setOffset(0);
  }

  function refresh() {
    loadOverview();
    loadTable(selected, offset);
  }

  const total = data?.total ?? 0;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <RagView />

      <Panel title={`${text("데이터베이스 브라우저", "Database Browser")} · ${(overview?.backend || "database").toUpperCase()}`} icon="box" dense pad={0}
        right={
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {overview && (
              <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>
                {overview.backend === "sqlite" ? `waferguard.db · ${formatBytes(overview.db_size_bytes)}` : "runtime PostgreSQL"}
              </span>
            )}
            <button className="btn btn-ghost" onClick={refresh} style={{ padding: "4px 8px" }} title={text("새로고침", "Refresh")}>
              <Icon name="refresh" size={13} />
            </button>
          </span>
        }>
        {error && <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--high)", borderBottom: "1px solid var(--border)" }}>{error}</div>}
        <div style={{ display: "grid", gridTemplateColumns: "190px 1fr", alignItems: "stretch" }}>
          <div style={{ borderRight: "1px solid var(--border)", padding: "8px 6px", display: "flex", flexDirection: "column", gap: 2 }}>
            <div className="label-cap" style={{ padding: "4px 8px 6px" }}>{text("테이블", "Tables")}</div>
            {(overview?.tables || []).map(t => {
              const on = t.name === selected;
              const label = TABLE_LABELS[t.name]?.[language] || t.name;
              return (
                <button key={t.name} onClick={() => selectTable(t.name)} className="focusable"
                  style={{
                    display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                    padding: "6px 8px", borderRadius: 7, border: "1px solid transparent",
                    font: "inherit", textAlign: "left", width: "100%",
                    background: on ? "var(--accent-dim)" : "transparent",
                    color: on ? "var(--accent)" : "var(--text-2)",
                    transition: "background .15s, color .15s",
                  }}>
                  <span style={{ fontSize: 11.5, fontWeight: on ? 600 : 500, flex: 1, minWidth: 0 }}>{label}</span>
                  <span className="mono" style={{ fontSize: 10, color: on ? "var(--accent)" : "var(--text-3)" }}>{t.row_count.toLocaleString()}</span>
                </button>
              );
            })}
          </div>

          <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
              <span className="mono" style={{ fontSize: 11.5, color: "var(--text)", fontWeight: 600 }}>{selected}</span>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{pageStart}–{pageEnd} / {total.toLocaleString()} {text("행", "rows")}</span>
              {loading && <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{text("로딩…", "Loading…")}</span>}
              <span style={{ marginLeft: "auto", display: "flex", gap: 5 }}>
                <button className="btn btn-ghost" disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))} style={{ padding: "3px 9px", fontSize: 11 }}>{text("이전", "Previous")}</button>
                <button className="btn btn-ghost" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(o => o + PAGE_SIZE)} style={{ padding: "3px 9px", fontSize: 11 }}>{text("다음", "Next")}</button>
              </span>
            </div>
            <div style={{ overflowX: "auto", maxHeight: 440, overflowY: "auto" }}>
              <table style={{ borderCollapse: "collapse", fontSize: 11, width: "100%" }}>
                <thead><tr>{(data?.columns || []).map(c => <th key={c} className="mono" style={{ textAlign: "left", padding: "7px 10px", fontWeight: 600, fontSize: 9.5, letterSpacing: ".05em", textTransform: "uppercase", color: "var(--text-3)", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap", position: "sticky", top: 0, background: "var(--panel)", zIndex: 1 }}>{c}</th>)}</tr></thead>
                <tbody>
                  {(data?.rows || []).map((row, i) => (
                    <tr key={i}>{(data?.columns || []).map(c => <td key={c} className="mono" style={{ padding: "6px 10px", color: "var(--text-2)", whiteSpace: "nowrap", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", borderBottom: "1px solid var(--border-soft)" }}><CellValue value={row[c]} /></td>)}</tr>
                  ))}
                  {data && data.rows.length === 0 && <tr><td colSpan={Math.max(1, (data.columns || []).length)} style={{ padding: "22px 12px", textAlign: "center", color: "var(--text-3)", fontSize: 11.5 }}>{text("레코드 없음", "No records")}</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
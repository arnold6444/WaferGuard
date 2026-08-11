import React, { useState, useEffect, useRef } from "react";
import { Icon, RiskBadge, RiskGauge, Panel, Metric, StatusDot } from "./lib";
import { WaferMap, GradCAM, buildWaferMap } from "./wafer";
import { DefectCatBars, RiskHistogram } from "./charts";
import { riskHist, defectCats, defaultInspection } from "./mock";
import { useStream } from "./SettingsContext";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

function evidenceUrl(value) {
  if (!value) return value;
  if (/^https?:\/\//.test(value) || value.startsWith("/wafer-vision/")) return value;
  return `${API_BASE}${value}`;
}

// Map backend result → display format (shared with AgentView)
export function mapResult(r) {
  const card = r.action_card || {};
  const pc   = r.process_context || {};
  const met  = r.metrology || {};
  const riskScore = Math.round((r.risk_score ?? 0) * 100);

  const causes = (card.possible_causes || []).map((label, i) => ({
    label, conf: [0.42, 0.27, 0.15, 0.10][i] ?? 0.08,
  }));

  const checks = [
    ...(card.process_checks || []),
    ...(card.metrology_checks || []),
  ].map(label => ({ label, priority: "low", done: false }));

  const nextActions = (card.next_actions || ["정상 처리 진행"]).map((label, i) => ({
    label, kind: i === 0 ? "primary" : "ghost",
  }));

  const metrologyRows = Object.entries(met).length > 0 ? [
    { id: "CD",      name: "CD (nm)",          value: met.cd_nm ?? "N/A",              spec: "≤ 35",    ok: (met.cd_nm ?? 0) <= 35,      delta: 0 },
    { id: "OVL",     name: "Overlay (nm)",     value: met.overlay_nm ?? "N/A",         spec: "≤ 6.0",   ok: (met.overlay_nm ?? 0) <= 6,   delta: 0 },
    { id: "THICK",   name: "Film Thickness",   value: met.film_thickness_nm ?? "N/A",  spec: "80–100",  ok: true, delta: 0 },
    { id: "ROUGH",   name: "Roughness (nm)",   value: met.roughness_nm ?? "N/A",       spec: "≤ 2.0",   ok: (met.roughness_nm ?? 0) <= 2,  delta: 0 },
    { id: "PART",    name: "Defect Count",     value: met.defect_count ?? "N/A",       spec: "≤ 12",    ok: (met.defect_count ?? 0) <= 12, delta: card.metrology_risk_delta ?? 0 },
  ] : defaultInspection.metrology;

  return {
    lot: pc.lot_id || r.lot_id || "LOT-DEMO",
    wafer: r.wafer_id || "W01",
    waferIdx: parseInt((r.wafer_id || "W01").replace(/\D/g, "")) || 1,
    waferTotal: 25,
    device: `${pc.process_step || "Etch"} · ${pc.recipe_id || "RCP-001"}`,
    recipe: pc.recipe_id || "RCP-001",
    tool: pc.tool_id || r.equipment_id || "ETCH-01",
    chamber: "C2",
    operator: "Demo Engineer",
    startedAt: r.created_at || new Date().toISOString().slice(0,19).replace("T", " "),
    riskLevel: r.risk_level || "Low",
    riskScore: riskScore,
    confidence: r.confidence || 0,
    yieldEst: ((1 - (r.hotspot_ratio || 0)) * 100).toFixed(1),
    dieTotal: 612,
    dieFail: Math.round((r.hotspot_ratio || 0.008) * 612),
    waferMap: buildWaferMap(r.hotspot_ratio ?? 0.045, r.risk_level === "High"),
    causes,
    checks,
    nextActions,
    metrology: metrologyRows,
    report: r.report || "",
    defectType: r.defect_type || "Unknown",
    imageUrl: r.image_url,
    overlayUrl: r.overlay_url,
    roiUrl: r.roi_url,
    modelVersion: r.model_version || "v4.2.1",
    status: r.status || "approved",
    inspectionId: r.id,
    agentMode: r.agent_mode || "rule_only",
    agentFinalAction: r.agent_final_action || null,
    agentToolCalls: r.agent_tool_calls || [],
  };
}

function MetaItem({ label, value, mono = true }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
      <span className="label-cap" style={{ fontSize: 9.5 }}>{label}</span>
      <span className={mono ? "mono" : ""} style={{ fontSize: 12.5, color: "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{value}</span>
    </div>
  );
}

function RiskScorePanel({ insp }) {
  const human = insp.riskLevel === "High";
  return (
    <Panel title="리스크 점수" icon="shield" dense>
      <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
        <RiskGauge score={insp.riskScore} level={insp.riskLevel} size={118} />
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          <RiskBadge level={insp.riskLevel} size="lg" />
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5,
            color: human ? "var(--high)" : "var(--low)", fontWeight: 600,
          }}>
            <Icon name={human ? "alert" : "check"} size={13} />
            {human ? "사람 리뷰 필요" : "사람 리뷰 불필요"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 22, marginLeft: "auto", paddingRight: 6, flexWrap: "wrap" }}>
          <Metric label="신뢰도" value={(insp.confidence * 100).toFixed(1)} unit="%" />
          <Metric label="추정 수율" value={insp.yieldEst} unit="%" accent="var(--low)" />
          <Metric label="영향 다이" value={insp.dieFail} unit={`/ ${insp.dieTotal}`}
            accent={human ? "var(--high)" : "var(--text)"} />
        </div>
      </div>
    </Panel>
  );
}

function MetrologyTable({ rows }) {
  return (
    <Panel title="측정 결과" icon="gauge" dense pad={0}
      right={<span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{rows.filter(r => r.ok).length}/{rows.length} PASS</span>}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
        <thead>
          <tr style={{ color: "var(--text-3)" }}>
            {["항목", "측정값", "스펙", "Δrisk", ""].map((h, i) => (
              <th key={i} style={{ textAlign: i > 0 && i < 4 ? "right" : "left", padding: "7px 12px", fontWeight: 600, fontSize: 10, letterSpacing: ".05em", textTransform: "uppercase", borderBottom: "1px solid var(--border)" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ background: r.ok ? "transparent" : "var(--high-dim)" }}>
              <td style={{ padding: "7px 12px", color: "var(--text-2)", borderBottom: "1px solid var(--border-soft)" }}>{r.name}</td>
              <td className="mono" style={{ padding: "7px 12px", textAlign: "right", color: r.ok ? "var(--text)" : "var(--high)", fontWeight: 600, borderBottom: "1px solid var(--border-soft)" }}>{r.value}</td>
              <td className="mono" style={{ padding: "7px 12px", textAlign: "right", color: "var(--text-3)", borderBottom: "1px solid var(--border-soft)" }}>{r.spec}</td>
              <td className="mono" style={{ padding: "7px 12px", textAlign: "right", color: r.delta ? "var(--high)" : "var(--text-3)", borderBottom: "1px solid var(--border-soft)" }}>{r.delta ? `+${r.delta}` : "—"}</td>
              <td style={{ padding: "7px 12px", borderBottom: "1px solid var(--border-soft)" }}><StatusDot kind={r.ok ? "ok" : "failed"} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// transient bottom-right nudge: pops when a Medium/High inspection lands, then fades
function AgentNudgeToast({ data, onGo, onClose }) {
  const high = data.level === "High";
  const c = high ? "var(--high)" : "var(--med)";
  return (
    <div className="toast-in" style={{ position: "fixed", bottom: 18, right: 18, zIndex: 200, width: 320 }}>
      <div style={{
        display: "flex", flexDirection: "column", gap: 9, padding: "12px 14px",
        background: "var(--panel)", border: `1px solid ${c}`, borderRadius: 10, boxShadow: "var(--shadow)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="pulse-high" style={{ width: 8, height: 8, borderRadius: 99, background: c, flex: "none", boxShadow: `0 0 8px ${c}` }} />
          <RiskBadge level={data.level} />
          <span className="mono" style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>{data.wafer}</span>
          <span style={{ fontSize: 11, color: "var(--text-2)" }}>{data.defect}</span>
          <button className="btn btn-ghost" onClick={onClose} aria-label="닫기"
            style={{ marginLeft: "auto", padding: "3px 6px" }}><Icon name="x" size={13} /></button>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-2)", lineHeight: 1.5 }}>
          {high ? "High" : "Medium"} 리스크 검사입니다. 검사 에이전트에서 추정 원인·권장 액션을 확인하세요.
        </div>
        <button className="btn btn-accent" onClick={onGo} style={{ alignSelf: "flex-start", padding: "5px 12px", fontSize: 11.5, gap: 6 }}>
          <Icon name="bot" size={13} />검사 에이전트에서 확인
        </button>
      </div>
    </div>
  );
}

export default function InspectionView({ onOpenAgent, inspection = null, embedded = false }) {
  const { latest, tick, settings, updateSettings, inFlight, runOnce } = useStream();
  const [insp, setInsp]         = useState(inspection ? mapResult(inspection) : defaultInspection);
  const [scanning, setScanning] = useState(false);
  const scanTimer = useRef(null);
  const [nudge, setNudge]       = useState(null);
  const lastNudgeTick = useRef(null);
  const nudgeTimer = useRef(null);

  // pop a transient toast when a fresh Medium/High inspection arrives
  useEffect(() => {
    if (embedded) return;
    if (inspection) {
      setInsp(mapResult(inspection));
      return;
    }
    if (!latest) return;
    if (lastNudgeTick.current === null) { lastNudgeTick.current = tick; return; } // skip first paint
    if (tick === lastNudgeTick.current) return;
    lastNudgeTick.current = tick;
    if (latest.risk_level === "High" || latest.risk_level === "Medium") {
      setNudge({ id: latest.id, level: latest.risk_level, wafer: latest.wafer_id || "W?", defect: latest.defect_type || "결함" });
      clearTimeout(nudgeTimer.current);
      nudgeTimer.current = setTimeout(() => setNudge(null), 6000);
    }
  }, [tick, latest, inspection, embedded]);
  useEffect(() => () => clearTimeout(nudgeTimer.current), []);

  useEffect(() => {
    if (embedded || inspection) return;
    if (!latest) return;
    setInsp(mapResult(latest));
    setScanning(true);
    if (scanTimer.current) clearTimeout(scanTimer.current);
    scanTimer.current = setTimeout(() => setScanning(false), 350);
    return () => { if (scanTimer.current) clearTimeout(scanTimer.current); };
  }, [tick, latest, embedded, inspection]);

  const hot = insp.riskLevel === "High" ? "cluster" : "edge";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* meta strip */}
      <div className="panel" style={{ display: "flex", alignItems: "center", gap: 0, padding: "11px 14px", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 22, flex: 1, flexWrap: "wrap" }}>
          <MetaItem label="Lot"            value={insp.lot} />
          <MetaItem label="Wafer"          value={`${insp.wafer} · ${insp.waferIdx}/${insp.waferTotal}`} />
          <MetaItem label="Defect"         value={insp.defectType} />
          <MetaItem label="Recipe"         value={insp.recipe} />
          <MetaItem label="Tool"           value={insp.tool} />
          <MetaItem label="Model"          value={insp.modelVersion || "v4.2.1"} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ textAlign: "right" }}>
            <div className="label-cap" style={{ fontSize: 9 }}>검사 시각</div>
            <div className="mono" style={{ fontSize: 11.5, color: "var(--text-2)" }}>{insp.startedAt?.slice(0,19)}</div>
          </div>
          {embedded ? (
            <button className="btn btn-accent" onClick={() => onOpenAgent?.(insp.inspectionId)} disabled={!insp.inspectionId}>
              <Icon name="bot" size={14} />AI Analysis에서 분석
            </button>
          ) : (
            <>
              <span className="chip" title={`주기 ${settings.intervalMs}ms · 비정상률 ${(settings.anomalyRate*100).toFixed(0)}%`}
                style={{ color: settings.enabled ? "var(--low)" : "var(--text-3)", borderColor: settings.enabled ? "var(--low)" : "var(--border-strong)" }}>
                <StatusDot kind={settings.enabled ? "ok" : "idle"} />
                {settings.enabled ? `스트림 ${(settings.intervalMs/1000).toFixed(1)}s` : "스트림 일시정지"}
              </span>
              <button className="btn" onClick={() => updateSettings({ enabled: !settings.enabled })} style={{ gap: 6 }}>
                <Icon name={settings.enabled ? "pause" : "play"} size={14} />
                {settings.enabled ? "일시정지" : "재개"}
              </button>
              <button className="btn btn-accent" onClick={runOnce} disabled={inFlight}>
                <Icon name="refresh" size={14} style={inFlight ? { animation: "spin 1s linear infinite" } : undefined} />
                {inFlight ? "검사 중…" : "1회 실행"}
              </button>
            </>
          )}
        </div>
      </div>

      {nudge && (
        <AgentNudgeToast data={nudge}
          onGo={() => { onOpenAgent && onOpenAgent(nudge.id); setNudge(null); }}
          onClose={() => setNudge(null)} />
      )}

      {/* main grid — imagery and metrology split 50/50 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, alignItems: "start" }}>
        <Panel title="검사 영상" icon="layers" dense
          right={<span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{insp.dieTotal} die · {insp.dieFail} flagged</span>}>
          <div style={{ display: "flex", gap: 14, justifyContent: "center", flexWrap: "wrap" }}>
            <figure style={{ margin: 0, textAlign: "center" }}>
              {insp.imageUrl
                ? <img src={evidenceUrl(insp.imageUrl)} alt="wafer map"
                    style={{ width: 220, height: 220, objectFit: "contain", borderRadius: 8, background: "var(--panel-2)" }} />
                : <WaferMap map={insp.waferMap} size={220} scanning={scanning} />}
              <figcaption style={{ fontSize: 10, color: "var(--text-3)", marginTop: 6 }}>Wafer Map · {insp.wafer}</figcaption>
            </figure>
            <figure style={{ margin: 0, textAlign: "center" }}>
              {insp.overlayUrl
                ? <img src={evidenceUrl(insp.overlayUrl)} alt="grad-cam"
                    style={{ width: 220, height: 220, objectFit: "contain", borderRadius: 8, background: "#0b1a2e" }} />
                : <GradCAM size={220} hot={hot} />}
              <figcaption style={{ fontSize: 10, color: "var(--text-3)", marginTop: 6 }}>Grad-CAM 활성화 맵</figcaption>
            </figure>
          </div>
        </Panel>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <RiskScorePanel insp={insp} />
          <MetrologyTable rows={insp.metrology} />
        </div>
      </div>

      {/* defect bars + risk histogram */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.25fr", gap: 12, alignItems: "start" }}>
        <Panel title="결함 카테고리 빈도 · 7d" icon="activity" dense>
          <DefectCatBars data={defectCats} />
        </Panel>
        <Panel title="리스크 점수 분포 · 24h" icon="pulse" dense
          right={<span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>595 inspections</span>}>
          <RiskHistogram data={riskHist} />
        </Panel>
      </div>
    </div>
  );
}

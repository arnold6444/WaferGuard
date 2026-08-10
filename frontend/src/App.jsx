import React, { useEffect, useRef, useState } from "react";
import { Icon, RiskBadge, StatusDot } from "./lib";

import InspectionWorkspace from "./InspectionWorkspace";
import MlopsWorkspace      from "./MlopsWorkspace";
import DatabaseView    from "./DatabaseView";
import SettingsView    from "./SettingsView";
import ChamberView     from "./ChamberView";
import { SettingsProvider, useStream } from "./SettingsContext";
import { MlopsAgentProvider } from "./MlopsAgentContext";
import { DefectChatProvider } from "./DefectChatContext";

const NAV = [
  { id: "chamber", icon: "activity", label: "챔버 이상탐지", en: "Chamber AI",       View: ChamberView, badge: 5 },
  { id: "inspect", icon: "layers",  label: "실시간 검사",    en: "Live Inspection", View: InspectionWorkspace },
  { id: "mlops",   icon: "box",     label: "MLOps 콘솔",    en: "MLOps",           View: MlopsWorkspace },
  { id: "data",    icon: "history", label: "데이터 관리",    en: "Data & RAG",      View: DatabaseView },
  { id: "settings",icon: "cpu",     label: "설정",          en: "Settings",        View: SettingsView },
];

function Clock() {
  const [t, setT] = useState("");
  useEffect(() => {
    const f = () => setT(new Date().toTimeString().slice(0, 8));
    f(); const id = setInterval(f, 1000); return () => clearInterval(id);
  }, []);
  return <span className="mono" style={{ fontSize: 12, color: "var(--text-2)" }}>{t}</span>;
}

function Logo() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="9" stroke="var(--accent)" strokeWidth="1.6" />
        <circle cx="12" cy="12" r="4" stroke="var(--accent)" strokeWidth="1.6" />
        <path d="M12 3v4 M12 17v4 M3 12h4 M17 12h4" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
        <circle cx="12" cy="12" r="1.4" fill="var(--accent)" />
      </svg>
      <div style={{ lineHeight: 1 }}>
        <div style={{ fontSize: 14.5, fontWeight: 700, letterSpacing: "-.02em", color: "var(--text)" }}>WaferGuard</div>
        <div className="mono" style={{ fontSize: 8.5, color: "var(--text-3)", letterSpacing: ".08em", marginTop: 1 }}>FAB QUALITY OPS</div>
      </div>
    </div>
  );
}

function AgentToast({ data, onGo, onClose }) {
  return (
    <div className="toast-in" style={{ position: "fixed", bottom: 18, right: 18, zIndex: 200, width: 330 }}>
      <div style={{
        display: "flex", flexDirection: "column", gap: 9, padding: "12px 14px",
        background: "var(--panel)", border: "1px solid var(--accent-line)", borderRadius: 10,
        boxShadow: "var(--shadow)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="bot" size={15} style={{ color: "var(--accent)" }} />
          <RiskBadge level={data.level} />
          <span className="mono" style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>{data.wafer}</span>
          <span style={{ fontSize: 11, color: "var(--text-2)" }}>{data.defect}</span>
          <button className="btn btn-ghost" onClick={onClose} aria-label="닫기"
            style={{ marginLeft: "auto", padding: "3px 6px" }}><Icon name="x" size={13} /></button>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-2)", lineHeight: 1.5 }}>
          AI 분석이 진행 중입니다. 실시간 검사 → 검사 에이전트에서 추정 원인과 권장 액션을 확인하세요.
        </div>
        <button className="btn btn-accent" onClick={onGo} style={{ alignSelf: "flex-start", padding: "5px 12px", fontSize: 11.5 }}>
          <Icon name="bot" size={13} />분석 보러 가기
        </button>
      </div>
    </div>
  );
}

function Sidebar({ active, setActive }) {
  return (
    <nav style={{
      width: 212, flex: "none", background: "var(--bg-elev)",
      borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column",
      padding: "12px 10px", gap: 3,
      position: "sticky", top: 54, height: "calc(100vh - 54px)", overflowY: "auto",
    }}>
      <div className="label-cap" style={{ padding: "6px 8px 8px" }}>운영 섹션</div>
      {NAV.map(n => {
        const on = active === n.id;
        return (
          <button key={n.id} onClick={() => setActive(n.id)} className="focusable"
            style={{
              display: "flex", alignItems: "center", gap: 11, cursor: "pointer", position: "relative",
              padding: "8px 10px", borderRadius: 8, border: "1px solid transparent",
              font: "inherit", textAlign: "left", width: "100%",
              background: on ? "var(--accent-dim)" : "transparent",
              color: on ? "var(--accent)" : "var(--text-2)",
              transition: "background .15s, color .15s",
            }}>
            {on && <span style={{ position: "absolute", left: 0, top: 8, width: 3, height: "calc(100% - 16px)", background: "var(--accent)", borderRadius: 99 }} />}
            <span style={{ display: "flex", flex: "none" }}><Icon name={n.icon} size={17} /></span>
            <span style={{ fontSize: 12.5, fontWeight: on ? 600 : 500, flex: 1 }}>{n.label}</span>
            {n.badge && <span className="mono" style={{ fontSize: 10, color: "var(--high)", background: "var(--high-dim)", borderRadius: 99, padding: "1px 6px", fontWeight: 700 }}>{n.badge}</span>}
          </button>
        );
      })}
      <div style={{ marginTop: "auto", padding: "10px 8px 4px" }}>
        <div className="panel-inset" style={{ padding: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 5 }}>
            <span className="pulse-high" style={{ width: 7, height: 7, borderRadius: 99, background: "var(--low)" }} />
            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text)" }}>라인 정상 운영</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--text-3)", lineHeight: 1.5 }}>FAB-2 · ETCH/CMP<br />MTBF 312h · OEE 91%</div>
        </div>
      </div>
    </nav>
  );
}

function AppInner() {
  const [theme, setTheme]   = useState("light");
  const [active, setActive] = useState("chamber");
  const [toast, setToast]   = useState(null);
  const [agentFocus, setAgentFocus] = useState(null);
  const { latest, tick } = useStream();
  const lastToastTick = useRef(0);
  const toastTimer = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // transient nudge toward the Agent tab when a Medium/High inspection lands
  useEffect(() => {
    if (!latest || tick === lastToastTick.current) return;
    if ((latest.risk_level === "High" || latest.risk_level === "Medium") && active !== "inspect") {
      lastToastTick.current = tick;
      setToast({ id: latest.id, level: latest.risk_level, wafer: latest.wafer_id || "W?", defect: latest.defect_type || "결함" });
      clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(() => setToast(null), 6000);
    }
  }, [tick, latest, active]);

  const cur = NAV.find(n => n.id === active);
  const View = cur.View;

  return (
    <div style={{ minHeight: "100vh" }}>
      {toast && (
        <AgentToast
          data={toast}
          onGo={() => { setAgentFocus(toast.id); setActive("inspect"); setToast(null); }}
          onClose={() => setToast(null)}
        />
      )}

      {/* top app bar */}
      <header style={{
        display: "flex", alignItems: "center", gap: 16, padding: "0 18px", height: 54,
        background: "var(--bg-elev)", borderBottom: "1px solid var(--border)",
        position: "sticky", top: 0, zIndex: 100,
      }}>
        <Logo />
        <div className="vdivider" style={{ height: 26 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="chip" style={{ color: "var(--low)", borderColor: "var(--low)" }}><StatusDot kind="ok" />시스템 정상</span>
          <span className="chip"><span style={{ color: "var(--text-3)" }}>모델</span> {active === "chamber" ? "GBR · OOF" : "v4.2.1"}</span>
          <span className="chip"><span style={{ color: "var(--text-3)" }}>{active === "chamber" ? "분석" : "큐"}</span> <span className="mono">{active === "chamber" ? "2 MODE" : "3 대기"}</span></span>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ textAlign: "right", lineHeight: 1.2 }}>
            <div className="label-cap" style={{ fontSize: 8.5 }}>KST · 야간 A조</div>
            <Clock />
          </div>
          <button className="btn btn-ghost" onClick={() => setTheme(t => t === "dark" ? "light" : "dark")}
            title="테마 전환" style={{ padding: "7px 9px" }}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
          </button>
        </div>
      </header>

      <div style={{ display: "flex", alignItems: "stretch" }}>
        <Sidebar active={active} setActive={setActive} />
        <main style={{ flex: 1, minWidth: 0, padding: 15, maxWidth: 1480, margin: "0 auto", width: "100%" }}>
          {/* context bar */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 15, paddingTop: 2 }}>
            <span style={{ color: "var(--accent)", display: "flex" }}><Icon name={cur.icon} size={18} /></span>
            <h1 style={{ margin: 0, fontSize: 17, fontWeight: 650, letterSpacing: "-.02em", color: "var(--text)" }}>{cur.label}</h1>
            <span className="mono" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 3 }}>/ {cur.en}</span>
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
              <span className="kbd">⌘K</span>
              <span style={{ fontSize: 11, color: "var(--text-3)" }}>명령 팔레트</span>
            </div>
          </div>

          <div key={active} className="fade-in">
            <View focusId={cur.id === "inspect" ? agentFocus : undefined}
              onFocusHandled={cur.id === "inspect" ? () => setAgentFocus(null) : undefined}
              onOpenInspection={(inspectionId) => { setAgentFocus(inspectionId); setActive("inspect"); }} />
          </div>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <SettingsProvider>
      <MlopsAgentProvider>
        <DefectChatProvider>
          <AppInner />
        </DefectChatProvider>
      </MlopsAgentProvider>
    </SettingsProvider>
  );
}

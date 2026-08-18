import React, { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Icon, RiskBadge, StatusDot } from "./lib";

import { SettingsProvider, useStream } from "./SettingsContext";
import { UiProvider, useUi } from "./UiContext";
import { MlopsAgentProvider } from "./MlopsAgentContext";
import { DefectChatProvider } from "./DefectChatContext";

const FabOverview = lazy(() => import("./FabOverview"));
const ProcessMonitoringView = lazy(() => import("./ProcessMonitoring/ProcessMonitoringView"));
const WaferQualityView = lazy(() => import("./WaferQuality/WaferQualityView"));
const AIAnalysisView = lazy(() => import("./AIAnalysis/AIAnalysisView"));
const MlopsWorkspace = lazy(() => import("./MlopsWorkspace"));
const DatabaseView = lazy(() => import("./DatabaseView"));
const SettingsView = lazy(() => import("./SettingsView"));

const NAV = [
  { id: "overview", icon: "gauge",    ko: "Fab 개요",       en: "Fab Overview",       View: FabOverview },
  { id: "process",  icon: "activity", ko: "공정 모니터링",   en: "Process Monitoring", View: ProcessMonitoringView },
  { id: "quality",  icon: "layers",   ko: "웨이퍼 품질",     en: "Wafer Quality",      View: WaferQualityView },
  { id: "ai",       icon: "bot",      ko: "AI 분석",         en: "AI Analysis",        View: AIAnalysisView },
  { id: "mlops",    icon: "box",      ko: "MLOps",           en: "MLOps",              View: MlopsWorkspace },
  { id: "data",     icon: "history",  ko: "데이터 / RAG",    en: "Data & RAG",         View: DatabaseView },
  { id: "settings", icon: "cpu",      ko: "설정",            en: "Settings",           View: SettingsView },
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
  const { text } = useUi();
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
          <button className="btn btn-ghost" onClick={onClose} aria-label={text("닫기", "Close")}
            style={{ marginLeft: "auto", padding: "3px 6px" }}><Icon name="x" size={13} /></button>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-2)", lineHeight: 1.5 }}>
          {text(
            "AI 분석이 진행 중입니다. AI 분석에서 공정·웨이퍼·과거 사례 근거와 권장 액션을 확인하세요.",
            "AI analysis is running. Open AI Analysis to review process, wafer, historical evidence, and recommended actions.",
          )}
        </div>
        <button className="btn btn-accent" onClick={onGo} style={{ alignSelf: "flex-start", padding: "5px 12px", fontSize: 11.5 }}>
          <Icon name="bot" size={13} />{text("분석 보러 가기", "Open analysis")}
        </button>
      </div>
    </div>
  );
}

function Sidebar({ active, setActive }) {
  const { text } = useUi();
  return (
    <nav className="app-sidebar">
      <div className="label-cap" style={{ padding: "6px 8px 8px" }}>{text("운영 섹션", "Operations")}</div>
      {NAV.map(n => {
        const on = active === n.id;
        return (
          <button key={n.id} onClick={() => setActive(n.id)} className="focusable sidebar-nav-button"
            data-active={on ? "true" : "false"}>
            {on && <span className="sidebar-active-rail" />}
            <span style={{ display: "flex", flex: "none" }}><Icon name={n.icon} size={17} /></span>
            <span style={{ fontSize: 12.5, fontWeight: on ? 600 : 500, flex: 1 }}>{text(n.ko, n.en)}</span>
            {n.badge && <span className="mono" style={{ fontSize: 10, color: "var(--high)", background: "var(--high-dim)", borderRadius: 99, padding: "1px 6px", fontWeight: 700 }}>{n.badge}</span>}
          </button>
        );
      })}
      <div style={{ marginTop: "auto", padding: "10px 8px 4px" }}>
        <div className="panel-inset" style={{ padding: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 5 }}>
            <span className="pulse-high" style={{ width: 7, height: 7, borderRadius: 99, background: "var(--low)" }} />
            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text)" }}>{text("라인 정상 운영", "Line operating normally")}</span>
          </div>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--text-3)", lineHeight: 1.5 }}>FAB-2 · ETCH/CMP<br />MTBF 312h · OEE 91%</div>
        </div>
      </div>
    </nav>
  );
}

function LanguageSwitch() {
  const { language, setLanguage, text } = useUi();
  return (
    <div className="language-switch" aria-label={text("언어 선택", "Language selector")}>
      <button type="button" className="focusable" data-active={language === "ko" ? "true" : "false"} onClick={() => setLanguage("ko")}>한국어</button>
      <button type="button" className="focusable" data-active={language === "en" ? "true" : "false"} onClick={() => setLanguage("en")}>English</button>
    </div>
  );
}

function AppInner() {
  const [active, setActive] = useState("overview");
  const [toast, setToast] = useState(null);
  const [agentFocus, setAgentFocus] = useState(null);
  const [target, setTarget] = useState(null);
  const { latest, tick } = useStream();
  const { theme, toggleTheme, text } = useUi();
  const lastToastTick = useRef(0);
  const toastTimer = useRef(null);

  // transient nudge toward the Agent tab when a Medium/High inspection lands
  useEffect(() => {
    if (!latest || tick === lastToastTick.current) return;
    if ((latest.risk_level === "High" || latest.risk_level === "Medium") && active !== "ai") {
      lastToastTick.current = tick;
      setToast({ id: latest.id, level: latest.risk_level, wafer: latest.wafer_id || "W?", defect: latest.defect_type || text("결함", "Defect") });
      clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(() => setToast(null), 6000);
    }
  }, [tick, latest, active, text]);

  const cur = NAV.find(n => n.id === active);
  const View = cur.View;

  function navigate(next) {
    const destination = typeof next === "string" ? { id: next } : next;
    if (!destination?.id || !NAV.some(item => item.id === destination.id)) return;
    if (destination.focusId) setAgentFocus(destination.focusId);
    setTarget(destination);
    setActive(destination.id);
  }

  return (
    <div className="app-root">
      {toast && (
        <AgentToast
          data={toast}
          onGo={() => { navigate({ id: "ai", focusId: toast.id }); setToast(null); }}
          onClose={() => setToast(null)}
        />
      )}

      <header className="app-header">
        <Logo />
        <div className="vdivider" style={{ height: 26 }} />
        <div className="header-status-group">
          <span className="chip" style={{ color: "var(--low)", borderColor: "var(--low)" }}><StatusDot kind="ok" />{text("시스템 정상", "System healthy")}</span>
          <span className="chip"><span style={{ color: "var(--text-3)" }}>{text("제품", "Product")}</span> FAB QUALITY OPS</span>
          <span className="chip"><span style={{ color: "var(--text-3)" }}>{text("데이터", "Data")}</span> <span className="mono">RUNTIME + DEMO</span></span>
        </div>
        <div className="header-actions">
          <LanguageSwitch />
          <div style={{ textAlign: "right", lineHeight: 1.2 }}>
            <div className="label-cap" style={{ fontSize: 8.5 }}>{text("KST · 야간 A조", "KST · Night Shift A")}</div>
            <Clock />
          </div>
          <button className="btn btn-ghost theme-toggle" onClick={toggleTheme}
            title={text("테마 전환", "Toggle theme")} aria-label={text("테마 전환", "Toggle theme")}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
            <span>{theme === "dark" ? text("라이트", "Light") : text("다크", "Dark")}</span>
          </button>
        </div>
      </header>

      <div className="app-body">
        <Sidebar active={active} setActive={(id) => navigate(id)} />
        <main className="app-main">
          <div className="context-bar">
            <span style={{ color: "var(--accent)", display: "flex" }}><Icon name={cur.icon} size={18} /></span>
            <h1>{text(cur.ko, cur.en)}</h1>
            <div className="context-tools">
              <span className="kbd">⌘K</span>
              <span style={{ fontSize: 11, color: "var(--text-3)" }}>{text("명령 팔레트", "Command palette")}</span>
            </div>
          </div>

          <Suspense fallback={<div className="panel" style={{ padding: 18 }}>{text("화면을 불러오는 중입니다…", "Loading view…")}</div>}>
            <div key={active} className="fade-in">
              <View
                target={target?.id === active ? target : undefined}
                onNavigate={navigate}
                focusId={cur.id === "ai" ? agentFocus : undefined}
                onFocusHandled={cur.id === "ai" ? () => setAgentFocus(null) : undefined}
              />
            </div>
          </Suspense>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <UiProvider>
      <SettingsProvider>
        <MlopsAgentProvider>
          <DefectChatProvider>
            <AppInner />
          </DefectChatProvider>
        </MlopsAgentProvider>
      </SettingsProvider>
    </UiProvider>
  );
}
import React from "react";
import { Icon, Panel, StatusDot } from "./lib";
import { ANOMALY_DEFECT_OPTIONS, useStream } from "./SettingsContext";
import { useUi } from "./UiContext";

const STEP_OPTIONS = ["Lithography", "Etch", "Deposition", "CMP", "Cleaning", "Inspection"];

const inputStyle = {
  background: "var(--panel-2)", border: "1px solid var(--border-strong)",
  borderRadius: 6, padding: "6px 9px", color: "var(--text)",
  font: "inherit", fontSize: 12,
};

function Field({ label, children }) {
  return (
    <label style={{ display: "grid", gap: 4 }}>
      <span className="label-cap">{label}</span>
      {children}
    </label>
  );
}

export default function SettingsView() {
  const { settings, updateSettings, tick, inFlight, runOnce } = useStream();
  const { language, theme, setLanguage, setTheme, text } = useUi();

  const setBase = (key, value) => updateSettings({ basePayload: { [key]: value } });
  const toggleDefect = (d) => {
    const has = settings.anomalyTypes.includes(d);
    const next = has ? settings.anomalyTypes.filter(x => x !== d) : [...settings.anomalyTypes, d];
    updateSettings({ anomalyTypes: next.length > 0 ? next : [d] });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Panel title={text("화면 설정", "Interface Preferences")} icon="cpu" dense>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
          <Field label={text("언어", "Language")}>
            <div className="language-switch" style={{ justifySelf: "start" }}>
              <button type="button" data-active={language === "ko" ? "true" : "false"} onClick={() => setLanguage("ko")}>한국어</button>
              <button type="button" data-active={language === "en" ? "true" : "false"} onClick={() => setLanguage("en")}>English</button>
            </div>
            <span style={{ fontSize: 10.5, color: "var(--text-3)" }}>{text("한글/영문 보조 표기를 섞지 않고 선택한 언어만 표시합니다.", "Only the selected language is shown; secondary Korean/English labels are hidden.")}</span>
          </Field>
          <Field label={text("화면 테마", "Appearance")}>
            <div style={{ display: "flex", gap: 8 }}>
              <button className={`btn ${theme === "light" ? "btn-accent" : ""}`} onClick={() => setTheme("light")}><Icon name="sun" size={14} />{text("라이트", "Light")}</button>
              <button className={`btn ${theme === "dark" ? "btn-accent" : ""}`} onClick={() => setTheme("dark")}><Icon name="moon" size={14} />{text("다크", "Dark")}</button>
            </div>
            <span style={{ fontSize: 10.5, color: "var(--text-3)" }}>{text("상단바, 사이드바, 패널, 표, 입력창, 차트까지 동일 테마 토큰을 사용합니다.", "Header, sidebar, panels, tables, inputs, and charts use the same theme tokens.")}</span>
          </Field>
        </div>
      </Panel>

      <Panel title={text("실시간 Inspection 스트림", "Live Inspection Stream")} icon="pulse" dense
        right={
          <span className="chip" style={{ color: settings.enabled ? "var(--low)" : "var(--text-3)", borderColor: settings.enabled ? "var(--low)" : "var(--border-strong)" }}>
            <StatusDot kind={settings.enabled ? "ok" : "idle"} />{settings.enabled ? text("스트리밍", "Streaming") : text("일시정지", "Paused")}
          </span>
        }>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14, alignItems: "start" }}>
          <Field label={text("스트림 활성화", "Stream Control")}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button
                className={`btn ${settings.enabled ? "btn-accent" : ""}`}
                onClick={() => updateSettings({ enabled: !settings.enabled })}
                style={{ minWidth: 110 }}>
                <Icon name={settings.enabled ? "pause" : "play"} size={13} />
                {settings.enabled ? text("일시정지", "Pause") : text("재개", "Resume")}
              </button>
              <button className="btn btn-ghost" onClick={runOnce} disabled={inFlight} title={text("수동 1회 실행", "Run once manually")}>
                <Icon name="refresh" size={13} style={inFlight ? { animation: "spin 1s linear infinite" } : undefined} />
                {text("1회 실행", "Run once")}
              </button>
            </div>
            <span style={{ fontSize: 10.5, color: "var(--text-3)", marginTop: 4 }}>{text(`누적 검사 ${tick}건`, `${tick} inspections processed`)}</span>
          </Field>

          <Field label={text(`스트림 주기 · ${settings.intervalMs} ms`, `Stream Interval · ${settings.intervalMs} ms`)}>
            <input type="range" min="500" max="10000" step="250"
              value={settings.intervalMs}
              onChange={e => updateSettings({ intervalMs: Number(e.target.value) })} />
            <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>0.5s — 10s</span>
          </Field>

          <Field label={text(`비정상 발생 확률 · ${(settings.anomalyRate * 100).toFixed(0)}%`, `Anomaly Probability · ${(settings.anomalyRate * 100).toFixed(0)}%`)}>
            <input type="range" min="0" max="1" step="0.01"
              value={settings.anomalyRate}
              onChange={e => updateSettings({ anomalyRate: Number(e.target.value) })} />
            <span className="mono" style={{ fontSize: 10.5, color: "var(--text-3)" }}>{text("0% (전부 정상) — 100% (전부 비정상)", "0% (all normal) — 100% (all anomalous)")}</span>
          </Field>

          <Field label={text("Agent LLM 분석", "Agent LLM Analysis")}>
            <button
              className={`btn ${settings.useLlm ? "btn-accent" : ""}`}
              onClick={() => updateSettings({ useLlm: !settings.useLlm })}
              style={{ minWidth: 130 }}>
              <Icon name={settings.useLlm ? "bot" : "gauge"} size={13} />
              {settings.useLlm ? text("실제 LLM 호출", "Use LLM") : text("룰 기반 폴백", "Rule fallback")}
            </button>
            <span style={{ fontSize: 10.5, color: "var(--text-3)", marginTop: 4, lineHeight: 1.5 }}>
              {settings.useLlm
                ? text("Medium/High 검사마다 LLM 분석을 수행합니다.", "Run LLM analysis for each Medium/High inspection.")
                : text("LLM 호출 없이 즉시 룰 기반 판단만 수행합니다.", "Skip LLM calls and use the rule-based fallback immediately.")}
            </span>
          </Field>
        </div>

        <div style={{ marginTop: 18 }}>
          <div className="label-cap" style={{ marginBottom: 8 }}>{text("비정상 결함 유형 (선택된 유형만 주입됨)", "Injected Defect Types (selected types only)")}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {ANOMALY_DEFECT_OPTIONS.map(d => {
              const on = settings.anomalyTypes.includes(d);
              return (
                <button key={d} onClick={() => toggleDefect(d)}
                  className="focusable"
                  style={{
                    fontSize: 11.5, padding: "5px 10px", borderRadius: 99, cursor: "pointer",
                    border: `1px solid ${on ? "var(--accent)" : "var(--border-strong)"}`,
                    background: on ? "var(--accent-dim)" : "transparent",
                    color: on ? "var(--accent)" : "var(--text-2)",
                    font: "inherit",
                  }}>
                  {d}
                </button>
              );
            })}
          </div>
        </div>
      </Panel>

      <Panel title={text("공정 기본 Context", "Base Process Context")} icon="layers" dense>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 12, marginBottom: 14 }}>
          {[
            ["Lot ID", "lot_id"],
            ["Line", "line_id"],
            ["Equipment", "equipment_id"],
            ["Recipe", "recipe_id"],
          ].map(([label, key]) => (
            <Field key={key} label={label}>
              <input type="text" value={settings.basePayload[key]}
                onChange={e => setBase(key, e.target.value)} style={inputStyle} />
            </Field>
          ))}
          <Field label={text("공정 Step", "Process Step")}>
            <select value={settings.basePayload.process_step}
              onChange={e => setBase("process_step", e.target.value)} style={inputStyle}>
              {STEP_OPTIONS.map(o => <option key={o}>{o}</option>)}
            </select>
          </Field>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 12 }}>
          {[
            ["CD (nm)", "cd_nm"],
            ["Overlay (nm)", "overlay_nm"],
            ["Thickness (nm)", "film_thickness_nm"],
            ["Roughness (nm)", "roughness_nm"],
            ["Yield Proxy", "yield_proxy"],
          ].map(([label, key]) => (
            <Field key={key} label={label}>
              <input type="number" step="0.1" value={settings.basePayload[key]}
                onChange={e => setBase(key, Number(e.target.value))} style={inputStyle} />
            </Field>
          ))}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 12, lineHeight: 1.6 }}>
          {text(
            "정상 데이터는 defect_hint=None으로 주입되며, 비정상은 위에서 선택된 결함 유형에서 무작위로 선택됩니다. 계측값은 매 검사마다 작은 지터를 추가합니다.",
            "Normal data uses defect_hint=None. Anomalies are sampled from the selected defect types, with small metrology jitter added per inspection.",
          )}
        </div>
      </Panel>
    </div>
  );
}
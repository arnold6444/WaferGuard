import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const ANOMALY_DEFECTS = ["Center", "Donut", "Edge-Loc", "Edge-Ring", "Loc", "Random", "Scratch", "Near-full"];

const DEFAULT_SETTINGS = {
  enabled: true,
  intervalMs: 2000,
  anomalyRate: 0.15,
  useLlm: true,
  anomalyTypes: [...ANOMALY_DEFECTS],
  basePayload: {
    lot_id: "LOT-DEMO-042",
    line_id: "LINE-7",
    equipment_id: "ETCH-02",
    process_step: "Etch",
    recipe_id: "RCP-ETCH-EDGE-02",
    cd_nm: 32.5,
    overlay_nm: 4.2,
    film_thickness_nm: 88.0,
    roughness_nm: 1.2,
    yield_proxy: 0.982,
  },
};

const STORAGE_KEY = "waferguard.stream.settings.v1";

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw);
    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      basePayload: { ...DEFAULT_SETTINGS.basePayload, ...(parsed.basePayload || {}) },
      anomalyTypes: Array.isArray(parsed.anomalyTypes) && parsed.anomalyTypes.length > 0
        ? parsed.anomalyTypes
        : DEFAULT_SETTINGS.anomalyTypes,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function saveSettings(s) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

const SettingsCtx = createContext(null);

export const ANOMALY_DEFECT_OPTIONS = ANOMALY_DEFECTS;

export function useStream() {
  const ctx = useContext(SettingsCtx);
  if (!ctx) throw new Error("useStream must be used within SettingsProvider");
  return ctx;
}

export function SettingsProvider({ children }) {
  const [settings, setSettings] = useState(loadSettings);
  const [latest, setLatest] = useState(null);
  const [tick, setTick] = useState(0);
  const [inFlight, setInFlight] = useState(false);
  // session-scoped risk queue: filled live as Medium/High inspections stream in
  // (NOT from a bulk DB fetch), so it starts empty on refresh
  const [riskQueue, setRiskQueue] = useState([]);
  const counterRef = useRef(0);
  const settingsRef = useRef(settings);
  const inFlightRef = useRef(false);

  useEffect(() => { settingsRef.current = settings; saveSettings(settings); }, [settings]);

  const updateSettings = useCallback((patch) => {
    setSettings(prev => {
      if (typeof patch === "function") return patch(prev);
      return { ...prev, ...patch, basePayload: { ...prev.basePayload, ...(patch.basePayload || {}) } };
    });
  }, []);

  const ingestVisionFinding = useCallback(async (payload) => {
    setInFlight(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/inspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || "영상 이상 결과를 검사 흐름으로 전달하지 못했습니다.");
      }
      const data = await res.json();
      setLatest(data);
      setTick(t => t + 1);
      if (data && (data.risk_level === "High" || data.risk_level === "Medium")) {
        setRiskQueue(q => (q.some(r => r.id === data.id) ? q : [data, ...q].slice(0, 50)));
      }
      return data;
    } finally {
      setInFlight(false);
    }
  }, []);

  const runOnce = useCallback(async () => {
    if (inFlightRef.current) return;
    const s = settingsRef.current;
    counterRef.current += 1;
    const idx = counterRef.current;
    const wafer_id = `WF-${String(idx).padStart(3, "0")}`;
    const isAnomaly = Math.random() < s.anomalyRate;
    const pool = s.anomalyTypes && s.anomalyTypes.length > 0 ? s.anomalyTypes : ANOMALY_DEFECTS;
    const defect_hint = isAnomaly ? pool[Math.floor(Math.random() * pool.length)] : "None";

    const jitter = (v, pct) => Number((v * (1 + (Math.random() - 0.5) * 2 * pct)).toFixed(2));
    const bp = s.basePayload;
    const payload = {
      ...bp,
      wafer_id,
      defect_hint,
      cd_nm: jitter(Number(bp.cd_nm), 0.06),
      overlay_nm: jitter(Number(bp.overlay_nm), 0.08),
      film_thickness_nm: jitter(Number(bp.film_thickness_nm), 0.03),
      roughness_nm: jitter(Number(bp.roughness_nm), 0.1),
      defect_count: null,
      yield_proxy: Number(bp.yield_proxy),
      image_source: "synthetic_wafer",
      proxy_dataset: "mvtec-ad",
      use_llm: s.useLlm !== false,
    };

    inFlightRef.current = true;
    setInFlight(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/inspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) return;
      const data = await res.json();
      setLatest(data);
      setTick(t => t + 1);
      // only actual Medium/High results enter the agent queue
      if (data && (data.risk_level === "High" || data.risk_level === "Medium")) {
        setRiskQueue(q => (q.some(r => r.id === data.id) ? q : [data, ...q].slice(0, 50)));
      }
    } catch {
      // silently keep last
    } finally {
      inFlightRef.current = false;
      setInFlight(false);
    }
  }, []);

  // initial load: try latest inspection from history so first paint isn't empty
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/inspections?limit=1`);
        if (!res.ok) return;
        const data = await res.json();
        const first = Array.isArray(data) ? data[0] : data?.inspections?.[0];
        if (first) { setLatest(first); setTick(t => t + 1); }
      } catch { /* ignore */ }
    })();
  }, []);

  // stream loop
  useEffect(() => {
    if (!settings.enabled) return;
    const id = setInterval(runOnce, Math.max(500, settings.intervalMs));
    return () => clearInterval(id);
  }, [settings.enabled, settings.intervalMs, runOnce]);

  const value = {
    settings,
    updateSettings,
    latest,
    tick,
    inFlight,
    runOnce,
    ingestVisionFinding,
    riskQueue,
    setRiskQueue,
  };

  return <SettingsCtx.Provider value={value}>{children}</SettingsCtx.Provider>;
}

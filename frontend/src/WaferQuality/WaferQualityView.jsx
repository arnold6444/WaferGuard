import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import InspectionView from "../InspectionView";
import { Icon, Panel, SubTabs } from "../lib";
import { useUi } from "../UiContext";
import WaferVisionView from "../WaferVisionView";
import { buildDemoQuality, expandRuntimeLot } from "./qualityData";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const DEMO = buildDemoQuality();
const TAB_DEFS = [
  { id: "overview", ko: "Lot 개요", en: "Lot Overview", icon: "layers" },
  { id: "timeline", ko: "웨이퍼 추세", en: "Wafer Timeline", icon: "pulse" },
  { id: "defect", ko: "누적 결함", en: "Defect Map", icon: "activity" },
  { id: "detail", ko: "웨이퍼 상세", en: "Wafer Detail", icon: "zoom" },
];

function SourceTag({ source }) {
  const demo = source?.includes("demo") || source?.includes("snapshot");
  return <span className={`source-tag source-${demo ? "demo" : "runtime"}`}>{demo ? "Demo / Proxy" : "Runtime DB"}</span>;
}

function StatusLabel({ status }) {
  const { text } = useUi();
  const labels = {
    normal: text("정상", "Normal"),
    warning: text("경고", "Warning"),
    critical: text("위험", "Critical"),
    not_inspected: text("미검사", "Not inspected"),
  };
  return labels[status] || status;
}

function LotSummary({ lot }) {
  const { text } = useUi();
  const cards = [
    [text("검사 완료", "Inspected"), `${lot.inspected} / ${lot.wafer_total}`],
    [text("정상", "Normal"), lot.normal],
    [text("경고", "Warning"), lot.warning],
    [text("위험", "Critical"), lot.critical],
    [text("공정 경고", "Process Alerts"), lot.process_alerts ?? 0],
    [text("평균 Risk", "Average Risk"), `${lot.avg_risk ?? "—"}`],
  ];
  return <div className="quality-summary">{cards.map(([label, value]) => <div className="panel" key={label}><span>{label}</span><strong className="mono">{value}</strong></div>)}</div>;
}

function WaferGrid({ wafers, selected, onSelect }) {
  const { text } = useUi();
  return (
    <div className="quality-wafer-grid">
      {wafers.map(wafer => (
        <button key={wafer.wafer_id} type="button" className={`focusable quality-wafer status-${wafer.status} ${selected === wafer.wafer_id ? "is-selected" : ""}`} disabled={wafer.status === "not_inspected"} onClick={() => onSelect(wafer)}>
          <span className={`status-dot status-${wafer.status}`} />
          <strong className="mono">{wafer.wafer_id}</strong>
          <small>{wafer.risk_score == null ? "—" : `${wafer.risk_score.toFixed?.(1) ?? wafer.risk_score} ${text("risk", "risk")}`}</small>
        </button>
      ))}
    </div>
  );
}

function AccumulatedMap({ wafers }) {
  const { text } = useUi();
  const points = wafers.filter(item => item.defect_point);
  return (
    <div className="accumulated-map-wrap">
      <svg className="accumulated-map" viewBox="0 0 320 320" role="img" aria-label={text("누적 결함 위치 proxy map", "Accumulated defect position proxy map")}>
        <defs><radialGradient id="waferFill"><stop offset="0" stopColor="var(--panel)" /><stop offset="1" stopColor="var(--panel-2)" /></radialGradient></defs>
        <circle cx="160" cy="160" r="138" fill="url(#waferFill)" stroke="var(--border-strong)" strokeWidth="2" />
        <path d="M146 297h28" stroke="var(--border-strong)" strokeWidth="3" strokeLinecap="round" />
        {[46, 92].map(radius => <circle key={radius} cx="160" cy="160" r={radius} fill="none" stroke="var(--border-soft)" strokeDasharray="4 7" />)}
        <path d="M22 160h276 M160 22v276" stroke="var(--border-soft)" strokeDasharray="4 7" />
        {points.map((item, index) => {
          const x = 160 + item.defect_point.x * 132;
          const y = 160 + item.defect_point.y * 132;
          const radius = item.status === "critical" ? 22 : 15;
          return <g key={`${item.wafer_id}-${index}`}><circle cx={x} cy={y} r={radius + 10} fill="var(--high-dim)" opacity=".35" /><circle cx={x} cy={y} r={radius} fill={item.status === "critical" ? "var(--high)" : "var(--med)"} opacity=".68" /><text x={x} y={y + 3} textAnchor="middle" fontSize="9" fill="white">{item.wafer_id}</text></g>;
        })}
      </svg>
      <div className="accumulated-map-meta"><strong>{points.length} {text("개 반복 위치 후보", "repeated position candidates")}</strong><span>{text("ROI 중심 proxy · 실제 die-level 좌표가 아닙니다.", "ROI center proxy · Not actual die-level coordinates.")}</span></div>
    </div>
  );
}

export default function WaferQualityView({ target, onNavigate }) {
  const { language, text } = useUi();
  const [tab, setTab] = useState(target?.tab || "overview");
  const [lots, setLots] = useState([]);
  const [lotId, setLotId] = useState(target?.lotId || "");
  const [detail, setDetail] = useState(DEMO);
  const [selectedId, setSelectedId] = useState(target?.waferId || "W15");
  const [evidenceTab, setEvidenceTab] = useState("inspection");
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ equipment: "all", process: "all", recipe: "all", time: "all" });
  const tabs = useMemo(() => TAB_DEFS.map(item => ({ ...item, label: language === "en" ? item.en : item.ko, en: null })), [language]);

  const loadLots = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/quality/lots?limit=1000`);
      const body = response.ok ? await response.json() : { items: [] };
      const runtimeLots = body.items || [];
      const all = [...runtimeLots, DEMO.lot];
      setLots(all);
      setLotId(current => current || runtimeLots[0]?.lot_id || DEMO.lot.lot_id);
    } catch {
      setLots([DEMO.lot]);
      setLotId(current => current || DEMO.lot.lot_id);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadLots(); }, [loadLots]);
  useEffect(() => {
    if (!lotId) return;
    if (lotId === DEMO.lot.lot_id) {
      setDetail(DEMO);
      setSelectedId(target?.waferId || "W15");
      return;
    }
    setLoading(true);
    fetch(`${API_BASE}/api/v1/quality/lots/${encodeURIComponent(lotId)}`)
      .then(response => response.ok ? response.json() : null)
      .then(body => {
        if (!body) return;
        const expanded = expandRuntimeLot(body);
        setDetail(expanded);
        const first = [...expanded.wafers].filter(item => item.status !== "not_inspected").sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0))[0];
        setSelectedId(target?.waferId || first?.wafer_id || "W01");
      })
      .finally(() => setLoading(false));
  }, [lotId, target?.waferId]);
  useEffect(() => {
    if (target?.tab) setTab(target.tab);
    if (target?.lotId) setLotId(target.lotId);
    if (target?.waferId) setSelectedId(target.waferId);
  }, [target]);

  const selected = detail.wafers.find(item => item.wafer_id === selectedId) || detail.wafers.find(item => item.status !== "not_inspected");
  const actualWafers = detail.wafers.filter(item => item.status !== "not_inspected");
  const values = useMemo(() => ({
    equipment: [...new Set(actualWafers.map(item => item.equipment_id).filter(Boolean))],
    process: [...new Set(actualWafers.map(item => item.process_step).filter(Boolean))],
    recipe: [...new Set(actualWafers.map(item => item.recipe_id).filter(Boolean))],
  }), [actualWafers]);
  const filtered = useMemo(() => actualWafers.filter(item => {
    if (filters.equipment !== "all" && item.equipment_id !== filters.equipment) return false;
    if (filters.process !== "all" && item.process_step !== filters.process) return false;
    if (filters.recipe !== "all" && item.recipe_id !== filters.recipe) return false;
    if (filters.time === "latest5" && item.sequence < Math.max(...actualWafers.map(row => row.sequence)) - 4) return false;
    return true;
  }), [actualWafers, filters]);

  function chooseWafer(wafer) {
    setSelectedId(wafer.wafer_id);
    setTab("detail");
    setEvidenceTab("inspection");
  }

  const statusLabels = {
    normal: text("정상", "Normal"), warning: text("경고", "Warning"), critical: text("위험", "Critical"), not_inspected: text("미검사", "Not inspected"),
  };

  return (
    <div className="quality-page">
      <div className="quality-toolbar panel">
        <div><span className="label-cap">Lot</span><select value={lotId} onChange={event => setLotId(event.target.value)}>{lots.map(lot => <option key={lot.lot_id} value={lot.lot_id}>{lot.lot_id}</option>)}</select></div>
        <SourceTag source={detail.data_source} />
        <span className="quality-toolbar-note">{text("DB 우선 · 부족 시 기존 Vision snapshot demo", "Use runtime DB first · Fall back to the Vision snapshot demo when data is insufficient")}</span>
        {loading && <span className="chip">{text("로딩", "LOADING")}</span>}
      </div>
      <SubTabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === "overview" && (
        <div className="quality-section">
          <LotSummary lot={detail.lot} />
          <Panel title={`${detail.lot.lot_id} · ${text("웨이퍼 상태", "Wafer Status")}`} icon="layers" right={<SourceTag source={detail.data_source} />}>
            <WaferGrid wafers={detail.wafers} selected={selectedId} onSelect={chooseWafer} />
            <div className="quality-legend">{["normal", "warning", "critical", "not_inspected"].map(status => <span key={status}><i className={`status-dot status-${status}`} />{statusLabels[status]}</span>)}</div>
          </Panel>
        </div>
      )}

      {tab === "timeline" && (
        <div className="quality-section">
          <div className="source-notice"><Icon name="pulse" size={13} />{text("웨이퍼 sequence별 proxy/runtime 품질 변화 · 미검사 웨이퍼는 그래프에서 제외됩니다.", "Proxy/runtime quality trend by wafer sequence · Uninspected wafers are excluded from the chart.")}</div>
          <Panel title={text("Risk / Vision 점수 추세", "Risk / Vision Score Timeline")} icon="pulse" right={<span className="chip">WAFER SEQUENCE</span>}>
            <div className="quality-timeline-chart"><ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: 270 }}><LineChart data={actualWafers} margin={{ top: 14, right: 20, bottom: 0, left: -8 }}><CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="wafer_id" tick={{ fontSize: 9, fill: "var(--text-3)" }} /><YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: "var(--text-3)" }} /><Tooltip contentStyle={{ background: "var(--panel)", borderColor: "var(--border)", color: "var(--text)" }} /><Legend wrapperStyle={{ fontSize: 10 }} /><Line type="monotone" dataKey="risk_score" name={text("Risk 점수", "Risk Score")} stroke="var(--high)" strokeWidth={2.4} dot /><Line type="monotone" dataKey="vision_score" name={text("Vision 점수", "Vision Score")} stroke="var(--accent)" strokeWidth={1.8} dot /></LineChart></ResponsiveContainer></div>
          </Panel>
          <Panel title={text("Metrology / 결함 추세", "Metrology / Defect Trend")} icon="activity" right={<span className="chip">PROXY METRICS</span>}>
            <div className="quality-timeline-chart"><ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 640, height: 270 }}><LineChart data={actualWafers} margin={{ top: 14, right: 20, bottom: 0, left: -8 }}><CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="wafer_id" tick={{ fontSize: 9, fill: "var(--text-3)" }} /><YAxis tick={{ fontSize: 9, fill: "var(--text-3)" }} /><Tooltip contentStyle={{ background: "var(--panel)", borderColor: "var(--border)", color: "var(--text)" }} /><Legend wrapperStyle={{ fontSize: 10 }} /><Line type="monotone" dataKey="defect_count" name={text("결함 수", "Defect Count")} stroke="var(--med)" dot /><Line type="monotone" dataKey="overlay_nm" name="Overlay nm" stroke="var(--chart-purple)" dot /></LineChart></ResponsiveContainer></div>
          </Panel>
        </div>
      )}

      {tab === "defect" && (
        <div className="quality-section">
          <div className="quality-filters panel">
            <label>{text("설비", "Equipment")}<select value={filters.equipment} onChange={event => setFilters(value => ({ ...value, equipment: event.target.value }))}><option value="all">{text("전체", "All")}</option>{values.equipment.map(value => <option key={value}>{value}</option>)}</select></label>
            <label>{text("공정 단계", "Process Step")}<select value={filters.process} onChange={event => setFilters(value => ({ ...value, process: event.target.value }))}><option value="all">{text("전체", "All")}</option>{values.process.map(value => <option key={value}>{value}</option>)}</select></label>
            <label>Recipe<select value={filters.recipe} onChange={event => setFilters(value => ({ ...value, recipe: event.target.value }))}><option value="all">{text("전체", "All")}</option>{values.recipe.map(value => <option key={value}>{value}</option>)}</select></label>
            <label>{text("시간 범위", "Time Range")}<select value={filters.time} onChange={event => setFilters(value => ({ ...value, time: event.target.value }))}><option value="all">{text("검사 완료 전체", "All inspected")}</option><option value="latest5">{text("최근 5개 웨이퍼", "Latest 5 wafers")}</option></select></label>
          </div>
          <div className="quality-defect-grid">
            <Panel title={text("누적 결함 맵", "Accumulated Defect Map")} icon="activity" right={<span className="chip">{filtered.length} WAFERS</span>}><AccumulatedMap wafers={filtered} /></Panel>
            <Panel title={text("반복 위치 후보", "Repeated Position Candidates")} icon="alert" right={<span className="chip">NOT CAUSAL</span>}><div className="quality-candidate-copy"><strong>{text("Edge 방향 반복 hotspot", "Repeated edge-direction hotspot")}</strong><p>{text("선택 구간의 ROI center가 비슷한 방향에 누적됩니다. 원본 defect segmentation 좌표가 아닌 proxy map이므로 공정 원인으로 단정하지 않습니다.", "ROI centers accumulate in a similar direction in the selected range. This is a proxy map, not original defect-segmentation coordinates, so it must not be treated as a confirmed process cause.")}</p><div>{filtered.filter(item => item.defect_point).map(item => <button key={item.wafer_id} type="button" className="btn btn-ghost" onClick={() => chooseWafer(item)}>{item.wafer_id} · {item.defect_type}</button>)}</div></div></Panel>
          </div>
        </div>
      )}

      {tab === "detail" && selected && (
        <div className="quality-section">
          <div className="quality-detail-header panel">
            <div><span className="label-cap">{text("선택 웨이퍼", "Selected Wafer")}</span><strong className="mono">{selected.wafer_id}</strong><span className={`status-label status-${selected.status}`}><StatusLabel status={selected.status} /></span></div>
            <div className="quality-evidence-tabs"><button type="button" className={evidenceTab === "inspection" ? "is-active" : ""} onClick={() => setEvidenceTab("inspection")}>{text("검사 / Metrology", "Inspection / Metrology")}</button><button type="button" className={evidenceTab === "vision" ? "is-active" : ""} onClick={() => setEvidenceTab("vision")}>{text("Vision 근거", "Vision Evidence")}</button></div>
          </div>
          {evidenceTab === "inspection" ? (
            selected.inspection ? <InspectionView inspection={selected.inspection} embedded onOpenAgent={inspectionId => onNavigate?.({ id: "ai", focusId: inspectionId })} /> : <div className="panel process-empty">{text("선택 웨이퍼는 아직 검사되지 않았습니다.", "The selected wafer has not been inspected yet.")}</div>
          ) : (
            <WaferVisionView key={`${selected.wafer_id}-${selected.vision_chamber_id || "proxy"}`} embedded initialChamberId={selected.vision_chamber_id} onOpenInspection={inspectionId => onNavigate?.({ id: "ai", focusId: inspectionId })} />
          )}
        </div>
      )}
    </div>
  );
}
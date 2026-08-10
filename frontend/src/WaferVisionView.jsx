import React, { useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ComposedChart,
} from "recharts";

import waferVision from "./data/waferVisionSample.json";
import { Icon, Metric, Panel } from "./lib";
import { useStream } from "./SettingsContext";


const MODE_META = {
  statistical: { label: "통계 기준", short: "STAT", color: "var(--high)" },
  ai: { label: "정상 전용 AI", short: "AI", color: "var(--accent)" },
  comparison: { label: "두 방식 비교", short: "COMPARE", color: "var(--med)" },
};


function statusFor(chamber, mode) {
  if (mode === "statistical") return chamber.statStatus;
  if (mode === "ai") return chamber.aiStatus;
  if (chamber.statStatus === "anomaly" || chamber.aiStatus === "anomaly") return "anomaly";
  if (chamber.statStatus === "caution" || chamber.aiStatus === "caution") return "caution";
  return "normal";
}


function ModeSwitch({ value, onChange }) {
  return (
    <div className="vision-mode-switch" role="group" aria-label="영상 분석 방식 선택">
      {Object.entries(MODE_META).map(([id, meta]) => (
        <button
          type="button"
          key={id}
          className={`focusable${value === id ? " is-active" : ""}`}
          onClick={() => onChange(id)}
          aria-pressed={value === id}
        >
          <span className="mono">{meta.short}</span>{meta.label}
        </button>
      ))}
    </div>
  );
}


function VisionMetric({ label, value, unit, sub, icon, tone = "accent" }) {
  const color = tone === "high" ? "var(--high)" : tone === "med" ? "var(--med)" : tone === "low" ? "var(--low)" : "var(--accent)";
  return (
    <div className="panel chamber-stat vision-stat">
      <span className="chamber-stat-icon" style={{ color, background: `var(--${tone}-dim, var(--accent-dim))` }}>
        <Icon name={icon} size={16} />
      </span>
      <Metric label={label} value={value} unit={unit} sub={sub} accent={color} />
    </div>
  );
}


function ScoreTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chamber-tooltip">
      <div className="mono chamber-tooltip-label">{label}</div>
      {payload.filter(item => item.value != null).map(item => (
        <div className="chamber-tooltip-row" key={item.dataKey}>
          <span className="chamber-tooltip-dot" style={{ background: item.color }} />
          <span>{item.name}</span><strong className="mono">{Number(item.value).toFixed(2)}</strong>
        </div>
      ))}
    </div>
  );
}


function ImageEvidence({ title, badge, src, caption, tone = "stat" }) {
  return (
    <figure className={`vision-image-card ${tone}`}>
      <div className="vision-image-head"><strong>{title}</strong><span className="mono">{badge}</span></div>
      <div className="vision-image-stage"><img src={src} alt={`${title} 시각화`} /></div>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}


export default function WaferVisionView({ onOpenInspection }) {
  const [detectorMode, setDetectorMode] = useState("comparison");
  const [selectedChamberId, setSelectedChamberId] = useState(waferVision.anomalyChambers[0].id);
  const [handoff, setHandoff] = useState({ status: "idle", message: "" });
  const { ingestVisionFinding } = useStream();

  const selectedChamber = waferVision.grid.find(item => item.id === selectedChamberId) || waferVision.anomalyChambers[0];
  const detail = waferVision.details[selectedChamberId];
  const trend = waferVision.series[selectedChamberId] || [];
  const anomalyCount = detectorMode === "ai"
    ? waferVision.grid.filter(item => item.aiStatus === "anomaly").length
    : waferVision.grid.filter(item => item.statStatus === "anomaly").length;
  const anomalyImages = detectorMode === "ai"
    ? waferVision.summary.anomaly_images
    : waferVision.summary.anomaly_images;

  const sendToInspectionAgent = async () => {
    if (!detail) return;
    setHandoff({ status: "sending", message: "Inspection Agent에 영상 근거를 전달하고 있습니다." });
    const day = detail.sequenceLabel.replace("Day ", "D");
    const chamberNumber = selectedChamberId.replace("Chamber_", "");
    try {
      const inspection = await ingestVisionFinding({
        lot_id: "LOT-WAFER-VISION-SAMPLE",
        wafer_id: `WF-CH${chamberNumber}-${day}`,
        line_id: "LINE-7",
        equipment_id: selectedChamberId,
        process_step: "Inspection",
        recipe_id: "RCP-WAFER-VISION-COMPARE",
        image_source: "public_proxy",
        proxy_dataset: "wafer-particle-sample",
        defect_hint: "Edge-Loc",
        cd_nm: 32.5,
        overlay_nm: 4.2,
        film_thickness_nm: 88.0,
        roughness_nm: 1.2,
        defect_count: null,
        yield_proxy: 0.982,
        operator_note: `${selectedChamberId} ${detail.sequenceLabel} 영상 이상. 통계 ${detail.statScore}, AI ${detail.aiScore}, 방향 ${detail.direction}, 통계 이상면적 ${detail.statArea}%. 이미지 신호만으로 물리 원인은 확정하지 않음.`,
        vision_source: "wafer_particle comparison",
        vision_stat_score: detail.statScore,
        vision_ai_score: detail.aiScore,
        vision_direction: detail.direction,
        vision_sequence_label: detail.sequenceLabel,
        vision_first_anomaly: selectedChamber.firstAnomaly,
        vision_anomaly_area_ratio: detail.statArea / 100,
        use_llm: true,
      });
      setHandoff({ status: "done", message: `${inspection.risk_level} 리스크로 등록됐습니다.` });
      onOpenInspection?.(inspection.id);
    } catch (error) {
      setHandoff({ status: "error", message: error.message || "Agent 연결에 실패했습니다." });
    }
  };

  return (
    <div className="vision-page">
      <section className="panel vision-hero">
        <div className="vision-hero-copy">
          <div className="chamber-eyebrow"><span />WAFER IMAGE · CHAMBER ANOMALY</div>
          <h2>저항 이상을 찾은 뒤, 웨이퍼 영상에서<br />언제·어디서 변화가 시작됐는지 확인합니다.</h2>
          <p>
            `wafer_particle`의 픽셀 중앙값·MAD 통계 탐지와 정상 전용 ResNet18-PaDiM 특징 탐지를 WaferGuard에 병합했습니다.
            영상 결과는 기존 Inspection Agent와 MLOps 판단 흐름으로 넘길 수 있습니다.
          </p>
          <ModeSwitch value={detectorMode} onChange={setDetectorMode} />
        </div>
        <div className="vision-hero-result">
          <div className="vision-result-number"><span className="mono">{anomalyCount}</span><small>/ {waferVision.source.chambers} CHAMBERS</small></div>
          <div>
            <span className="chamber-status chamber-status-high"><span />영상 이상 후보</span>
            <p><strong>CH-007 · CH-047 · CH-061</strong><br />총 {anomalyImages}개 시점에서 반복 변화가 검출됐습니다.</p>
          </div>
        </div>
      </section>

      <div className="chamber-stats">
        <VisionMetric label="분석 이미지" value={waferVision.source.images.toLocaleString()} unit="PNG" sub={`${waferVision.source.imageSize.join("×")} px · 파싱 경고 0`} icon="layers" />
        <VisionMetric label="이상 챔버" value={anomalyCount} unit="CH" sub={`${waferVision.source.chambers - anomalyCount}개 정상 범위`} icon="alert" tone="high" />
        <VisionMetric label="이상 이미지" value={anomalyImages} unit="장" sub="최초 시점과 방향까지 추적" icon="history" tone="med" />
        <VisionMetric label="판정 일치" value={waferVision.evaluation.comparison.same_status.toLocaleString()} unit="/ 3,000" sub="현재 반복 샘플 내 통계·AI 비교" icon="check" tone="low" />
      </div>

      <div className="vision-main-grid">
        <Panel title="100개 챔버 영상 상태판" icon="layers" right={<span className="chip">{MODE_META[detectorMode].short}</span>}>
          <div className="vision-grid-legend">
            <span><i className="normal" />정상</span><span><i className="anomaly" />이상</span>
            <small>이상 셀을 누르면 상세 근거가 바뀝니다.</small>
          </div>
          <div className="vision-chamber-grid">
            {waferVision.grid.map(chamber => {
              const status = statusFor(chamber, detectorMode);
              const selectable = Boolean(waferVision.details[chamber.id]);
              return (
                <button
                  type="button"
                  key={chamber.id}
                  className={`focusable ${status}${selectedChamberId === chamber.id ? " is-selected" : ""}`}
                  onClick={() => selectable && setSelectedChamberId(chamber.id)}
                  title={`${chamber.label} · ${status === "anomaly" ? "이상" : "정상"}`}
                  aria-pressed={selectedChamberId === chamber.id}
                >
                  <span>{chamber.label.replace("CH-", "")}</span>
                  {status === "anomaly" && <i />}
                </button>
              );
            })}
          </div>
        </Panel>

        <Panel title="우선 점검 챔버" icon="alert" right={<span className="chip chamber-chip-high">TOP 3</span>}>
          <div className="vision-ranking">
            {waferVision.anomalyChambers.map((chamber, index) => (
              <button
                type="button"
                key={chamber.id}
                className={`focusable${selectedChamberId === chamber.id ? " is-selected" : ""}`}
                onClick={() => setSelectedChamberId(chamber.id)}
              >
                <span className="mono vision-rank">0{index + 1}</span>
                <div><strong>{chamber.label}</strong><small>{chamber.firstAnomaly}부터 · {chamber.direction}</small></div>
                <div className="vision-rank-score"><strong className="mono">{detectorMode === "ai" ? chamber.aiScore.toFixed(1) : chamber.statScore.toFixed(1)}</strong><small>{detectorMode === "ai" ? "AI" : "STAT"}</small></div>
              </button>
            ))}
          </div>
          <div className="vision-model-note">
            <span className="mono">MODEL</span>
            <strong>{waferVision.model.architecture}</strong>
            <p>저장된 정상 전용 모델로 추론하며 새 폴더를 자동 재학습하지 않습니다. 현재 정상 고유 영상은 {waferVision.model.normalUnique}종입니다.</p>
          </div>
        </Panel>
      </div>

      <div className="chamber-grid chamber-grid-secondary">
        <Panel title={`${selectedChamber.label} · 시점별 영상 이상 점수`} icon="pulse" right={<span className="chamber-status chamber-status-high"><span />{selectedChamber.firstAnomaly} 최초</span>}>
          <div className="chamber-chart chamber-chart-md">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={trend} margin={{ top: 10, right: 12, bottom: 0, left: -8 }}>
                <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 9.5, fill: "var(--text-3)" }} axisLine={false} tickLine={false} interval={4} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 9.5, fill: "var(--text-3)" }} axisLine={false} tickLine={false} />
                <Tooltip content={<ScoreTooltip />} />
                {(detectorMode === "statistical" || detectorMode === "comparison") && <ReferenceLine y={waferVision.thresholds.statAnomaly} stroke="var(--high)" strokeDasharray="4 4" />}
                {(detectorMode === "ai" || detectorMode === "comparison") && <ReferenceLine y={waferVision.thresholds.aiAnomaly} stroke="var(--accent)" strokeDasharray="4 4" />}
                {(detectorMode === "statistical" || detectorMode === "comparison") && <Line type="stepAfter" dataKey="statScore" name="통계 점수" stroke="var(--high)" strokeWidth={2.2} dot={false} />}
                {(detectorMode === "ai" || detectorMode === "comparison") && <Line type="stepAfter" dataKey="aiScore" name="AI 점수" stroke="var(--accent)" strokeWidth={2.2} dot={false} />}
                <Legend iconType="line" wrapperStyle={{ fontSize: 11 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="vision-selected-strip">
            <span>대표 시점<strong>{detail.sequenceLabel}</strong></span>
            <span>통계 점수<strong className="t-high">{detail.statScore.toFixed(2)}</strong></span>
            <span>AI 점수<strong className="t-accent">{detail.aiScore.toFixed(2)}</strong></span>
            <span>이상 방향<strong>{detail.direction}</strong></span>
          </div>
        </Panel>

        <Panel title="WaferGuard 운영 연결" icon="bot" right={<span className="chip">VISION → AGENT</span>}>
          <div className="vision-evidence-card">
            <div className="vision-evidence-head">
              <span className="mono">{selectedChamber.label} / {detail.sequenceLabel}</span>
              <span className="chamber-status chamber-status-high"><span />검토 필요</span>
            </div>
            <h4>영상 이상 근거를 Inspection Agent에 전달</h4>
            <p>통계·AI 점수, 최초 발생 시점, 방향과 이상 면적을 공정 컨텍스트로 저장합니다. Agent는 RAG 사례를 찾아 대응안을 제안하고 필요하면 MLOps Agent로 위임합니다.</p>
            <div className="vision-evidence-values">
              <span>STAT<strong className="mono">{detail.statScore.toFixed(2)}</strong></span>
              <span>AI<strong className="mono">{detail.aiScore.toFixed(2)}</strong></span>
              <span>AREA<strong className="mono">{detail.statArea.toFixed(2)}%</strong></span>
              <span>DIR<strong>{detail.direction}</strong></span>
            </div>
            <button type="button" className="btn btn-accent" onClick={sendToInspectionAgent} disabled={handoff.status === "sending"}>
              <Icon name={handoff.status === "sending" ? "refresh" : "bot"} size={14} style={handoff.status === "sending" ? { animation: "spin 1s linear infinite" } : undefined} />
              {handoff.status === "sending" ? "전달 중" : "Inspection Agent로 전달"}
            </button>
            {handoff.message && <div className={`vision-handoff-message ${handoff.status}`}>{handoff.message}</div>}
          </div>
        </Panel>
      </div>

      <Panel title={`${selectedChamber.label} · 영상 근거 비교`} icon="zoom" right={<span className="chip">{detail.sequenceLabel} · {detail.direction}</span>}>
        <div className="vision-image-grid">
          <ImageEvidence title="원본 회색조 영상" badge="INPUT" src={detail.assets.original} caption="모델 입력으로 사용한 300×300 샘플 영상" tone="raw" />
          <ImageEvidence title="통계 차이 히트맵" badge={`SCORE ${detail.statScore.toFixed(1)}`} src={detail.assets.statHeatmap} caption={`기준영상 대비 ${detail.polarity} · 평균 ${detail.brightnessDelta >= 0 ? "+" : ""}${detail.brightnessDelta}/255`} tone="stat" />
          <ImageEvidence title="AI 특징 히트맵" badge={`SCORE ${detail.aiScore.toFixed(1)}`} src={detail.assets.aiHeatmap} caption={`정상 특징 분포에서 벗어난 ${detail.direction} 영역`} tone="ai" />
        </div>
      </Panel>

      <div className="chamber-grid chamber-grid-secondary vision-aggregate-grid">
        <Panel title="전체 이상 위치 누적" icon="layers" right={<span className="chip">51 IMAGES</span>}>
          <div className="vision-aggregate-pair">
            <ImageEvidence title="통계 누적 위치" badge="PIXEL DIFF" src={waferVision.assets.statAggregate} caption="픽셀 차이가 반복된 방향을 누적" tone="stat" />
            <ImageEvidence title="AI 누적 위치" badge="FEATURE" src={waferVision.assets.aiAggregate} caption="정상 특징 이탈 위치를 누적" tone="ai" />
          </div>
        </Panel>
        <Panel title="해석 경계" icon="shield" right={<span className="chip">SAMPLE MODEL</span>}>
          <div className="vision-limitations">
            <strong>영상 신호만으로 파티클이나 공정 원인을 확정하지 않습니다.</strong>
            <p>밝기 변화는 촬영 방식, 막 두께, 반사율, 표면 거칠기 등 여러 조건의 영향을 받을 수 있습니다. 현재 3,000장은 고유 정상 1종·불량 3종이 반복된 과제 샘플이므로 새 환경의 일반화 성능을 의미하지 않습니다.</p>
            <div><span>통계 방식</span><small>현재 폴더에서 기준영상·임계값 재계산</small></div>
            <div><span>AI 방식</span><small>저장 모델 추론 · 자동 재학습 없음</small></div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

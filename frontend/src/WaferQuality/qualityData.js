import waferVision from "../data/waferVisionSample.json";

const VISION_BY_WAFER = {
  W13: "Chamber_007",
  W14: "Chamber_047",
  W15: "Chamber_061",
};

function statusFor(sequence) {
  if (sequence === 13 || sequence === 14) return "warning";
  if (sequence === 15) return "critical";
  if (sequence <= 18) return "normal";
  return "not_inspected";
}

function riskFor(sequence, status) {
  if (status === "critical") return 82;
  if (status === "warning") return sequence === 14 ? 61 : 47;
  if (status === "normal") return 10 + ((sequence * 7) % 13);
  return null;
}

function demoInspection(wafer, visionId) {
  const vision = visionId ? waferVision.details[visionId] : null;
  const riskLevel = wafer.status === "critical" ? "High" : wafer.status === "warning" ? "Medium" : "Low";
  const metrology = {
    cd_nm: wafer.cd_nm,
    overlay_nm: wafer.overlay_nm,
    film_thickness_nm: wafer.film_thickness_nm,
    roughness_nm: wafer.roughness_nm,
    defect_count: wafer.defect_count,
    yield_proxy: wafer.yield_proxy,
  };
  return {
    id: null,
    lot_id: "LOT-VISION-DEMO-042",
    wafer_id: wafer.wafer_id,
    line_id: "LINE-DEMO",
    equipment_id: "ETCH-003",
    process_step: "Etch",
    recipe_id: "RCP-ETCH-DEMO-EDGE",
    defect_type: wafer.status === "normal" ? "None" : "Edge-Loc",
    confidence: vision ? 0.86 : 0.76,
    risk_score: (wafer.risk_score || 0) / 100,
    risk_level: riskLevel,
    hotspot_ratio: vision ? vision.statArea / 100 : 0.008,
    image_url: vision?.assets.original || waferVision.assets.reference,
    heatmap_url: vision?.assets.statHeatmap || waferVision.assets.statAggregate,
    overlay_url: vision?.assets.aiHeatmap || waferVision.assets.aiAggregate,
    roi_url: vision?.assets.statHeatmap || waferVision.assets.statAggregate,
    roi_bbox: vision ? [204, 108, 252, 156] : [132, 132, 150, 150],
    model_version: "VISION-SNAPSHOT-DEMO",
    status: "demo_evidence",
    created_at: `2026-08-11T01:${String(wafer.sequence).padStart(2, "0")}:00+00:00`,
    process_context: {
      lot_id: "LOT-VISION-DEMO-042",
      wafer_id: wafer.wafer_id,
      process_step: "Etch",
      tool_id: "ETCH-003",
      recipe_id: "RCP-ETCH-DEMO-EDGE",
      process_timestamp: `2026-08-11T01:${String(Math.max(0, wafer.sequence - 1)).padStart(2, "0")}:00+00:00`,
      inspection_timestamp: `2026-08-11T01:${String(wafer.sequence).padStart(2, "0")}:00+00:00`,
      vision_evidence: vision ? {
        source: "wafer_particle comparison snapshot",
        statistical_score: vision.statScore,
        ai_score: vision.aiScore,
        direction: vision.direction,
        sequence_label: vision.sequenceLabel,
        anomaly_area_ratio: vision.statArea / 100,
      } : null,
      related_process_events: wafer.sequence >= 13 ? [{
        id: `DEMO-PROC-${wafer.sequence}`,
        observed_at: `2026-08-11T01:${String(Math.max(0, wafer.sequence - 3)).padStart(2, "0")}:00+00:00`,
        equipment_id: "ETCH-003",
        event_type: "rf_power_drift",
        severity: "warning",
        source: "demo_profile",
        metadata: { interpretation: "Temporal candidate only" },
      }] : [],
    },
    metrology,
    action_card: { defect_type: wafer.status === "normal" ? "None" : "Edge-Loc", possible_causes: [], next_actions: ["Engineer review"] },
  };
}

export function buildDemoQuality() {
  const wafers = Array.from({ length: 25 }, (_, index) => {
    const sequence = index + 1;
    const wafer_id = `W${String(sequence).padStart(2, "0")}`;
    const status = statusFor(sequence);
    const risk_score = riskFor(sequence, status);
    const visionId = VISION_BY_WAFER[wafer_id] || null;
    const vision = visionId ? waferVision.details[visionId] : null;
    const angle = -0.48 + ((sequence - 13) * 0.08);
    const wafer = {
      wafer_id,
      sequence,
      status,
      risk_score,
      vision_score: vision?.aiScore ?? (status === "not_inspected" ? null : 12 + ((sequence * 3) % 14)),
      defect_count: status === "critical" ? 21 : status === "warning" ? 13 + (sequence % 3) : status === "not_inspected" ? null : 3 + (sequence % 4),
      cd_nm: Number((31.7 + sequence * 0.05).toFixed(2)),
      overlay_nm: Number((3.8 + (sequence >= 13 ? (sequence - 12) * 0.65 : sequence * 0.04)).toFixed(2)),
      film_thickness_nm: Number((88.5 - sequence * 0.08).toFixed(2)),
      roughness_nm: Number((1.1 + sequence * 0.025).toFixed(2)),
      yield_proxy: Number((0.988 - (risk_score || 0) * 0.00045).toFixed(4)),
      equipment_id: "ETCH-003",
      process_step: "Etch",
      recipe_id: "RCP-ETCH-DEMO-EDGE",
      inspection_at: status === "not_inspected" ? null : `2026-08-11T01:${String(sequence).padStart(2, "0")}:00+00:00`,
      defect_type: status === "normal" ? "None" : status === "not_inspected" ? null : "Edge-Loc",
      defect_point: status === "normal" || status === "not_inspected" ? null : { x: Number((0.78 * Math.cos(angle)).toFixed(3)), y: Number((0.78 * Math.sin(angle)).toFixed(3)) },
      vision_chamber_id: visionId,
      data_source: "vision_snapshot_demo",
    };
    wafer.inspection = status === "not_inspected" ? null : demoInspection(wafer, visionId);
    return wafer;
  });
  const inspected = wafers.filter(item => item.status !== "not_inspected");
  const lot = {
    lot_id: "LOT-VISION-DEMO-042",
    inspected: inspected.length,
    wafer_total: 25,
    normal: inspected.filter(item => item.status === "normal").length,
    warning: inspected.filter(item => item.status === "warning").length,
    critical: inspected.filter(item => item.status === "critical").length,
    avg_risk: Number((inspected.reduce((sum, item) => sum + item.risk_score, 0) / inspected.length).toFixed(1)),
    latest_at: inspected.at(-1).inspection_at,
    data_source: "vision_snapshot_demo",
  };
  return { lot, wafers, data_source: "vision_snapshot_demo" };
}

export function expandRuntimeLot(detail) {
  const actual = new Map((detail?.wafers || []).map(item => [item.sequence, item]));
  const total = Math.max(25, detail?.lot?.wafer_total || 25);
  const wafers = Array.from({ length: total }, (_, index) => actual.get(index + 1) || {
    wafer_id: `W${String(index + 1).padStart(2, "0")}`,
    sequence: index + 1,
    status: "not_inspected",
    risk_score: null,
    data_source: "runtime_inspection_db",
  });
  return { ...detail, wafers };
}

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from app.services.handoff import generate_handoff_report, get_latest_handoff_report
from app.services.mlops import simulate_drift
from app.services.pipeline import run_inspection
from app.services.schemas import AutomationTickRequest, DriftRequest, HandoffReportRequest, InspectRequest
from app.services.storage import list_inspections_for_handoff, metrics, utc_now

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Demo scenario script — loaded once at module import
# ---------------------------------------------------------------------------

_DEMO_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_scenario_script.json"

def _load_demo_script() -> list[dict]:
    try:
        with _DEMO_SCRIPT_PATH.open(encoding="utf-8") as f:
            steps = json.load(f)
        logger.info("Loaded %d demo scenario steps from %s", len(steps), _DEMO_SCRIPT_PATH)
        return steps
    except Exception as exc:
        logger.warning("Could not load demo_scenario_script.json: %s — using SCENARIOS fallback", exc)
        return []

_DEMO_SCRIPT: list[dict] = _load_demo_script()
_scenario_index: int = 0  # module-level sequential counter (wraps around)


def _next_scenario() -> dict:
    """Return the next scenario step from the demo script, or fall back to random SCENARIOS."""
    global _scenario_index  # noqa: PLW0603
    if _DEMO_SCRIPT:
        step = _DEMO_SCRIPT[_scenario_index % len(_DEMO_SCRIPT)]
        _scenario_index += 1
        return step
    return random.choice(SCENARIOS)


SCENARIOS = [
    {
        "defect_hint": "Scratch",
        "equipment_id": "ETCH-02",
        "process_step": "Etch",
        "recipe_id": "RCP-ETCH-EDGE-02",
        "cd_nm": 35.8,
        "overlay_nm": 5.9,
        "roughness_nm": 3.4,
        "defect_count": 520,
        "yield_proxy": 0.956,
    },
    {
        "defect_hint": "Edge-Ring",
        "equipment_id": "CMP-01",
        "process_step": "CMP",
        "recipe_id": "RCP-CMP-EDGE-01",
        "cd_nm": 32.2,
        "overlay_nm": 4.6,
        "roughness_nm": 2.6,
        "defect_count": 440,
        "yield_proxy": 0.968,
    },
    {
        "defect_hint": "Near-full",
        "equipment_id": "ETCH-02",
        "process_step": "Etch",
        "recipe_id": "RCP-ETCH-BULK-09",
        "cd_nm": 39.4,
        "overlay_nm": 7.3,
        "roughness_nm": 4.1,
        "defect_count": 780,
        "yield_proxy": 0.931,
    },
    {
        "defect_hint": "Center",
        "equipment_id": "LITHO-03",
        "process_step": "Lithography",
        "recipe_id": "RCP-LITHO-FE-11",
        "cd_nm": 31.4,
        "overlay_nm": 3.7,
        "roughness_nm": 1.1,
        "defect_count": 160,
        "yield_proxy": 0.981,
    },
    {
        "defect_hint": "Donut",
        "equipment_id": "CVD-04",
        "process_step": "Deposition",
        "recipe_id": "RCP-CVD-THK-07",
        "cd_nm": 33.1,
        "overlay_nm": 3.9,
        "roughness_nm": 1.8,
        "defect_count": 240,
        "yield_proxy": 0.974,
    },
    {
        "defect_hint": "Random",
        "equipment_id": "CLEAN-01",
        "process_step": "Cleaning",
        "recipe_id": "RCP-CLN-PARTICLE-03",
        "cd_nm": 31.9,
        "overlay_nm": 3.5,
        "roughness_nm": 1.5,
        "defect_count": 220,
        "yield_proxy": 0.977,
    },
]


def automation_status(line_id: str = "LINE-7") -> dict[str, object]:
    data = metrics()
    rows = list_inspections_for_handoff(line_id, limit=12)
    handoff = get_latest_handoff_report()
    open_items = [
        row
        for row in rows
        if row["risk_level"] == "High" or row["status"] == "review_required"
    ]
    return {
        "mode": "server_tick_ready",
        "line_id": line_id,
        "last_checked_at": utc_now(),
        "tick_endpoint": "/api/v1/automation/tick",
        "deployment_hook": "server cron 또는 외부 scheduler가 tick endpoint를 주기 호출",
        "latest_inspection": rows[0] if rows else None,
        "open_review_count": len(open_items),
        "latest_handoff": handoff,
        "latest_drift_event": data["latest_drift_event"],
        "recent_alerts": data["recent_alerts"],
        "total_inspections": data["total_inspections"],
    }


def run_automation_tick(request: AutomationTickRequest) -> dict[str, object]:
    scenario = _next_scenario()
    stamp = datetime.now(timezone.utc).strftime("%m%d%H%M%S%f")
    wafer_id = f"WF-AUTO-{stamp[-10:]}"
    lot_id = f"LOT-AUTO-{stamp[:6]}"

    inspection = run_inspection(
        InspectRequest(
            lot_id=lot_id,
            wafer_id=wafer_id,
            line_id=request.line_id,
            equipment_id=str(scenario["equipment_id"]),
            process_step=scenario["process_step"],
            recipe_id=str(scenario["recipe_id"]),
            # SCENARIOS use "defect_hint"; demo_scenario_script.json uses
            # "defect_type" — accept either so the tick works with both sources.
            defect_hint=str(scenario.get("defect_hint") or scenario.get("defect_type") or "Random"),
            cd_nm=float(scenario["cd_nm"]),
            overlay_nm=float(scenario["overlay_nm"]),
            film_thickness_nm=88.0 + random.uniform(-5.0, 8.0),
            roughness_nm=float(scenario["roughness_nm"]),
            defect_count=int(scenario["defect_count"]),
            yield_proxy=float(scenario["yield_proxy"]),
            operator_note="자동 감시 tick에서 생성된 공정 이벤트",
        )
    )

    events: list[dict[str, object]] = [
        {
            "type": "inspection_ingested",
            "severity": "info",
            "message": f"{inspection['wafer_id']} {inspection['defect_type']} 검사 이벤트 수집",
        },
        {
            "type": "agent_decision",
            "severity": inspection["risk_level"].lower(),
            "message": f"Agent 판단: {inspection['risk_level']} risk / {inspection['status']}",
        },
    ]

    drift_event = None
    if request.drift_check and (inspection["risk_level"] == "High" or random.random() < 0.25):
        intensity = "strong" if inspection["risk_level"] == "High" else "mild"
        drift_event = simulate_drift(DriftRequest(intensity=intensity, line_id=request.line_id))
        events.append(
            {
                "type": "drift_check",
                "severity": drift_event["status"],
                "message": f"Drift {drift_event['status']} / score {drift_event['drift_score']}",
            }
        )

    handoff_report = None
    needs_handoff = (
        request.auto_handoff
        and (
            inspection["risk_level"] == "High"
            or inspection["status"] == "review_required"
            or bool(drift_event and drift_event["status"] == "detected")
        )
    )
    if needs_handoff:
        handoff_report = generate_handoff_report(
            HandoffReportRequest(
                shift_from=request.shift_from,
                shift_to=request.shift_to,
                line_id=request.line_id,
                operator=request.operator,
                scheduled_for="auto-monitor",
                reuse_existing=False,
                note=f"자동 감시가 {inspection['equipment_id']} {inspection['defect_type']} 이벤트를 인수인계 대상으로 등록",
            )
        )
        events.append(
            {
                "type": "handoff_draft",
                "severity": handoff_report["scrap_risk"].lower(),
                "message": f"Daily Report 초안 생성: Scrap Risk {handoff_report['scrap_risk']}",
            }
        )

    # The server tick may train a reviewable Chamber candidate, but deliberately
    # never promotes it. Promotion remains an explicit human action/API call.
    from app.services.chamber_runtime import chamber_runtime  # noqa: PLC0415

    chamber_retraining = chamber_runtime.maybe_auto_retrain()

    return {
        "tick_id": f"AUTO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "mode": "automation_tick",
        "created_at": utc_now(),
        "inspection": inspection,
        "drift_event": drift_event,
        "handoff_report": handoff_report,
        "chamber_retraining": chamber_retraining,
        "events": events,
        "status": automation_status(request.line_id),
    }

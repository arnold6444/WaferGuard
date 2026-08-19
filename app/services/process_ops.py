"""Fab/quality read models and the process-agnostic event projection layer.

The source Chamber and inspection tables stay authoritative.  This module only
normalizes their data for the redesigned UI and clearly marks demo/proxy values.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import chamber_storage, storage


PROCESS_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "process_id": "oxidation",
        "display_name": "Oxidation",
        "equipment_type": "Furnace",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "temperature", "label": "Temperature", "unit": "°C*", "normal_range": "Demo profile", "display_order": 1},
            {"id": "o2_flow", "label": "O₂ Flow", "unit": "sccm*", "normal_range": "Demo profile", "display_order": 2},
            {"id": "oxide_thickness", "label": "Oxide Thickness", "unit": "nm*", "normal_range": "Proxy metric", "display_order": 3},
        ],
    },
    {
        "process_id": "photo",
        "display_name": "Photo",
        "equipment_type": "Scanner / Track",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "exposure_dose", "label": "Exposure Dose", "unit": "mJ/cm²*", "normal_range": "Demo profile", "display_order": 1},
            {"id": "focus", "label": "Focus", "unit": "µm*", "normal_range": "Demo profile", "display_order": 2},
            {"id": "track_temperature", "label": "Track Temperature", "unit": "°C*", "normal_range": "Demo profile", "display_order": 3},
            {"id": "overlay", "label": "Overlay", "unit": "nm*", "normal_range": "Sample threshold", "display_order": 4},
        ],
    },
    {
        "process_id": "etch",
        "display_name": "Etch",
        "equipment_type": "Plasma Etcher",
        "connection": "runtime",
        "data_source": "synthetic_runtime",
        "parameters": [
            {"id": "chamber_pressure", "label": "Pressure", "unit": "Torr*", "normal_range": "Recipe setpoint ± synthetic drift", "display_order": 1},
            {"id": "source_rf_power", "label": "Source RF", "unit": "W*", "normal_range": "Recipe setpoint ± synthetic drift", "display_order": 2},
            {"id": "bias_rf_power", "label": "Bias RF", "unit": "W*", "normal_range": "Recipe setpoint ± synthetic drift", "display_order": 3},
            {"id": "total_gas_flow", "label": "Gas Flow", "unit": "sccm*", "normal_range": "Recipe setpoint ± synthetic drift", "display_order": 4},
            {"id": "chamber_temperature", "label": "Temperature", "unit": "°C*", "normal_range": "Recipe setpoint ± synthetic drift", "display_order": 5},
            {"id": "resistance", "label": "Resistance", "unit": "Ω", "normal_range": "Model residual threshold", "display_order": 6},
        ],
    },
    {
        "process_id": "deposition",
        "display_name": "Deposition",
        "equipment_type": "CVD / PVD (Thin Film)",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "pressure", "label": "Pressure", "unit": "Torr*", "normal_range": "Demo profile", "display_order": 1},
            {"id": "temperature", "label": "Temperature", "unit": "°C*", "normal_range": "Demo profile", "display_order": 2},
            {"id": "gas_flow", "label": "Gas Flow", "unit": "sccm*", "normal_range": "Demo profile", "display_order": 3},
            {"id": "film_thickness", "label": "Film Thickness", "unit": "nm*", "normal_range": "Proxy metric", "display_order": 4},
        ],
    },
    {
        "process_id": "implant",
        "display_name": "Implant",
        "equipment_type": "Ion Implanter",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "beam_current", "label": "Beam Current", "unit": "mA*", "normal_range": "Demo profile", "display_order": 1},
            {"id": "dose", "label": "Dose", "unit": "ions/cm²*", "normal_range": "Demo profile", "display_order": 2},
            {"id": "energy", "label": "Energy", "unit": "keV*", "normal_range": "Demo profile", "display_order": 3},
        ],
    },
    {
        "process_id": "metal",
        "display_name": "Metal",
        "equipment_type": "Metal Deposition",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "vacuum", "label": "Vacuum", "unit": "Torr*", "normal_range": "Demo profile", "display_order": 1},
            {"id": "power", "label": "Power", "unit": "W*", "normal_range": "Demo profile", "display_order": 2},
            {"id": "sheet_resistance", "label": "Sheet Resistance", "unit": "Ω/sq*", "normal_range": "Proxy metric", "display_order": 3},
        ],
    },
    {
        "process_id": "cmp",
        "display_name": "CMP",
        "equipment_type": "Polisher (C&C)",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "down_force", "label": "Down Force", "unit": "psi*", "normal_range": "Demo profile", "display_order": 1},
            {"id": "platen_speed", "label": "Platen Speed", "unit": "rpm*", "normal_range": "Demo profile", "display_order": 2},
            {"id": "slurry_flow", "label": "Slurry Flow", "unit": "ml/min*", "normal_range": "Demo profile", "display_order": 3},
            {"id": "removal_rate", "label": "Removal Rate", "unit": "nm/min*", "normal_range": "Proxy metric", "display_order": 4},
        ],
    },
    {
        "process_id": "cleaning",
        "display_name": "Cleaning",
        "equipment_type": "Single-wafer Wet Cleaner (C&C)",
        "connection": "not_connected",
        "data_source": "demo_profile",
        "parameters": [
            {"id": "chemical_concentration_percent", "label": "Chemical Concentration", "unit": "%*", "normal_range": "Synthetic recipe context", "display_order": 1},
            {"id": "chemical_flow", "label": "Chemical Flow", "unit": "mL/min*", "normal_range": "Synthetic recipe context", "display_order": 2},
            {"id": "di_water_resistivity", "label": "DI Water Resistivity", "unit": "MΩ·cm*", "normal_range": "Synthetic water-quality proxy", "display_order": 3},
            {"id": "drain_particle_count", "label": "Drain Particle Count", "unit": "count/mL*", "normal_range": "Synthetic removal indicator", "display_order": 4},
        ],
    },
    {
        "process_id": "inspection",
        "display_name": "Inspection",
        "equipment_type": "Wafer Inspection",
        "connection": "runtime",
        "data_source": "proxy_runtime",
        "parameters": [
            {"id": "risk_score", "label": "Risk Score", "unit": "%", "normal_range": "Demo rule score", "display_order": 1},
            {"id": "vision_score", "label": "Vision Score", "unit": "score*", "normal_range": "Snapshot evidence", "display_order": 2},
            {"id": "defect_count", "label": "Defect Count", "unit": "count", "normal_range": "Proxy/sample", "display_order": 3},
            {"id": "overlay", "label": "Overlay", "unit": "nm*", "normal_range": "Sample threshold", "display_order": 4},
        ],
    },
)


def process_profiles() -> list[dict[str, Any]]:
    return [{**profile, "parameters": [dict(item) for item in profile["parameters"]]} for profile in PROCESS_PROFILES]


def equipment_aliases(equipment_id: str | None) -> list[str]:
    """Return safe numeric aliases for demo identifiers such as ETCH-02/ETCH-002.

    `Chamber_047` originates from the snapshot and may temporally compare with
    `ETCH-047`; the UI still labels this as a proxy alias rather than identity.
    """
    if not equipment_id:
        return []
    aliases = {equipment_id}
    match = re.search(r"(\d+)$", equipment_id)
    if match:
        number = int(match.group(1))
        aliases.update({f"ETCH-{number}", f"ETCH-{number:02d}", f"ETCH-{number:03d}"})
    return sorted(aliases)


def _as_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def project_chamber_anomaly(
    telemetry: dict[str, Any],
    prediction: dict[str, Any],
    *,
    detections: list[dict[str, Any]] | None = None,
) -> dict[str, object] | None:
    detector_rows = detections or []
    flagged_detectors = [item for item in detector_rows if bool(item.get("is_anomaly"))]
    if not bool(prediction.get("is_anomaly")) and not flagged_detectors:
        return None
    detector_ratios = [
        float(item.get("score") or 0.0) / max(float(item.get("threshold") or 0.0), 1e-9)
        for item in detector_rows
    ]
    score = max([float(prediction.get("anomaly_score") or 0.0), *detector_ratios])
    only_ewma = bool(flagged_detectors) and not bool(prediction.get("is_anomaly"))
    return storage.insert_process_event(
        {
            "id": f"PROC-{prediction['id']}",
            "process_step": "Etch",
            "equipment_id": telemetry["equipment_id"],
            "recipe_id": telemetry.get("recipe_id"),
            "lot_id": telemetry.get("lot_id"),
            "wafer_id": telemetry.get("wafer_id"),
            "observed_at": prediction["observed_at"],
            "event_type": telemetry.get("synthetic_anomaly_type") or (
                "residual_ewma" if only_ewma else "resistance_residual"
            ),
            "severity": "critical" if score >= 2.0 else "warning",
            "source": "chamber_synthetic_runtime",
            "metadata": {
                "actual_resistance": prediction.get("actual_resistance"),
                "expected_resistance": prediction.get("expected_resistance"),
                "residual": prediction.get("residual"),
                "anomaly_score": prediction.get("anomaly_score"),
                "threshold": prediction.get("threshold"),
                "model_version": prediction.get("model_version"),
                "detectors": [
                    {
                        "name": item.get("detector_name"),
                        "score": item.get("score"),
                        "threshold": item.get("threshold"),
                        "is_anomaly": bool(item.get("is_anomaly")),
                        "context_key": item.get("context_key"),
                    }
                    for item in detector_rows
                ],
                "flagged_detectors": [item.get("detector_name") for item in flagged_detectors],
                "synthetic": True,
                "interpretation": "Temporal signal candidate; not a causal claim.",
            },
        }
    )


def related_process_events(
    equipment_id: str | None,
    inspection_at: str | datetime | None,
    *,
    lot_id: str | None = None,
    minutes: int = 30,
) -> list[dict[str, object]]:
    end = _as_datetime(inspection_at)
    start = end - timedelta(minutes=max(1, min(int(minutes), 1440)))
    return storage.list_process_events(
        lot_id=lot_id,
        equipment_ids=equipment_aliases(equipment_id),
        observed_after=start.isoformat(timespec="seconds"),
        observed_before=end.isoformat(timespec="seconds"),
        limit=50,
    )


def _risk_status(level: str | None) -> str:
    return {"High": "critical", "Medium": "warning", "Low": "normal"}.get(str(level), "normal")


def _wafer_index(wafer_id: str | None) -> int:
    match = re.search(r"(\d+)(?!.*\d)", str(wafer_id or ""))
    return int(match.group(1)) if match else 0


def quality_lots(limit: int = 500) -> dict[str, object]:
    inspections = storage.list_inspections(limit=max(1, min(int(limit), 2000)))
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for item in inspections:
        lot_id = str(item.get("lot_id") or "LOT-UNASSIGNED")
        wafer_id = str(item.get("wafer_id") or "W00")
        grouped.setdefault(lot_id, {})
        grouped[lot_id].setdefault(wafer_id, item)

    lot_records = {str(item["lot_id"]): item for item in storage.list_lots(limit=limit)}
    lot_ids = set(grouped) | set(lot_records)
    lots: list[dict[str, object]] = []
    for lot_id in lot_ids:
        wafer_map = grouped.get(lot_id, {})
        wafers = list(wafer_map.values())
        lot_record = lot_records.get(lot_id, {})
        status_counts = {"normal": 0, "warning": 0, "critical": 0}
        for item in wafers:
            status_counts[_risk_status(str(item.get("risk_level")))] += 1
        avg_risk = sum(float(item.get("risk_score") or 0.0) for item in wafers) / max(1, len(wafers))
        process_alerts = len(
            [
                event
                for event in storage.list_process_events(lot_id=lot_id, limit=500)
                if str(event.get("severity")) in {"warning", "critical"}
            ]
        )
        latest_inspection = max((str(item.get("created_at") or "") for item in wafers), default="")
        lots.append(
            {
                "lot_id": lot_id,
                "inspected": len(wafers),
                "wafer_total": max(int(lot_record.get("wafer_count") or 25), max((_wafer_index(str(item.get("wafer_id"))) for item in wafers), default=0)),
                "normal": status_counts["normal"],
                "warning": status_counts["warning"],
                "critical": status_counts["critical"],
                "avg_risk": round(avg_risk * 100, 1),
                "process_alerts": process_alerts,
                "status": lot_record.get("status") or "inspection_only",
                "product_id": lot_record.get("product_id"),
                "recipe_route": lot_record.get("recipe_route"),
                "current_process_step": lot_record.get("current_process_step"),
                "started_at": lot_record.get("started_at"),
                "completed_at": lot_record.get("completed_at"),
                "latest_at": latest_inspection or str(lot_record.get("updated_at") or lot_record.get("started_at") or ""),
                "data_source": "runtime_lot_db" if lot_record else "runtime_inspection_db",
            }
        )
    lots.sort(key=lambda item: str(item["latest_at"]), reverse=True)
    return {"items": lots, "data_source": "runtime_inspection_db" if lots else "empty"}


def _defect_point(item: dict[str, object]) -> dict[str, float] | None:
    bbox = item.get("roi_bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    # Generated inspection images are square. Normalizing by the larger observed
    # coordinate keeps the point useful without pretending to be die-level truth.
    scale = max(1.0, x1, y1, x2, y2)
    return {"x": round((((x1 + x2) / 2) / scale) * 2 - 1, 4), "y": round((((y1 + y2) / 2) / scale) * 2 - 1, 4)}


def _quality_wafer(item: dict[str, object]) -> dict[str, object]:
    process_context = item.get("process_context") if isinstance(item.get("process_context"), dict) else {}
    metrology = item.get("metrology") if isinstance(item.get("metrology"), dict) else {}
    vision = process_context.get("vision_evidence") if isinstance(process_context.get("vision_evidence"), dict) else {}
    return {
        "wafer_id": item.get("wafer_id"),
        "sequence": _wafer_index(str(item.get("wafer_id"))),
        "status": _risk_status(str(item.get("risk_level"))),
        "risk_score": round(float(item.get("risk_score") or 0.0) * 100, 1),
        "vision_score": vision.get("ai_score"),
        "defect_count": metrology.get("defect_count"),
        "cd_nm": metrology.get("cd_nm"),
        "overlay_nm": metrology.get("overlay_nm"),
        "film_thickness_nm": metrology.get("film_thickness_nm"),
        "roughness_nm": metrology.get("roughness_nm"),
        "yield_proxy": metrology.get("yield_proxy"),
        "equipment_id": item.get("equipment_id"),
        "process_step": item.get("process_step"),
        "recipe_id": item.get("recipe_id"),
        "inspection_at": item.get("created_at"),
        "defect_type": item.get("defect_type"),
        "defect_point": _defect_point(item),
        "inspection": item,
        "data_source": "runtime_inspection_db",
    }


def quality_lot(lot_id: str, limit: int = 2000) -> dict[str, object] | None:
    inspections = [item for item in storage.list_inspections(limit=limit) if str(item.get("lot_id") or "LOT-UNASSIGNED") == lot_id]
    latest: dict[str, dict[str, object]] = {}
    for item in inspections:
        latest.setdefault(str(item.get("wafer_id") or "W00"), item)
    lot_record = storage.get_lot(lot_id)
    if not latest and lot_record is None:
        return None
    wafers = sorted((_quality_wafer(item) for item in latest.values()), key=lambda item: (int(item["sequence"]), str(item["wafer_id"])))
    summary = next((item for item in quality_lots(limit=limit)["items"] if item["lot_id"] == lot_id), None)
    return {"lot": summary, "wafers": wafers, "data_source": "runtime_lot_db" if lot_record else "runtime_inspection_db"}


def inspection_operating_context(
    *,
    lot_id: str,
    wafer_id: str,
    equipment_id: str,
    inspection_at: str,
    recipe_id: str | None = None,
) -> dict[str, object]:
    inspection_time = _as_datetime(inspection_at)
    history = [
        item
        for item in storage.list_inspections(limit=2000)
        if _as_datetime(str(item.get("created_at") or inspection_at)) <= inspection_time
    ]
    prior = [
        item
        for item in history
        if str(item.get("lot_id")) == lot_id and str(item.get("wafer_id")) != wafer_id
    ]
    prior.sort(key=lambda item: _wafer_index(str(item.get("wafer_id"))), reverse=True)
    neighbors = [
        {
            "wafer_id": item.get("wafer_id"),
            "risk_level": item.get("risk_level"),
            "risk_score": item.get("risk_score"),
            "defect_type": item.get("defect_type"),
            "equipment_id": item.get("equipment_id"),
            "inspection_at": item.get("created_at"),
        }
        for item in prior[:5]
    ]
    repeated = len([item for item in neighbors if item.get("equipment_id") == equipment_id])
    equipment_history = [item for item in history if str(item.get("equipment_id")) == equipment_id]
    resolved_recipe_id = recipe_id or next(
        (str(item.get("recipe_id")) for item in prior if item.get("recipe_id")), None
    )
    recipe_history = [
        item for item in history if resolved_recipe_id and str(item.get("recipe_id")) == resolved_recipe_id
    ]
    defect_counts: dict[str, int] = {}
    for item in prior:
        defect = str(item.get("defect_type") or "Unknown")
        defect_counts[defect] = defect_counts.get(defect, 0) + 1
    lot_record = storage.get_lot(lot_id)
    return {
        "related_process_events": related_process_events(equipment_id, inspection_at, lot_id=lot_id, minutes=30),
        "equipment_history": {
            "inspection_count": len(equipment_history),
            "high_risk_count": len([item for item in equipment_history if item.get("risk_level") == "High"]),
            "recent_defects": [str(item.get("defect_type") or "Unknown") for item in equipment_history[:5]],
        },
        "recipe_context": {
            "recipe_id": resolved_recipe_id,
            "inspection_count": len(recipe_history),
            "high_risk_count": len([item for item in recipe_history if item.get("risk_level") == "High"]),
        },
        "lot_context": {
            "neighboring_wafers": neighbors,
            "same_equipment_recent_count": repeated,
            "accumulated_defect_pattern": defect_counts,
            "lot_record": lot_record,
            "interpretation": "Runtime history only; missing rows do not imply normal production.",
        },
    }


_DEMO_PROCESS_STATUS = {
    "oxidation": "normal",
    "photo": "normal",
    "deposition": "normal",
    "implant": "normal",
    "metal": "warning",
    "cmp": "normal",
    "cleaning": "normal",
}


def fab_overview() -> dict[str, object]:
    equipment = chamber_storage.equipment_summary()
    inspections = storage.list_inspections(limit=200)
    events = storage.list_process_events(limit=20)

    if equipment:
        etch_status = "critical" if any(str(item.get("machine_state")) == "alarm" for item in equipment) else (
            "warning" if any(bool(item.get("is_anomaly")) for item in equipment) else "normal"
        )
        etch_source = "synthetic_runtime"
    else:
        etch_status, etch_source = "warning", "demo_profile"

    if inspections:
        levels = {str(item.get("risk_level")) for item in inspections[:25]}
        inspection_status = "critical" if "High" in levels else ("warning" if "Medium" in levels else "normal")
        inspection_source = "proxy_runtime"
    else:
        inspection_status, inspection_source = "critical", "demo_profile"

    process_status = []
    for profile in PROCESS_PROFILES:
        process_id = str(profile["process_id"])
        if process_id == "etch":
            status, source = etch_status, etch_source
        elif process_id == "inspection":
            status, source = inspection_status, inspection_source
        else:
            status, source = _DEMO_PROCESS_STATUS[process_id], "demo_profile"
        process_status.append(
            {
                "process_id": process_id,
                "display_name": profile["display_name"],
                "status": status,
                "data_source": source,
                "connection": profile["connection"],
            }
        )

    active_lots = storage.list_lots(status="running", limit=500)
    inspection_lots = {str(item.get("lot_id")) for item in inspections if item.get("lot_id")}
    running = len([item for item in equipment if str(item.get("machine_state")) == "running"])
    alerts: list[dict[str, object]] = []
    for event in events[:8]:
        alerts.append(
            {
                "id": event.get("id"),
                "observed_at": event.get("observed_at"),
                "entity_id": event.get("equipment_id"),
                "label": str(event.get("event_type") or "process anomaly").replace("_", " "),
                "severity": event.get("severity"),
                "process_id": str(event.get("process_step") or "").lower(),
                "data_source": event.get("source"),
            }
        )
    for item in inspections[:8]:
        if str(item.get("risk_level")) not in {"High", "Medium"}:
            continue
        alerts.append(
            {
                "id": item.get("id"),
                "observed_at": item.get("created_at"),
                "entity_id": item.get("wafer_id"),
                "label": f"{item.get('defect_type')} defect candidate",
                "severity": "critical" if item.get("risk_level") == "High" else "warning",
                "process_id": "inspection",
                "data_source": "proxy_runtime",
            }
        )
    if not alerts:
        alerts = [
            {"id": "DEMO-ETCH-003", "observed_at": None, "entity_id": "ETCH-003", "label": "RF drift candidate", "severity": "warning", "process_id": "etch", "data_source": "demo_profile"},
            {"id": "DEMO-WAFER-014", "observed_at": None, "entity_id": "WAFER-014", "label": "Edge defect candidate", "severity": "critical", "process_id": "inspection", "data_source": "demo_profile"},
            {"id": "DEMO-METAL-002", "observed_at": None, "entity_id": "METAL-002", "label": "Pressure warning", "severity": "warning", "process_id": "metal", "data_source": "demo_profile"},
        ]
    alerts.sort(key=lambda item: str(item.get("observed_at") or ""), reverse=True)

    warning_count = len([item for item in process_status if item["status"] == "warning"])
    critical_count = len([item for item in process_status if item["status"] == "critical"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": {
            "active_lots": {
                "value": len(active_lots) if active_lots else (len(inspection_lots) if inspection_lots else 12),
                "data_source": "runtime_lot_db" if active_lots else ("runtime_inspection_db" if inspection_lots else "demo_profile"),
            },
            "running_equipment": {"value": running if equipment else 38, "data_source": "synthetic_runtime" if equipment else "demo_profile"},
            "warnings": {"value": warning_count, "data_source": "hybrid"},
            "critical": {"value": critical_count, "data_source": "hybrid"},
        },
        "process_status": process_status,
        "recent_alerts": alerts[:10],
        "wafer_quality": {
            "status": inspection_status,
            "latest_wafer": inspections[0].get("wafer_id") if inspections else "WAFER-014",
            "data_source": inspection_source,
        },
        "disclaimer": "Runtime values are synthetic/proxy. Demo profiles are not connected to Fab equipment.",
    }

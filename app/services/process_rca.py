"""PostgreSQL-backed RCA bridge for generic process multimodal events.

This path intentionally queries persisted `process_events` instead of trusting
in-memory generator state. It enriches the existing Inspection Agent evidence
with process-specific anomaly scores and related tag candidates.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import object_store, storage


def _as_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_process_rca_evidence(
    *,
    process_step: str,
    equipment_id: str,
    lot_id: str | None = None,
    wafer_id: str | None = None,
    observed_at: str | datetime | None = None,
    minutes: int = 30,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Build Agent evidence from persisted process events in an exact time window."""
    end = _as_datetime(observed_at)
    start = end - timedelta(minutes=max(1, min(int(minutes), 1440)))
    events = storage.list_process_events(
        process_step=process_step,
        equipment_ids=[equipment_id],
        lot_id=lot_id,
        wafer_id=wafer_id,
        observed_after=start.isoformat(timespec="seconds"),
        observed_before=end.isoformat(timespec="seconds"),
        limit=50,
    )

    related_tags: list[str] = []
    image_urls: list[str] = []
    enriched_events: list[dict[str, Any]] = []
    for event in events:
        metadata = dict(event.get("metadata") or {})
        tags = [str(tag) for tag in metadata.get("related_tags") or []]
        for tag in tags:
            if tag not in related_tags:
                related_tags.append(tag)
        image_key = metadata.get("image_key")
        if image_key:
            url = object_store.presign(str(image_key))
            if url not in image_urls:
                image_urls.append(url)
        score = metadata.get("anomaly_score")
        threshold = metadata.get("threshold")
        metadata["residual"] = (
            f"anomaly_score={score}, threshold={threshold}, related_tags={tags or '-'}"
        )
        enriched_events.append(
            {
                **event,
                "event_type": (
                    f"{event.get('event_type', 'process_anomaly')} | related tags: "
                    f"{', '.join(tags) if tags else '-'}"
                ),
                "metadata": metadata,
            }
        )

    latest = enriched_events[0] if enriched_events else None
    latest_metadata = dict(latest.get("metadata") or {}) if latest else {}
    injected = latest_metadata.get("injected_anomaly") if latest else None
    modality = latest_metadata.get("modality") if latest else None
    critical = any(str(event.get("severity")) == "critical" for event in enriched_events)
    warning = any(str(event.get("severity")) == "warning" for event in enriched_events)
    risk_level = "High" if critical else ("Medium" if warning else "Low")
    risk_score = 0.85 if critical else (0.65 if warning else 0.20)

    return {
        "inspection_id": f"PROCESS-RCA-{equipment_id}-{int(end.timestamp())}",
        "defect_type": str(injected or latest.get("event_type") if latest else "process_anomaly"),
        "equipment_id": equipment_id,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "confidence": 0.80 if enriched_events else 0.40,
        "metrology_rule_hits": [],
        "rag_cases": [],
        "process_context": {
            "lot_id": lot_id or "-",
            "wafer_id": wafer_id or "-",
            "process_step": process_step,
            "tool_id": equipment_id,
            "recipe_id": latest.get("recipe_id") if latest else None,
            "inspection_timestamp": end.isoformat(timespec="seconds"),
            "related_process_events": enriched_events,
            "multimodal_rca": {
                "event_count": len(enriched_events),
                "modalities": sorted(
                    {
                        str(event.get("metadata", {}).get("modality"))
                        for event in enriched_events
                        if event.get("metadata", {}).get("modality")
                    }
                ),
                "related_tags": related_tags,
                "latest_modality": modality,
                "source": "persisted_process_events",
                "interpretation": "Candidates only; temporal association does not establish root cause.",
            },
        },
        "metrology": {},
        "image_urls": image_urls[:3],
        "use_llm": use_llm,
    }


def run_process_rca(**kwargs: Any) -> dict[str, Any]:
    """Run the existing LangGraph Inspection Agent on persisted process evidence."""
    from app.services.agent import run as agent_run  # lazy to avoid startup coupling

    evidence = build_process_rca_evidence(**kwargs)
    result = agent_run(evidence)
    return {
        "evidence_summary": {
            "inspection_id": evidence["inspection_id"],
            "risk_level": evidence["risk_level"],
            "event_count": evidence["process_context"]["multimodal_rca"]["event_count"],
            "modalities": evidence["process_context"]["multimodal_rca"]["modalities"],
            "related_tags": evidence["process_context"]["multimodal_rca"]["related_tags"],
            "source": "PostgreSQL/process_events",
        },
        **result,
    }

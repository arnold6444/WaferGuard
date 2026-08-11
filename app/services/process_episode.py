"""Read-model helpers that group point process events into anomaly episodes.

Authoritative process_events remain immutable point evidence. Operators and
agents generally care about one sustained incident, so this module groups
contiguous warning/critical events without throwing away the source rows.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.services import storage


_ANOMALY_SOURCES = {"chamber_synthetic_runtime"}
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _instant(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _episode_id(key: tuple[str, ...], started_at: str) -> str:
    digest = hashlib.sha1(("|".join(key) + "|" + started_at).encode()).hexdigest()[:12].upper()
    return f"EP-{digest}"


def build_anomaly_episodes(
    events: list[dict[str, Any]] | None = None,
    *,
    lot_id: str | None = None,
    equipment_id: str | None = None,
    gap_seconds: float = 5.0,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Group contiguous anomaly points by tool/lot/event type.

    ``gap_seconds`` describes the maximum gap between two point events that are
    still considered the same sustained incident. Lifecycle and data-quality
    events are intentionally excluded; those already have their own semantics.
    """
    if events is None:
        events = storage.list_process_events(
            lot_id=lot_id,
            equipment_ids=[equipment_id] if equipment_id else None,
            limit=max(1, min(int(limit), 5000)),
        )

    candidates = [
        event
        for event in events
        if str(event.get("severity")) in {"warning", "critical"}
        and str(event.get("source")) in _ANOMALY_SOURCES
        and str(event.get("event_type")) not in {"data_quality", "lot_started", "wafer_completed", "lot_completed"}
    ]
    candidates.sort(key=lambda event: _instant(event.get("observed_at")))

    episodes: list[dict[str, Any]] = []
    active: dict[tuple[str, ...], dict[str, Any]] = {}
    max_gap = max(0.0, float(gap_seconds))

    for event in candidates:
        key = (
            str(event.get("process_step") or ""),
            str(event.get("equipment_id") or ""),
            str(event.get("lot_id") or ""),
            str(event.get("event_type") or ""),
            str(event.get("source") or ""),
        )
        observed = _instant(event.get("observed_at"))
        current = active.get(key)
        if current is None or (observed - current["_last_dt"]).total_seconds() > max_gap:
            if current is not None:
                episodes.append(_finalize(current))
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            current = {
                "episode_id": _episode_id(key, observed.isoformat(timespec="seconds")),
                "process_step": key[0],
                "equipment_id": key[1],
                "lot_id": event.get("lot_id"),
                "wafer_ids": [],
                "event_type": key[3],
                "source": key[4],
                "severity": str(event.get("severity") or "warning"),
                "started_at": observed.isoformat(timespec="seconds"),
                "ended_at": observed.isoformat(timespec="seconds"),
                "sample_count": 0,
                "max_anomaly_score": 0.0,
                "event_ids": [],
                "_last_dt": observed,
            }
            active[key] = current
        current["_last_dt"] = observed
        current["ended_at"] = observed.isoformat(timespec="seconds")
        current["sample_count"] += 1
        current["event_ids"].append(event.get("id"))
        wafer_id = event.get("wafer_id")
        if wafer_id and wafer_id not in current["wafer_ids"]:
            current["wafer_ids"].append(wafer_id)
        if _SEVERITY_RANK.get(str(event.get("severity")), 0) > _SEVERITY_RANK.get(str(current["severity"]), 0):
            current["severity"] = str(event.get("severity"))
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        try:
            score = float(metadata.get("anomaly_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        current["max_anomaly_score"] = max(float(current["max_anomaly_score"]), score)

    for current in active.values():
        episodes.append(_finalize(current))
    episodes.sort(key=lambda item: str(item["started_at"]), reverse=True)
    return episodes


def _finalize(item: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in item.items() if not key.startswith("_")}
    result["duration_seconds"] = max(
        0.0,
        (_instant(result["ended_at"]) - _instant(result["started_at"])).total_seconds(),
    )
    result["max_anomaly_score"] = round(float(result["max_anomaly_score"]), 4)
    return result


def episode_summary(*, lot_id: str | None = None, equipment_id: str | None = None) -> dict[str, Any]:
    episodes = build_anomaly_episodes(lot_id=lot_id, equipment_id=equipment_id)
    return {
        "episode_count": len(episodes),
        "critical_count": sum(1 for item in episodes if item["severity"] == "critical"),
        "latest": episodes[:10],
    }

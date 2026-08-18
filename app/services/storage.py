from __future__ import annotations

import json
import os
import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.services import db, object_store
from app.services.config import DB_PATH, MODEL_VERSION


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    """Backend-aware connection (SQLite or PostgreSQL), chosen by STORAGE_BACKEND.

    Used as ``with connect() as conn: conn.execute(...)`` throughout this module;
    db.connect() returns either a closing sqlite3.Connection subclass or a
    psycopg2 adapter that exposes the same execute/executemany/executescript
    surface.
    """
    return db.connect()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS inspections (
                id TEXT PRIMARY KEY,
                wafer_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                equipment_id TEXT NOT NULL,
                defect_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                risk_score REAL NOT NULL,
                risk_level TEXT NOT NULL,
                hotspot_ratio REAL NOT NULL,
                image_url TEXT NOT NULL,
                heatmap_url TEXT NOT NULL,
                overlay_url TEXT NOT NULL,
                roi_url TEXT,
                report TEXT NOT NULL,
                cases_json TEXT NOT NULL,
                model_version TEXT NOT NULL,
                status TEXT NOT NULL,
                engineer_decision TEXT,
                reviewer TEXT,
                review_note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_registry (
                version TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                f1_score REAL NOT NULL,
                latency_p95_ms INTEGER NOT NULL,
                registered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS drift_events (
                id TEXT PRIMARY KEY,
                line_id TEXT NOT NULL,
                drift_score REAL NOT NULL,
                status TEXT NOT NULL,
                action_taken TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS retraining_jobs (
                id TEXT PRIMARY KEY,
                trigger_type TEXT NOT NULL,
                status TEXT NOT NULL,
                candidate_version TEXT,
                f1_score REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                severity TEXT NOT NULL,
                channel TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lots (
                lot_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                recipe_route TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                current_process_step TEXT NOT NULL,
                wafer_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS process_events (
                id TEXT PRIMARY KEY,
                process_step TEXT NOT NULL,
                equipment_id TEXT NOT NULL,
                recipe_id TEXT,
                lot_id TEXT,
                wafer_id TEXT,
                observed_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handoff_reports (
                id TEXT PRIMARY KEY,
                shift_from TEXT NOT NULL,
                shift_to TEXT NOT NULL,
                line_id TEXT NOT NULL,
                operator TEXT NOT NULL,
                headline TEXT NOT NULL,
                scrap_risk TEXT NOT NULL,
                report_json TEXT NOT NULL,
                markdown TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                scheduled_for TEXT,
                schedule_key TEXT,
                sent_at TEXT,
                updated_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_traces (
                id TEXT PRIMARY KEY,
                inspection_id TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_approvals (
                id TEXT PRIMARY KEY,
                inspection_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS rag_documents (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                defect_type TEXT,
                embedding BLOB,
                metadata_json TEXT
            );
            """
        )
        _ensure_column(conn, "handoff_reports", "status", "TEXT NOT NULL DEFAULT 'draft'")
        _ensure_column(conn, "handoff_reports", "scheduled_for", "TEXT")
        _ensure_column(conn, "handoff_reports", "schedule_key", "TEXT")
        _ensure_column(conn, "handoff_reports", "sent_at", "TEXT")
        _ensure_column(conn, "handoff_reports", "updated_at", "TEXT")
        _ensure_column(conn, "inspections", "lot_id", "TEXT")
        _ensure_column(conn, "inspections", "process_step", "TEXT")
        _ensure_column(conn, "inspections", "recipe_id", "TEXT")
        _ensure_column(conn, "inspections", "image_source", "TEXT")
        _ensure_column(conn, "inspections", "proxy_dataset", "TEXT")
        _ensure_column(conn, "inspections", "proxy_status", "TEXT")
        _ensure_column(conn, "inspections", "roi_url", "TEXT")
        _ensure_column(conn, "inspections", "roi_bbox_json", "TEXT")
        _ensure_column(conn, "inspections", "process_context_json", "TEXT")
        _ensure_column(conn, "inspections", "metrology_json", "TEXT")
        _ensure_column(conn, "inspections", "action_card_json", "TEXT")
        # Report files in object storage (S3/local) — keys, presigned on read.
        _ensure_column(conn, "inspections", "report_csv_url", "TEXT")
        _ensure_column(conn, "inspections", "report_pdf_url", "TEXT")
        # Metrology values as individual columns for SQL search/aggregation.
        _ensure_column(conn, "inspections", "cd_nm", "REAL")
        _ensure_column(conn, "inspections", "overlay_nm", "REAL")
        _ensure_column(conn, "inspections", "film_thickness_nm", "REAL")
        _ensure_column(conn, "inspections", "roughness_nm", "REAL")
        # human comment captured when an approval is approved/rejected — fed back
        # to the agent as context so it learns from the engineer's reasoning.
        _ensure_column(conn, "pending_approvals", "comment", "TEXT")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_handoff_reports_schedule_key
            ON handoff_reports(schedule_key)
            WHERE schedule_key IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lots_status_started
            ON lots(status, started_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_process_events_process_time
            ON process_events(process_step, observed_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_process_events_equipment_time
            ON process_events(equipment_id, observed_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_process_events_lot_time
            ON process_events(lot_id, observed_at)
            """
        )
    seed_model_registry()
    cleanup_legacy_retraining_jobs()
    from app.services.chamber_storage import init_chamber_db  # noqa: PLC0415
    from app.services.fab_storage import init_fab_db  # noqa: PLC0415

    init_chamber_db()
    init_fab_db()


def cleanup_legacy_retraining_jobs() -> None:
    """Drop retraining-history rows that name the old ``wg-local-v<timestamp>`` models.

    The registry reseed already removed those models; this clears the matching
    entries from the retraining history (and the alerts that announced them) so the
    MLOps console no longer shows the legacy timestamp names. Idempotent — new jobs
    use the wafer-defectnet lineage and are never matched.
    """
    with connect() as conn:
        conn.execute("DELETE FROM retraining_jobs WHERE candidate_version LIKE 'wg-local-v%'")
        conn.execute("DELETE FROM alerts WHERE content LIKE '%wg-local-v%'")


# A small, realistic baseline registry: one Production model with a few archived
# predecessors. Retraining adds Staging candidates on top of this lineage instead
# of minting a new timestamp-named model on every drift tick.
_BASELINE_MODELS = [
    # version,                     stage,        f1,    p95, registered_at (backdated)
    ("wafer-defectnet-v2.3.1", "Production", 0.901, 78, "2026-05-20T09:12:00+00:00"),
    ("wafer-defectnet-v2.2.0", "Archived",   0.887, 82, "2026-03-08T14:03:00+00:00"),
    ("wafer-defectnet-v2.1.0", "Archived",   0.871, 86, "2026-01-15T10:40:00+00:00"),
    ("wafer-defectnet-v2.0.0", "Archived",   0.844, 91, "2025-11-02T08:25:00+00:00"),
]


def seed_model_registry() -> None:
    """Ensure the registry holds the realistic baseline lineage.

    Runs once: if the baseline Production model is already present, do nothing.
    Otherwise reset the registry to the baseline — this also cleans up the legacy
    ``wg-local-v<timestamp>`` models that the old drift→auto-retrain loop minted on
    every tick, which is what made the registry balloon.
    """
    baseline_prod = _BASELINE_MODELS[0][0]
    with connect() as conn:
        have = conn.execute(
            "SELECT COUNT(*) AS c FROM model_registry WHERE version = ?", (baseline_prod,)
        ).fetchone()["c"]
        if have:
            return
        conn.execute("DELETE FROM model_registry")
        conn.executemany(
            """
            INSERT INTO model_registry (version, stage, f1_score, latency_p95_ms, registered_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            _BASELINE_MODELS,
        )


def insert_inspection(record: dict[str, object]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO inspections (
                id, lot_id, wafer_id, line_id, equipment_id, process_step, recipe_id, image_source, proxy_dataset, proxy_status,
                defect_type, confidence, risk_score, risk_level, hotspot_ratio, image_url, heatmap_url,
                overlay_url, roi_url, roi_bbox_json, report, cases_json, process_context_json, metrology_json,
                action_card_json, model_version, status, created_at,
                report_csv_url, report_pdf_url, cd_nm, overlay_nm, film_thickness_nm, roughness_nm
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record.get("lot_id"),
                record["wafer_id"],
                record["line_id"],
                record["equipment_id"],
                record.get("process_step"),
                record.get("recipe_id"),
                record.get("image_source"),
                record.get("proxy_dataset"),
                record.get("proxy_status"),
                record["defect_type"],
                record["confidence"],
                record["risk_score"],
                record["risk_level"],
                record["hotspot_ratio"],
                record["image_url"],
                record["heatmap_url"],
                record["overlay_url"],
                record.get("roi_url"),
                json.dumps(record.get("roi_bbox", []), ensure_ascii=False),
                record["report"],
                json.dumps(record["cases"], ensure_ascii=False),
                json.dumps(record.get("process_context", {}), ensure_ascii=False),
                json.dumps(record.get("metrology", {}), ensure_ascii=False),
                json.dumps(record.get("action_card", {}), ensure_ascii=False),
                record["model_version"],
                record["status"],
                record["created_at"],
                record.get("report_csv_url"),
                record.get("report_pdf_url"),
                record.get("cd_nm"),
                record.get("overlay_nm"),
                record.get("film_thickness_nm"),
                record.get("roughness_nm"),
            ),
        )


def get_inspection(inspection_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,)).fetchone()
    return _inspection_to_dict(row) if row else None


def list_inspections(limit: int = 20) -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM inspections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_inspection_to_dict(row) for row in rows]


def list_inspections_for_handoff(line_id: str, limit: int = 50) -> list[dict[str, object]]:
    query = "SELECT * FROM inspections"
    params: tuple[object, ...] = ()
    if line_id != "ALL":
        query += " WHERE line_id = ?"
        params = (line_id,)
    query += " ORDER BY created_at DESC LIMIT ?"
    params = (*params, limit)
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_inspection_to_dict(row) for row in rows]


def _time_threshold(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def equipment_history(
    equipment_id: str,
    defect_type: str | None = None,
    hours: float = 24.0,
    limit: int = 12,
) -> dict[str, object]:
    """Recent inspection history for one piece of equipment.

    Used by the Agent/chat to judge recurrence: e.g. "ETCH-02 had Scratch 5x in
    24h → recurring, prioritise equipment check". Queries the inspections table.
    """
    threshold = _time_threshold(hours)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, wafer_id, lot_id, defect_type, risk_level, risk_score, confidence, created_at
            FROM inspections
            WHERE equipment_id = ? AND created_at >= ?
            ORDER BY created_at DESC
            """,
            (equipment_id, threshold),
        ).fetchall()

    records = [dict(r) for r in rows]
    by_defect: dict[str, int] = {}
    high_risk = 0
    for r in records:
        dt = str(r.get("defect_type"))
        by_defect[dt] = by_defect.get(dt, 0) + 1
        if r.get("risk_level") in ("High", "Medium"):
            high_risk += 1

    same_defect = by_defect.get(defect_type, 0) if defect_type else 0
    total = len(records)
    # Recurrence heuristic: same defect ≥3 in the window, or it dominates volume.
    recurring = bool(
        defect_type
        and (same_defect >= 3 or (total >= 4 and same_defect / max(total, 1) >= 0.5))
    )

    return {
        "equipment_id": equipment_id,
        "window_hours": hours,
        "defect_type_filter": defect_type,
        "total_inspections": total,
        "same_defect_count": same_defect,
        "by_defect": by_defect,
        "high_or_medium_risk_count": high_risk,
        "is_recurring": recurring,
        "recent": [
            {
                "id": r["id"],
                "wafer_id": r["wafer_id"],
                "defect_type": r["defect_type"],
                "risk_level": r["risk_level"],
                "risk_score": round(float(r["risk_score"] or 0), 3),
                "created_at": r["created_at"],
            }
            for r in records[:limit]
        ],
    }


_METROLOGY_METRICS = {"cd_nm", "overlay_nm", "film_thickness_nm", "roughness_nm", "defect_count", "yield_proxy"}


def metrology_trend(
    equipment_id: str,
    metric: str = "overlay_nm",
    hours: float = 72.0,
    max_points: int = 40,
) -> dict[str, object]:
    """Time series of one metrology metric for an equipment, to tell a one-off
    spike apart from a sustained drift. Reads metrology_json from inspections."""
    if metric not in _METROLOGY_METRICS:
        return {"error": f"unknown metric '{metric}'", "valid_metrics": sorted(_METROLOGY_METRICS)}
    threshold = _time_threshold(hours)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT created_at, metrology_json
            FROM inspections
            WHERE equipment_id = ? AND created_at >= ? AND metrology_json IS NOT NULL
            ORDER BY created_at ASC
            """,
            (equipment_id, threshold),
        ).fetchall()

    points: list[dict[str, object]] = []
    for r in rows:
        try:
            met = json.loads(r["metrology_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        val = met.get(metric)
        if isinstance(val, (int, float)):
            points.append({"t": r["created_at"], "value": round(float(val), 4)})

    n = len(points)
    if n == 0:
        return {
            "equipment_id": equipment_id,
            "metric": metric,
            "window_hours": hours,
            "n": 0,
            "points": [],
            "note": "해당 기간에 계측 데이터가 없습니다.",
        }

    values = [float(p["value"]) for p in points]
    first, last = values[0], values[-1]
    mean = sum(values) / n
    delta = last - first
    # simple drift classification relative to the series mean
    rel = (delta / mean) if mean else 0.0
    if abs(rel) < 0.05 or n < 3:
        trend = "stable"
    elif rel > 0:
        trend = "rising"
    else:
        trend = "falling"

    # downsample to the most recent max_points for transport
    sampled = points[-max_points:]
    return {
        "equipment_id": equipment_id,
        "metric": metric,
        "window_hours": hours,
        "n": n,
        "first": round(first, 4),
        "last": round(last, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(mean, 4),
        "delta": round(delta, 4),
        "pct_change": round(rel * 100, 1),
        "trend": trend,
        "points": sampled,
    }


def record_review(inspection_id: str, decision: str, reviewer: str, note: str) -> dict[str, object] | None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE inspections
            SET engineer_decision = ?, reviewer = ?, review_note = ?, status = ?
            WHERE id = ?
            """,
            (decision, reviewer, note, "reviewed", inspection_id),
        )
    return get_inspection(inspection_id)


def current_models() -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM model_registry ORDER BY registered_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def production_model() -> dict[str, object]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM model_registry WHERE stage = 'Production' ORDER BY registered_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else {"version": MODEL_VERSION, "f1_score": 0.872, "latency_p95_ms": 84}


def insert_model(version: str, stage: str, f1_score: float, latency_p95_ms: int) -> None:
    with connect() as conn:
        db.upsert(
            conn,
            "model_registry",
            ["version", "stage", "f1_score", "latency_p95_ms", "registered_at"],
            (version, stage, f1_score, latency_p95_ms, utc_now()),
            conflict="version",
        )


def promote_model(version: str) -> dict[str, object]:
    with connect() as conn:
        conn.execute("UPDATE model_registry SET stage = 'Archived' WHERE stage = 'Production'")
        conn.execute("UPDATE model_registry SET stage = 'Production' WHERE version = ?", (version,))
    return production_model()


def rollback_model(reason: str) -> dict[str, object]:
    models = current_models()
    archived = [m for m in models if m["stage"] == "Archived"]
    target = archived[0] if archived else models[-1]
    promoted = promote_model(str(target["version"]))
    insert_alert("critical", "sns/slack", f"자동 롤백 완료: {promoted['version']} / 사유: {reason}")
    return promoted


def insert_drift_event(event: dict[str, object]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO drift_events (id, line_id, drift_score, status, action_taken, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["line_id"],
                event["drift_score"],
                event["status"],
                event["action_taken"],
                event["created_at"],
            ),
        )


def list_drift_events(limit: int = 30) -> list[dict[str, object]]:
    """Recent drift events, newest first (used by the drift-trend chart)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM drift_events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def insert_retraining_job(job: dict[str, object]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO retraining_jobs (id, trigger_type, status, candidate_version, f1_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job["id"],
                job["trigger_type"],
                job["status"],
                job.get("candidate_version"),
                job.get("f1_score"),
                job["created_at"],
            ),
        )


def update_retraining_job(job_id: str, status: str, f1_score: float | None = None) -> None:
    """Flip a job's status (e.g. running -> completed), optionally setting its F1."""
    with connect() as conn:
        if f1_score is None:
            conn.execute(
                "UPDATE retraining_jobs SET status = ? WHERE id = ?", (status, job_id)
            )
        else:
            conn.execute(
                "UPDATE retraining_jobs SET status = ?, f1_score = ? WHERE id = ?",
                (status, f1_score, job_id),
            )


def running_retraining_job() -> dict[str, object] | None:
    """The most recent still-training job, or None — used to enforce one-at-a-time."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM retraining_jobs WHERE status = 'running' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def insert_alert(severity: str, channel: str, content: str) -> None:
    alert_id = f"ALT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO alerts (id, severity, channel, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (alert_id, severity, channel, content, utc_now()),
        )
    _publish_alert(severity, content)


def upsert_lot(lot: dict[str, object]) -> dict[str, object]:
    """Create or update the Lot operating record used across process and quality."""
    timestamp = str(lot.get("updated_at") or utc_now())
    lot_id = str(lot["lot_id"])
    existing = get_lot(lot_id)
    metadata = dict(existing.get("metadata", {})) if existing else {}
    if isinstance(lot.get("metadata"), dict):
        metadata.update(lot["metadata"])
    record = {
        "lot_id": lot_id,
        "product_id": str(lot.get("product_id") or (existing or {}).get("product_id") or "PRODUCT-DEMO"),
        "recipe_route": str(lot.get("recipe_route") or (existing or {}).get("recipe_route") or "Etch"),
        "status": str(lot.get("status") or (existing or {}).get("status") or "running"),
        "started_at": str(lot.get("started_at") or (existing or {}).get("started_at") or timestamp),
        "completed_at": lot.get("completed_at", (existing or {}).get("completed_at")),
        "current_process_step": str(lot.get("current_process_step") or (existing or {}).get("current_process_step") or "Etch"),
        "wafer_count": int(lot.get("wafer_count") or (existing or {}).get("wafer_count") or 25),
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "created_at": str(lot.get("created_at") or (existing or {}).get("created_at") or timestamp),
        "updated_at": timestamp,
    }
    columns = list(record)
    with connect() as conn:
        db.upsert(
            conn,
            "lots",
            columns,
            tuple(record[column] for column in columns),
            conflict="lot_id",
        )
    return get_lot(record["lot_id"]) or {**record, "metadata": lot.get("metadata", {})}


def get_lot(lot_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM lots WHERE lot_id = ?", (lot_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["metadata"] = _json_or_default(item.pop("metadata_json", None), {})
    return item


def list_lots(*, status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
    safe_limit = max(1, min(int(limit), 1000))
    where = "WHERE status = ?" if status else ""
    params: tuple[object, ...] = (status, safe_limit) if status else (safe_limit,)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM lots {where} ORDER BY started_at DESC, lot_id DESC LIMIT ?",
            params,
        ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _json_or_default(item.pop("metadata_json", None), {})
        items.append(item)
    return items


def insert_process_event(event: dict[str, object]) -> dict[str, object]:
    """Persist a process-agnostic event without changing source telemetry tables."""
    record = {
        "id": str(event["id"]),
        "process_step": str(event["process_step"]),
        "equipment_id": str(event["equipment_id"]),
        "recipe_id": event.get("recipe_id"),
        "lot_id": event.get("lot_id"),
        "wafer_id": event.get("wafer_id"),
        "observed_at": str(event["observed_at"]),
        "event_type": str(event["event_type"]),
        "severity": str(event["severity"]),
        "metadata_json": json.dumps(event.get("metadata", {}), ensure_ascii=False),
        "source": str(event.get("source") or "runtime"),
        "created_at": str(event.get("created_at") or utc_now()),
    }
    columns = list(record)
    with connect() as conn:
        db.upsert(conn, "process_events", columns, tuple(record[column] for column in columns))
    return {**record, "metadata": json.loads(record["metadata_json"])}


def list_process_events(
    *,
    process_step: str | None = None,
    lot_id: str | None = None,
    wafer_id: str | None = None,
    recipe_id: str | None = None,
    equipment_ids: list[str] | None = None,
    observed_after: str | None = None,
    observed_before: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    where: list[str] = []
    params: list[object] = []
    if process_step:
        where.append("process_step = ?")
        params.append(process_step)
    if lot_id:
        where.append("lot_id = ?")
        params.append(lot_id)
    if wafer_id:
        where.append("wafer_id = ?")
        params.append(wafer_id)
    if recipe_id:
        where.append("recipe_id = ?")
        params.append(recipe_id)
    if equipment_ids:
        placeholders = ", ".join(["?"] * len(equipment_ids))
        where.append(f"equipment_id IN ({placeholders})")
        params.extend(equipment_ids)
    if observed_after:
        where.append("observed_at >= ?")
        params.append(observed_after)
    if observed_before:
        where.append("observed_at <= ?")
        params.append(observed_before)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    safe_limit = max(1, min(int(limit), 500))
    params.append(safe_limit)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM process_events {clause} ORDER BY observed_at DESC, created_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _json_or_default(item.pop("metadata_json", None), {})
        items.append(item)
    return items


def _publish_alert(severity: str, content: str) -> None:
    """Best-effort SNS publish for an alert.

    No-op unless SNS_TOPIC_ARN is set, so local dev and the DB-only path are
    unaffected. Failures are swallowed — a notification problem must never break
    the inspection/MLOps flow that recorded the alert.
    """
    topic = os.environ.get("SNS_TOPIC_ARN")
    if not topic:
        return
    try:
        from app.services import aws  # noqa: PLC0415

        aws.sns().publish(
            TopicArn=topic,
            Subject=f"[WaferGuard] {severity.upper()}"[:100],
            Message=content,
        )
    except Exception:  # noqa: BLE001
        pass


def insert_handoff_report(report: dict[str, object]) -> None:
    report.setdefault("status", "draft")
    report.setdefault("scheduled_for", None)
    report.setdefault("schedule_key", None)
    report.setdefault("sent_at", None)
    report.setdefault("updated_at", report["created_at"])
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO handoff_reports (
                id, shift_from, shift_to, line_id, operator, headline,
                scrap_risk, report_json, markdown, status, scheduled_for,
                schedule_key, sent_at, updated_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["id"],
                report["shift_from"],
                report["shift_to"],
                report["line_id"],
                report["operator"],
                report["headline"],
                report["scrap_risk"],
                json.dumps(report, ensure_ascii=False),
                report["markdown"],
                report["status"],
                report["scheduled_for"],
                report["schedule_key"],
                report["sent_at"],
                report["updated_at"],
                report["created_at"],
            ),
        )


def latest_handoff_report() -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT report_json FROM handoff_reports ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["report_json"]) if row else None


def get_handoff_report(report_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT report_json FROM handoff_reports WHERE id = ?",
            (report_id,),
        ).fetchone()
    return json.loads(row["report_json"]) if row else None


def find_handoff_by_schedule_key(schedule_key: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT report_json
            FROM handoff_reports
            WHERE schedule_key = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (schedule_key,),
        ).fetchone()
    return json.loads(row["report_json"]) if row else None


def save_handoff_report(report: dict[str, object]) -> None:
    report["updated_at"] = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE handoff_reports
            SET headline = ?, scrap_risk = ?, report_json = ?, markdown = ?,
                status = ?, scheduled_for = ?, schedule_key = ?, sent_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                report["headline"],
                report["scrap_risk"],
                json.dumps(report, ensure_ascii=False),
                report["markdown"],
                report.get("status", "draft"),
                report.get("scheduled_for"),
                report.get("schedule_key"),
                report.get("sent_at"),
                report["updated_at"],
                report["id"],
            ),
        )


def metrics() -> dict[str, object]:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM inspections").fetchone()["count"]
        high = conn.execute("SELECT COUNT(*) AS count FROM inspections WHERE risk_level = 'High'").fetchone()["count"]
        review = conn.execute(
            "SELECT COUNT(*) AS count FROM inspections WHERE status = 'review_required'"
        ).fetchone()["count"]
        avg_conf = conn.execute("SELECT AVG(confidence) AS value FROM inspections").fetchone()["value"]
        defect_rows = conn.execute(
            "SELECT defect_type, COUNT(*) AS count FROM inspections GROUP BY defect_type ORDER BY count DESC"
        ).fetchall()
        risk_rows = conn.execute(
            """
            SELECT id, risk_score, risk_level, created_at
            FROM inspections
            ORDER BY created_at DESC LIMIT 20
            """
        ).fetchall()
        drift = conn.execute(
            "SELECT * FROM drift_events ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        jobs = conn.execute(
            "SELECT * FROM retraining_jobs ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        alerts = conn.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
    model = production_model()
    return {
        "total_inspections": total,
        "high_risk_count": high,
        "review_queue_count": review,
        "avg_confidence": round(float(avg_conf or 0), 3),
        "production_model": model,
        "defect_distribution": [dict(row) for row in defect_rows],
        "risk_trend": [dict(row) for row in reversed(risk_rows)],
        "latest_drift_event": dict(drift) if drift else None,
        "recent_retraining_jobs": [dict(row) for row in jobs],
        "recent_alerts": [dict(row) for row in alerts],
    }


def _inspection_to_dict(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    data["cases"] = json.loads(data.pop("cases_json"))
    data["roi_bbox"] = _json_or_default(data.pop("roi_bbox_json", None), [])
    data["process_context"] = _json_or_default(data.pop("process_context_json", None), _legacy_process_context(data))
    data["metrology"] = _json_or_default(data.pop("metrology_json", None), {})
    action_card = _json_or_default(data.pop("action_card_json", None), {})
    data["action_card"] = (
        action_card
        if isinstance(action_card, dict) and action_card.get("defect_type")
        else _legacy_action_card(data)
    )
    # Convert stored object keys into browser-fetchable URLs (local path or S3
    # presigned URL) at read time, so the DB never holds an expiring URL.
    for field in ("image_url", "heatmap_url", "overlay_url", "roi_url", "report_csv_url", "report_pdf_url"):
        if data.get(field):
            data[field] = object_store.presign(data[field])
    return data


def _json_or_default(raw: str | None, default: object) -> object:
    if not raw:
        return default
    return json.loads(raw)


def _legacy_process_context(data: dict[str, object]) -> dict[str, object]:
    return {
        "lot_id": data.get("lot_id") or "LOT-LEGACY",
        "wafer_id": data.get("wafer_id"),
        "line_id": data.get("line_id"),
        "process_step": data.get("process_step") or "Inspection",
        "tool_id": data.get("equipment_id"),
        "recipe_id": data.get("recipe_id") or "RCP-LEGACY",
    }


def _legacy_action_card(data: dict[str, object]) -> dict[str, object]:
    return {
        "title": f"{data.get('defect_type', 'Unknown')} Defect Action Card",
        "defect_type": data.get("defect_type"),
        "risk_level": data.get("risk_level"),
        "human_review_rule": "기존 검사 이력입니다. 새 검사를 실행하면 process/metrology 기반 Action Card가 저장됩니다.",
        "source_boundary": "legacy row fallback",
    }


def insert_agent_trace(inspection_id: str, trace: dict) -> str:
    trace_id = f"TRC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_traces (id, inspection_id, trace_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (trace_id, inspection_id, json.dumps(trace, ensure_ascii=False), utc_now()),
        )
    return trace_id


def list_mlops_agent_traces(limit: int = 20) -> list[dict]:
    """Recent MLOps-agent runs (trace_key starts with 'MLOPS-'), newest first.

    Returns lightweight rows (no full message history) for the monitoring log —
    including the ``trigger`` so the UI can flag delegation-initiated runs.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_traces WHERE inspection_id LIKE 'MLOPS-%' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            trace = json.loads(row["trace_json"])
        except Exception:  # noqa: BLE001
            continue
        out.append(
            {
                "trace_id": row["id"],
                "created_at": row["created_at"],
                "final_action": trace.get("final_action"),
                "tool_calls": trace.get("tool_calls", []),
                "agent_mode": trace.get("agent_mode"),
                "trigger": trace.get("trigger", "manual"),
                "source": trace.get("source"),
                "autonomy": trace.get("autonomy"),
            }
        )
    return out


def get_agent_trace_for_inspection(inspection_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_traces WHERE inspection_id = ? ORDER BY created_at DESC LIMIT 1",
            (inspection_id,),
        ).fetchone()
    if row is None:
        return None
    trace = json.loads(row["trace_json"])
    trace.pop("messages", None)
    return {
        "trace_id": row["id"],
        "inspection_id": inspection_id,
        "created_at": row["created_at"],
        **trace,
    }


def insert_pending_approval(
    inspection_id: str,
    tool_name: str,
    payload: dict,
    reason: str,
) -> str:
    approval_id = f"APR-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_approvals (id, inspection_id, tool_name, payload_json, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                approval_id,
                inspection_id,
                tool_name,
                json.dumps(payload, ensure_ascii=False),
                reason,
                utc_now(),
            ),
        )
    return approval_id


def get_approval(approval_id: str) -> dict | None:
    """Fetch a single approval by id (incl. its current status + engineer comment).

    Lets the agent reconcile a past ``recommend_retrain`` recommendation with how a
    human eventually ruled on it (approved/rejected) instead of assuming pending.
    """
    if not approval_id:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json", "{}"))
    return d


def list_pending_approvals(status: str = "pending") -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_approvals WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["payload"] = json.loads(d.pop("payload_json", "{}"))
        result.append(d)
    return result


def pending_approval_for_tool(tool_name: str) -> dict | None:
    """The oldest still-pending approval for a given tool, or None.

    Used to dedupe agent recommendations: you can't train several models at once,
    so a second ``recommend_retrain`` should fold into the one already queued
    rather than stacking up another approval card.
    """
    for row in list_pending_approvals("pending"):
        if row.get("tool_name") == tool_name:
            return row
    return None


def resolve_approval(approval_id: str, status: str, comment: str | None = None) -> dict | None:
    """Set status to 'approved' or 'rejected', optionally storing a human comment."""
    now = utc_now()
    comment = (comment or "").strip() or None
    with connect() as conn:
        conn.execute(
            "UPDATE pending_approvals SET status = ?, resolved_at = ?, comment = ? WHERE id = ?",
            (status, now, comment, approval_id),
        )
        row = conn.execute(
            "SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json", "{}"))
    return d


def count_rag_documents() -> int:
    """Number of indexed RAG documents (used to decide whether to seed)."""
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM rag_documents").fetchone()["c"]


def existing_rag_ids() -> set[str]:
    """IDs already present in rag_documents (used for incremental seeding)."""
    with connect() as conn:
        rows = conn.execute("SELECT id FROM rag_documents").fetchall()
    return {r["id"] for r in rows}


def rag_type_counts() -> dict[str, int]:
    """Indexed RAG document counts grouped by defect_type (for the data view)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT defect_type, COUNT(*) AS c FROM rag_documents GROUP BY defect_type"
        ).fetchall()
    return {(r["defect_type"] or "기타"): r["c"] for r in rows}


def recent_human_feedback(
    equipment_id: str | None = None,
    defect_type: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Resolved human decisions the agent should learn from (episodic memory).

    Combines two signals, newest first:
      - approved/rejected High-risk Tool requests (pending_approvals), joined to
        the originating inspection for equipment/defect context.
      - engineer review decisions recorded on inspections.
    Filters by equipment_id / defect_type when provided so the agent only sees
    feedback relevant to the case in front of it.
    """
    out: list[dict] = []
    with connect() as conn:
        # 1) Resolved approvals (the agent's own recommendations that a human ruled on)
        rows = conn.execute(
            """
            SELECT a.tool_name, a.reason, a.comment, a.status, a.resolved_at,
                   a.inspection_id, i.equipment_id, i.defect_type
            FROM pending_approvals a
            LEFT JOIN inspections i ON i.id = a.inspection_id
            WHERE a.status IN ('approved', 'rejected')
            ORDER BY a.resolved_at DESC
            """
        ).fetchall()
        for r in rows:
            d = dict(r)
            # When a filter is given, require an exact match: a per-wafer agent
            # asking about ETCH-02/Scratch should not see fleet-level (NULL
            # equipment) retrain decisions. The MLOps path passes no filter and
            # therefore sees everything, including FLEET rows.
            if equipment_id and d.get("equipment_id") != equipment_id:
                continue
            if defect_type and d.get("defect_type") != defect_type:
                continue
            out.append(
                {
                    "kind": "approval",
                    "decision": d["status"],
                    "tool_name": d["tool_name"],
                    "reason": d.get("reason"),
                    "comment": d.get("comment"),
                    "equipment_id": d.get("equipment_id"),
                    "defect_type": d.get("defect_type"),
                    "inspection_id": d.get("inspection_id"),
                    "at": d.get("resolved_at"),
                }
            )
            if len(out) >= limit:
                return out

        # 2) Engineer review decisions on inspections
        params: list[object] = []
        where = ["engineer_decision IS NOT NULL AND engineer_decision != ''"]
        if equipment_id:
            where.append("equipment_id = ?")
            params.append(equipment_id)
        if defect_type:
            where.append("defect_type = ?")
            params.append(defect_type)
        params.append(limit)
        review_rows = conn.execute(
            f"""
            SELECT id, equipment_id, defect_type, engineer_decision, review_note, created_at
            FROM inspections
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    for r in review_rows:
        d = dict(r)
        out.append(
            {
                "kind": "review",
                "decision": d.get("engineer_decision"),
                "reason": d.get("review_note"),
                "equipment_id": d.get("equipment_id"),
                "defect_type": d.get("defect_type"),
                "inspection_id": d.get("id"),
                "at": d.get("created_at"),
            }
        )
        if len(out) >= limit:
            break
    return out[:limit]


def upsert_rag_document(
    doc_id: str,
    content: str,
    defect_type: str | None,
    embedding: list[float] | None,
    metadata: dict | None = None,
) -> None:
    embedding_blob: bytes | None = None
    if embedding is not None:
        embedding_blob = struct.pack(f"{len(embedding)}f", *embedding)
    with connect() as conn:
        db.upsert(
            conn,
            "rag_documents",
            ["id", "content", "defect_type", "embedding", "metadata_json"],
            (
                doc_id,
                content,
                defect_type,
                embedding_blob,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
            conflict="id",
        )


def query_rag(query_vec: list[float], k: int = 3) -> list[dict]:
    """Cosine similarity search over rag_documents.

    Returns top-k documents sorted by descending similarity.
    Falls back to returning first k rows if no embeddings are indexed.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, content, defect_type, embedding, metadata_json FROM rag_documents"
        ).fetchall()

    if not rows:
        return []

    q = np.array(query_vec, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        q_norm = 1.0

    scored: list[tuple[float, dict]] = []
    no_embedding_rows: list[dict] = []

    for row in rows:
        blob = row["embedding"]
        doc = {
            "id": row["id"],
            "content": row["content"],
            "defect_type": row["defect_type"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }
        if blob is None:
            no_embedding_rows.append(doc)
            continue
        blob = bytes(blob)  # psycopg2 BYTEA → memoryview; sqlite → bytes
        n = len(blob) // 4
        vec = np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)
        vec_norm = np.linalg.norm(vec)
        if vec_norm == 0:
            sim = 0.0
        else:
            sim = float(np.dot(q, vec) / (q_norm * vec_norm))
        doc["similarity"] = sim
        scored.append((sim, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [doc for _, doc in scored[:k]]

    # If not enough results, pad with no-embedding rows
    if len(result) < k:
        result.extend(no_embedding_rows[: k - len(result)])

    return result


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    if column not in db.table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ---------------------------------------------------------------------------
# DB browser (read-only access for the Data tab)
# ---------------------------------------------------------------------------

BROWSABLE_TABLES: tuple[str, ...] = (
    "inspections",
    "lots",
    "model_registry",
    "drift_events",
    "retraining_jobs",
    "alerts",
    "process_events",
    "handoff_reports",
    "agent_traces",
    "pending_approvals",
    "rag_documents",
    "chamber_telemetry",
    "chamber_predictions",
    "anomaly_detections",
    "chamber_model_registry",
)


def db_overview() -> dict[str, object]:
    with connect() as conn:
        tables = []
        for name in BROWSABLE_TABLES:
            count = conn.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()["count"]
            columns = db.table_columns(conn, name)
            tables.append({"name": name, "row_count": count, "columns": columns})
    db_file = Path(DB_PATH)
    return {
        "backend": db.backend(),
        "db_path": str(DB_PATH),
        "db_size_bytes": db_file.stat().st_size if db_file.exists() else 0,
        "tables": tables,
    }


def browse_table(name: str, limit: int = 50, offset: int = 0) -> dict[str, object] | None:
    if name not in BROWSABLE_TABLES:
        return None
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    with connect() as conn:
        columns = db.table_columns(conn, name)
        total = conn.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()["count"]
        if "created_at" in columns:
            order = "created_at DESC"
        elif "registered_at" in columns:
            order = "registered_at DESC"
        else:
            order = f"{columns[0]} DESC"
        rows = conn.execute(
            f"SELECT * FROM {name} ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        item: dict[str, object] = {}
        for key in row.keys():
            value = row[key]
            if isinstance(value, bytes):
                value = f"<blob {len(value)} bytes>"
            item[key] = value
        items.append(item)
    return {
        "table": name,
        "columns": columns,
        "total": total,
        "limit": limit,
        "offset": offset,
        "rows": items,
    }

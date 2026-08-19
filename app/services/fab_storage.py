"""Persistence and read models for the Virtual FAB v2 runtime.

The module deliberately accepts plain mappings instead of importing simulator
types.  This keeps the database writer usable by both the direct and MQTT
transports and prevents synthetic ground truth from leaking into inference
services.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from app.services import db


FAB_SCHEMA_VERSION = "fab.v2"
_ROUTE_ORDER = {"photo": 1, "etch": 2, "deposition": 3, "cmp": 4, "cleaning": 5, "inspection": 6}
_PRIVATE_GT_KEYS = {
    "fault_id",
    "ground_truth",
    "ground_truth_defect",
    "ground_truth_mask",
    "ground_truth_tags",
    "ground_truth_tags_json",
    "injected_anomaly",
    "injected_fault",
    "mask_key",
    "simulation_fault_id",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_timestamp(value: Any | None, *, default_now: bool = True) -> str | None:
    """Return an ISO-8601 UTC timestamp and reject ambiguous timestamps."""
    if value is None or str(value).strip() == "":
        return utc_now() if default_now else None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _public_fab_config() -> dict[str, Any]:
    from app.services.fab_generator import load_fab_config  # noqa: PLC0415

    return load_fab_config()


def process_definitions(process_id: str | None = None) -> list[dict[str, Any]]:
    """Return the public, vendor-neutral FAB equipment and measurement catalog."""
    configured = _public_fab_config()["processes"]
    selected = str(process_id).lower() if process_id else None
    definitions: list[dict[str, Any]] = []
    for current_id, process in configured.items():
        if selected and current_id != selected:
            continue
        metrology_plan = [
            {
                "metric_id": metric_id,
                **{
                    key: spec[key]
                    for key in (
                        "display_name", "unit", "modality", "instrument_class",
                        "method", "sampling_level", "target", "tolerance",
                    )
                },
            }
            for metric_id, spec in process["metrology"].items()
        ]
        definitions.append({
            "process_id": current_id,
            "display_name": process["display_name"],
            "process_step": process["process_step"],
            "equipment_class": dict(process["equipment_class"]),
            "unit_type": process["unit_type"],
            "unit_class": process["unit_class"],
            "sensor_tags": {key: dict(value) for key, value in process["sensor_tags"].items()},
            "equipment": [
                {
                    "equipment_id": tool["id"],
                    "display_name": tool["display_name"],
                    "equipment_class_id": tool["equipment_class_id"],
                    "units": [
                        {
                            "unit_id": unit["id"],
                            "display_name": unit["display_name"],
                            "unit_class": unit["unit_class"],
                        }
                        for unit in tool["units"]
                    ],
                }
                for tool in process["equipment"]
            ],
            "recipes": [
                {
                    "recipe_id": recipe["id"],
                    "display_name": recipe["display_name"],
                    "recipe_class": recipe["recipe_class"],
                }
                for recipe in process["recipes"]
            ],
            "metrology_plan": metrology_plan,
            "inspection_plan": dict(process["inspection"]),
            "runtime_supported": True,
            "synthetic_proxy": True,
            "disclaimer": "Vendor-neutral synthetic configuration; not equipment or metrology control data.",
        })
    return definitions


def _configured_context(
    process_id: str | None,
    *,
    equipment_id: str | None = None,
    unit_id: str | None = None,
    recipe_id: str | None = None,
) -> dict[str, Any]:
    definitions = process_definitions(process_id)
    if not definitions:
        return {}
    definition = definitions[0]
    equipment = next(
        (item for item in definition["equipment"] if item["equipment_id"] == equipment_id),
        None,
    )
    unit = next(
        (item for item in (equipment or {}).get("units", []) if item["unit_id"] == unit_id),
        None,
    )
    recipe = next(
        (item for item in definition["recipes"] if item["recipe_id"] == recipe_id),
        None,
    )
    return {
        "process_id": definition["process_id"],
        "equipment_class": definition["equipment_class"],
        "equipment_display_name": (equipment or {}).get("display_name", equipment_id),
        "unit_class": (unit or {}).get("unit_class", definition["unit_class"]),
        "unit_display_name": (unit or {}).get("display_name", unit_id),
        "recipe_class": (recipe or {}).get("recipe_class"),
        "recipe_display_name": (recipe or {}).get("display_name", recipe_id),
        "sensor_tags": definition["sensor_tags"],
        "supported_recipes": definition["recipes"],
        "metrology_plan": definition["metrology_plan"],
        "inspection_plan": definition["inspection_plan"],
        "runtime_supported": True,
        "synthetic_proxy": True,
        "disclaimer": definition["disclaimer"],
    }


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    raise TypeError("FAB persistence records must be mappings or mapping-compatible objects")


def _required(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{key} is required")
    return str(value)


def _json(value: Any, default: Any) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        try:
            json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("JSON string fields must contain valid JSON") from exc
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _table_exists(conn: Any, table: str) -> bool:
    return bool(db.table_columns(conn, table))


def _upsert(conn: Any, table: str, record: Mapping[str, Any], conflict: tuple[str, ...]) -> None:
    columns = list(record)
    placeholders = ", ".join(["?"] * len(columns))
    conflict_sql = ", ".join(conflict)
    updates = [column for column in columns if column not in conflict]
    if db.backend() == "postgres":
        action = (
            "DO UPDATE SET " + ", ".join(f"{column}=EXCLUDED.{column}" for column in updates)
            if updates
            else "DO NOTHING"
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) {action}"
        )
    else:
        sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, tuple(record[column] for column in columns))


def _insert_ignore(
    conn: Any,
    table: str,
    record: Mapping[str, Any],
    conflict: tuple[str, ...],
) -> bool:
    columns = list(record)
    placeholders = ", ".join(["?"] * len(columns))
    if db.backend() == "postgres":
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(conflict)}) DO NOTHING"
        )
    else:
        sql = f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    cursor = conn.execute(sql, tuple(record[column] for column in columns))
    return int(cursor.rowcount or 0) == 1


def init_fab_db() -> None:
    """Create v2 tables and apply only additive migrations to legacy tables."""
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wafers (
                wafer_id TEXT PRIMARY KEY,
                lot_id TEXT NOT NULL,
                wafer_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                current_process_step TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS equipment_units (
                equipment_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                process_id TEXT,
                process_step TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                status TEXT NOT NULL,
                recipe_id TEXT,
                current_lot_id TEXT,
                current_wafer_id TEXT,
                current_process_run_id TEXT,
                cycle_id TEXT,
                cycle_index INTEGER NOT NULL DEFAULT 0,
                cycle_index_since_maintenance INTEGER NOT NULL DEFAULT 0,
                machine_state TEXT,
                phase TEXT,
                last_observed_at TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (equipment_id, unit_id)
            );

            CREATE TABLE IF NOT EXISTS process_runs (
                process_run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                wafer_id TEXT NOT NULL,
                process_id TEXT,
                process_step TEXT NOT NULL,
                equipment_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                cycle_index INTEGER NOT NULL DEFAULT 0,
                cycle_index_since_maintenance INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS process_telemetry (
                id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                process_run_id TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                wafer_id TEXT NOT NULL,
                process_id TEXT,
                process_step TEXT NOT NULL,
                equipment_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                cycle_index INTEGER NOT NULL DEFAULT 0,
                cycle_index_since_maintenance INTEGER NOT NULL,
                machine_state TEXT NOT NULL,
                phase TEXT NOT NULL,
                phase_progress REAL NOT NULL,
                observed_at TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                detector_context_json TEXT NOT NULL DEFAULT '{}',
                data_quality_status TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fab_detector_results (
                id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                message_id TEXT,
                process_run_id TEXT NOT NULL,
                process_id TEXT NOT NULL,
                process_step TEXT NOT NULL,
                modality TEXT NOT NULL,
                equipment_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                wafer_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                cycle_index INTEGER NOT NULL DEFAULT 0,
                cycle_index_since_maintenance INTEGER NOT NULL,
                machine_state TEXT,
                phase TEXT,
                phase_progress REAL,
                observed_at TEXT NOT NULL,
                model_version TEXT NOT NULL,
                raw_score REAL NOT NULL,
                threshold REAL NOT NULL,
                margin REAL NOT NULL,
                is_anomaly INTEGER NOT NULL,
                related_tags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                image_key TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (message_id, modality)
            );

            CREATE TABLE IF NOT EXISTS metrology_results (
                id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                process_run_id TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                wafer_id TEXT NOT NULL,
                process_id TEXT,
                process_step TEXT NOT NULL,
                equipment_id TEXT,
                unit_id TEXT,
                recipe_id TEXT,
                cycle_index INTEGER,
                modality TEXT NOT NULL,
                model_version TEXT,
                raw_score REAL,
                threshold REAL,
                margin REAL,
                is_anomaly INTEGER,
                related_tags_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                quality_targets_json TEXT NOT NULL DEFAULT '{}',
                measurements_json TEXT NOT NULL DEFAULT '[]',
                metrology_context_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inspection_assets (
                id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                process_run_id TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                wafer_id TEXT NOT NULL,
                process_id TEXT,
                process_step TEXT NOT NULL,
                equipment_id TEXT,
                unit_id TEXT,
                recipe_id TEXT,
                cycle_index INTEGER,
                modality TEXT NOT NULL,
                model_version TEXT,
                raw_score REAL,
                threshold REAL,
                margin REAL,
                is_anomaly INTEGER,
                related_tags_json TEXT NOT NULL,
                image_key TEXT NOT NULL,
                mask_key TEXT,
                bbox_json TEXT,
                defect_type TEXT,
                defect_severity REAL,
                observed_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                inspection_modality TEXT,
                instrument_class TEXT,
                image_type TEXT,
                sampling_level TEXT,
                inspection_context_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_faults (
                fault_id TEXT PRIMARY KEY,
                process_run_id TEXT NOT NULL,
                fault_type TEXT NOT NULL,
                fault_scope TEXT NOT NULL,
                started_at TEXT NOT NULL,
                severity REAL NOT NULL,
                ground_truth_tags_json TEXT NOT NULL,
                ground_truth_defect TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rca_results (
                id TEXT PRIMARY KEY,
                wafer_id TEXT NOT NULL,
                process_run_id TEXT NOT NULL,
                candidate_causes_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                recommended_checks_json TEXT NOT NULL,
                top_candidate TEXT,
                confidence REAL NOT NULL,
                overall_risk REAL,
                fusion_version TEXT,
                model_or_rule_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (process_run_id, model_or_rule_version)
            );

            CREATE TABLE IF NOT EXISTS fusion_results (
                id TEXT PRIMARY KEY,
                process_run_id TEXT NOT NULL,
                wafer_id TEXT NOT NULL,
                fusion_version TEXT NOT NULL,
                overall_risk REAL NOT NULL,
                threshold REAL NOT NULL,
                margin REAL NOT NULL,
                is_anomaly INTEGER NOT NULL,
                modality_risks_json TEXT NOT NULL,
                weights_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (process_run_id, fusion_version)
            );

            CREATE TABLE IF NOT EXISTS fab_message_receipts (
                consumer_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                payload_hash TEXT,
                status TEXT NOT NULL,
                error TEXT,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                PRIMARY KEY (consumer_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_wafers_lot ON wafers(lot_id, wafer_index);
            CREATE INDEX IF NOT EXISTS idx_equipment_units_process ON equipment_units(process_step, status);
            CREATE INDEX IF NOT EXISTS idx_process_runs_wafer ON process_runs(wafer_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_process_runs_lot ON process_runs(lot_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_process_runs_equipment ON process_runs(equipment_id, unit_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_process_telemetry_run_time ON process_telemetry(process_run_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_process_telemetry_live ON process_telemetry(process_step, equipment_id, unit_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_fab_detector_run_time ON fab_detector_results(process_run_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_fab_detector_live ON fab_detector_results(process_id, equipment_id, unit_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_metrology_run_time ON metrology_results(process_run_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_inspection_run_time ON inspection_assets(process_run_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_simulation_fault_run ON simulation_faults(process_run_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_rca_run_time ON rca_results(process_run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_fusion_run_time ON fusion_results(process_run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_fab_receipts_time ON fab_message_receipts(received_at, status);
            """
        )
        if "detector_context_json" not in set(db.table_columns(conn, "process_telemetry")):
            conn.execute(
                "ALTER TABLE process_telemetry ADD COLUMN detector_context_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        if "quality_targets_json" not in set(db.table_columns(conn, "metrology_results")):
            conn.execute(
                "ALTER TABLE metrology_results ADD COLUMN quality_targets_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        metrology_columns = set(db.table_columns(conn, "metrology_results"))
        if "measurements_json" not in metrology_columns:
            conn.execute(
                "ALTER TABLE metrology_results ADD COLUMN measurements_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        if "metrology_context_json" not in metrology_columns:
            conn.execute(
                "ALTER TABLE metrology_results ADD COLUMN metrology_context_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        inspection_columns = set(db.table_columns(conn, "inspection_assets"))
        for column, definition in (
            ("inspection_modality", "TEXT"),
            ("instrument_class", "TEXT"),
            ("image_type", "TEXT"),
            ("sampling_level", "TEXT"),
            ("inspection_context_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if column not in inspection_columns:
                conn.execute(f"ALTER TABLE inspection_assets ADD COLUMN {column} {definition}")
        for table in (
            "equipment_units",
            "process_runs",
            "process_telemetry",
            "fab_detector_results",
            "metrology_results",
            "inspection_assets",
        ):
            columns = set(db.table_columns(conn, table))
            if "process_id" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN process_id TEXT")
            if "cycle_index" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN cycle_index INTEGER")
        _ensure_legacy_identity_columns(conn)
        # Early FAB v2 state envelopes did not carry an explicit started_at.
        # Repair only impossible completed ranges, using the first persisted
        # telemetry timestamp as the non-destructive source of truth.
        run_rows = conn.execute(
            "SELECT process_run_id, started_at, completed_at FROM process_runs "
            "WHERE completed_at IS NOT NULL"
        ).fetchall()
        for run_row in run_rows:
            started_at = normalize_timestamp(run_row["started_at"], default_now=False)
            completed_at = normalize_timestamp(run_row["completed_at"], default_now=False)
            if not started_at or not completed_at:
                continue
            if datetime.fromisoformat(started_at) <= datetime.fromisoformat(completed_at):
                continue
            first_row = conn.execute(
                "SELECT MIN(observed_at) AS first_observed_at FROM process_telemetry "
                "WHERE process_run_id = ?",
                (run_row["process_run_id"],),
            ).fetchone()
            first_observed_at = normalize_timestamp(
                first_row["first_observed_at"] if first_row else None,
                default_now=False,
            )
            if first_observed_at and datetime.fromisoformat(first_observed_at) <= datetime.fromisoformat(completed_at):
                conn.execute(
                    "UPDATE process_runs SET started_at = ? WHERE process_run_id = ?",
                    (first_observed_at, run_row["process_run_id"]),
                )


_LEGACY_IDENTITY_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "process_events": (
        ("schema_version", "TEXT"),
        ("message_id", "TEXT"),
        ("process_run_id", "TEXT"),
        ("process_id", "TEXT"),
        ("unit_id", "TEXT"),
        ("unit_type", "TEXT"),
        ("cycle_id", "TEXT"),
        ("cycle_index", "INTEGER"),
        ("cycle_index_since_maintenance", "INTEGER"),
        ("machine_state", "TEXT"),
        ("phase", "TEXT"),
        ("phase_progress", "REAL"),
    ),
    "process_runtime_samples": (
        ("schema_version", "TEXT"),
        ("message_id", "TEXT"),
        ("process_run_id", "TEXT"),
        ("process_id", "TEXT"),
        ("unit_id", "TEXT"),
        ("unit_type", "TEXT"),
        ("cycle_id", "TEXT"),
        ("cycle_index", "INTEGER"),
        ("cycle_index_since_maintenance", "INTEGER"),
        ("machine_state", "TEXT"),
        ("phase", "TEXT"),
        ("phase_progress", "REAL"),
        ("margin", "REAL"),
        ("related_tags_json", "TEXT"),
    ),
    "inspections": (
        ("schema_version", "TEXT"),
        ("message_id", "TEXT"),
        ("process_run_id", "TEXT"),
        ("process_id", "TEXT"),
        ("unit_id", "TEXT"),
        ("unit_type", "TEXT"),
        ("cycle_id", "TEXT"),
        ("cycle_index", "INTEGER"),
        ("cycle_index_since_maintenance", "INTEGER"),
        ("machine_state", "TEXT"),
        ("phase", "TEXT"),
        ("phase_progress", "REAL"),
        ("observed_at", "TEXT"),
    ),
}


def _ensure_legacy_identity_columns(conn: Any) -> None:
    for table, definitions in _LEGACY_IDENTITY_COLUMNS.items():
        columns = set(db.table_columns(conn, table))
        if not columns:
            continue
        for column, ddl in definitions:
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                columns.add(column)


def ensure_legacy_identity_columns() -> None:
    """Re-run migrations after a lazily-created legacy runtime table appears."""
    with db.connect() as conn:
        _ensure_legacy_identity_columns(conn)


def _infer_wafer_index(wafer_id: str) -> int:
    match = re.search(r"(?:-W|W)(\d+)$", wafer_id, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def get_wafer(wafer_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM wafers WHERE wafer_id = ?", (wafer_id,)).fetchone()
    if row is None:
        return None
    item = _row(row)
    item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    return item


def upsert_wafer(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    wafer_id = _required(payload, "wafer_id")
    lot_id = _required(payload, "lot_id")
    existing = get_wafer(wafer_id)
    if existing and existing["lot_id"] != lot_id:
        raise ValueError(f"wafer_id {wafer_id!r} is already assigned to lot {existing['lot_id']!r}")
    now = normalize_timestamp(payload.get("updated_at"))
    metadata = dict((existing or {}).get("metadata", {}))
    incoming_metadata = payload.get("metadata", payload.get("metadata_json"))
    if incoming_metadata is not None:
        decoded = _decode_json(incoming_metadata, {})
        if not isinstance(decoded, dict):
            raise ValueError("wafer metadata must be an object")
        metadata.update(decoded)
    record = {
        "wafer_id": wafer_id,
        "lot_id": lot_id,
        "wafer_index": int(payload.get("wafer_index", (existing or {}).get("wafer_index", _infer_wafer_index(wafer_id)))),
        "status": str(payload.get("status") or (existing or {}).get("status") or "queued"),
        "current_process_step": payload.get("current_process_step", (existing or {}).get("current_process_step")),
        "metadata_json": _json(metadata, {}),
        "created_at": str(payload.get("created_at") or (existing or {}).get("created_at") or now),
        "updated_at": str(now),
    }
    with db.connect() as conn:
        _upsert(conn, "wafers", record, ("wafer_id",))
    return get_wafer(wafer_id) or record


def get_equipment_unit(equipment_id: str, unit_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM equipment_units WHERE equipment_id = ? AND unit_id = ?",
            (equipment_id, unit_id),
        ).fetchone()
    if row is None:
        return None
    item = _row(row)
    item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    return item


def upsert_equipment_unit(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    equipment_id = _required(payload, "equipment_id")
    unit_id = _required(payload, "unit_id")
    existing = get_equipment_unit(equipment_id, unit_id)
    now = normalize_timestamp(payload.get("updated_at"))
    process_id = str(
        payload.get("process_id")
        or (existing or {}).get("process_id")
        or payload.get("process_step")
        or (existing or {}).get("process_step")
        or "unknown"
    ).lower()
    process_step = str(
        payload.get("process_step") or (existing or {}).get("process_step") or "unknown"
    )
    recipe_id = payload.get("recipe_id", (existing or {}).get("recipe_id"))
    metadata = _configured_context(
        process_id,
        equipment_id=equipment_id,
        unit_id=unit_id,
        recipe_id=str(recipe_id) if recipe_id else None,
    )
    metadata.update(dict((existing or {}).get("metadata", {})))
    incoming_metadata = payload.get("metadata", payload.get("metadata_json"))
    if incoming_metadata is not None:
        decoded = _decode_json(incoming_metadata, {})
        if not isinstance(decoded, dict):
            raise ValueError("equipment metadata must be an object")
        metadata.update(decoded)
    record = {
        "equipment_id": equipment_id,
        "unit_id": unit_id,
        "process_id": process_id,
        "process_step": process_step,
        "unit_type": str(payload.get("unit_type") or (existing or {}).get("unit_type") or "unit"),
        "status": str(payload.get("status") or (existing or {}).get("status") or "available"),
        "recipe_id": recipe_id,
        "current_lot_id": payload.get("lot_id", payload.get("current_lot_id", (existing or {}).get("current_lot_id"))),
        "current_wafer_id": payload.get("wafer_id", payload.get("current_wafer_id", (existing or {}).get("current_wafer_id"))),
        "current_process_run_id": payload.get("process_run_id", payload.get("current_process_run_id", (existing or {}).get("current_process_run_id"))),
        "cycle_id": payload.get("cycle_id", (existing or {}).get("cycle_id")),
        "cycle_index": int(payload.get("cycle_index", (existing or {}).get("cycle_index", payload.get("cycle_index_since_maintenance", 0)))),
        "cycle_index_since_maintenance": int(payload.get("cycle_index_since_maintenance", (existing or {}).get("cycle_index_since_maintenance", 0))),
        "machine_state": payload.get("machine_state", (existing or {}).get("machine_state")),
        "phase": payload.get("phase", (existing or {}).get("phase")),
        "last_observed_at": normalize_timestamp(payload.get("observed_at", payload.get("last_observed_at")), default_now=False) or (existing or {}).get("last_observed_at"),
        "metadata_json": _json(metadata, {}),
        "created_at": str(payload.get("created_at") or (existing or {}).get("created_at") or now),
        "updated_at": str(now),
    }
    with db.connect() as conn:
        _upsert(conn, "equipment_units", record, ("equipment_id", "unit_id"))
    return get_equipment_unit(equipment_id, unit_id) or record


def get_process_run(process_run_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM process_runs WHERE process_run_id = ?", (process_run_id,)
        ).fetchone()
    if row is None:
        return None
    item = _row(row)
    item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    return item


def upsert_process_run(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    process_run_id = str(payload.get("process_run_id") or payload.get("id") or "").strip()
    if not process_run_id:
        raise ValueError("process_run_id is required")
    existing = get_process_run(process_run_id)
    now = normalize_timestamp(payload.get("updated_at"))
    process_id = str(
        payload.get("process_id")
        or (existing or {}).get("process_id")
        or payload.get("process_step")
        or (existing or {}).get("process_step")
        or ""
    ).lower()
    equipment_id = str(payload.get("equipment_id") or (existing or {}).get("equipment_id") or "")
    unit_id = str(payload.get("unit_id") or (existing or {}).get("unit_id") or "")
    recipe_id = str(payload.get("recipe_id") or (existing or {}).get("recipe_id") or "")
    metadata = _configured_context(
        process_id,
        equipment_id=equipment_id or None,
        unit_id=unit_id or None,
        recipe_id=recipe_id or None,
    )
    metadata.update(dict((existing or {}).get("metadata", {})))
    incoming_metadata = payload.get("metadata", payload.get("metadata_json"))
    if incoming_metadata is not None:
        decoded = _decode_json(incoming_metadata, {})
        if not isinstance(decoded, dict):
            raise ValueError("process run metadata must be an object")
        metadata.update(decoded)
    completed_at = normalize_timestamp(payload.get("completed_at"), default_now=False)
    if completed_at is None:
        completed_at = (existing or {}).get("completed_at")
    observed_at = normalize_timestamp(payload.get("observed_at"), default_now=False)
    record = {
        "process_run_id": process_run_id,
        "schema_version": str(payload.get("schema_version") or (existing or {}).get("schema_version") or FAB_SCHEMA_VERSION),
        "lot_id": str(payload.get("lot_id") or (existing or {}).get("lot_id") or ""),
        "wafer_id": str(payload.get("wafer_id") or (existing or {}).get("wafer_id") or ""),
        "process_id": process_id,
        "process_step": str(payload.get("process_step") or (existing or {}).get("process_step") or ""),
        "equipment_id": equipment_id,
        "unit_id": unit_id,
        "unit_type": str(payload.get("unit_type") or (existing or {}).get("unit_type") or "unit"),
        "recipe_id": recipe_id,
        "cycle_id": str(payload.get("cycle_id") or (existing or {}).get("cycle_id") or process_run_id),
        "cycle_index": int(payload.get("cycle_index", (existing or {}).get("cycle_index", payload.get("cycle_index_since_maintenance", 0)))),
        "cycle_index_since_maintenance": int(payload.get("cycle_index_since_maintenance", (existing or {}).get("cycle_index_since_maintenance", 0))),
        "started_at": str(
            normalize_timestamp(payload.get("started_at"), default_now=False)
            or (existing or {}).get("started_at")
            or observed_at
            or now
        ),
        "completed_at": completed_at,
        "status": str(payload.get("status") or (existing or {}).get("status") or "running"),
        "metadata_json": _json(metadata, {}),
        "created_at": str(payload.get("created_at") or (existing or {}).get("created_at") or now),
        "updated_at": str(now),
    }
    for key in ("lot_id", "wafer_id", "process_id", "process_step", "equipment_id", "unit_id", "recipe_id"):
        if not record[key]:
            raise ValueError(f"{key} is required")
    with db.connect() as conn:
        _upsert(conn, "process_runs", record, ("process_run_id",))
    return get_process_run(process_run_id) or record


def _sync_identity(payload: Mapping[str, Any], *, run_status: str = "running") -> None:
    wafer_id = _required(payload, "wafer_id")
    lot_id = _required(payload, "lot_id")
    upsert_wafer(
        {
            "wafer_id": wafer_id,
            "lot_id": lot_id,
            "wafer_index": payload.get("wafer_index", _infer_wafer_index(wafer_id)),
            "status": "processing" if run_status == "running" else "processed",
            "current_process_step": payload.get("process_step"),
        }
    )
    upsert_equipment_unit({**payload, "status": payload.get("equipment_status") or "connected"})
    upsert_process_run({**payload, "status": payload.get("run_status") or run_status})


def _score_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("raw_score", payload.get("anomaly_score"))
    threshold = payload.get("threshold")
    raw_score = float(raw) if raw is not None else None
    threshold_value = float(threshold) if threshold is not None else None
    if raw_score is not None and not math.isfinite(raw_score):
        raise ValueError("raw_score must be finite")
    if threshold_value is not None and not math.isfinite(threshold_value):
        raise ValueError("threshold must be finite")
    if raw_score is not None and threshold_value is not None:
        margin = raw_score - threshold_value
        is_anomaly = margin >= 0
    else:
        margin_value = payload.get("margin")
        margin = float(margin_value) if margin_value is not None else None
        is_anomaly = bool(payload.get("is_anomaly")) if payload.get("is_anomaly") is not None else None
    return {
        "raw_score": raw_score,
        "threshold": threshold_value,
        "margin": margin,
        "is_anomaly": int(is_anomaly) if is_anomaly is not None else None,
    }


def insert_process_telemetry(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    message_id = _required(payload, "message_id")
    observed_at = normalize_timestamp(payload.get("observed_at"))
    phase_progress = float(payload.get("phase_progress", 0.0))
    if not 0.0 <= phase_progress <= 1.0:
        raise ValueError("phase_progress must be between 0 and 1")
    tags = payload.get("tags", payload.get("payload", payload.get("tags_json", {})))
    decoded_tags = _decode_json(tags, {})
    if not isinstance(decoded_tags, dict):
        raise ValueError("tags must be an object")
    decoded_tags = _sanitize_public(decoded_tags)
    context = payload.get("detector_context", payload.get("detector_context_json", {}))
    decoded_context = _decode_json(context, {})
    if not isinstance(decoded_context, dict):
        raise ValueError("detector_context must be an object")
    decoded_context = _sanitize_public(decoded_context)
    _sync_identity(payload)
    record = {
        "id": str(payload.get("id") or f"TEL-{uuid.uuid4().hex}"),
        "schema_version": str(payload.get("schema_version") or FAB_SCHEMA_VERSION),
        "message_id": message_id,
        "process_run_id": _required(payload, "process_run_id"),
        "lot_id": _required(payload, "lot_id"),
        "wafer_id": _required(payload, "wafer_id"),
        "process_id": str(payload.get("process_id") or payload.get("process_step") or "").lower(),
        "process_step": _required(payload, "process_step"),
        "equipment_id": _required(payload, "equipment_id"),
        "unit_id": _required(payload, "unit_id"),
        "unit_type": str(payload.get("unit_type") or "unit"),
        "recipe_id": _required(payload, "recipe_id"),
        "cycle_id": _required(payload, "cycle_id"),
        "cycle_index": int(payload.get("cycle_index", payload.get("cycle_index_since_maintenance", 0))),
        "cycle_index_since_maintenance": int(payload.get("cycle_index_since_maintenance", 0)),
        "machine_state": _required(payload, "machine_state").upper(),
        "phase": _required(payload, "phase").upper(),
        "phase_progress": phase_progress,
        "observed_at": observed_at,
        "tags_json": _json(decoded_tags, {}),
        "detector_context_json": _json(decoded_context, {}),
        "data_quality_status": str(payload.get("data_quality_status") or "VALID").upper(),
        "source": str(payload.get("source") or "fab_direct"),
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        inserted = _insert_ignore(conn, "process_telemetry", record, ("message_id",))
        row = conn.execute(
            "SELECT * FROM process_telemetry WHERE message_id = ?", (message_id,)
        ).fetchone()
    result = _decode_telemetry(row or record)
    result["deduplicated"] = not inserted
    return result


def _decode_telemetry(row: Any) -> dict[str, Any]:
    item = _row(row) if hasattr(row, "keys") else dict(row)
    item["tags"] = _sanitize_public(_decode_json(item.pop("tags_json", None), {}))
    item["detector_context"] = _sanitize_public(
        _decode_json(item.pop("detector_context_json", None), {})
    )
    return item


def insert_metrology_result(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    message_id = _required(payload, "message_id")
    observed_at = normalize_timestamp(payload.get("observed_at"))
    available_at = normalize_timestamp(payload.get("available_at"), default_now=False) or observed_at
    _sync_identity(payload, run_status=str(payload.get("run_status") or "completed"))
    scores = _score_fields(payload)
    record = {
        "id": str(payload.get("id") or f"MET-{uuid.uuid4().hex}"),
        "schema_version": str(payload.get("schema_version") or FAB_SCHEMA_VERSION),
        "message_id": message_id,
        "process_run_id": _required(payload, "process_run_id"),
        "lot_id": _required(payload, "lot_id"),
        "wafer_id": _required(payload, "wafer_id"),
        "process_id": str(payload.get("process_id") or payload.get("process_step") or "").lower(),
        "process_step": _required(payload, "process_step"),
        "equipment_id": payload.get("equipment_id"),
        "unit_id": payload.get("unit_id"),
        "recipe_id": payload.get("recipe_id"),
        "cycle_index": int(payload.get("cycle_index", payload.get("cycle_index_since_maintenance", 0))),
        "modality": str(payload.get("modality") or "metrology"),
        "model_version": payload.get("model_version"),
        **scores,
        "related_tags_json": _json(_sanitize_public(payload.get("related_tags", payload.get("related_tags_json", []))), []),
        "observed_at": observed_at,
        "available_at": available_at,
        "metrics_json": _json(_sanitize_public(payload.get("metrics", payload.get("metrics_json", {}))), {}),
        "quality_targets_json": _json(
            _sanitize_public(payload.get(
                "quality_targets",
                payload.get("quality_target", payload.get("quality_targets_json", {})),
            )),
            {},
        ),
        "measurements_json": _json(
            _sanitize_public(payload.get("measurements", payload.get("measurements_json", []))),
            [],
        ),
        "metrology_context_json": _json(
            _sanitize_public(payload.get(
                "metrology_context",
                payload.get("metrology_context_json", {}),
            )),
            {},
        ),
        "status": str(payload.get("status") or "available"),
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        inserted = _insert_ignore(conn, "metrology_results", record, ("message_id",))
        row = conn.execute(
            "SELECT * FROM metrology_results WHERE message_id = ?", (message_id,)
        ).fetchone()
    result = _decode_result(
        row or record,
        json_fields=(
            "related_tags", "metrics", "quality_targets", "measurements", "metrology_context",
        ),
    )
    result["deduplicated"] = not inserted
    return result


def insert_inspection_asset(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    message_id = _required(payload, "message_id")
    observed_at = normalize_timestamp(payload.get("observed_at"))
    available_at = normalize_timestamp(payload.get("available_at"), default_now=False) or observed_at
    _sync_identity(payload, run_status=str(payload.get("run_status") or "completed"))
    scores = _score_fields(payload)
    record = {
        "id": str(payload.get("id") or f"VIS-{uuid.uuid4().hex}"),
        "schema_version": str(payload.get("schema_version") or FAB_SCHEMA_VERSION),
        "message_id": message_id,
        "process_run_id": _required(payload, "process_run_id"),
        "lot_id": _required(payload, "lot_id"),
        "wafer_id": _required(payload, "wafer_id"),
        "process_id": str(payload.get("process_id") or payload.get("process_step") or "").lower(),
        "process_step": _required(payload, "process_step"),
        "equipment_id": payload.get("equipment_id"),
        "unit_id": payload.get("unit_id"),
        "recipe_id": payload.get("recipe_id"),
        "cycle_index": int(payload.get("cycle_index", payload.get("cycle_index_since_maintenance", 0))),
        "modality": str(payload.get("modality") or "vision"),
        "model_version": payload.get("model_version"),
        **scores,
        "related_tags_json": _json(_sanitize_public(payload.get("related_tags", payload.get("related_tags_json", []))), []),
        "image_key": _required(payload, "image_key"),
        "mask_key": payload.get("mask_key"),
        "bbox_json": _json(payload.get("bbox", payload.get("bbox_json")), None) if payload.get("bbox", payload.get("bbox_json")) is not None else None,
        "defect_type": payload.get("defect_type"),
        "defect_severity": float(payload["defect_severity"]) if payload.get("defect_severity") is not None else None,
        "observed_at": observed_at,
        "available_at": available_at,
        "inspection_modality": payload.get("inspection_modality"),
        "instrument_class": payload.get("instrument_class"),
        "image_type": payload.get("image_type"),
        "sampling_level": payload.get("sampling_level"),
        "inspection_context_json": _json(
            _sanitize_public(payload.get(
                "inspection_context",
                payload.get("inspection_context_json", {}),
            )),
            {},
        ),
        "metadata_json": _json(_sanitize_public(payload.get("metadata", payload.get("metadata_json", {}))), {}),
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        inserted = _insert_ignore(conn, "inspection_assets", record, ("message_id",))
        row = conn.execute(
            "SELECT * FROM inspection_assets WHERE message_id = ?", (message_id,)
        ).fetchone()
    result = _decode_inspection_asset(row or record, include_debug=False)
    result["deduplicated"] = not inserted
    return result


def _decode_result(row: Any, *, json_fields: tuple[str, ...]) -> dict[str, Any]:
    item = _row(row) if hasattr(row, "keys") else dict(row)
    for field in json_fields:
        default = [] if field in {"related_tags", "measurements"} else {}
        item[field] = _decode_json(item.pop(f"{field}_json", None), default)
    if item.get("is_anomaly") is not None:
        item["is_anomaly"] = bool(item["is_anomaly"])
    return _sanitize_public(item)


def _sanitize_public(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_public(item)
            for key, item in value.items()
            if key not in _PRIVATE_GT_KEYS
            and "ground_truth" not in key
            and not key.startswith("injected_")
        }
    if isinstance(value, list):
        return [_sanitize_public(item) for item in value]
    return value


def _decode_inspection_asset(row: Any, *, include_debug: bool) -> dict[str, Any]:
    item = _row(row) if hasattr(row, "keys") else dict(row)
    item["bbox"] = _decode_json(item.pop("bbox_json", None), None)
    item["related_tags"] = _decode_json(item.pop("related_tags_json", None), [])
    item["inspection_context"] = _decode_json(item.pop("inspection_context_json", None), {})
    item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    if item.get("is_anomaly") is not None:
        item["is_anomaly"] = bool(item["is_anomaly"])
    if include_debug:
        return item
    item.pop("mask_key", None)
    item.pop("bbox", None)
    return _sanitize_public(item)


def insert_simulation_fault(value: Any) -> dict[str, Any]:
    """Persist simulator-only ground truth. Inference code must not call readers below."""
    payload = _mapping(value)
    record = {
        "fault_id": _required(payload, "fault_id"),
        "process_run_id": _required(payload, "process_run_id"),
        "fault_type": _required(payload, "fault_type"),
        "fault_scope": str(payload.get("fault_scope") or "process_run"),
        "started_at": normalize_timestamp(payload.get("started_at")),
        "severity": float(payload.get("severity", 1.0)),
        "ground_truth_tags_json": _json(payload.get("ground_truth_tags", payload.get("ground_truth_tags_json", [])), []),
        "ground_truth_defect": payload.get("ground_truth_defect"),
        "metadata_json": _json(payload.get("metadata", payload.get("metadata_json", {})), {}),
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        _upsert(conn, "simulation_faults", record, ("fault_id",))
    return _decode_simulation_fault(record)


def _decode_simulation_fault(row: Any) -> dict[str, Any]:
    item = _row(row) if hasattr(row, "keys") else dict(row)
    item["ground_truth_tags"] = _decode_json(item.pop("ground_truth_tags_json", None), [])
    item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    return item


def list_simulation_faults(process_run_id: str | None = None, *, debug: bool = False) -> list[dict[str, Any]]:
    if not debug:
        raise PermissionError("simulation_faults are available only to explicit debug/evaluation code")
    where = "WHERE process_run_id = ?" if process_run_id else ""
    params: tuple[Any, ...] = (process_run_id,) if process_run_id else ()
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM simulation_faults {where} ORDER BY started_at, fault_id", params
        ).fetchall()
    return [_decode_simulation_fault(row) for row in rows]


def upsert_rca_result(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    process_run_id = _required(payload, "process_run_id")
    candidates = _sanitize_public(
        _decode_json(payload.get("candidate_causes", payload.get("candidate_causes_json", [])), [])
    )
    evidence = _sanitize_public(
        _decode_json(payload.get("evidence", payload.get("evidence_json", {})), {})
    )
    recommended = _sanitize_public(
        _decode_json(payload.get("recommended_checks", payload.get("recommended_checks_json", [])), [])
    )
    top = payload.get("top_candidate")
    if not top and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            top = first.get("candidate") or first.get("cause") or first.get("label")
        else:
            top = first
    version = str(payload.get("model_or_rule_version") or payload.get("rca_version") or "rca-rules-v1")
    record = {
        "id": str(payload.get("id") or f"RCA-{uuid.uuid4().hex}"),
        "wafer_id": _required(payload, "wafer_id"),
        "process_run_id": process_run_id,
        "candidate_causes_json": _json(candidates, []),
        "evidence_json": _json(evidence, {}),
        "recommended_checks_json": _json(recommended, []),
        "top_candidate": str(top) if top is not None else None,
        "confidence": float(payload.get("confidence", 0.0)),
        "overall_risk": float(payload["overall_risk"]) if payload.get("overall_risk") is not None else None,
        "fusion_version": payload.get("fusion_version"),
        "model_or_rule_version": version,
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        _upsert(conn, "rca_results", record, ("process_run_id", "model_or_rule_version"))
    return get_rca_result(process_run_id, version=version) or _decode_rca(record)


def _decode_rca(row: Any) -> dict[str, Any]:
    item = _row(row) if hasattr(row, "keys") else dict(row)
    item["candidate_causes"] = _decode_json(item.pop("candidate_causes_json", None), [])
    item["evidence"] = _decode_json(item.pop("evidence_json", None), {})
    item["recommended_checks"] = _decode_json(item.pop("recommended_checks_json", None), [])
    item["result_label"] = "Candidate root cause"
    return _sanitize_public(item)


def get_rca_result(process_run_id: str, *, version: str | None = None) -> dict[str, Any] | None:
    clause = "AND model_or_rule_version = ?" if version else ""
    params: tuple[Any, ...] = (process_run_id, version) if version else (process_run_id,)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM rca_results WHERE process_run_id = ? "
            f"{clause} ORDER BY created_at DESC LIMIT 1",
            params,
        ).fetchone()
    return _decode_rca(row) if row else None


def claim_message_receipt(
    consumer_id: str,
    message_id: str,
    *,
    topic: str,
    payload_hash: str | None = None,
    received_at: Any | None = None,
) -> bool:
    """Atomically claim a message for one consumer; False means duplicate."""
    record = {
        "consumer_id": str(consumer_id),
        "message_id": str(message_id),
        "topic": str(topic),
        "payload_hash": payload_hash,
        "status": "received",
        "error": None,
        "received_at": normalize_timestamp(received_at),
        "processed_at": None,
    }
    if not record["consumer_id"].strip() or not record["message_id"].strip():
        raise ValueError("consumer_id and message_id are required")
    with db.connect() as conn:
        return _insert_ignore(
            conn,
            "fab_message_receipts",
            record,
            ("consumer_id", "message_id"),
        )


def complete_message_receipt(
    consumer_id: str,
    message_id: str,
    *,
    status: str = "processed",
    error: str | None = None,
    processed_at: Any | None = None,
) -> dict[str, Any] | None:
    normalized = str(status).lower()
    if normalized not in {"processed", "rejected", "error"}:
        raise ValueError("receipt status must be processed, rejected, or error")
    with db.connect() as conn:
        conn.execute(
            "UPDATE fab_message_receipts SET status = ?, error = ?, processed_at = ? "
            "WHERE consumer_id = ? AND message_id = ?",
            (normalized, error, normalize_timestamp(processed_at), consumer_id, message_id),
        )
        row = conn.execute(
            "SELECT * FROM fab_message_receipts WHERE consumer_id = ? AND message_id = ?",
            (consumer_id, message_id),
        ).fetchone()
    return _row(row) if row else None


def claim_message(message_id: str, consumer: str) -> bool:
    """Compatibility interface used by the transport router."""
    return claim_message_receipt(consumer, message_id, topic="fab/router")


def release_message(message_id: str, consumer: str) -> bool:
    """Release a failed claim so QoS-1 redelivery can retry the consumer."""
    with db.connect() as conn:
        cursor = conn.execute(
            "DELETE FROM fab_message_receipts WHERE consumer_id = ? AND message_id = ?",
            (consumer, message_id),
        )
    return int(cursor.rowcount or 0) == 1


def mark_message_processed(message_id: str, consumer: str) -> dict[str, Any] | None:
    return complete_message_receipt(consumer, message_id, status="processed")


def message_processed(
    message_id: str,
    consumer_names: tuple[str, ...] = ("db_writer", "detector"),
) -> bool:
    """Return True only after every requested consumer completed successfully."""
    names = tuple(dict.fromkeys(str(name) for name in consumer_names if str(name).strip()))
    if not names:
        return True
    placeholders = ", ".join(["?"] * len(names))
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT consumer_id, status FROM fab_message_receipts WHERE message_id = ? "
            f"AND consumer_id IN ({placeholders})",
            (message_id, *names),
        ).fetchall()
    statuses = {str(row["consumer_id"]): str(row["status"]) for row in rows}
    return all(statuses.get(name) == "processed" for name in names)


def persist_envelope(value: Any) -> dict[str, Any]:
    """Persist one validated transport envelope through the canonical writer."""
    envelope = _mapping(value)
    identity = envelope.get("identity")
    payload_value = envelope.get("payload", {})
    if not isinstance(identity, Mapping) or not isinstance(payload_value, Mapping):
        raise ValueError("FAB envelope requires identity and payload objects")
    payload = dict(payload_value)
    combined = {
        **payload,
        **dict(identity),
        "schema_version": envelope.get("schema_version") or FAB_SCHEMA_VERSION,
        "message_id": _required(envelope, "message_id"),
        "source": str(envelope.get("source") or "fab_transport"),
    }
    message_type = str(envelope.get("message_type") or "").lower()
    if message_type == "telemetry":
        combined["tags"] = payload.get("tags", payload.get("payload", {}))
        supplied_context = payload.get("detector_context", {})
        combined["detector_context"] = {
            **(dict(supplied_context) if isinstance(supplied_context, Mapping) else {}),
            **{
                key: payload[key]
                for key in (
                    "normalized_values",
                    "feature_vector",
                    "feature_names",
                    "related_tags",
                    "generator_version",
                    "feature_fingerprint",
                    "config_fingerprint",
                )
                if key in payload
            },
        }
        return insert_process_telemetry(combined)
    if message_type == "state":
        _sync_identity(combined, run_status=str(payload.get("run_status") or "running"))
        return upsert_equipment_unit(combined)
    if message_type == "event":
        from app.services import storage  # noqa: PLC0415 - avoid module cycle

        _sync_identity(combined, run_status=str(payload.get("run_status") or "running"))
        event_id = str(payload.get("id") or f"FABEVT-{envelope['message_id']}")
        event = storage.insert_process_event(
            {
                "id": event_id,
                "process_step": identity["process_step"],
                "equipment_id": identity["equipment_id"],
                "recipe_id": identity.get("recipe_id"),
                "lot_id": identity.get("lot_id"),
                "wafer_id": identity.get("wafer_id"),
                "observed_at": normalize_timestamp(identity.get("observed_at")),
                "event_type": payload.get("event_type") or "fab_event",
                "severity": payload.get("severity") or "info",
                "metadata": _sanitize_public(payload.get("metadata", payload)),
                "source": combined["source"],
            }
        )
        identity_columns = {
            "schema_version": combined["schema_version"],
            "message_id": combined["message_id"],
            "process_run_id": identity["process_run_id"],
            "process_id": identity.get("process_id") or str(identity["process_step"]).lower(),
            "unit_id": identity["unit_id"],
            "unit_type": identity.get("unit_type"),
            "cycle_id": identity["cycle_id"],
            "cycle_index": identity.get("cycle_index", identity["cycle_index_since_maintenance"]),
            "cycle_index_since_maintenance": identity["cycle_index_since_maintenance"],
            "machine_state": identity["machine_state"],
            "phase": identity["phase"],
            "phase_progress": identity["phase_progress"],
        }
        with db.connect() as conn:
            available = set(db.table_columns(conn, "process_events"))
            updates = {key: item for key, item in identity_columns.items() if key in available}
            if updates:
                conn.execute(
                    "UPDATE process_events SET "
                    + ", ".join(f"{key} = ?" for key in updates)
                    + " WHERE id = ?",
                    (*updates.values(), event_id),
                )
        return _sanitize_public({**event, **identity_columns})
    detector = payload.get("detector_result")
    if isinstance(detector, Mapping):
        for field in ("modality", "model_version", "raw_score", "threshold", "margin", "is_anomaly", "related_tags"):
            if field in detector:
                combined[field] = detector[field]
    if message_type == "metrology":
        combined["metrics"] = payload.get("metrics", {})
        combined["measurements"] = payload.get("measurements", [])
        combined["metrology_context"] = payload.get("metrology_context", {})
        combined["related_tags"] = combined.get("related_tags", payload.get("related_tags", []))
        return insert_metrology_result(combined)
    if message_type == "inspection":
        synthetic_debug = payload.get("synthetic_debug", {})
        if isinstance(synthetic_debug, Mapping):
            combined["mask_key"] = synthetic_debug.get("mask_key")
            combined["bbox"] = synthetic_debug.get("bbox", payload.get("bbox"))
        supplied_metadata = _decode_json(payload.get("metadata"), {})
        if not isinstance(supplied_metadata, dict):
            raise ValueError("inspection metadata must be an object")
        combined["metadata"] = {
            **supplied_metadata,
            **{
                key: payload[key]
                for key in ("features", "generator_version")
                if payload.get(key) is not None
            },
        }
        return insert_inspection_asset(combined)
    raise ValueError(f"Unsupported FAB message_type: {message_type!r}")


def _latest_identity_for_run(process_run_id: str) -> dict[str, Any]:
    run = get_process_run(process_run_id)
    if run is None:
        raise ValueError(f"Unknown process_run_id: {process_run_id}")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM process_telemetry WHERE process_run_id = ? "
            "ORDER BY observed_at DESC, id DESC LIMIT 1",
            (process_run_id,),
        ).fetchone()
    return {**run, **(_row(row) if row else {})}


def save_detector_result(value: Any) -> dict[str, Any]:
    """Persist a v2 detector result without writing simulator-truth columns."""
    payload = _mapping(value)
    process_run_id = _required(payload, "process_run_id")
    identity = _latest_identity_for_run(process_run_id)
    scores = _score_fields(payload)
    result_id = str(payload.get("id") or f"FABDET-{uuid.uuid4().hex}")
    metadata = _sanitize_public(_decode_json(payload.get("metadata", {}), {}))
    related_tags = _sanitize_public(_decode_json(payload.get("related_tags", []), []))
    record = {
        "id": result_id,
        "schema_version": FAB_SCHEMA_VERSION,
        "message_id": payload.get("message_id"),
        "process_run_id": process_run_id,
        "process_id": str(identity.get("process_id") or identity["process_step"]).lower(),
        "process_step": identity["process_step"],
        "modality": str(payload.get("modality") or "timeseries"),
        "equipment_id": identity["equipment_id"],
        "unit_id": identity["unit_id"],
        "unit_type": identity["unit_type"],
        "recipe_id": identity["recipe_id"],
        "lot_id": identity["lot_id"],
        "wafer_id": identity["wafer_id"],
        "cycle_id": identity["cycle_id"],
        "cycle_index": identity.get("cycle_index", identity["cycle_index_since_maintenance"]),
        "cycle_index_since_maintenance": identity["cycle_index_since_maintenance"],
        "machine_state": identity.get("machine_state"),
        "phase": identity.get("phase"),
        "phase_progress": identity.get("phase_progress"),
        "observed_at": normalize_timestamp(payload.get("observed_at")),
        "model_version": _required(payload, "model_version"),
        "raw_score": scores["raw_score"],
        "threshold": scores["threshold"],
        "margin": scores["margin"],
        "is_anomaly": scores["is_anomaly"],
        "related_tags_json": _json(related_tags, []),
        "metadata_json": _json(
            {
                "context_key": payload.get("context_key"),
                "metadata": metadata,
                "related_tags": related_tags,
            },
            {},
        ),
        "image_key": metadata.get("image_key") if isinstance(metadata, dict) else None,
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        _upsert(conn, "fab_detector_results", record, ("id",))
        message_id = payload.get("message_id")
        if message_id:
            update_values = (
                scores["raw_score"],
                scores["threshold"],
                scores["margin"],
                scores["is_anomaly"],
                payload.get("model_version"),
                _json(related_tags, []),
                message_id,
            )
            for table in ("metrology_results", "inspection_assets"):
                conn.execute(
                    f"UPDATE {table} SET raw_score = ?, threshold = ?, margin = ?, is_anomaly = ?, "
                    "model_version = ?, related_tags_json = ? WHERE message_id = ?",
                    update_values,
                )
    saved = next(
        (item for item in detector_results(process_run_id) if item.get("id") == result_id),
        None,
    )
    return saved or _sanitize_public(record)


def detector_results(process_run_id: str) -> list[dict[str, Any]]:
    return _runtime_detections(process_run_id=process_run_id, limit=10_000)


def _decode_fusion(row: Any) -> dict[str, Any]:
    item = _row(row) if hasattr(row, "keys") else dict(row)
    item["modality_risks"] = _decode_json(item.pop("modality_risks_json", None), {})
    item["weights"] = _decode_json(item.pop("weights_json", None), {})
    item["evidence"] = _decode_json(item.pop("evidence_json", None), {})
    item["is_anomaly"] = bool(item.get("is_anomaly"))
    return _sanitize_public(item)


def save_fusion_result(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    process_run_id = _required(payload, "process_run_id")
    run = get_process_run(process_run_id)
    if run is None:
        raise ValueError(f"Unknown process_run_id: {process_run_id}")
    risk = float(payload.get("overall_risk", payload.get("risk", 0.0)))
    threshold = float(payload.get("threshold", 0.5))
    margin = risk - threshold
    version = str(payload.get("fusion_version") or "fab-fusion-v1")
    record = {
        "id": str(payload.get("id") or f"FUS-{uuid.uuid4().hex}"),
        "process_run_id": process_run_id,
        "wafer_id": str(payload.get("wafer_id") or run["wafer_id"]),
        "fusion_version": version,
        "overall_risk": risk,
        "threshold": threshold,
        "margin": margin,
        "is_anomaly": int(margin >= 0),
        "modality_risks_json": _json(payload.get("modality_risks", payload.get("risks", {})), {}),
        "weights_json": _json(payload.get("weights", {}), {}),
        "evidence_json": _json(_sanitize_public(payload.get("evidence", {})), {}),
        "observed_at": normalize_timestamp(payload.get("observed_at")),
        "created_at": normalize_timestamp(payload.get("created_at")),
    }
    with db.connect() as conn:
        _upsert(conn, "fusion_results", record, ("process_run_id", "fusion_version"))
        row = conn.execute(
            "SELECT * FROM fusion_results WHERE process_run_id = ? AND fusion_version = ?",
            (process_run_id, version),
        ).fetchone()
    return _decode_fusion(row or record)


def fusion_for_run(process_run_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM fusion_results WHERE process_run_id = ? ORDER BY created_at DESC LIMIT 1",
            (process_run_id,),
        ).fetchone()
    return _decode_fusion(row) if row else None


def save_rca_result(value: Any) -> dict[str, Any]:
    return upsert_rca_result(value)


def rca_for_run(process_run_id: str) -> dict[str, Any] | None:
    return get_rca_result(process_run_id)


def save_simulation_fault(value: Any) -> dict[str, Any]:
    return insert_simulation_fault(value)


def _metrology_for_run(process_run_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM metrology_results WHERE process_run_id = ? ORDER BY observed_at, id",
            (process_run_id,),
        ).fetchall()
    return [
        _decode_result(
            row,
            json_fields=(
                "related_tags", "metrics", "quality_targets", "measurements", "metrology_context",
            ),
        )
        for row in rows
    ]


def _inspections_for_run(process_run_id: str, *, include_debug: bool = False) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM inspection_assets WHERE process_run_id = ? ORDER BY observed_at, id",
            (process_run_id,),
        ).fetchall()
    return [_decode_inspection_asset(row, include_debug=include_debug) for row in rows]


def save_inspection_debug(
    process_run_id: str,
    *,
    mask_key: str | None,
    bbox: list[int] | None,
    debug: bool = False,
) -> dict[str, Any] | None:
    """Attach simulator-only mask evidence outside the public transport path."""
    if not debug:
        raise PermissionError("inspection ground truth requires explicit synthetic debug mode")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM inspection_assets WHERE process_run_id = ? "
            "ORDER BY observed_at DESC, id DESC LIMIT 1",
            (process_run_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE inspection_assets SET mask_key = ?, bbox_json = ? WHERE id = ?",
            (mask_key, _json(bbox, None) if bbox is not None else None, row["id"]),
        )
    rows = _inspections_for_run(process_run_id, include_debug=True)
    return rows[-1] if rows else None


def _telemetry_for_run(process_run_id: str, *, limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 10_000))
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM process_telemetry WHERE process_run_id = ? "
            "ORDER BY observed_at DESC, id DESC LIMIT ?",
            (process_run_id, safe_limit),
        ).fetchall()
    return list(reversed([_decode_telemetry(row) for row in rows]))


def _runtime_detections(
    *,
    process_run_id: str | None = None,
    process_step: str | None = None,
    equipment_id: str | None = None,
    unit_id: str | None = None,
    recipe_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    with db.connect() as conn:
        columns = set(db.table_columns(conn, "fab_detector_results"))
        if not columns:
            return []
        filters: list[str] = []
        params: list[Any] = []
        if process_run_id:
            if "process_run_id" not in columns:
                return []
            filters.append("process_run_id = ?")
            params.append(process_run_id)
        if process_step:
            field = "process_id" if "process_id" in columns else "process_step"
            filters.append(f"LOWER({field}) = LOWER(?)")
            params.append(process_step)
        for field, value in (("equipment_id", equipment_id), ("unit_id", unit_id), ("recipe_id", recipe_id)):
            if value and field in columns:
                filters.append(f"{field} = ?")
                params.append(value)
        safe_columns = [
            name
            for name in (
                "id",
                "schema_version",
                "message_id",
                "process_run_id",
                "process_id",
                "process_step",
                "modality",
                "equipment_id",
                "unit_id",
                "unit_type",
                "recipe_id",
                "lot_id",
                "wafer_id",
                "cycle_id",
                "cycle_index",
                "cycle_index_since_maintenance",
                "machine_state",
                "phase",
                "phase_progress",
                "observed_at",
                "model_version",
                "raw_score",
                "threshold",
                "margin",
                "is_anomaly",
                "related_tags_json",
                "metadata_json",
                "image_key",
                "created_at",
            )
            if name in columns
        ]
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(max(1, min(int(limit), 10_000)))
        rows = conn.execute(
            f"SELECT {', '.join(safe_columns)} FROM fab_detector_results {where} "
            "ORDER BY observed_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in reversed(rows):
        item = _row(row)
        if item.get("raw_score") is not None and item.get("threshold") is not None:
            # PostgreSQL REAL columns round each value independently.  Rebuild
            # the derived fields from the stored score pair so the public
            # detector contract remains exact after a database round trip.
            item["margin"] = float(item["raw_score"]) - float(item["threshold"])
            item["is_anomaly"] = item["margin"] >= 0.0
        elif item.get("is_anomaly") is not None:
            item["is_anomaly"] = bool(item["is_anomaly"])
        item["related_tags"] = _decode_json(item.pop("related_tags_json", None), [])
        if "metadata_json" in item:
            item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
        results.append(_sanitize_public(item))
    return results


def _events_for_run(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    with db.connect() as conn:
        columns = set(db.table_columns(conn, "process_events"))
        if not columns:
            return []
        if "process_run_id" in columns:
            rows = conn.execute(
                "SELECT * FROM process_events WHERE process_run_id = ? ORDER BY observed_at, id",
                (run["process_run_id"],),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM process_events WHERE lot_id = ? AND wafer_id = ? "
                "AND equipment_id = ? ORDER BY observed_at, id",
                (run["lot_id"], run["wafer_id"], run["equipment_id"]),
            ).fetchall()
    items = []
    for row in rows:
        item = _row(row)
        item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
        items.append(_sanitize_public(item))
    return items


def process_run_detail(
    process_run_id: str,
    *,
    telemetry_limit: int = 2000,
    include_debug: bool = False,
) -> dict[str, Any] | None:
    run = get_process_run(process_run_id)
    if run is None:
        return None
    equipment_context = _configured_context(
        str(run.get("process_id") or run.get("process_step") or "").lower(),
        equipment_id=str(run.get("equipment_id") or "") or None,
        unit_id=str(run.get("unit_id") or "") or None,
        recipe_id=str(run.get("recipe_id") or "") or None,
    )
    result = {
        **run,
        "equipment_context": equipment_context,
        "post_process_plan": {
            "metrology": equipment_context.get("metrology_plan", []),
            "inspection": equipment_context.get("inspection_plan", {}),
        },
        "telemetry": _telemetry_for_run(process_run_id, limit=telemetry_limit),
        "detections": _runtime_detections(process_run_id=process_run_id, limit=telemetry_limit),
        "metrology": _metrology_for_run(process_run_id),
        "inspections": _inspections_for_run(process_run_id, include_debug=include_debug),
        "events": _events_for_run(run),
        "fusion": fusion_for_run(process_run_id),
        "rca": get_rca_result(process_run_id),
    }
    if include_debug:
        result["synthetic_debug"] = {
            "simulation_faults": list_simulation_faults(process_run_id, debug=True),
        }
    return result


def _lot_row(lot_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        if not _table_exists(conn, "lots"):
            return None
        row = conn.execute("SELECT * FROM lots WHERE lot_id = ?", (lot_id,)).fetchone()
    if row is None:
        return None
    item = _row(row)
    if "metadata_json" in item:
        item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
    return item


def wafer_trace(
    wafer_id: str,
    *,
    telemetry_limit_per_run: int = 500,
    include_debug: bool = False,
) -> dict[str, Any] | None:
    wafer = get_wafer(wafer_id)
    if wafer is None:
        return None
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT process_run_id FROM process_runs WHERE wafer_id = ? ORDER BY started_at, process_run_id",
            (wafer_id,),
        ).fetchall()
    runs = [
        detail
        for detail in (
            process_run_detail(
                str(row["process_run_id"]),
                telemetry_limit=telemetry_limit_per_run,
                include_debug=include_debug,
            )
            for row in rows
        )
        if detail is not None
    ]
    runs.sort(
        key=lambda item: (
            _ROUTE_ORDER.get(str(item.get("process_step", "")).lower(), 99),
            str(item.get("started_at") or ""),
        )
    )
    return {
        "wafer": wafer,
        "lot": _lot_row(str(wafer["lot_id"])),
        "route": [str(item["process_step"]) for item in runs],
        "process_runs": runs,
        "inspection_count": sum(len(item["inspections"]) for item in runs),
        "generated_at": utc_now(),
        "disclaimer": "Synthetic proxy, not Fab-calibrated control limits.",
    }


def lot_trace(lot_id: str) -> dict[str, Any] | None:
    lot = _lot_row(lot_id)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM wafers WHERE lot_id = ? ORDER BY wafer_index, wafer_id", (lot_id,)
        ).fetchall()
        run_rows = conn.execute(
            "SELECT * FROM process_runs WHERE lot_id = ? ORDER BY started_at, process_run_id", (lot_id,)
        ).fetchall()
    if lot is None and not rows and not run_rows:
        return None
    wafers = []
    for row in rows:
        item = _row(row)
        item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
        wafers.append(item)
    runs = []
    for row in run_rows:
        item = _row(row)
        item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
        runs.append(item)
    return {"lot": lot, "wafers": wafers, "process_runs": runs}


def enrich_lots(lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in lots:
        trace = lot_trace(str(item["lot_id"]))
        if trace is None:
            enriched.append(item)
            continue
        enriched.append(
            {
                **item,
                "fab": {
                    "wafer_count": len(trace["wafers"]),
                    "process_run_count": len(trace["process_runs"]),
                    "current_wafers": [
                        wafer["wafer_id"]
                        for wafer in trace["wafers"]
                        if str(wafer.get("status", "")).lower() in {"queued", "processing", "hold"}
                    ],
                },
            }
        )
    return enriched


def equipment_overview(process_step: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE LOWER(COALESCE(process_id, process_step)) = LOWER(?)" if process_step else ""
    params: tuple[Any, ...] = (process_step,) if process_step else ()
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM equipment_units {where} ORDER BY process_step, equipment_id, unit_id",
            params,
        ).fetchall()
    items: list[dict[str, Any]] = []
    runtime_keys: set[tuple[str, str]] = set()
    for row in rows:
        item = _row(row)
        item["metadata"] = _decode_json(item.pop("metadata_json", None), {})
        process_id = str(item.get("process_id") or item.get("process_step") or "").lower()
        context = _configured_context(
            process_id,
            equipment_id=str(item.get("equipment_id") or "") or None,
            unit_id=str(item.get("unit_id") or "") or None,
            recipe_id=str(item.get("recipe_id") or "") or None,
        )
        runtime_keys.add((str(item["equipment_id"]), str(item["unit_id"])))
        item.update({
            "equipment_context": context,
            "equipment_class": context.get("equipment_class"),
            "unit_class": context.get("unit_class"),
            "recipe_class": context.get("recipe_class"),
            "sensor_tags": context.get("sensor_tags", {}),
            "metrology_plan": context.get("metrology_plan", []),
            "inspection_plan": context.get("inspection_plan", {}),
            "runtime_supported": bool(context.get("runtime_supported")),
            "has_runtime_data": True,
        })
        item["connected"] = True
        item["connection"] = "runtime"
        items.append(item)

    for definition in process_definitions(process_step):
        for equipment in definition["equipment"]:
            for unit in equipment["units"]:
                key = (str(equipment["equipment_id"]), str(unit["unit_id"]))
                if key in runtime_keys:
                    continue
                context = _configured_context(
                    str(definition["process_id"]),
                    equipment_id=key[0],
                    unit_id=key[1],
                )
                items.append({
                    "equipment_id": key[0],
                    "unit_id": key[1],
                    "process_id": definition["process_id"],
                    "process_step": definition["process_step"],
                    "unit_type": definition["unit_type"],
                    "status": "not_connected",
                    "recipe_id": None,
                    "current_lot_id": None,
                    "current_wafer_id": None,
                    "current_process_run_id": None,
                    "cycle_id": None,
                    "cycle_index": 0,
                    "cycle_index_since_maintenance": 0,
                    "machine_state": "NOT_CONNECTED",
                    "phase": None,
                    "last_observed_at": None,
                    "metadata": context,
                    "equipment_context": context,
                    "equipment_class": context.get("equipment_class"),
                    "unit_class": context.get("unit_class"),
                    "recipe_class": None,
                    "sensor_tags": context.get("sensor_tags", {}),
                    "metrology_plan": context.get("metrology_plan", []),
                    "inspection_plan": context.get("inspection_plan", {}),
                    "runtime_supported": True,
                    "has_runtime_data": False,
                    "connected": False,
                    "connection": "configured",
                })
    return items


def process_live(
    process_id: str,
    *,
    equipment_id: str | None = None,
    unit_id: str | None = None,
    recipe_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 2000))
    filters = ["LOWER(COALESCE(process_id, process_step)) = LOWER(?)"]
    params: list[Any] = [process_id]
    for field, value in (("equipment_id", equipment_id), ("unit_id", unit_id), ("recipe_id", recipe_id)):
        if value:
            filters.append(f"{field} = ?")
            params.append(value)
    inventory = equipment_overview(process_id)
    run_where = " AND ".join(filters)
    with db.connect() as conn:
        active_run_row = conn.execute(
            "SELECT process_run_id FROM process_runs WHERE " + run_where +
            " AND LOWER(status) IN ('running', 'processing', 'active') "
            "ORDER BY started_at DESC, updated_at DESC LIMIT 1",
            tuple(params),
        ).fetchone()
        latest_run_row = active_run_row or conn.execute(
            "SELECT process_run_id FROM process_runs WHERE " + run_where +
            " ORDER BY COALESCE(completed_at, started_at) DESC, started_at DESC, updated_at DESC LIMIT 1",
            tuple(params),
        ).fetchone()
        current_run_id = str(latest_run_row["process_run_id"]) if latest_run_row else None
        if current_run_id:
            rows = conn.execute(
                "SELECT * FROM process_telemetry WHERE process_run_id = ? "
                "ORDER BY observed_at DESC, id DESC LIMIT ?",
                (current_run_id, safe_limit),
            ).fetchall()
        else:
            rows = []
    telemetry = list(reversed([_decode_telemetry(row) for row in rows]))
    detections = _runtime_detections(
        process_run_id=current_run_id,
        limit=safe_limit,
    ) if current_run_id else []
    phase_boundaries: list[dict[str, Any]] = []
    prior_phase: str | None = None
    for sample in telemetry:
        phase = str(sample.get("phase") or "")
        if phase and phase != prior_phase:
            phase_boundaries.append(
                {"phase": phase, "observed_at": sample["observed_at"], "process_run_id": sample["process_run_id"]}
            )
        prior_phase = phase
    current = telemetry[-1] if telemetry else None
    current_run = get_process_run(current_run_id) if current_run_id else None
    events = _events_for_run(current_run) if current_run else []
    normalized_status = str((current_run or {}).get("status") or "").lower()
    run_state = (
        "running"
        if normalized_status in {"running", "processing", "active"}
        else "latest_completed"
        if normalized_status in {"completed", "complete", "processed"}
        else "latest_stored"
        if current_run
        else "no_run"
    )
    runtime_supported = any(bool(item.get("runtime_supported")) for item in inventory)
    has_runtime_data = bool(current_run or telemetry)
    return {
        "process_id": process_id.lower(),
        "runtime_supported": runtime_supported,
        "connected": any(bool(item.get("has_runtime_data")) for item in inventory),
        "receiving": run_state == "running",
        "has_runtime_data": has_runtime_data,
        "run_state": run_state,
        "filters": {"equipment_id": equipment_id, "unit_id": unit_id, "recipe_id": recipe_id},
        "identity": current or current_run,
        "process_run": current_run,
        "current": current,
        "telemetry": telemetry,
        "detections": detections,
        "events": events,
        "phase_boundaries": phase_boundaries,
        "metrology": _metrology_for_run(current_run_id) if current_run_id else [],
        "inspections": _inspections_for_run(current_run_id, include_debug=False) if current_run_id else [],
        "equipment_context": (current_run or {}).get("metadata", {}),
        "rca": get_rca_result(current_run_id) if current_run_id else None,
        "equipment": inventory,
        "generated_at": utc_now(),
    }


def fab_metrics() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=60)).isoformat(timespec="seconds")
    table_names = (
        "wafers",
        "process_runs",
        "equipment_units",
        "process_telemetry",
        "fab_detector_results",
        "metrology_results",
        "inspection_assets",
        "fusion_results",
        "rca_results",
        "fab_message_receipts",
    )
    with db.connect() as conn:
        counts = {
            name: int(conn.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()["count"])
            for name in table_names
        }
        latest = conn.execute(
            "SELECT observed_at, created_at FROM process_telemetry ORDER BY observed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        recent_receipts = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM fab_message_receipts WHERE received_at >= ?", (recent,)
            ).fetchone()["count"]
        )
        receipt_errors = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM fab_message_receipts WHERE status IN ('error', 'rejected')"
            ).fetchone()["count"]
        )
        recent_writes = 0
        for table, timestamp_column in (
            ("process_telemetry", "created_at"),
            ("metrology_results", "created_at"),
            ("inspection_assets", "created_at"),
            ("rca_results", "created_at"),
        ):
            recent_writes += int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE {timestamp_column} >= ?", (recent,)
                ).fetchone()["count"]
            )
        runtime_columns = set(db.table_columns(conn, "fab_detector_results"))
        anomaly_rate: float | None = None
        model_versions: list[dict[str, Any]] = []
        detection_latency_ms: float | None = None
        if runtime_columns:
            aggregate = conn.execute(
                "SELECT COUNT(*) AS count, SUM(CASE WHEN is_anomaly = 1 THEN 1 ELSE 0 END) AS anomalies "
                "FROM fab_detector_results"
            ).fetchone()
            total = int(aggregate["count"] or 0)
            anomaly_rate = float(aggregate["anomalies"] or 0) / total if total else None
            if {"process_id", "modality", "model_version", "observed_at"}.issubset(runtime_columns):
                version_rows = conn.execute(
                    "SELECT process_id, modality, model_version, observed_at FROM fab_detector_results "
                    "ORDER BY observed_at DESC"
                ).fetchall()
                seen: set[tuple[str, str]] = set()
                for row in version_rows:
                    key = (str(row["process_id"]), str(row["modality"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    model_versions.append(_row(row))
            if {"observed_at", "created_at"}.issubset(runtime_columns):
                latency_rows = conn.execute(
                    "SELECT observed_at, created_at FROM fab_detector_results "
                    "ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
                latencies = []
                for row in latency_rows:
                    observed = normalize_timestamp(row["observed_at"], default_now=False)
                    created = normalize_timestamp(row["created_at"], default_now=False)
                    if observed and created:
                        latencies.append(
                            max(0.0, (datetime.fromisoformat(created) - datetime.fromisoformat(observed)).total_seconds() * 1000)
                        )
                detection_latency_ms = sum(latencies) / len(latencies) if latencies else None
    latest_at = str(latest["observed_at"]) if latest else None
    ingestion_lag = (
        max(0.0, (now - datetime.fromisoformat(str(normalize_timestamp(latest_at, default_now=False)))).total_seconds())
        if latest_at
        else None
    )
    transport: dict[str, Any] = {}
    try:
        from app.services.fab_mqtt import transport_metrics  # noqa: PLC0415

        transport = transport_metrics.snapshot()
    except (ImportError, AttributeError):
        transport = {}
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "database_backend": db.backend(),
        "row_counts": counts,
        "mqtt_messages_per_second": float(transport.get("messages_per_second", recent_receipts / 60.0)),
        "mqtt_reconnect_count": int(transport.get("reconnects", 0)),
        "db_writes_per_second": recent_writes / 60.0,
        "db_error_count": receipt_errors,
        "latest_telemetry_timestamp": latest_at,
        "ingestion_lag_seconds": ingestion_lag,
        "detection_latency_ms": transport.get("detection_latency_ms_avg", detection_latency_ms),
        "anomaly_rate": anomaly_rate,
        "model_versions": model_versions,
        "transport": transport,
        "data_source": "fab_v2_persistence",
    }


def enrich_overview(base: dict[str, Any]) -> dict[str, Any]:
    """Overlay connected v2 runtimes while preserving all legacy demo processes."""
    result = dict(base)
    units = equipment_overview()
    metrics = fab_metrics()
    by_process: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        by_process.setdefault(str(unit.get("process_id") or unit["process_step"]).lower(), []).append(unit)
    statuses = []
    for item in base.get("process_status", []):
        current = dict(item)
        process_id = str(current.get("process_id", "")).lower()
        connected_units = by_process.get(process_id, [])
        if connected_units:
            states = {str(unit.get("machine_state") or unit.get("status") or "").lower() for unit in connected_units}
            current.update(
                {
                    "status": "critical" if "alarm" in states else ("warning" if states & {"hold", "warning"} else "normal"),
                    "data_source": "fab_v2_runtime",
                    "connection": "runtime",
                    "equipment_count": len({unit["equipment_id"] for unit in connected_units}),
                    "unit_count": len(connected_units),
                }
            )
        statuses.append(current)
    result["process_status"] = statuses
    result["fab_v2"] = {
        "connected_processes": sorted(by_process),
        "equipment_count": len({unit["equipment_id"] for unit in units}),
        "unit_count": len(units),
        "current_wafers": sorted({str(unit["current_wafer_id"]) for unit in units if unit.get("current_wafer_id")}),
        "row_counts": metrics["row_counts"],
        "latest_telemetry_timestamp": metrics["latest_telemetry_timestamp"],
    }
    if units:
        base_metrics = dict(result.get("metrics", {}))
        base_metrics["running_equipment"] = {
            "value": len({unit["equipment_id"] for unit in units if str(unit.get("machine_state", "")).upper() == "RUNNING"}),
            "data_source": "fab_v2_runtime",
        }
        result["metrics"] = base_metrics
    return result

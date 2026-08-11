"""Persistence helpers for the Chamber telemetry/model lifecycle."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services import db


TELEMETRY_COLUMNS = (
    "id",
    "lot_id",
    "wafer_id",
    "equipment_id",
    "observed_at",
    "recipe_id",
    "machine_state",
    "use_time_total",
    "use_time_since_clean",
    "wafer_count_since_clean",
    "seasoning_level",
    "chamber_pressure",
    "chamber_pressure_setpoint",
    "pressure_delta",
    "source_rf_power",
    "source_rf_setpoint",
    "source_rf_delta",
    "bias_rf_power",
    "bias_rf_setpoint",
    "bias_rf_delta",
    "gas_1_name",
    "gas_1_flow",
    "gas_1_setpoint",
    "gas_2_name",
    "gas_2_flow",
    "gas_2_setpoint",
    "gas_3_name",
    "gas_3_flow",
    "gas_3_setpoint",
    "total_gas_flow",
    "total_gas_setpoint",
    "gas_flow_delta",
    "gas_ratio",
    "gas_ratio_delta",
    "chamber_temperature",
    "chamber_temperature_setpoint",
    "temperature_delta",
    "esc_temperature",
    "esc_temperature_setpoint",
    "esc_temperature_delta",
    "gas_json",
    "resistance",
    "quality",
    "data_quality_status",
    "data_quality_issues_json",
    "is_synthetic",
    "synthetic_anomaly_type",
    "created_at",
)

PREDICTION_COLUMNS = (
    "id",
    "telemetry_id",
    "equipment_id",
    "observed_at",
    "model_version",
    "actual_resistance",
    "expected_resistance",
    "residual",
    "abs_error",
    "anomaly_score",
    "threshold",
    "is_anomaly",
    "created_at",
)

MODEL_COLUMNS = (
    "version",
    "stage",
    "model_type",
    "artifact_path",
    "mae",
    "rmse",
    "threshold",
    "training_rows",
    "feature_names_json",
    "feature_importance_json",
    "training_started_at",
    "training_ended_at",
    "training_cutoff_at",
    "registered_at",
    "promoted_at",
    "metadata_json",
)

DETECTION_COLUMNS = (
    "id",
    "prediction_id",
    "detector_name",
    "score",
    "threshold",
    "is_anomaly",
    "context_key",
    "observed_at",
    "metadata_json",
    "created_at",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    return db.connect()


def init_chamber_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chamber_telemetry (
                id TEXT PRIMARY KEY,
                lot_id TEXT,
                wafer_id TEXT,
                equipment_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                recipe_id TEXT NOT NULL,
                machine_state TEXT NOT NULL,
                use_time_total REAL NOT NULL,
                use_time_since_clean REAL NOT NULL,
                wafer_count_since_clean INTEGER NOT NULL,
                seasoning_level REAL NOT NULL,
                chamber_pressure REAL NOT NULL,
                chamber_pressure_setpoint REAL NOT NULL,
                pressure_delta REAL NOT NULL,
                source_rf_power REAL NOT NULL,
                source_rf_setpoint REAL NOT NULL,
                source_rf_delta REAL NOT NULL,
                bias_rf_power REAL NOT NULL,
                bias_rf_setpoint REAL NOT NULL,
                bias_rf_delta REAL NOT NULL,
                gas_1_name TEXT NOT NULL,
                gas_1_flow REAL NOT NULL,
                gas_1_setpoint REAL NOT NULL,
                gas_2_name TEXT NOT NULL,
                gas_2_flow REAL NOT NULL,
                gas_2_setpoint REAL NOT NULL,
                gas_3_name TEXT NOT NULL,
                gas_3_flow REAL NOT NULL,
                gas_3_setpoint REAL NOT NULL,
                total_gas_flow REAL NOT NULL,
                total_gas_setpoint REAL NOT NULL,
                gas_flow_delta REAL NOT NULL,
                gas_ratio REAL NOT NULL,
                gas_ratio_delta REAL NOT NULL,
                chamber_temperature REAL NOT NULL,
                chamber_temperature_setpoint REAL NOT NULL,
                temperature_delta REAL NOT NULL,
                esc_temperature REAL NOT NULL,
                esc_temperature_setpoint REAL NOT NULL,
                esc_temperature_delta REAL NOT NULL,
                gas_json TEXT NOT NULL,
                resistance REAL NOT NULL,
                quality TEXT NOT NULL,
                data_quality_status TEXT NOT NULL DEFAULT 'VALID',
                data_quality_issues_json TEXT NOT NULL DEFAULT '[]',
                is_synthetic INTEGER NOT NULL,
                synthetic_anomaly_type TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chamber_predictions (
                id TEXT PRIMARY KEY,
                telemetry_id TEXT NOT NULL UNIQUE,
                equipment_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                model_version TEXT NOT NULL,
                actual_resistance REAL NOT NULL,
                expected_resistance REAL NOT NULL,
                residual REAL NOT NULL,
                abs_error REAL NOT NULL,
                anomaly_score REAL NOT NULL,
                threshold REAL NOT NULL,
                is_anomaly INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chamber_model_registry (
                version TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                model_type TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                mae REAL NOT NULL,
                rmse REAL NOT NULL,
                threshold REAL NOT NULL,
                training_rows INTEGER NOT NULL,
                feature_names_json TEXT NOT NULL,
                feature_importance_json TEXT NOT NULL,
                training_started_at TEXT NOT NULL,
                training_ended_at TEXT NOT NULL,
                training_cutoff_at TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                promoted_at TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS anomaly_detections (
                id TEXT PRIMARY KEY,
                prediction_id TEXT NOT NULL,
                detector_name TEXT NOT NULL,
                score REAL NOT NULL,
                threshold REAL NOT NULL,
                is_anomaly INTEGER NOT NULL,
                context_key TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chamber_telemetry_equipment_time
            ON chamber_telemetry(equipment_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_chamber_telemetry_training
            ON chamber_telemetry(machine_state, quality, observed_at);
            CREATE INDEX IF NOT EXISTS idx_chamber_predictions_equipment_time
            ON chamber_predictions(equipment_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_chamber_predictions_model_time
            ON chamber_predictions(model_version, observed_at);
            CREATE INDEX IF NOT EXISTS idx_anomaly_detection_prediction
            ON anomaly_detections(prediction_id, detector_name);
            CREATE INDEX IF NOT EXISTS idx_anomaly_detection_time
            ON anomaly_detections(detector_name, observed_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chamber_one_production
            ON chamber_model_registry(stage) WHERE stage = 'Production';
            """
        )
        _ensure_column(conn, "chamber_telemetry", "lot_id", "TEXT")
        _ensure_column(conn, "chamber_telemetry", "wafer_id", "TEXT")
        _ensure_column(conn, "chamber_telemetry", "data_quality_status", "TEXT NOT NULL DEFAULT 'VALID'")
        _ensure_column(conn, "chamber_telemetry", "data_quality_issues_json", "TEXT NOT NULL DEFAULT '[]'")
        # Additive indexes that reference migrated columns must be created only
        # after older SQLite databases receive those columns.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chamber_telemetry_lot_time "
            "ON chamber_telemetry(lot_id, observed_at)"
        )


def _row_to_dict(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def insert_telemetry(sample: dict[str, Any]) -> dict[str, Any]:
    record = {**sample}
    record["id"] = str(record.get("id") or uuid.uuid4())
    record["created_at"] = str(record.get("created_at") or utc_now())
    record["is_synthetic"] = int(bool(record.get("is_synthetic")))
    record["data_quality_status"] = str(record.get("data_quality_status") or "VALID")
    issues = record.get("data_quality_issues", record.get("data_quality_issues_json", []))
    record["data_quality_issues_json"] = issues if isinstance(issues, str) else json.dumps(issues, ensure_ascii=False)
    placeholders = ", ".join(["?"] * len(TELEMETRY_COLUMNS))
    with connect() as conn:
        conn.execute(
            f"INSERT INTO chamber_telemetry ({', '.join(TELEMETRY_COLUMNS)}) VALUES ({placeholders})",
            tuple(record.get(column) for column in TELEMETRY_COLUMNS),
        )
    return record


def insert_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    record = {**prediction}
    record["id"] = str(record.get("id") or uuid.uuid4())
    record["created_at"] = str(record.get("created_at") or utc_now())
    record["is_anomaly"] = int(bool(record.get("is_anomaly")))
    placeholders = ", ".join(["?"] * len(PREDICTION_COLUMNS))
    with connect() as conn:
        conn.execute(
            f"INSERT INTO chamber_predictions ({', '.join(PREDICTION_COLUMNS)}) VALUES ({placeholders})",
            tuple(record.get(column) for column in PREDICTION_COLUMNS),
        )
    return record


def insert_anomaly_detection(detection: dict[str, Any]) -> dict[str, Any]:
    record = {**detection}
    record["id"] = str(record.get("id") or uuid.uuid4())
    record["created_at"] = str(record.get("created_at") or utc_now())
    record["is_anomaly"] = int(bool(record.get("is_anomaly")))
    metadata = record.get("metadata", record.get("metadata_json", {}))
    record["metadata_json"] = metadata if isinstance(metadata, str) else json.dumps(metadata, ensure_ascii=False)
    placeholders = ", ".join(["?"] * len(DETECTION_COLUMNS))
    with connect() as conn:
        conn.execute(
            f"INSERT INTO anomaly_detections ({', '.join(DETECTION_COLUMNS)}) VALUES ({placeholders})",
            tuple(record.get(column) for column in DETECTION_COLUMNS),
        )
    return _decode_detection(record)


def _decode_detection(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json", None) or "{}")
    item["is_anomaly"] = bool(item.get("is_anomaly"))
    return item


def detection_rows(*, prediction_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 2000))
    where = "WHERE prediction_id = ?" if prediction_id else ""
    params = (prediction_id, safe_limit) if prediction_id else (safe_limit,)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(DETECTION_COLUMNS)} FROM anomaly_detections {where} "
            "ORDER BY observed_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    return [_decode_detection(row) for row in rows]


def detector_summary() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT detector_name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN is_anomaly = 1 THEN 1 ELSE 0 END) AS anomaly_count,
                   AVG(score) AS average_score,
                   AVG(threshold) AS average_threshold
            FROM anomaly_detections
            GROUP BY detector_name
            ORDER BY detector_name
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def data_quality_summary() -> dict[str, int]:
    counts = {"VALID": 0, "WARNING": 0, "REJECT": 0}
    with connect() as conn:
        rows = conn.execute(
            "SELECT data_quality_status, COUNT(*) AS count "
            "FROM chamber_telemetry GROUP BY data_quality_status"
        ).fetchall()
    for row in rows:
        counts[str(row["data_quality_status"])] = int(row["count"])
    return counts


def clean_training_rows(*, since: str | None = None) -> list[dict[str, Any]]:
    where = [
        "t.quality = 'good'",
        "t.machine_state = 'running'",
        "t.data_quality_status = 'VALID'",
        "(t.is_synthetic = 0 OR t.synthetic_anomaly_type IS NULL)",
        "(p.id IS NULL OR p.is_anomaly = 0)",
    ]
    params: list[Any] = []
    if since:
        where.append("t.observed_at > ?")
        params.append(since)
    sql = (
        f"SELECT {', '.join(f't.{column}' for column in TELEMETRY_COLUMNS)} "
        "FROM chamber_telemetry t "
        "LEFT JOIN chamber_predictions p ON p.telemetry_id = t.id "
        f"WHERE {' AND '.join(where)} ORDER BY t.observed_at ASC, t.id ASC"
    )
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def telemetry_rows(equipment_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 2000))
    where = "WHERE equipment_id = ?" if equipment_id else ""
    params = (equipment_id, limit) if equipment_id else (limit,)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(TELEMETRY_COLUMNS)} FROM chamber_telemetry {where} "
            "ORDER BY observed_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    return list(reversed([_row_to_dict(row) for row in rows]))


def prediction_rows(equipment_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 2000))
    where = "WHERE p.equipment_id = ?" if equipment_id else ""
    params = (equipment_id, limit) if equipment_id else (limit,)
    telemetry_fields = (
        "recipe_id",
        "machine_state",
        "pressure_delta",
        "source_rf_delta",
        "bias_rf_delta",
        "gas_flow_delta",
        "gas_ratio_delta",
        "temperature_delta",
        "esc_temperature_delta",
        "chamber_pressure",
        "source_rf_power",
        "bias_rf_power",
        "total_gas_flow",
        "chamber_temperature",
        "esc_temperature",
        "use_time_since_clean",
        "wafer_count_since_clean",
    )
    select = [f"p.{column}" for column in PREDICTION_COLUMNS] + [f"t.{column}" for column in telemetry_fields]
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(select)} FROM chamber_predictions p "
            "JOIN chamber_telemetry t ON t.id = p.telemetry_id "
            f"{where} ORDER BY p.observed_at DESC, p.id DESC LIMIT ?",
            params,
        ).fetchall()
    return list(reversed([_row_to_dict(row) for row in rows]))


def equipment_summary() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.equipment_id, t.observed_at, t.recipe_id, t.machine_state,
                   t.resistance, t.quality, t.synthetic_anomaly_type,
                   p.expected_resistance, p.residual, p.is_anomaly, p.model_version
            FROM chamber_telemetry t
            LEFT JOIN chamber_predictions p ON p.telemetry_id = t.id
            WHERE t.observed_at = (
                SELECT MAX(t2.observed_at) FROM chamber_telemetry t2
                WHERE t2.equipment_id = t.equipment_id
            )
            ORDER BY t.equipment_id
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_rows(table: str) -> int:
    allowed = {"chamber_telemetry", "chamber_predictions", "chamber_model_registry", "anomaly_detections"}
    if table not in allowed:
        raise ValueError(f"Unsupported Chamber table: {table}")
    with connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


def recent_abs_errors(limit: int) -> list[float]:
    limit = max(1, min(int(limit), 2000))
    with connect() as conn:
        rows = conn.execute(
            "SELECT abs_error FROM chamber_predictions ORDER BY observed_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [float(row["abs_error"]) for row in rows]


def register_model(model: dict[str, Any]) -> dict[str, Any]:
    record = {**model}
    record["registered_at"] = str(record.get("registered_at") or utc_now())
    record["feature_names_json"] = json.dumps(record.pop("feature_names", []), ensure_ascii=False)
    record["feature_importance_json"] = json.dumps(record.pop("feature_importance", []), ensure_ascii=False)
    record["metadata_json"] = json.dumps(record.pop("metadata", {}), ensure_ascii=False)
    placeholders = ", ".join(["?"] * len(MODEL_COLUMNS))
    with connect() as conn:
        conn.execute(
            f"INSERT INTO chamber_model_registry ({', '.join(MODEL_COLUMNS)}) VALUES ({placeholders})",
            tuple(record.get(column) for column in MODEL_COLUMNS),
        )
    return get_model(str(record["version"])) or record


def _decode_model(row) -> dict[str, Any]:
    item = _row_to_dict(row)
    item["feature_names"] = json.loads(item.pop("feature_names_json") or "[]")
    item["feature_importance"] = json.loads(item.pop("feature_importance_json") or "[]")
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


def list_models() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(MODEL_COLUMNS)} FROM chamber_model_registry ORDER BY registered_at DESC"
        ).fetchall()
    return [_decode_model(row) for row in rows]


def get_model(version: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(MODEL_COLUMNS)} FROM chamber_model_registry WHERE version = ?",
            (version,),
        ).fetchone()
    return _decode_model(row) if row else None


def production_model() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(MODEL_COLUMNS)} FROM chamber_model_registry WHERE stage = 'Production'"
        ).fetchone()
    return _decode_model(row) if row else None


def staging_model() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(MODEL_COLUMNS)} FROM chamber_model_registry "
            "WHERE stage = 'Staging' ORDER BY registered_at DESC LIMIT 1"
        ).fetchone()
    return _decode_model(row) if row else None


def promote_model(version: str, promoted_at: str | None = None) -> dict[str, Any] | None:
    timestamp = promoted_at or utc_now()
    with connect() as conn:
        target = conn.execute(
            "SELECT version FROM chamber_model_registry WHERE version = ?", (version,)
        ).fetchone()
        if target is None:
            return None
        conn.execute("UPDATE chamber_model_registry SET stage = 'Archived' WHERE stage = 'Production' AND version <> ?", (version,))
        conn.execute(
            "UPDATE chamber_model_registry SET stage = 'Production', promoted_at = ? WHERE version = ?",
            (timestamp, version),
        )
    return get_model(version)


def next_model_version() -> str:
    highest = 0
    for model in list_models():
        version = str(model["version"])
        if version.startswith("resistance-v"):
            try:
                highest = max(highest, int(version.removeprefix("resistance-v")))
            except ValueError:
                continue
    return f"resistance-v{highest + 1}"


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    if column not in db.table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.services import chamber_storage, db, storage
from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 against the dedicated WaferGuard PostgreSQL",
)


def test_postgres_schema_and_cross_layer_crud() -> None:
    assert db.backend() == "postgres"
    assert db.validate_connection() == {"backend": "postgres", "status": "connected"}
    storage.init_db()
    storage.init_db()  # schema and additive migrations must remain idempotent

    suffix = uuid.uuid4().hex[:10]
    lot_id = f"PG-LOT-{suffix}"
    telemetry_id = f"PG-TEL-{suffix}"
    prediction_id = f"PG-PRED-{suffix}"
    detection_id = f"PG-DET-{suffix}"
    process_event_id = f"PG-PROC-{suffix}"
    inspection_id = f"PG-INS-{suffix}"
    rag_id = f"PG-RAG-{suffix}"
    model_version = f"pg-integration-{suffix}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        lot = storage.upsert_lot(
            {
                "lot_id": lot_id,
                "product_id": "PG-PRODUCT",
                "recipe_route": "Etch:ETCH_OXIDE_A",
                "status": "running",
                "started_at": now,
                "current_process_step": "Etch",
                "wafer_count": 25,
                "metadata": {"integration": True},
            }
        )
        assert lot["metadata"]["integration"] is True

        config = load_chamber_config()
        config["lot"]["startup_samples"] = 0
        sample = EtchTelemetryGenerator(config, equipment_count=1, seed=9).next_sample(
            "ETCH-001", machine_state="running"
        )
        sample.update(
            id=telemetry_id,
            lot_id=lot_id,
            wafer_id="W01",
            data_quality_status="VALID",
            data_quality_issues=[],
        )
        telemetry = chamber_storage.insert_telemetry(sample)
        assert telemetry["lot_id"] == lot_id

        prediction = chamber_storage.insert_prediction(
            {
                "id": prediction_id,
                "telemetry_id": telemetry_id,
                "equipment_id": "ETCH-001",
                "observed_at": sample["observed_at"],
                "model_version": model_version,
                "actual_resistance": sample["resistance"],
                "expected_resistance": sample["resistance"] - 0.5,
                "residual": 0.5,
                "abs_error": 0.5,
                "anomaly_score": 2.0,
                "threshold": 0.25,
                "is_anomaly": True,
            }
        )
        detection = chamber_storage.insert_anomaly_detection(
            {
                "id": detection_id,
                "prediction_id": prediction_id,
                "detector_name": "robust_mad",
                "score": 0.5,
                "threshold": 0.25,
                "is_anomaly": True,
                "context_key": "equipment_recipe:ETCH-001|ETCH_OXIDE_A",
                "observed_at": sample["observed_at"],
                "metadata": {"primary": True},
            }
        )
        assert prediction["id"] == prediction_id
        assert detection["metadata"]["primary"] is True

        storage.insert_process_event(
            {
                "id": process_event_id,
                "process_step": "Etch",
                "equipment_id": "ETCH-001",
                "recipe_id": "ETCH_OXIDE_A",
                "lot_id": lot_id,
                "wafer_id": "W01",
                "observed_at": sample["observed_at"],
                "event_type": "resistance_residual",
                "severity": "warning",
                "source": "postgres_integration",
                "metadata": {"prediction_id": prediction_id},
            }
        )
        assert storage.list_process_events(lot_id=lot_id)[0]["id"] == process_event_id

        storage.insert_inspection(
            {
                "id": inspection_id,
                "lot_id": lot_id,
                "wafer_id": "W01",
                "line_id": "PG-LINE",
                "equipment_id": "ETCH-001",
                "process_step": "Etch",
                "recipe_id": "ETCH_OXIDE_A",
                "image_source": "synthetic_wafer",
                "proxy_dataset": "integration",
                "proxy_status": "test",
                "defect_type": "Edge-Loc",
                "confidence": 0.9,
                "risk_score": 0.7,
                "risk_level": "High",
                "hotspot_ratio": 0.1,
                "image_url": "images/test.png",
                "heatmap_url": "images/test-heat.png",
                "overlay_url": "images/test-overlay.png",
                "roi_url": "images/test-roi.png",
                "roi_bbox": [1, 2, 3, 4],
                "report": "PostgreSQL integration",
                "cases": [],
                "process_context": {"lot_id": lot_id},
                "metrology": {"cd_nm": 35.0},
                "action_card": {"possible_causes": []},
                "model_version": "integration",
                "status": "review_required",
                "created_at": now,
            }
        )
        assert storage.get_inspection(inspection_id)["lot_id"] == lot_id

        storage.upsert_rag_document(
            rag_id,
            "PostgreSQL integration evidence",
            "Edge-Loc",
            [0.1, 0.2, 0.3],
            {"source": "integration"},
        )
        assert storage.query_rag([0.1, 0.2, 0.3], k=1)[0]["id"] == rag_id

        chamber_storage.register_model(
            {
                "version": model_version,
                "stage": "Staging",
                "model_type": "integration.fixture",
                "artifact_path": "runtime/models/chamber/integration.joblib",
                "mae": 0.2,
                "rmse": 0.3,
                "threshold": 0.5,
                "training_rows": 10,
                "feature_names": ["pressure_delta"],
                "feature_importance": [{"feature": "pressure_delta", "importance": 1.0}],
                "training_started_at": now,
                "training_ended_at": now,
                "training_cutoff_at": now,
                "registered_at": now,
                "promoted_at": None,
                "metadata": {"integration": True},
            }
        )
        assert chamber_storage.get_model(model_version)["metadata"]["integration"] is True
        assert storage.browse_table("lots", limit=5) is not None
        assert storage.db_overview()["backend"] == "postgres"
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM anomaly_detections WHERE id = ?", (detection_id,))
            conn.execute("DELETE FROM chamber_predictions WHERE id = ?", (prediction_id,))
            conn.execute("DELETE FROM chamber_telemetry WHERE id = ?", (telemetry_id,))
            conn.execute("DELETE FROM chamber_model_registry WHERE version = ?", (model_version,))
            conn.execute("DELETE FROM process_events WHERE id = ?", (process_event_id,))
            conn.execute("DELETE FROM inspections WHERE id = ?", (inspection_id,))
            conn.execute("DELETE FROM rag_documents WHERE id = ?", (rag_id,))
            conn.execute("DELETE FROM lots WHERE lot_id = ?", (lot_id,))

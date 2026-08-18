from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import config, db, fab_analysis, fab_storage, storage


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    original_thread_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    if "app.main" in sys.modules:
        main = importlib.reload(sys.modules["app.main"])
    else:
        main = importlib.import_module("app.main")
    monkeypatch.setattr(threading.Thread, "start", original_thread_start)
    return TestClient(main.app)


def _seed_api_run() -> None:
    storage.upsert_lot(
        {
            "lot_id": "LOT-API-001",
            "product_id": "PRODUCT-API",
            "recipe_route": "CMP",
            "status": "running",
            "started_at": "2026-08-18T01:00:00+00:00",
            "current_process_step": "CMP",
            "wafer_count": 1,
        }
    )
    identity = {
        "lot_id": "LOT-API-001",
        "wafer_id": "LOT-API-001-W01",
        "process_run_id": "RUN-API-CMP-001",
        "process_id": "cmp",
        "process_step": "CMP",
        "equipment_id": "CMP_EQ_01",
        "unit_id": "PLATEN_A",
        "unit_type": "platen",
        "recipe_id": "CMP_RECIPE_A",
        "cycle_id": "CMP_EQ_01-9",
        "cycle_index": 9,
        "cycle_index_since_maintenance": 9,
        "machine_state": "RUNNING",
        "phase": "POLISH",
        "phase_progress": 0.6,
        "observed_at": "2026-08-18T01:01:00Z",
    }
    fab_storage.persist_envelope(
        {
            "schema_version": "fab.v2",
            "message_id": "MSG-API-TEL-001",
            "message_type": "telemetry",
            "identity": identity,
            "payload": {"tags": {"slurry_flow": 170.0, "motor_current": 9.1}},
        }
    )
    fab_storage.save_fusion_result(
        {
            "process_run_id": "RUN-API-CMP-001",
            "fusion_version": "fusion-api-v1",
            "overall_risk": 0.76,
            "modality_risks": {"timeseries": 0.76},
        }
    )
    fab_storage.save_rca_result(
        {
            "id": "RCA-API-001",
            "wafer_id": "LOT-API-001-W01",
            "process_run_id": "RUN-API-CMP-001",
            "candidate_causes": [
                {
                    "candidate": "slurry delivery degradation",
                    "score": 0.8,
                    "evidence": ["slurry_flow"],
                }
            ],
            "evidence": {"tags": ["slurry_flow"]},
            "confidence": 0.8,
            "overall_risk": 0.76,
            "model_or_rule_version": "rca-api-v1",
        }
    )


def test_fab_read_routes_and_backward_compatible_extensions(api_client: TestClient) -> None:
    _seed_api_run()

    health = api_client.get("/health")
    assert health.status_code == 200
    assert health.json()["fab"]["row_counts"]["process_telemetry"] == 1

    equipment = api_client.get("/api/v1/fab/equipment?process_id=cmp")
    assert equipment.status_code == 200
    assert equipment.json()[0]["unit_id"] == "PLATEN_A"

    live = api_client.get(
        "/api/v1/process/cmp/live?equipment_id=CMP_EQ_01&unit_id=PLATEN_A"
    )
    assert live.status_code == 200
    assert live.json()["current"]["phase"] == "POLISH"
    assert live.json()["telemetry"][0]["tags"]["slurry_flow"] == 170.0

    trace = api_client.get("/api/v1/fab/wafers/LOT-API-001-W01/trace")
    assert trace.status_code == 200
    assert trace.json()["route"] == ["CMP"]

    detail = api_client.get("/api/v1/fab/process-runs/RUN-API-CMP-001")
    assert detail.status_code == 200
    assert detail.json()["fusion"]["fusion_version"] == "fusion-api-v1"

    rca = api_client.get("/api/v1/fab/rca/RUN-API-CMP-001")
    assert rca.status_code == 200
    assert rca.json()["result_label"] == "Candidate root cause"
    assert rca.json()["candidate_causes"][0]["candidate"] == "slurry delivery degradation"

    metrics = api_client.get("/api/v1/fab/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["latest_telemetry_timestamp"].endswith("+00:00")

    lots = api_client.get("/api/v1/lots")
    assert lots.status_code == 200
    assert lots.json()[0]["fab"]["process_run_count"] == 1

    quality = api_client.get("/api/v1/quality/lots/LOT-API-001")
    assert quality.status_code == 200
    assert quality.json()["fab_trace"]["process_runs"][0]["process_run_id"] == "RUN-API-CMP-001"


def test_fab_read_routes_return_not_found(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/fab/wafers/UNKNOWN/trace").status_code == 404
    assert api_client.get("/api/v1/fab/process-runs/UNKNOWN").status_code == 404
    assert api_client.get("/api/v1/fab/rca/UNKNOWN").status_code == 404


def test_fab_analysis_endpoint_returns_local_summary(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fab_analysis,
        "latest_dashboard_summary",
        lambda: {
            "schema_version": "fab-analysis.v1",
            "status": "ready",
            "dataset": {"rows": 40, "columns": 6},
            "feature_importance": [{"feature": "slurry_flow", "importance": 0.7}],
        },
    )
    response = api_client.get("/api/v1/fab/analysis/latest")
    assert response.status_code == 200
    assert response.json()["dataset"]["rows"] == 40

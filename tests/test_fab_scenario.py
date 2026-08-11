from __future__ import annotations

import copy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services import chamber_training, config, db, object_store, process_ops, storage
from app.services.chamber_generator import load_chamber_config
from app.services.fab_scenario import FabScenarioOrchestrator


@pytest.fixture()
def scenario_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "waferguard.db"
    model_root = tmp_path / "runtime" / "models" / "chamber"
    model_root.mkdir(parents=True)
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(chamber_training, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(chamber_training, "CHAMBER_MODEL_DIR", model_root)
    monkeypatch.setattr(object_store, "OUTPUT_DIR", tmp_path / "outputs")
    storage.init_db()

    cfg = copy.deepcopy(load_chamber_config())
    cfg["lot"].update(
        {
            "wafer_count": 25,
            "startup_samples": 1,
            "idle_samples_between_lots": 1,
            "wafer_process_samples": {"min": 3, "max": 3},
            "hold_probability_per_wafer": 0,
            "initial_transient_samples": 3,
        }
    )
    cfg["maintenance"]["wafers_between_clean"] = 10_000
    cfg["model"].update(
        {
            "bootstrap_min_rows": 24,
            "min_training_rows": 20,
            "context_threshold_min_rows": 6,
            "group_metric_min_rows": 2,
        }
    )
    cfg["anomalies"]["rf_drift_per_sample"] = 30.0
    return cfg


def test_rf_drift_scenario_links_same_lot_process_inspection_and_structured_agent(scenario_env):
    result = FabScenarioOrchestrator(scenario_env, seed=17, inspection_lag_minutes=5).run()

    assert result["target_wafers"] == ["W13", "W14", "W15"]
    assert len(result["inspections"]) == 3
    assert result["production_model"] is not None
    assert result["anomaly_events"]
    assert {item["lot_id"] for item in result["inspections"]} == {result["lot_id"]}

    for inspection in result["inspections"]:
        process_context = inspection["process_context"]
        related = process_context["related_process_events"]
        assert related
        assert all(event["lot_id"] == result["lot_id"] for event in related)
        assert datetime.fromisoformat(inspection["created_at"]) > datetime.fromisoformat(
            str(process_context["process_timestamp"])
        )
        final_action = str(inspection["agent_final_action"])
        for heading in (
            "## Observation",
            "## Possible Causes",
            "## Evidence",
            "## Recommended Checks",
            "## Recommended Action",
            "## Confidence / Uncertainty",
        ):
            assert heading in final_action
        assert "인과" in final_action


def test_same_time_event_from_unrelated_lot_is_excluded(scenario_env):
    observed = datetime.fromisoformat("2026-08-11T02:00:00+00:00")
    storage.insert_process_event(
        {
            "id": "PROC-SAME-LOT",
            "process_step": "Etch",
            "equipment_id": "ETCH-001",
            "recipe_id": "ETCH_OXIDE_A",
            "lot_id": "LOT-A",
            "wafer_id": "W13",
            "observed_at": observed.isoformat(),
            "event_type": "rf_power_drift",
            "severity": "warning",
            "source": "chamber_synthetic_runtime",
            "metadata": {},
        }
    )
    storage.insert_process_event(
        {
            "id": "PROC-OTHER-LOT",
            "process_step": "Etch",
            "equipment_id": "ETCH-001",
            "recipe_id": "ETCH_OXIDE_A",
            "lot_id": "LOT-B",
            "wafer_id": "W13",
            "observed_at": observed.isoformat(),
            "event_type": "rf_power_drift",
            "severity": "warning",
            "source": "chamber_synthetic_runtime",
            "metadata": {},
        }
    )

    context = process_ops.inspection_operating_context(
        lot_id="LOT-A",
        wafer_id="W13",
        equipment_id="ETCH-001",
        recipe_id="ETCH_OXIDE_A",
        inspection_at=(observed + timedelta(minutes=5)).isoformat(),
    )

    assert [event["id"] for event in context["related_process_events"]] == ["PROC-SAME-LOT"]

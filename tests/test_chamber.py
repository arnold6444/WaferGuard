from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services import chamber_storage, chamber_training, config, db, storage
from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config
from app.services.chamber_runtime import ChamberRuntime


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def chamber_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "waferguard.db"
    model_root = tmp_path / "runtime" / "models" / "chamber"
    model_root.mkdir(parents=True)
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(chamber_training, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(chamber_training, "CHAMBER_MODEL_DIR", model_root)
    storage.init_db()
    cfg = copy.deepcopy(load_chamber_config())
    cfg["maintenance"]["wafers_between_clean"] = 10_000
    cfg["model"].update(
        {
            "bootstrap_min_rows": 80,
            "min_training_rows": 60,
            "retrain_min_new_rows": 30,
            "recent_prediction_count": 20,
        }
    )
    return cfg, model_root


def test_generator_is_reproducible_continuous_and_equipment_specific():
    cfg = load_chamber_config()
    left = EtchTelemetryGenerator(cfg, equipment_count=2, seed=77, start_time=START)
    right = EtchTelemetryGenerator(cfg, equipment_count=2, seed=77, start_time=START)

    left_rows = [left.next_sample("ETCH-001") for _ in range(8)]
    right_rows = [right.next_sample("ETCH-001") for _ in range(8)]
    assert left_rows == right_rows
    assert all(abs(row["pressure_delta"]) < 1.5 for row in left_rows)
    assert max(abs(b["chamber_pressure"] - a["chamber_pressure"]) for a, b in zip(left_rows, left_rows[1:])) < 0.6

    equipment_two = left.next_sample("ETCH-002")
    assert equipment_two["equipment_id"] == "ETCH-002"
    assert equipment_two["chamber_pressure"] != left_rows[-1]["chamber_pressure"]
    assert equipment_two["chamber_pressure_setpoint"] != left_rows[-1]["chamber_pressure_setpoint"]

    resumed = EtchTelemetryGenerator(cfg, equipment_count=2, seed=77, start_time=START)
    resumed.restore_from_rows([left_rows[-1], equipment_two])
    resumed_row = resumed.next_sample("ETCH-001")
    assert resumed_row["observed_at"] > left_rows[-1]["observed_at"]
    assert resumed_row["use_time_total"] > left_rows[-1]["use_time_total"]


def test_cleaning_resets_since_clean_counters_and_all_states_are_supported():
    cfg = copy.deepcopy(load_chamber_config())
    cfg["maintenance"].update({"wafers_between_clean": 3, "cleaning_samples": 1})
    generator = EtchTelemetryGenerator(cfg, equipment_count=1, seed=4, start_time=START)

    for _ in range(3):
        running = generator.next_sample()
        assert running["machine_state"] == "running"
    cleaning = generator.next_sample()
    assert cleaning["machine_state"] == "cleaning"
    assert cleaning["use_time_since_clean"] == 0
    assert cleaning["wafer_count_since_clean"] == 0

    for state in ("idle", "running", "cleaning", "maintenance", "alarm"):
        row = generator.next_sample(machine_state=state)
        assert row["machine_state"] == state
    assert generator.next_sample(machine_state="alarm")["quality"] == "bad"


def test_gas_flow_drift_moves_actual_not_recipe_setpoint_and_changes_resistance():
    cfg = load_chamber_config()
    generator = EtchTelemetryGenerator(cfg, equipment_count=1, seed=9, start_time=START)
    normal = [generator.next_sample() for _ in range(18)]
    drifted = [generator.next_sample(anomaly="gas_flow_drift") for _ in range(10)]

    assert {row["gas_1_setpoint"] for row in drifted} == {normal[-1]["gas_1_setpoint"]}
    assert abs(drifted[-1]["gas_1_flow"] - drifted[-1]["gas_1_setpoint"]) > abs(normal[-1]["gas_1_flow"] - normal[-1]["gas_1_setpoint"])
    assert abs(drifted[-1]["resistance"] - normal[-1]["resistance"]) > 0.5
    with pytest.raises(ValueError, match="Unsupported anomaly"):
        generator.next_sample(anomaly="made_up_fault")


def test_multivariate_model_beats_use_time_baseline_and_accepts_unknown_categories(chamber_env):
    cfg, model_root = chamber_env
    generator = EtchTelemetryGenerator(cfg, equipment_count=4, seed=21, start_time=START)
    rows = []
    for _ in range(105):
        rows.extend(generator.next_sample(equipment_id) for equipment_id in generator.equipment_ids)

    trained = chamber_training.train_model(
        rows,
        version="resistance-v1",
        stage="Production",
        config=cfg,
    )
    candidate = trained["metadata"]["candidate_holdout"]
    baseline = trained["metadata"]["use_time_only_holdout"]
    assert candidate["mae"] < baseline["mae"]
    assert candidate["rmse"] < baseline["rmse"]
    assert (model_root / "resistance-v1.joblib").is_file()

    bundle = chamber_training.load_artifact(trained)
    unknown = dict(rows[-1], equipment_id="ETCH-NEW", recipe_id="ETCH_NEW_RECIPE")
    prediction = chamber_training.predict_rows(bundle, [unknown])
    assert len(prediction) == 1


def test_runtime_bootstrap_filters_state_and_detects_rf_cause_anomaly(chamber_env):
    cfg, _ = chamber_env
    generator = EtchTelemetryGenerator(cfg, equipment_count=2, seed=42, start_time=START)
    runtime = ChamberRuntime(cfg)

    skipped = runtime.process_sample(generator.next_sample("ETCH-001", machine_state="idle"))
    assert skipped["state"] == "SKIPPED"
    for _ in range(55):
        for equipment_id in generator.equipment_ids:
            runtime.process_sample(generator.next_sample(equipment_id))

    production = chamber_storage.production_model()
    assert production is not None
    assert production["stage"] == "Production"
    assert chamber_storage.count_rows("chamber_predictions") > 0

    anomaly_results = [
        runtime.process_sample(generator.next_sample("ETCH-001", anomaly="rf_power_drift"))
        for _ in range(14)
    ]
    predictions = [result["prediction"] for result in anomaly_results if result["prediction"]]
    assert predictions[-1]["abs_error"] > predictions[0]["abs_error"]
    assert any(prediction["is_anomaly"] for prediction in predictions)


def test_retrain_staging_promotion_and_artifact_guard(chamber_env):
    cfg, _ = chamber_env
    generator = EtchTelemetryGenerator(cfg, equipment_count=3, seed=101, start_time=START)
    runtime = ChamberRuntime(cfg)
    for _ in range(75):
        for equipment_id in generator.equipment_ids:
            runtime.process_sample(generator.next_sample(equipment_id))

    old_production = chamber_storage.production_model()
    result = runtime.retrain(force=True, trigger_type="manual")
    assert result["accepted"] is True, result
    candidate = result["candidate"]
    assert candidate["stage"] == "Staging"

    promoted = runtime.promote(candidate["version"])
    assert promoted["stage"] == "Production"
    assert chamber_storage.get_model(old_production["version"])["stage"] == "Archived"
    next_result = runtime.process_sample(generator.next_sample("ETCH-001"))
    assert next_result["prediction"]["model_version"] == candidate["version"]

    missing = dict(candidate)
    missing.update(
        version="resistance-v999",
        stage="Staging",
        artifact_path="runtime/models/chamber/missing.joblib",
    )
    missing.pop("feature_names_json", None)
    missing.pop("feature_importance_json", None)
    missing.pop("metadata_json", None)
    chamber_storage.register_model(missing)
    with pytest.raises(FileNotFoundError, match="Cannot promote without artifact"):
        runtime.promote("resistance-v999")

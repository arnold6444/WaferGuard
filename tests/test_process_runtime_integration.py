from __future__ import annotations

from pathlib import Path

import pytest

from app.services import db, process_mlops, process_runtime, storage


@pytest.fixture()
def process_runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "waferguard.db"
    model_root = tmp_path / "runtime" / "models" / "process"
    model_root.mkdir(parents=True)
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(process_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(process_runtime, "MODEL_DIR", model_root)
    storage.init_db()
    return database, model_root


def test_runtime_persists_detection_and_uses_promoted_artifact(process_runtime_env):
    _, model_root = process_runtime_env

    first = process_runtime.simulate_sample(
        "deposition",
        modality="timeseries",
        anomaly="pressure_drift",
        equipment_id="DEP-01",
        lot_id="LOT-DEP-001",
        wafer_id="W01",
        seed=23,
    )
    first_row = first["results"]["timeseries"]
    assert first_row["ground_truth"] is True
    assert first_row["is_anomaly"] is True
    assert first_row["event"] is not None
    assert first_row["event"]["source"] == "process_multimodal_synthetic_runtime"
    assert "chamber_pressure" in first_row["event"]["metadata"]["related_tags"]

    production = process_runtime.production_model("deposition", "timeseries")
    assert production is not None
    assert (model_root / f"{production['version']}.joblib").is_file()

    candidate_result = process_mlops.train_candidate("deposition", "timeseries", force=True)
    candidate = candidate_result["candidate"]
    assert candidate["stage"] == "Staging"
    promoted = process_mlops.promote_candidate("deposition", "timeseries", candidate["version"])
    assert promoted["version"] == candidate["version"]
    assert promoted["stage"] == "Production"

    second = process_runtime.simulate_sample(
        "deposition",
        modality="timeseries",
        equipment_id="DEP-01",
        lot_id="LOT-DEP-001",
        wafer_id="W02",
        seed=24,
    )
    assert second["results"]["timeseries"]["model_version"] == candidate["version"]

    with db.connect() as conn:
        samples = conn.execute("SELECT COUNT(*) AS c FROM process_runtime_samples").fetchone()["c"]
        events = conn.execute(
            "SELECT COUNT(*) AS c FROM process_events WHERE source = ?",
            ("process_multimodal_synthetic_runtime",),
        ).fetchone()["c"]
    assert samples == 2
    assert events >= 1

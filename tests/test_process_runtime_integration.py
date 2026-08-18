from __future__ import annotations

from pathlib import Path

import pytest

from app.services import db, process_mlops, process_runtime, storage
from app.services.process_temporal import (
    TEMPORAL_GENERATOR_VERSION,
    TemporalProcessGenerator,
    simulate_temporal_sample,
)


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
    generator = TemporalProcessGenerator("deposition", seed=23)
    [generator.next() for _ in range(20)]

    first = simulate_temporal_sample(
        "deposition",
        generator,
        modality="timeseries",
        anomaly="pressure_drift",
        equipment_id="DEP-01",
        lot_id="LOT-DEP-001",
        wafer_id="W01",
    )
    first_row = first["results"]["timeseries"]
    assert first_row["ground_truth"] is True
    assert first_row["margin"] == pytest.approx(first_row["anomaly_score"] - first_row["threshold"])
    assert first_row["phase"]
    assert first_row["window_size"] == 8

    production = process_runtime.production_model("deposition", "timeseries")
    assert production is not None
    assert production["metadata"]["generator_version"] == TEMPORAL_GENERATOR_VERSION
    artifact = model_root / "deposition" / "timeseries" / f"{production['version']}.joblib"
    assert artifact.is_file()

    candidate_result = process_mlops.train_candidate("deposition", "timeseries", force=True)
    candidate = candidate_result["candidate"]
    assert candidate["stage"] == "Staging"
    assert candidate["metadata"]["generator_version"] == TEMPORAL_GENERATOR_VERSION

    if candidate_result["promotion_recommended"]:
        promoted = process_mlops.promote_candidate("deposition", "timeseries", candidate["version"])
        assert promoted["version"] == candidate["version"]
        expected_version = candidate["version"]
    else:
        expected_version = production["version"]

    second = simulate_temporal_sample(
        "deposition",
        generator,
        modality="timeseries",
        equipment_id="DEP-01",
        lot_id="LOT-DEP-001",
        wafer_id="W02",
    )
    assert second["results"]["timeseries"]["model_version"] == expected_version

    with db.connect() as conn:
        samples = conn.execute("SELECT COUNT(*) AS c FROM process_runtime_samples").fetchone()["c"]
    assert samples == 2

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np

from app.services import config, db, storage
from app.services.fab_models import register_fab_candidate
from app.services.fab_generator import load_fab_config
from app.services.fab_schema import config_fingerprint


class _DecisionModel:
    def decision_function(self, matrix):
        return np.zeros(len(matrix), dtype=float)


def test_external_fab_artifact_registers_as_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "models.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    storage.init_db()
    artifact = tmp_path / "cmp-fab-v1.joblib"
    feature_names = ["z:slurry_flow", "context:phase_progress"]
    feature_fingerprint = config_fingerprint(
        {"feature_contract": "fab-context-v1", "feature_names": feature_names}
    )
    fab_config_fingerprint = config_fingerprint(load_fab_config())
    joblib.dump(
        {
            "process_id": "cmp",
            "modality": "timeseries",
            "version": "cmp-fab-v1",
            "model": _DecisionModel(),
            "model_name": "contract-double",
            "threshold": 0.4,
            "feature_contract": "fab-context-v1",
            "feature_names": feature_names,
            "feature_fingerprint": feature_fingerprint,
            "config_fingerprint": fab_config_fingerprint,
        },
        artifact,
    )

    registered = register_fab_candidate(
        artifact,
        process_id="cmp",
        metrics={"precision": 0.9, "recall": 0.8, "f2": 0.82, "false_positive_rate": 0.03},
    )

    assert registered["stage"] == "Staging"
    assert registered["metadata"]["feature_fingerprint"] == feature_fingerprint
    assert registered["metadata"]["config_fingerprint"] == fab_config_fingerprint

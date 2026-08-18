"""Registration boundary for externally trained FAB-compatible candidates."""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any, Mapping

import joblib

from app.services import db, process_runtime
from app.services.config import ROOT_DIR
from app.services.fab_generator import load_fab_config
from app.services.fab_schema import FAB_FEATURE_CONTRACT, config_fingerprint


_REQUIRED_METRICS = ("precision", "recall", "f2", "false_positive_rate")


def register_fab_candidate(
    artifact_path: str | Path,
    *,
    process_id: str,
    metrics: Mapping[str, float],
) -> dict[str, Any]:
    """Validate and register an externally trained time-series artifact as Staging.

    Training stays outside the runtime.  This function supplies the stable
    hand-off: a candidate cannot enter the registry without the same feature
    and config fingerprints enforced by FAB inference.
    """
    path = Path(artifact_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"FAB artifact not found: {path}")
    bundle = joblib.load(path)
    if not isinstance(bundle, Mapping):
        raise ValueError("FAB artifact bundle must be a mapping")

    normalized_process = str(process_id).strip().lower()
    if bundle.get("process_id") != normalized_process:
        raise ValueError("artifact process_id does not match registration request")
    if bundle.get("modality") != "timeseries":
        raise ValueError("FAB candidate registration currently supports timeseries artifacts")
    if bundle.get("feature_contract") != FAB_FEATURE_CONTRACT:
        raise ValueError(f"feature_contract must be {FAB_FEATURE_CONTRACT}")
    for field in ("version", "model", "feature_names", "feature_fingerprint", "config_fingerprint", "threshold"):
        if bundle.get(field) is None:
            raise ValueError(f"FAB artifact is missing {field}")
    if not isinstance(bundle["feature_names"], (list, tuple)) or not bundle["feature_names"]:
        raise ValueError("feature_names must be a non-empty list")
    feature_names = list(map(str, bundle["feature_names"]))
    expected_feature_fingerprint = config_fingerprint(
        {"feature_contract": FAB_FEATURE_CONTRACT, "feature_names": feature_names}
    )
    if str(bundle["feature_fingerprint"]) != expected_feature_fingerprint:
        raise ValueError("feature_fingerprint does not match feature_names and feature_contract")
    expected_config_fingerprint = config_fingerprint(load_fab_config())
    if str(bundle["config_fingerprint"]) != expected_config_fingerprint:
        raise ValueError("config_fingerprint does not match the current FAB config")
    if not math.isfinite(float(bundle["threshold"])):
        raise ValueError("threshold must be finite")
    if not callable(getattr(bundle["model"], "decision_function", None)):
        raise ValueError("FAB time-series model must implement decision_function")

    parsed_metrics = {name: float(metrics[name]) for name in _REQUIRED_METRICS}
    if any(not 0.0 <= value <= 1.0 for value in parsed_metrics.values()):
        raise ValueError("candidate metrics must be between 0 and 1")

    process_runtime._ensure_schema()
    version = str(bundle["version"])
    if any(
        row["version"] == version
        for row in process_runtime.list_models(normalized_process, "timeseries")
    ):
        raise ValueError(f"model version already registered: {version}")
    try:
        stored_path = str(path.relative_to(ROOT_DIR))
    except ValueError:
        stored_path = str(path)
    metadata = {
        "feature_contract": FAB_FEATURE_CONTRACT,
        "feature_names": feature_names,
        "feature_fingerprint": expected_feature_fingerprint,
        "config_fingerprint": expected_config_fingerprint,
        "data_source": str(bundle.get("data_source") or "external_fab_training"),
        "note": "Externally trained FAB candidate; Production promotion remains manual.",
    }
    record = {
        "id": str(uuid.uuid4()),
        "process_id": normalized_process,
        "modality": "timeseries",
        "version": version,
        "stage": "Staging",
        "model_name": str(bundle.get("model_name") or type(bundle["model"]).__name__),
        "artifact_path": stored_path,
        **parsed_metrics,
        "threshold": float(bundle["threshold"]),
        "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        "registered_at": process_runtime.utc_now(),
    }
    columns = list(record)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO process_model_registry (" + ", ".join(columns) + ") VALUES ("
            + ", ".join(["?"] * len(columns)) + ")",
            tuple(record[column] for column in columns),
        )
    return next(
        row
        for row in process_runtime.list_models(normalized_process, "timeseries")
        if row["version"] == version
    )

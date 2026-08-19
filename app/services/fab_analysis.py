"""FAB dataset loading, export, EDA output, and dashboard compatibility.

The leakage-safe workbench lives in :mod:`app.services.fab_experiment`.
Model-fitting helpers retained in this module are explicit legacy compatibility
APIs and are not called by the default notebook or CLI path.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    fbeta_score,
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import train_test_split

from app.services import db
from app.services.config import ROOT_DIR
from app.services.fab_generator import load_fab_config
from app.services.fab_schema import FAB_FEATURE_CONTRACT, config_fingerprint


ANALYSIS_SCHEMA_VERSION = "fab-analysis.v1"
DATA_ROOT = ROOT_DIR / "data"
DATA_INPUT_DIR = DATA_ROOT / "input"
DATA_OUTPUT_DIR = DATA_ROOT / "output"
DEFAULT_INPUT_PATH = DATA_INPUT_DIR / "fab_training.csv"
FAB_ANALYSIS_OUTPUT_DIR = DATA_OUTPUT_DIR / "fab_analysis"
DASHBOARD_SUMMARY_PATH = FAB_ANALYSIS_OUTPUT_DIR / "dashboard_summary.json"
LEGACY_DASHBOARD_SUMMARY_PATH = DATA_OUTPUT_DIR / "dashboard_summary.json"
SUPPORTED_SUFFIXES = {".csv", ".parquet"}
GROUND_TRUTH_COLUMN = "ground_truth_is_anomaly"
BASELINE_PREDICTION_COLUMN = "baseline_detector_is_anomaly"
IDENTITY_COLUMNS = {
    "message_id",
    "process_run_id",
    "process_id",
    "lot_id",
    "wafer_id",
    "equipment_id",
    "unit_id",
    "recipe_id",
    "observed_at",
    "feature_contract",
    "feature_fingerprint",
    "config_fingerprint",
    BASELINE_PREDICTION_COLUMN,
    GROUND_TRUTH_COLUMN,
    "ground_truth_fault_type",
    "ground_truth_tags",
    "ground_truth_defect",
    "ground_truth_started_at",
    "ground_truth_source",
    "baseline_prediction_source",
    "synthetic_seed",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_data_dirs() -> None:
    DATA_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_local_dataset(path: str | Path = DEFAULT_INPUT_PATH) -> pd.DataFrame:
    """Load a local CSV or Parquet file without any cloud/Drive dependency."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(
            f"Training data not found: {source}. Put CSV/Parquet data under {DATA_INPUT_DIR}."
        )
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("Local analysis supports only .csv and .parquet files")
    frame = pd.read_csv(source) if suffix == ".csv" else pd.read_parquet(source)
    if frame.empty:
        raise ValueError(f"Training data is empty: {source}")
    return frame


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def export_runtime_features(
    process_id: str,
    output_path: str | Path | None = None,
) -> Path:
    """Export persisted RUNNING detector vectors from the configured local DB.

    The exported fingerprint columns prove that the feature columns match the
    runtime contract.  This is the recommended source for a registerable FAB
    candidate; arbitrary local CSV files remain valid for EDA only.
    """
    normalized_process = str(process_id).strip().lower()
    if normalized_process not in {"photo", "etch", "deposition", "cmp", "cleaning"}:
        raise ValueError("process_id must be one of: photo, etch, deposition, cmp, cleaning")
    ensure_data_dirs()
    destination = Path(output_path or (DATA_INPUT_DIR / f"{normalized_process}_runtime_features.csv")).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with db.connect() as conn:
        columns = set(db.table_columns(conn, "process_telemetry"))
        required = {"message_id", "process_run_id", "process_id", "machine_state", "detector_context_json"}
        if not required.issubset(columns):
            raise RuntimeError("process_telemetry has no FAB v2 detector context; run the FAB stream first")
        source_column = ", t.source AS source" if "source" in columns else ""
        lot_columns = set(db.table_columns(conn, "lots"))
        lot_join = " LEFT JOIN lots l ON l.lot_id = t.lot_id" if "metadata_json" in lot_columns else ""
        lot_metadata_column = (
            ", l.metadata_json AS lot_metadata_json"
            if "metadata_json" in lot_columns
            else ""
        )
        rows = conn.execute(
            "SELECT t.message_id, t.process_run_id, t.process_id, t.lot_id, t.wafer_id, "
            "t.equipment_id, t.unit_id, t.recipe_id, t.observed_at, "
            f"t.detector_context_json{source_column}{lot_metadata_column} "
            f"FROM process_telemetry t{lot_join} WHERE LOWER(t.process_id) = LOWER(?) "
            "AND UPPER(t.machine_state) = 'RUNNING' ORDER BY t.observed_at, t.message_id",
            (normalized_process,),
        ).fetchall()
        detection_columns = set(db.table_columns(conn, "fab_detector_results"))
        baseline_predictions: dict[str, bool] = {}
        if {"message_id", "modality", "is_anomaly"}.issubset(detection_columns):
            label_rows = conn.execute(
                "SELECT message_id, is_anomaly FROM fab_detector_results "
                "WHERE LOWER(process_id) = LOWER(?) AND modality = 'timeseries'",
                (normalized_process,),
            ).fetchall()
            baseline_predictions = {
                str(dict(row)["message_id"]): bool(dict(row)["is_anomaly"])
                for row in label_rows
            }
        fault_columns = set(db.table_columns(conn, "simulation_faults"))
        faults_by_run: dict[str, list[dict[str, Any]]] = {}
        required_fault_columns = {
            "process_run_id",
            "fault_type",
            "started_at",
            "ground_truth_tags_json",
            "ground_truth_defect",
            "metadata_json",
        }
        if required_fault_columns.issubset(fault_columns):
            fault_rows = conn.execute(
                "SELECT process_run_id, fault_type, started_at, ground_truth_tags_json, "
                "ground_truth_defect, metadata_json FROM simulation_faults ORDER BY started_at, fault_id"
            ).fetchall()
            for fault_row in fault_rows:
                decoded = dict(fault_row)
                faults_by_run.setdefault(str(decoded["process_run_id"]), []).append(decoded)

    records: list[dict[str, Any]] = []
    expected_names: list[str] | None = None
    for row in rows:
        item = dict(row)
        context = _decode_json(item.pop("detector_context_json", None))
        lot_metadata = _decode_json(item.pop("lot_metadata_json", None))
        names = [str(name) for name in context.get("feature_names") or []]
        vector = list(context.get("feature_vector") or [])
        if not names or len(names) != len(vector):
            continue
        if expected_names is None:
            expected_names = names
        if names != expected_names:
            raise ValueError("Persisted runtime rows contain multiple feature contracts")
        run_faults = faults_by_run.get(str(item.get("process_run_id")), [])
        synthetic_provenance = bool(context.get("generator_version")) or str(
            item.get("source") or ""
        ).lower() in {"virtual_fab_v2", "fab_direct", "fab_transport"}
        fault_types = list(dict.fromkeys(str(fault["fault_type"]) for fault in run_faults))
        ground_truth_tags: list[str] = []
        ground_truth_defects: list[str] = []
        synthetic_seed: Any = lot_metadata.get("synthetic_seed")
        for fault in run_faults:
            tags = _decode_json(fault.get("ground_truth_tags_json"))
            if not tags:
                try:
                    parsed_tags = json.loads(str(fault.get("ground_truth_tags_json") or "[]"))
                except (TypeError, ValueError):
                    parsed_tags = []
                if isinstance(parsed_tags, list):
                    ground_truth_tags.extend(map(str, parsed_tags))
            else:
                ground_truth_tags.extend(map(str, tags))
            if fault.get("ground_truth_defect"):
                ground_truth_defects.append(str(fault["ground_truth_defect"]))
            metadata = _decode_json(fault.get("metadata_json"))
            if synthetic_seed is None:
                synthetic_seed = metadata.get("synthetic_seed")
        record = {
            **{key: item.get(key) for key in item},
            "feature_contract": context.get("feature_contract"),
            "feature_fingerprint": context.get("feature_fingerprint"),
            "config_fingerprint": context.get("config_fingerprint"),
            BASELINE_PREDICTION_COLUMN: baseline_predictions.get(str(item.get("message_id"))),
            GROUND_TRUTH_COLUMN: True if run_faults else (False if synthetic_provenance else None),
            "ground_truth_fault_type": "|".join(fault_types) if fault_types else None,
            "ground_truth_tags": json.dumps(
                list(dict.fromkeys(ground_truth_tags)), ensure_ascii=False
            ),
            "ground_truth_defect": "|".join(dict.fromkeys(ground_truth_defects)) or None,
            "ground_truth_started_at": min(
                (str(fault["started_at"]) for fault in run_faults),
                default=None,
            ),
            "ground_truth_source": "simulation_faults" if synthetic_provenance or run_faults else None,
            "baseline_prediction_source": (
                "fab_detector_results"
                if str(item.get("message_id")) in baseline_predictions
                else None
            ),
            "synthetic_seed": synthetic_seed,
        }
        record.update({name: float(value) for name, value in zip(names, vector, strict=True)})
        records.append(record)
    if not records:
        raise RuntimeError(f"No RUNNING FAB telemetry is available for process {normalized_process}")
    pd.DataFrame.from_records(records).to_csv(destination, index=False)
    return destination


def dataset_profile(frame: pd.DataFrame, target_column: str | None = None) -> dict[str, Any]:
    missing = frame.isna().sum().sort_values(ascending=False)
    target_distribution: dict[str, int] = {}
    if target_column and target_column in frame:
        target_distribution = {
            str(key): int(value)
            for key, value in frame[target_column].value_counts(dropna=False).items()
        }
    return {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "duplicate_rows": int(frame.duplicated().sum()),
        "missing_cells": int(frame.isna().sum().sum()),
        "numeric_columns": list(map(str, frame.select_dtypes(include=[np.number, "bool"]).columns)),
        "categorical_columns": list(map(str, frame.select_dtypes(exclude=[np.number, "bool"]).columns)),
        "missing_by_column": [
            {"column": str(column), "count": int(count), "ratio": float(count / max(1, len(frame)))}
            for column, count in missing.items()
            if int(count) > 0
        ],
        "target_distribution": target_distribution,
    }


def _feature_frame(
    frame: pd.DataFrame,
    *,
    target_column: str | None,
    feature_columns: Sequence[str] | None,
) -> pd.DataFrame:
    if feature_columns:
        missing = [name for name in feature_columns if name not in frame]
        if missing:
            raise ValueError(f"Missing feature columns: {', '.join(missing)}")
        selected = frame[list(feature_columns)].copy()
    else:
        excluded = set(IDENTITY_COLUMNS)
        if target_column:
            excluded.add(target_column)
        names = [
            str(name)
            for name in frame.select_dtypes(include=[np.number, "bool"]).columns
            if str(name) not in excluded
        ]
        selected = frame[names].copy()
    if selected.empty or not len(selected.columns):
        raise ValueError("No numeric feature columns are available for analysis")
    selected = selected.replace([np.inf, -np.inf], np.nan)
    for column in selected:
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
        median = selected[column].median()
        selected[column] = selected[column].fillna(0.0 if pd.isna(median) else float(median))
    return selected.astype(float)


def correlation_table(features: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    matrix = features.corr(numeric_only=True).fillna(0.0)
    pairs: list[dict[str, Any]] = []
    columns = list(matrix.columns)
    for left_index, left in enumerate(columns):
        for right in columns[left_index + 1 :]:
            value = float(matrix.loc[left, right])
            pairs.append({"left": str(left), "right": str(right), "correlation": value})
    pairs.sort(key=lambda item: abs(float(item["correlation"])), reverse=True)
    return matrix, pairs[:20]


def _as_binary_target(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    if pd.api.types.is_numeric_dtype(series):
        values = pd.to_numeric(series, errors="coerce").fillna(0.0)
        return values > 0
    normalized = series.astype(str).str.strip().str.lower()
    positives = {"1", "true", "yes", "y", "anomaly", "abnormal", "fault", "defect"}
    return normalized.isin(positives)


def feature_importance_analysis(
    frame: pd.DataFrame,
    *,
    target_column: str | None = GROUND_TRUTH_COLUMN,
    feature_columns: Sequence[str] | None = None,
    random_state: int = 42,
) -> dict[str, Any]:
    """Legacy opt-in model-based importance helper; this function fits models."""
    features = _feature_frame(frame, target_column=target_column, feature_columns=feature_columns)
    metrics: dict[str, Any]
    model: Any
    target_used = bool(target_column and target_column in frame and frame[target_column].nunique(dropna=True) >= 2)

    if target_used:
        raw_target = frame.loc[features.index, str(target_column)]
        classification = (
            pd.api.types.is_bool_dtype(raw_target)
            or not pd.api.types.is_numeric_dtype(raw_target)
            or raw_target.nunique(dropna=True) <= 20
        )
        if classification:
            target = raw_target.fillna("missing").astype(str)
            model = RandomForestClassifier(n_estimators=120, random_state=random_state, n_jobs=1)
            stratify = target if target.value_counts().min() >= 2 else None
            if len(features) >= 20:
                x_train, x_test, y_train, y_test = train_test_split(
                    features, target, test_size=0.25, random_state=random_state, stratify=stratify
                )
                model.fit(x_train, y_train)
                prediction = model.predict(x_test)
                metrics = {
                    "mode": "supervised_classification",
                    "accuracy": float(accuracy_score(y_test, prediction)),
                    "labels": list(map(str, model.classes_)),
                    "confusion_matrix": confusion_matrix(y_test, prediction, labels=model.classes_).tolist(),
                    "evaluation_rows": int(len(x_test)),
                }
            else:
                model.fit(features, target)
                metrics = {"mode": "supervised_classification_training_only", "evaluation_rows": 0}
        else:
            target = pd.to_numeric(raw_target, errors="coerce").fillna(raw_target.median())
            model = RandomForestRegressor(n_estimators=120, random_state=random_state, n_jobs=1)
            if len(features) >= 20:
                x_train, x_test, y_train, y_test = train_test_split(
                    features, target, test_size=0.25, random_state=random_state
                )
                model.fit(x_train, y_train)
                prediction = model.predict(x_test)
                metrics = {
                    "mode": "supervised_regression",
                    "mae": float(mean_absolute_error(y_test, prediction)),
                    "r2": float(r2_score(y_test, prediction)),
                    "evaluation_rows": int(len(x_test)),
                }
            else:
                model.fit(features, target)
                metrics = {"mode": "supervised_regression_training_only", "evaluation_rows": 0}
    else:
        detector = IsolationForest(n_estimators=120, contamination="auto", random_state=random_state, n_jobs=1)
        pseudo_target = detector.fit_predict(features) == -1
        model = RandomForestClassifier(n_estimators=120, random_state=random_state, n_jobs=1)
        model.fit(features, pseudo_target)
        metrics = {
            "mode": "unsupervised_surrogate",
            "note": "Importance explains Isolation Forest pseudo-labels, not ground truth.",
            "pseudo_anomaly_rows": int(pseudo_target.sum()),
            "evaluation_rows": 0,
        }

    importance = pd.DataFrame(
        {"feature": list(map(str, features.columns)), "importance": list(map(float, model.feature_importances_))}
    ).sort_values("importance", ascending=False, ignore_index=True)
    correlation, pairs = correlation_table(features)
    return {
        "features": features,
        "feature_columns": list(map(str, features.columns)),
        "feature_importance": importance,
        "correlation": correlation,
        "top_correlations": pairs,
        "metrics": metrics,
        "model": model,
    }


class HigherIsAnomalyIsolationForest:
    """Runtime adapter whose decision function follows the FAB score direction."""

    def __init__(self, estimator: IsolationForest, medians: Sequence[float]) -> None:
        self.estimator = estimator
        self.medians = np.asarray(list(medians), dtype=float)

    def decision_function(self, values: Any) -> np.ndarray:
        matrix = np.asarray(values, dtype=float)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape[1] != len(self.medians):
            raise ValueError("FAB candidate feature count does not match its artifact")
        invalid = ~np.isfinite(matrix)
        if invalid.any():
            matrix = matrix.copy()
            matrix[invalid] = np.take(self.medians, np.where(invalid)[1])
        return -np.asarray(self.estimator.decision_function(matrix), dtype=float)


def build_candidate_bundle(
    frame: pd.DataFrame,
    *,
    process_id: str,
    feature_columns: Sequence[str],
    target_column: str | None = GROUND_TRUTH_COLUMN,
    contamination: float = 0.05,
    version: str | None = None,
    random_state: int = 42,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Legacy opt-in Isolation Forest builder for exact runtime features.

    New workbench experiments should use ``fab_experiment`` so threshold and
    feature/model selection remain Validation-only.
    """
    normalized_process = str(process_id).strip().lower()
    if not 0.001 <= float(contamination) <= 0.4:
        raise ValueError("contamination must be between 0.001 and 0.4")
    names = list(map(str, feature_columns))
    expected_feature_fingerprint = config_fingerprint(
        {"feature_contract": FAB_FEATURE_CONTRACT, "feature_names": names}
    )
    expected_config_fingerprint = config_fingerprint(load_fab_config())
    required_metadata = {
        "feature_contract": FAB_FEATURE_CONTRACT,
        "feature_fingerprint": expected_feature_fingerprint,
        "config_fingerprint": expected_config_fingerprint,
    }
    for column, expected in required_metadata.items():
        if column not in frame:
            raise ValueError(
                f"{column} is required for a Production-compatible artifact; "
                "use export_runtime_features() first"
            )
        actual = {str(value) for value in frame[column].dropna().unique()}
        if actual != {expected}:
            raise ValueError(f"{column} does not match the current FAB runtime")

    features = _feature_frame(frame, target_column=target_column, feature_columns=names)
    medians = features.median().fillna(0.0).to_numpy(dtype=float)
    training = features
    if target_column and target_column in frame:
        normal_mask = ~_as_binary_target(frame.loc[features.index, target_column])
        if int(normal_mask.sum()) >= max(10, len(names) + 1):
            training = features.loc[normal_mask]
    estimator = IsolationForest(
        n_estimators=160,
        contamination=float(contamination),
        random_state=random_state,
        n_jobs=1,
    ).fit(training.to_numpy(dtype=float))
    model = HigherIsAnomalyIsolationForest(estimator, medians)
    scores = model.decision_function(training.to_numpy(dtype=float))
    threshold = float(np.quantile(scores, 1.0 - float(contamination)))
    artifact_version = version or f"fab-{normalized_process}-local-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    bundle = {
        "version": artifact_version,
        "process_id": normalized_process,
        "modality": "timeseries",
        "model_name": "HigherIsAnomalyIsolationForest",
        "model": model,
        "threshold": threshold,
        "feature_contract": FAB_FEATURE_CONTRACT,
        "feature_names": names,
        "feature_fingerprint": expected_feature_fingerprint,
        "config_fingerprint": expected_config_fingerprint,
        "data_source": "local_runtime_feature_export",
        "trained_at": utc_now(),
        "training_rows": int(len(training)),
    }

    registration_metrics = {
        "precision": 0.0,
        "recall": 0.0,
        "f2": 0.0,
        "false_positive_rate": 0.0,
    }
    if target_column and target_column in frame and frame[target_column].nunique(dropna=True) >= 2:
        truth = _as_binary_target(frame.loc[features.index, target_column]).to_numpy(dtype=bool)
        predicted = model.decision_function(features.to_numpy(dtype=float)) >= threshold
        negatives = ~truth
        registration_metrics = {
            "precision": float(precision_score(truth, predicted, zero_division=0)),
            "recall": float(recall_score(truth, predicted, zero_division=0)),
            "f2": float(fbeta_score(truth, predicted, beta=2, zero_division=0)),
            "false_positive_rate": float((predicted & negatives).sum() / max(1, negatives.sum())),
        }
    return bundle, registration_metrics


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def save_analysis_outputs(
    frame: pd.DataFrame,
    analysis: Mapping[str, Any],
    *,
    source_path: str | Path,
    process_id: str,
    target_column: str | None,
    output_dir: str | Path = DATA_OUTPUT_DIR,
    candidate_bundle: Mapping[str, Any] | None = None,
    registration_metrics: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    importance_path = destination / "feature_importance.csv"
    correlation_path = destination / "correlation_matrix.csv"
    summary_path = destination / "dashboard_summary.json"
    analysis["feature_importance"].to_csv(importance_path, index=False)
    analysis["correlation"].to_csv(correlation_path)
    artifact_path: Path | None = None
    if candidate_bundle is not None:
        artifact_path = destination / f"{candidate_bundle['version']}.joblib"
        joblib.dump(dict(candidate_bundle), artifact_path)

    summary = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "status": "ready",
        "generated_at": utc_now(),
        "source_file": str(Path(source_path).resolve()),
        "process_id": str(process_id).strip().lower(),
        "target_column": target_column,
        "dataset": dataset_profile(frame, target_column),
        "model": {
            "metrics": _jsonable(analysis["metrics"]),
            "candidate_artifact": str(artifact_path) if artifact_path else None,
            "registration_metrics": _jsonable(registration_metrics or {}),
        },
        "feature_importance": _jsonable(analysis["feature_importance"].head(20).to_dict("records")),
        "top_correlations": _jsonable(analysis["top_correlations"]),
        "artifacts": {
            "feature_importance_csv": str(importance_path),
            "correlation_csv": str(correlation_path),
            "dashboard_summary_json": str(summary_path),
        },
    }
    temporary_summary = summary_path.with_suffix(".json.tmp")
    temporary_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_summary.replace(summary_path)
    return summary


def run_local_analysis(
    input_path: str | Path = DEFAULT_INPUT_PATH,
    *,
    process_id: str = "cmp",
    target_column: str | None = GROUND_TRUTH_COLUMN,
    feature_columns: Sequence[str] | None = None,
    output_dir: str | Path = DATA_OUTPUT_DIR,
    create_candidate: bool = False,
    contamination: float = 0.05,
    random_state: int = 42,
) -> dict[str, Any]:
    """Legacy opt-in EDA/model workflow kept for API compatibility."""
    frame = load_local_dataset(input_path)
    analysis = feature_importance_analysis(
        frame,
        target_column=target_column,
        feature_columns=feature_columns,
        random_state=random_state,
    )
    bundle = None
    registration_metrics = None
    if create_candidate:
        bundle, registration_metrics = build_candidate_bundle(
            frame,
            process_id=process_id,
            feature_columns=analysis["feature_columns"],
            target_column=target_column,
            contamination=contamination,
            random_state=random_state,
        )
    return save_analysis_outputs(
        frame,
        analysis,
        source_path=input_path,
        process_id=process_id,
        target_column=target_column,
        output_dir=output_dir,
        candidate_bundle=bundle,
        registration_metrics=registration_metrics,
    )


def build_workbench_dashboard_summary(
    *,
    process_id: str,
    dataset: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any] | None,
    validation_winner: Mapping[str, Any] | None,
    final_test_metrics: Mapping[str, Any] | None,
    per_fault_metrics: Sequence[Mapping[str, Any]] = (),
    existing_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extend the existing AI Analysis contract without removing legacy keys."""
    summary = dict(existing_summary or {})
    model = dict(summary.get("model") or {})
    model.setdefault("metrics", {})
    summary.update(
        {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "status": "ready",
            "generated_at": utc_now(),
            "process_id": str(process_id).strip().lower(),
            "dataset": _jsonable(dataset),
            "model": model,
            "baseline_metrics": _jsonable(baseline_metrics or {}),
            "validation_winner": _jsonable(validation_winner or {}),
            "final_test_metrics": _jsonable(final_test_metrics or {}),
            "per_fault_metrics": _jsonable(list(per_fault_metrics)),
            "experiment_status": "validated" if validation_winner else "not_run",
        }
    )
    summary.setdefault("feature_importance", [])
    summary.setdefault("top_correlations", [])
    summary.setdefault("artifacts", {})
    return summary


def save_workbench_dashboard_summary(
    summary: Mapping[str, Any],
    path: str | Path = DASHBOARD_SUMMARY_PATH,
) -> Path:
    if summary.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise ValueError("Workbench dashboard summary must use fab-analysis.v1")
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(_jsonable(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def latest_dashboard_summary(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        preferred = DASHBOARD_SUMMARY_PATH.resolve()
        legacy = LEGACY_DASHBOARD_SUMMARY_PATH.resolve()
        source = preferred if preferred.is_file() or not legacy.is_file() else legacy
    else:
        source = Path(path).resolve()
    if not source.is_file():
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "status": "not_ready",
            "message": "Run the local FAB analysis notebook or CLI first.",
            "expected_path": str(source),
        }
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid FAB dashboard analysis summary: {source}") from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise ValueError(f"Unsupported FAB analysis summary schema: {source}")
    return dict(value)

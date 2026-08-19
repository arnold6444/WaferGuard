"""Leakage-safe experiment utilities for the FAB anomaly workbench.

Nothing in this module trains or writes artifacts at import time.  Candidate
fitting, final-test evaluation, and artifact creation are explicit calls used
only by opt-in notebook cells.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import platform
import re
import warnings
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from app.services.fab_analysis import BASELINE_PREDICTION_COLUMN, GROUND_TRUTH_COLUMN
from app.services.fab_schema import config_fingerprint
from app.services.process_temporal import _temporal_candidate_models


RUN_EXPERIMENT_DEFAULT = False
RUN_FINAL_TEST_DEFAULT = False
CREATE_CANDIDATE_DEFAULT = False
DEFAULT_SPLIT_RATIOS = (0.60, 0.20, 0.20)
ANALYSIS_OUTPUT_DIR = Path("data/output/fab_analysis")

GROUND_TRUTH_COLUMNS = {
    GROUND_TRUTH_COLUMN,
    "ground_truth_fault_type",
    "ground_truth_tags",
    "ground_truth_defect",
    "ground_truth_started_at",
    "ground_truth_source",
}
IDENTITY_COLUMNS = {
    "message_id",
    "process_run_id",
    "process_id",
    "lot_id",
    "wafer_id",
    "equipment_id",
    "unit_id",
    "recipe_id",
    "cycle_id",
    "observed_at",
}
FORBIDDEN_FEATURE_COLUMNS = GROUND_TRUTH_COLUMNS | IDENTITY_COLUMNS | {
    BASELINE_PREDICTION_COLUMN,
    "baseline_prediction_source",
    "feature_contract",
    "feature_fingerprint",
    "config_fingerprint",
    "synthetic_seed",
    "source",
    "is_anomaly",
}
ALLOWED_GROUND_TRUTH_SOURCES = {"simulation_faults"}
ALLOWED_BASELINE_SOURCES = {"fab_detector_results"}
FEATURE_PREFIXES = {
    "level": ("z:",),
    "change": ("delta:",),
    "relationship": ("rel:",),
    "rolling": ("roll_mean_delta:", "roll_std:"),
    "context": ("context:",),
}
CANDIDATE_SEARCH_SPACE: dict[str, dict[str, list[Any]]] = {
    "robust_z_univariate": {},
    "mahalanobis_multivariate": {},
    "isolation_forest": {
        "n_estimators": [160, 220],
        "max_samples": ["auto", 0.8],
        "max_features": [0.8, 1.0],
    },
    "one_class_svm": {
        "nu": [0.02, 0.05, 0.10],
        "gamma": ["scale", "auto"],
    },
}
LEADERBOARD_COLUMNS = (
    "candidate_id",
    "model",
    "feature_set",
    "hyperparameters",
    "precision",
    "recall",
    "f1",
    "f2",
    "false_positive_rate",
    "false_alarms_per_hour",
    "threshold",
    "selection_split",
)
LOCK_REQUIRED_FIELDS = {
    "model_name",
    "feature_set",
    "hyperparameters",
    "threshold",
    "feature_contract",
    "feature_fingerprint",
    "config_fingerprint",
    "split_hash",
    "experiment_hash",
}
OUTPUT_SCHEMAS: dict[str, set[str]] = {
    "dataset_profile.json": {"rows", "columns", "process_run_count", "label_source_audit"},
    "split_manifest.csv": {"process_run_id", "split", "run_started_at", "included_in_training"},
    "feature_importance.csv": {"feature", "importance"},
    "correlation_matrix.csv": set(),
    "candidate_leaderboard.csv": set(LEADERBOARD_COLUMNS),
    "validation_metrics.csv": {"candidate_id", "precision", "recall", "f1", "f2"},
    "per_fault_metrics.csv": {"fault_type", "support", "recall", "f1", "f2"},
    "context_false_positives.csv": {"context", "value", "normal_rows", "false_positives"},
    "locked_candidate.json": LOCK_REQUIRED_FIELDS,
    "final_test_metrics.json": {"precision", "recall", "f1", "f2", "threshold"},
    "experiment_manifest.json": {
        "experiment_id",
        "created_at",
        "process_id",
        "source_file",
        "source_hash",
        "feature_contract",
        "feature_fingerprint",
        "config_fingerprint",
        "random_state",
        "split_definition",
        "candidate_grid",
        "library_versions",
    },
}


@dataclass(frozen=True)
class ExperimentSplits:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    manifest: pd.DataFrame
    split_hash: str
    normal_only_applied: bool


def _binary(series: pd.Series, *, allow_missing: bool = False) -> pd.Series:
    missing = series.isna()
    if missing.any() and not allow_missing:
        raise ValueError("Ground truth contains missing labels in the requested evaluation rows")
    if pd.api.types.is_bool_dtype(series):
        result = series.astype("boolean")
    elif pd.api.types.is_numeric_dtype(series):
        result = pd.to_numeric(series, errors="coerce").astype("Float64") > 0
    else:
        normalized = series.astype("string").str.strip().str.lower()
        result = normalized.isin({"1", "true", "yes", "y", "anomaly", "fault", "defect"})
        result = result.astype("boolean")
    return result.mask(missing) if allow_missing else result.astype(bool)


def audit_label_sources(
    frame: pd.DataFrame,
    *,
    include_counts: bool = True,
) -> dict[str, Any]:
    """Prove that predictions and simulator truth have distinct roles."""
    if GROUND_TRUTH_COLUMN == BASELINE_PREDICTION_COLUMN:
        raise RuntimeError("Ground truth and baseline prediction columns must be different")
    if "is_anomaly" in frame and GROUND_TRUTH_COLUMN not in frame:
        raise ValueError(
            "Legacy is_anomaly is a detector prediction, not ground truth; export the FAB v2 labels"
        )
    ground_source = set(frame.get("ground_truth_source", pd.Series(dtype=str)).dropna().astype(str))
    baseline_source = set(
        frame.get("baseline_prediction_source", pd.Series(dtype=str)).dropna().astype(str)
    )
    if ground_source & baseline_source:
        raise ValueError("Ground truth and baseline prediction report the same source")
    ground_available = GROUND_TRUTH_COLUMN in frame and frame[GROUND_TRUTH_COLUMN].notna().any()
    baseline_available = (
        BASELINE_PREDICTION_COLUMN in frame
        and frame[BASELINE_PREDICTION_COLUMN].notna().any()
    )
    if ground_available:
        labelled_sources = frame.loc[
            frame[GROUND_TRUTH_COLUMN].notna(), "ground_truth_source"
        ] if "ground_truth_source" in frame else pd.Series(dtype=object)
        if labelled_sources.empty or labelled_sources.isna().any():
            raise ValueError("Ground truth labels require an explicit ground_truth_source")
        unknown_ground = set(labelled_sources.astype(str)) - ALLOWED_GROUND_TRUTH_SOURCES
        if unknown_ground:
            raise ValueError(
                "Unsupported ground_truth_source: " + ", ".join(sorted(unknown_ground))
            )
    if baseline_available:
        baseline_sources = frame.loc[
            frame[BASELINE_PREDICTION_COLUMN].notna(), "baseline_prediction_source"
        ] if "baseline_prediction_source" in frame else pd.Series(dtype=object)
        if baseline_sources.empty or baseline_sources.isna().any():
            raise ValueError("Baseline predictions require an explicit baseline_prediction_source")
        unknown_baseline = set(baseline_sources.astype(str)) - ALLOWED_BASELINE_SOURCES
        if unknown_baseline:
            raise ValueError(
                "Unsupported baseline_prediction_source: " + ", ".join(sorted(unknown_baseline))
            )
    faulted_runs = 0
    normal_runs = 0
    if ground_available and include_counts:
        labelled = frame[frame[GROUND_TRUTH_COLUMN].notna()].copy()
        run_truth = _binary(labelled[GROUND_TRUTH_COLUMN]).groupby(labelled["process_run_id"]).max()
        faulted_runs = int(run_truth.sum())
        normal_runs = int((~run_truth).sum())
    return {
        "ground_truth_available": bool(ground_available),
        "baseline_prediction_available": bool(baseline_available),
        "ground_truth_source": sorted(ground_source) or ["unavailable"],
        "baseline_prediction_source": sorted(baseline_source) or ["unavailable"],
        "faulted_process_runs": faulted_runs,
        "normal_process_runs": normal_runs,
        "label_counts_visible": bool(include_counts),
        "supervised_performance_evaluation": (
            "available" if ground_available else "unavailable"
        ),
    }


def validate_feature_columns(columns: Sequence[str]) -> list[str]:
    names = list(map(str, columns))
    if len(names) != len(set(names)):
        raise ValueError("Feature columns must be unique")
    forbidden: list[str] = []
    for name in names:
        normalized = name.strip().lower()
        semantic_parts = [
            part for part in re.split(r"[:./\\]+", normalized) if part
        ]
        if (
            normalized in FORBIDDEN_FEATURE_COLUMNS
            or any(part in FORBIDDEN_FEATURE_COLUMNS for part in semantic_parts)
            or any(part.startswith("ground_truth_") for part in semantic_parts)
            or any(part.startswith("baseline_detector_") for part in semantic_parts)
        ):
            forbidden.append(name)
    if forbidden:
        raise ValueError(f"Leakage/identity columns cannot be features: {', '.join(forbidden)}")
    return names


def feature_groups(columns: Sequence[str]) -> dict[str, list[str]]:
    names = validate_feature_columns(columns)
    return {
        group: [name for name in names if name.startswith(prefixes)]
        for group, prefixes in FEATURE_PREFIXES.items()
    }


def build_feature_sets(columns: Sequence[str]) -> dict[str, list[str]]:
    groups = feature_groups(columns)

    def combine(*group_names: str) -> list[str]:
        allowed = set(itertools.chain.from_iterable(groups[name] for name in group_names))
        return [str(column) for column in columns if str(column) in allowed]

    return {
        "level_only": combine("level"),
        "level_change": combine("level", "change"),
        "level_relationship": combine("level", "relationship"),
        "level_rolling": combine("level", "rolling"),
        "all_features": combine(*FEATURE_PREFIXES),
    }


def workbench_dataset_profile(
    frame: pd.DataFrame,
    *,
    label_audit_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    # Validate provenance across the full file, but expose label counts only
    # from the development (Train + Validation) rows when supplied.
    provenance = audit_label_sources(frame, include_counts=False)
    label_audit = audit_label_sources(
        label_audit_frame if label_audit_frame is not None else frame,
        include_counts=True,
    )
    label_audit["ground_truth_source"] = provenance["ground_truth_source"]
    label_audit["baseline_prediction_source"] = provenance["baseline_prediction_source"]
    label_audit["count_scope"] = (
        "train_validation" if label_audit_frame is not None else "all_data"
    )
    observed = pd.to_datetime(frame.get("observed_at"), utc=True, errors="coerce") if "observed_at" in frame else pd.Series(dtype="datetime64[ns, UTC]")
    return {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "missing_cells": int(frame.isna().sum().sum()),
        "duplicate_rows": int(frame.duplicated().sum()),
        "timestamp_start": observed.min().isoformat() if len(observed) and observed.notna().any() else None,
        "timestamp_end": observed.max().isoformat() if len(observed) and observed.notna().any() else None,
        "process_run_count": int(frame["process_run_id"].nunique()) if "process_run_id" in frame else 0,
        "wafer_count": int(frame["wafer_id"].nunique()) if "wafer_id" in frame else 0,
        "equipment_count": int(frame["equipment_id"].nunique()) if "equipment_id" in frame else 0,
        "unit_count": int(frame["unit_id"].nunique()) if "unit_id" in frame else 0,
        "label_source_audit": label_audit,
    }


def non_training_feature_analysis(
    frame: pd.DataFrame,
    feature_sets: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Correlation and standardized mean differences without fitting a model."""
    all_names = validate_feature_columns(
        list(dict.fromkeys(itertools.chain.from_iterable(feature_sets.values())))
    )
    numeric = frame[all_names].apply(pd.to_numeric, errors="coerce") if all_names else pd.DataFrame(index=frame.index)
    correlation = numeric.corr().fillna(0.0)
    importance_rows: list[dict[str, Any]] = []
    ground_available = GROUND_TRUTH_COLUMN in frame and frame[GROUND_TRUTH_COLUMN].notna().any()
    labelled = frame[GROUND_TRUTH_COLUMN].notna() if ground_available else pd.Series(False, index=frame.index)
    truth = _binary(frame.loc[labelled, GROUND_TRUTH_COLUMN]) if ground_available else pd.Series(dtype=bool)
    for name in all_names:
        values = numeric.loc[labelled, name] if ground_available else numeric[name]
        effect = 0.0
        if ground_available and truth.any() and (~truth).any():
            anomaly = values.loc[truth.index[truth]].dropna()
            normal = values.loc[truth.index[~truth]].dropna()
            pooled = float(pd.concat([anomaly, normal]).std(ddof=0))
            if pooled > 0:
                effect = float((anomaly.mean() - normal.mean()) / pooled)
        importance_rows.append(
            {
                "feature": name,
                "importance": abs(effect),
                "signed_effect_size": effect,
                "method": "standardized_mean_difference" if ground_available else "unavailable_without_ground_truth",
            }
        )
    importance = pd.DataFrame(importance_rows).sort_values(
        "importance", ascending=False, ignore_index=True
    ) if importance_rows else pd.DataFrame(columns=["feature", "importance", "signed_effect_size", "method"])
    group_summary = pd.DataFrame(
        [
            {
                "feature_set": name,
                "feature_count": len(validate_feature_columns(columns)),
                "features": list(columns),
            }
            for name, columns in feature_sets.items()
        ]
    )
    return {
        "correlation": correlation,
        "feature_effects": importance,
        "feature_group_summary": group_summary,
        "note": "EDA only; feature-set selection must use validation results.",
    }


def baseline_metrics(frame: pd.DataFrame) -> dict[str, Any] | None:
    if GROUND_TRUTH_COLUMN not in frame or BASELINE_PREDICTION_COLUMN not in frame:
        return None
    available = frame[GROUND_TRUTH_COLUMN].notna() & frame[BASELINE_PREDICTION_COLUMN].notna()
    if not available.any():
        return None
    return {
        **binary_metrics(
            _binary(frame.loc[available, GROUND_TRUTH_COLUMN]),
            _binary(frame.loc[available, BASELINE_PREDICTION_COLUMN]),
        ),
        "comparison_role": "baseline_prediction_only",
    }


def _allocation(size: int, ratios: Sequence[float]) -> tuple[int, int, int]:
    if size < 3:
        raise ValueError("At least three chronological wafer/run groups are required")
    values = tuple(float(value) for value in ratios)
    if len(values) != 3 or any(value <= 0 for value in values) or not math.isclose(sum(values), 1.0):
        raise ValueError("split ratios must contain three positive values summing to 1")
    train = max(1, int(math.floor(size * values[0])))
    validation = max(1, int(math.floor(size * values[1])))
    while train + validation >= size:
        if train > validation and train > 1:
            train -= 1
        elif validation > 1:
            validation -= 1
        else:
            break
    return train, validation, size - train - validation


def validate_split_integrity(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    *,
    run_column: str = "process_run_id",
    wafer_column: str = "wafer_id",
) -> None:
    for name, split in (("train", train), ("validation", validation), ("test", test)):
        if run_column not in split:
            raise ValueError(f"{name} split is missing {run_column}")
    sets = {
        "train": set(train[run_column].dropna().astype(str)),
        "validation": set(validation[run_column].dropna().astype(str)),
        "test": set(test[run_column].dropna().astype(str)),
    }
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        overlap = sets[left] & sets[right]
        if overlap:
            raise ValueError(f"process_run leakage between {left} and {right}: {sorted(overlap)[:3]}")
    if all(wafer_column in split for split in (train, validation, test)):
        wafer_sets = {
            "train": set(train[wafer_column].dropna().astype(str)),
            "validation": set(validation[wafer_column].dropna().astype(str)),
            "test": set(test[wafer_column].dropna().astype(str)),
        }
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
            overlap = wafer_sets[left] & wafer_sets[right]
            if overlap:
                raise ValueError(f"wafer leakage between {left} and {right}: {sorted(overlap)[:3]}")


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def grouped_chronological_split(
    frame: pd.DataFrame,
    *,
    ratios: Sequence[float] = DEFAULT_SPLIT_RATIOS,
    normal_only_train: bool = True,
    run_column: str = "process_run_id",
    wafer_column: str = "wafer_id",
    observed_column: str = "observed_at",
) -> ExperimentSplits:
    """Split chronological atomic wafer groups, then keep normal train runs only."""
    required = {run_column, observed_column}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Split data is missing: {', '.join(sorted(missing))}")
    working = frame.copy()
    working["_split_time"] = pd.to_datetime(working[observed_column], utc=True, errors="coerce")
    if working["_split_time"].isna().any():
        raise ValueError("observed_at must contain valid timezone-aware timestamps")
    atomic_column = wafer_column if wafer_column in working and working[wafer_column].notna().all() else run_column
    atoms = working.groupby(atomic_column, dropna=False)["_split_time"].agg(
        start="min", end="max"
    )
    atoms["_atom_key"] = atoms.index.astype(str)
    atoms = atoms.sort_values(["start", "end", "_atom_key"], kind="stable")
    desired_train, desired_validation, _ = _allocation(len(atoms), ratios)
    prefix_end = atoms["end"].cummax()
    suffix_start = atoms["start"].iloc[::-1].cummin().iloc[::-1]
    valid_cuts = [
        index
        for index in range(1, len(atoms))
        if prefix_end.iloc[index - 1] <= suffix_start.iloc[index]
    ]
    cut_pairs = [
        (left, right)
        for left in valid_cuts
        for right in valid_cuts
        if left < right
    ]
    if not cut_pairs:
        raise ValueError(
            "No non-overlapping chronological Train/Validation/Test boundary exists; "
            "use non-overlapping run/wafer cohorts"
        )
    target_second_cut = desired_train + desired_validation
    train_size, second_cut = min(
        cut_pairs,
        key=lambda pair: (
            abs(pair[0] - desired_train) + abs(pair[1] - target_second_cut),
            abs(pair[0] - desired_train),
            pair,
        ),
    )
    validation_size = second_cut - train_size
    atom_split: dict[str, str] = {}
    for index, atom in enumerate(atoms.index.astype(str)):
        atom_split[atom] = (
            "train"
            if index < train_size
            else "validation"
            if index < train_size + validation_size
            else "test"
        )
    working["_split"] = working[atomic_column].astype(str).map(atom_split)

    truth_available = (
        GROUND_TRUTH_COLUMN in working and working[GROUND_TRUTH_COLUMN].notna().any()
    )
    normal_only_applied = bool(normal_only_train and truth_available)
    run_truth: dict[str, bool] = {}
    if truth_available:
        labelled = working[working[GROUND_TRUTH_COLUMN].notna()]
        grouped_truth = _binary(labelled[GROUND_TRUTH_COLUMN]).groupby(
            labelled[run_column].astype(str)
        ).max()
        run_truth = {str(key): bool(value) for key, value in grouped_truth.items()}

    manifest_rows: list[dict[str, Any]] = []
    for process_run_id, group in working.groupby(run_column, sort=False):
        split = str(group["_split"].iloc[0])
        anomalous = run_truth.get(str(process_run_id))
        included = not (
            split == "train"
            and normal_only_applied
            and anomalous is not False
        )
        manifest_rows.append(
            {
                "process_run_id": str(process_run_id),
                "wafer_id": str(group[wafer_column].iloc[0]) if wafer_column in group else None,
                "split": split,
                "run_started_at": group["_split_time"].min().isoformat(),
                "ground_truth_is_anomaly": anomalous if split != "test" else None,
                "label_visibility": "sealed" if split == "test" else "development",
                "included_in_training": included if split == "train" else False,
            }
        )
    manifest = pd.DataFrame(manifest_rows).sort_values(
        ["run_started_at", "process_run_id"], ignore_index=True
    )
    included_train_runs = set(
        manifest.loc[
            (manifest["split"] == "train") & manifest["included_in_training"],
            "process_run_id",
        ]
    )
    train = working[
        (working["_split"] == "train")
        & working[run_column].astype(str).isin(included_train_runs)
    ]
    validation = working[working["_split"] == "validation"]
    test = working[working["_split"] == "test"]
    clean = lambda value: value.drop(columns=["_split", "_split_time"]).copy()  # noqa: E731
    train, validation, test = clean(train), clean(validation), clean(test)
    if train.empty:
        raise ValueError("Normal-only filtering left no training process runs")
    validate_split_integrity(train, validation, test, run_column=run_column, wafer_column=wafer_column)
    definition = manifest.to_dict("records")
    return ExperimentSplits(
        train=train,
        validation=validation,
        test=test,
        manifest=manifest,
        split_hash=_stable_hash(definition),
        normal_only_applied=normal_only_applied,
    )


def binary_metrics(truth: Sequence[Any], predicted: Sequence[Any]) -> dict[str, float | int]:
    y_true = np.asarray(list(truth), dtype=bool)
    y_pred = np.asarray(list(predicted), dtype=bool)
    if y_true.shape != y_pred.shape or y_true.ndim != 1:
        raise ValueError("truth and predicted must be aligned one-dimensional values")
    tp = int(np.sum(y_true & y_pred))
    fp = int(np.sum(~y_true & y_pred))
    tn = int(np.sum(~y_true & ~y_pred))
    fn = int(np.sum(y_true & ~y_pred))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    f2 = 5 * precision * recall / (4 * precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "f2": float(f2),
        "false_positive_rate": float(fpr),
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
    }


def false_alarms_per_hour(
    frame: pd.DataFrame,
    predicted: Sequence[Any],
    *,
    sampling_interval_seconds: float | None = None,
) -> float | None:
    if GROUND_TRUTH_COLUMN not in frame:
        return None
    labelled = frame[GROUND_TRUTH_COLUMN].notna().to_numpy()
    truth = _binary(frame.loc[labelled, GROUND_TRUTH_COLUMN]).to_numpy(dtype=bool)
    predictions = np.asarray(list(predicted), dtype=bool)[labelled]
    normal_positions = ~truth
    if not normal_positions.any():
        return None
    false_alarms = int(np.sum(predictions[normal_positions]))
    normal_frame = frame.loc[labelled].loc[normal_positions].copy()
    duration_seconds = 0.0
    if "observed_at" in normal_frame:
        normal_frame["_time"] = pd.to_datetime(
            normal_frame["observed_at"], utc=True, errors="coerce"
        )
        group_column = "process_run_id" if "process_run_id" in normal_frame else None
        groups = normal_frame.groupby(group_column) if group_column else [(None, normal_frame)]
        for _, group in groups:
            valid = group["_time"].dropna().sort_values()
            if len(valid) >= 2:
                duration_seconds += max(
                    0.0,
                    (valid.iloc[-1] - valid.iloc[0]).total_seconds(),
                )
    if duration_seconds <= 0:
        if sampling_interval_seconds is None or float(sampling_interval_seconds) <= 0:
            return None
        duration_seconds = len(normal_frame) * float(sampling_interval_seconds)
    return float(false_alarms / (duration_seconds / 3600.0))


def select_validation_threshold(
    scores: Sequence[float],
    truth: Sequence[Any],
    *,
    split_name: str = "validation",
    false_alarm_rate: float | None = None,
) -> dict[str, Any]:
    if split_name != "validation":
        raise ValueError("Threshold selection is allowed only on the validation split")
    values = np.asarray(list(scores), dtype=float)
    labels = np.asarray(list(truth), dtype=bool)
    if values.shape != labels.shape or values.ndim != 1 or not len(values):
        raise ValueError("validation scores and truth must be non-empty and aligned")
    if not np.isfinite(values).all():
        raise ValueError("validation scores must be finite")
    candidates = np.unique(values)
    best: dict[str, Any] | None = None
    for threshold in candidates:
        metrics = binary_metrics(labels, values >= threshold)
        record = {
            **metrics,
            "threshold": float(threshold),
            "selection_split": "validation",
            "false_alarms_per_hour": false_alarm_rate,
        }
        key = (record["f2"], -record["false_positive_rate"], record["precision"])
        if best is None or key > best["_selection_key"]:
            best = {**record, "_selection_key": key}
    assert best is not None
    best.pop("_selection_key")
    return best


def detection_delays(
    frame: pd.DataFrame,
    predicted: Sequence[Any],
) -> pd.DataFrame:
    required = {
        "process_run_id",
        "observed_at",
        "ground_truth_started_at",
        GROUND_TRUTH_COLUMN,
    }
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=["process_run_id", "fault_type", "delay_seconds", "missed"])
    working = frame.copy()
    working["_predicted"] = np.asarray(list(predicted), dtype=bool)
    working["_observed"] = pd.to_datetime(working["observed_at"], utc=True, errors="coerce")
    rows: list[dict[str, Any]] = []
    faulted = working[working[GROUND_TRUTH_COLUMN].fillna(False).astype(bool)]
    for process_run_id, group in faulted.groupby("process_run_id"):
        started = pd.to_datetime(group["ground_truth_started_at"].dropna().min(), utc=True, errors="coerce")
        detected = group.loc[group["_predicted"] & (group["_observed"] >= started), "_observed"]
        missed = detected.empty or pd.isna(started)
        rows.append(
            {
                "process_run_id": str(process_run_id),
                "fault_type": group.get("ground_truth_fault_type", pd.Series([None])).iloc[0],
                "delay_seconds": None if missed else float((detected.min() - started).total_seconds()),
                "missed": bool(missed),
            }
        )
    return pd.DataFrame(rows)


def per_fault_metrics(frame: pd.DataFrame, predicted: Sequence[Any]) -> pd.DataFrame:
    if "ground_truth_fault_type" not in frame or GROUND_TRUTH_COLUMN not in frame:
        return pd.DataFrame(columns=["fault_type", "support", "recall", "f1", "f2", "detection_delay"])
    working = frame.copy()
    working["_predicted"] = np.asarray(list(predicted), dtype=bool)
    normal = working[working[GROUND_TRUTH_COLUMN].eq(False)]
    delays = detection_delays(working, working["_predicted"])
    rows: list[dict[str, Any]] = []
    fault_types = sorted(working["ground_truth_fault_type"].dropna().astype(str).unique())
    for fault_type in fault_types:
        positive = working[working["ground_truth_fault_type"].astype(str).str.split("|").apply(lambda values: fault_type in values)]
        evaluation = pd.concat([normal, positive]).drop_duplicates()
        metrics = binary_metrics(
            evaluation[GROUND_TRUTH_COLUMN].astype(bool),
            evaluation["_predicted"],
        )
        fault_delays = delays[delays["fault_type"].astype(str).str.contains(fault_type, regex=False)]
        detected_delays = fault_delays.loc[~fault_delays["missed"], "delay_seconds"]
        rows.append(
            {
                "fault_type": fault_type,
                "support": int(positive["process_run_id"].nunique()),
                "recall": metrics["recall"],
                "f1": metrics["f1"],
                "f2": metrics["f2"],
                "detection_delay": (
                    float(detected_delays.mean()) if len(detected_delays) else "missed"
                ),
            }
        )
    return pd.DataFrame(rows)


def context_false_positives(
    frame: pd.DataFrame,
    predicted: Sequence[Any],
    *,
    context_columns: Sequence[str] = ("equipment_id", "unit_id", "recipe_id"),
) -> pd.DataFrame:
    if GROUND_TRUTH_COLUMN not in frame:
        return pd.DataFrame(columns=OUTPUT_SCHEMAS["context_false_positives.csv"])
    working = frame.copy()
    working["_predicted"] = np.asarray(list(predicted), dtype=bool)
    normal = working[working[GROUND_TRUTH_COLUMN].eq(False)]
    rows: list[dict[str, Any]] = []
    for context in context_columns:
        if context not in normal:
            continue
        for value, group in normal.groupby(context, dropna=False):
            false_positives = int(group["_predicted"].sum())
            rows.append(
                {
                    "context": context,
                    "value": str(value),
                    "normal_rows": int(len(group)),
                    "false_positives": false_positives,
                    "false_positive_rate": false_positives / max(1, len(group)),
                }
            )
    return pd.DataFrame(rows)


def error_examples(
    frame: pd.DataFrame,
    predicted: Sequence[Any],
    *,
    limit: int = 100,
) -> pd.DataFrame:
    """Return review rows for Validation/Test errors without changing scores."""
    if GROUND_TRUTH_COLUMN not in frame:
        return pd.DataFrame()
    working = frame.copy()
    values = np.asarray(list(predicted), dtype=bool)
    if len(values) != len(working):
        raise ValueError("predicted values must align with frame rows")
    labelled = working[GROUND_TRUTH_COLUMN].notna()
    working = working.loc[labelled].copy()
    working["candidate_is_anomaly"] = values[labelled.to_numpy()]
    working["_truth"] = _binary(working[GROUND_TRUTH_COLUMN]).to_numpy(dtype=bool)
    working["error_type"] = np.select(
        [working["candidate_is_anomaly"] & ~working["_truth"],
         ~working["candidate_is_anomaly"] & working["_truth"]],
        ["false_positive", "false_negative"],
        default="correct",
    )
    review = working[working["error_type"] != "correct"]
    preferred = [
        "error_type",
        "observed_at",
        "process_run_id",
        "ground_truth_fault_type",
        "equipment_id",
        "unit_id",
        "recipe_id",
        "phase",
        GROUND_TRUTH_COLUMN,
        "candidate_is_anomaly",
    ]
    columns = [name for name in preferred if name in review]
    return review[columns].head(max(0, int(limit))).reset_index(drop=True)


def seed_robustness(frame: pd.DataFrame, predicted: Sequence[Any]) -> dict[str, Any]:
    if GROUND_TRUTH_COLUMN not in frame:
        return {
            "status": "ground-truth unavailable",
            "seed_count": int(
                frame.get("synthetic_seed", pd.Series(dtype=object)).dropna().nunique()
            ),
        }
    if "synthetic_seed" not in frame or frame["synthetic_seed"].dropna().nunique() <= 1:
        return {
            "status": "single-seed evaluation",
            "seed_count": int(
                frame.get("synthetic_seed", pd.Series(dtype=object)).dropna().nunique()
            ),
        }
    working = frame.copy()
    working["_predicted"] = np.asarray(list(predicted), dtype=bool)
    per_seed: list[dict[str, Any]] = []
    for seed, group in working.dropna(subset=["synthetic_seed", GROUND_TRUTH_COLUMN]).groupby("synthetic_seed"):
        per_seed.append({"synthetic_seed": seed, **binary_metrics(_binary(group[GROUND_TRUTH_COLUMN]), group["_predicted"])})
    numeric = ("precision", "recall", "f1", "f2", "false_positive_rate")
    return {
        "status": "multi-seed evaluation",
        "seed_count": len(per_seed),
        "per_seed": per_seed,
        "summary": {
            metric: {
                "mean": float(np.mean([row[metric] for row in per_seed])),
                "std": float(np.std([row[metric] for row in per_seed])),
                "min": float(np.min([row[metric] for row in per_seed])),
                "max": float(np.max([row[metric] for row in per_seed])),
            }
            for metric in numeric
        },
    }


def candidate_grid(
    search_space: Mapping[str, Mapping[str, Sequence[Any]]] = CANDIDATE_SEARCH_SPACE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, parameters in search_space.items():
        names = list(parameters)
        combinations = itertools.product(*(parameters[name] for name in names)) if names else [()]
        for values in combinations:
            rows.append({"model": str(model), "hyperparameters": dict(zip(names, values, strict=True))})
    return rows


def make_candidate_estimator(
    model_name: str,
    hyperparameters: Mapping[str, Any] | None = None,
    *,
    random_state: int = 42,
) -> Pipeline:
    """Instantiate existing WaferGuard candidates, wrapped by train-only imputation."""
    candidates = _temporal_candidate_models(int(random_state))
    if model_name not in candidates:
        raise ValueError(f"Unknown candidate model: {model_name}")
    try:
        estimator = clone(candidates[model_name])
    except TypeError:
        estimator = copy.deepcopy(candidates[model_name])
    parameters = dict(hyperparameters or {})
    if model_name == "isolation_forest":
        estimator.set_params(**{f"model__{key}": value for key, value in parameters.items()})
    elif model_name == "one_class_svm":
        estimator.set_params(**{f"model__{key}": value for key, value in parameters.items()})
    elif parameters:
        raise ValueError(f"{model_name} does not define a hyperparameter grid")
    return Pipeline([("imputer", SimpleImputer(strategy="median")), ("candidate", estimator)])


def _anomaly_scores(estimator: Any, features: pd.DataFrame) -> np.ndarray:
    return -np.asarray(estimator.decision_function(features), dtype=float).reshape(-1)


def build_candidate_leaderboard(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame([dict(record) for record in records])
    missing = set(LEADERBOARD_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Leaderboard records are missing: {', '.join(sorted(missing))}")
    if set(frame["selection_split"].astype(str)) != {"validation"}:
        raise ValueError("Candidate selection records must come only from validation")
    return frame.sort_values(
        ["f2", "false_positive_rate", "precision"],
        ascending=[False, True, False],
        ignore_index=True,
    )


def run_validation_experiment(
    splits: ExperimentSplits,
    feature_sets: Mapping[str, Sequence[str]],
    *,
    search_space: Mapping[str, Mapping[str, Sequence[Any]]] = CANDIDATE_SEARCH_SPACE,
    random_state: int = 42,
    sampling_interval_seconds: float | None = None,
) -> dict[str, Any]:
    """Opt-in fitting path. The notebook calls this only when RUN_EXPERIMENT=True."""
    audit = audit_label_sources(splits.validation)
    if not audit["ground_truth_available"]:
        raise ValueError("supervised performance evaluation unavailable")
    records: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}
    labelled = splits.validation[GROUND_TRUTH_COLUMN].notna().to_numpy()
    truth = _binary(splits.validation.loc[labelled, GROUND_TRUTH_COLUMN])
    for feature_set, raw_names in feature_sets.items():
        names = validate_feature_columns(raw_names)
        if not names:
            continue
        for specification in candidate_grid(search_space):
            model_name = specification["model"]
            parameters = specification["hyperparameters"]
            estimator = make_candidate_estimator(
                model_name,
                parameters,
                random_state=random_state,
            )
            estimator.fit(splits.train[names])
            scores = _anomaly_scores(estimator, splits.validation[names])
            selected = select_validation_threshold(
                scores[labelled],
                truth,
                split_name="validation",
            )
            selected["false_alarms_per_hour"] = false_alarms_per_hour(
                splits.validation,
                scores >= selected["threshold"],
                sampling_interval_seconds=sampling_interval_seconds,
            )
            candidate_id = _stable_hash(
                {"model": model_name, "feature_set": feature_set, "hyperparameters": parameters}
            )[:16]
            records.append(
                {
                    "candidate_id": candidate_id,
                    "model": model_name,
                    "feature_set": feature_set,
                    "hyperparameters": parameters,
                    **selected,
                }
            )
            fitted[candidate_id] = estimator
    leaderboard = build_candidate_leaderboard(records)
    return {"leaderboard": leaderboard, "fitted_candidates": fitted}


def lock_validation_winner(
    winner: Mapping[str, Any],
    *,
    feature_names: Sequence[str],
    feature_contract: str,
    config_fingerprint_value: str,
    split_hash: str,
) -> dict[str, Any]:
    if winner.get("selection_split") != "validation":
        raise ValueError("Only a validation winner can be locked")
    names = validate_feature_columns(feature_names)
    feature_fingerprint = config_fingerprint(
        {"feature_contract": feature_contract, "feature_names": names}
    )
    base = {
        "model_name": winner["model"],
        "feature_set": winner["feature_set"],
        "feature_names": names,
        "hyperparameters": dict(winner.get("hyperparameters") or {}),
        "threshold": float(winner["threshold"]),
        "feature_contract": feature_contract,
        "feature_fingerprint": feature_fingerprint,
        "config_fingerprint": config_fingerprint_value,
        "split_hash": split_hash,
        "validation_metrics": {key: winner.get(key) for key in ("precision", "recall", "f1", "f2", "false_positive_rate")},
    }
    return {**base, "experiment_hash": _stable_hash(base)}


def evaluate_locked_test(
    estimator: Any,
    test: pd.DataFrame,
    locked_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    missing = LOCK_REQUIRED_FIELDS - set(locked_candidate)
    if missing:
        raise ValueError(f"Locked candidate is missing: {', '.join(sorted(missing))}")
    names = validate_feature_columns(locked_candidate.get("feature_names") or [])
    scores = _anomaly_scores(estimator, test[names])
    threshold = float(locked_candidate["threshold"])
    metrics = binary_metrics(_binary(test[GROUND_TRUTH_COLUMN]), scores >= threshold)
    return {
        **metrics,
        "threshold": threshold,
        "margin_contract": "margin = anomaly_score - threshold",
        "experiment_hash": locked_candidate["experiment_hash"],
        "split_hash": locked_candidate["split_hash"],
        "evaluation_split": "test",
    }


def final_test_reuse_warning(
    locked_candidate: Mapping[str, Any],
    existing_result: Mapping[str, Any] | None,
) -> str | None:
    if not existing_result:
        return None
    if existing_result.get("split_hash") == locked_candidate.get("split_hash"):
        if existing_result.get("experiment_hash") == locked_candidate.get("experiment_hash"):
            return "Final test already exists for the same experiment_hash and split_hash."
        return (
            "Final test already exists for this split_hash; evaluating another candidate "
            "reuses the sealed Test set."
        )
    return None


def build_candidate_artifact(
    estimator: Any,
    locked_candidate: Mapping[str, Any],
    *,
    process_id: str,
    training_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build in memory only; the explicit writer below remains separately gated."""
    preprocessor = (
        estimator.named_steps.get("imputer")
        if hasattr(estimator, "named_steps")
        else None
    )
    return {
        "model": estimator,
        "preprocessor": preprocessor,
        "preprocessor_contract": "train-fitted state is embedded in model pipeline",
        "feature_names": list(locked_candidate.get("feature_names") or []),
        "threshold": float(locked_candidate["threshold"]),
        "process_id": str(process_id).lower(),
        "feature_contract": locked_candidate["feature_contract"],
        "feature_fingerprint": locked_candidate["feature_fingerprint"],
        "config_fingerprint": locked_candidate["config_fingerprint"],
        "training_metadata": dict(training_metadata),
        "validation_metrics": dict(locked_candidate.get("validation_metrics") or {}),
    }


def save_candidate_artifact(
    bundle: Mapping[str, Any],
    path: str | Path,
    *,
    create_candidate: bool = CREATE_CANDIDATE_DEFAULT,
) -> Path:
    if not create_candidate:
        raise PermissionError("Candidate artifact creation requires CREATE_CANDIDATE=True")
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(dict(bundle), destination)
    return destination


def experiment_manifest(
    *,
    process_id: str,
    source_file: str | Path,
    source_hash: str,
    feature_contract: str,
    feature_fingerprint: str,
    config_fingerprint_value: str,
    random_state: int,
    split_definition: Mapping[str, Any],
    candidate_grid_value: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "process_id": str(process_id).lower(),
        "source_file": str(Path(source_file).resolve()),
        "source_hash": source_hash,
        "feature_contract": feature_contract,
        "feature_fingerprint": feature_fingerprint,
        "config_fingerprint": config_fingerprint_value,
        "random_state": int(random_state),
        "split_definition": dict(split_definition),
        "candidate_grid": [dict(item) for item in candidate_grid_value],
        "library_versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
        },
    }
    return {"experiment_id": _stable_hash(payload)[:20], **payload}


def file_sha256(path: str | Path) -> str:
    source = Path(path).resolve()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_output_schema(filename: str, value: Mapping[str, Any] | pd.DataFrame) -> None:
    if filename not in OUTPUT_SCHEMAS:
        raise ValueError(f"Unknown FAB analysis output: {filename}")
    actual = set(value.columns) if isinstance(value, pd.DataFrame) else set(value)
    missing = OUTPUT_SCHEMAS[filename] - actual
    if missing:
        raise ValueError(f"{filename} is missing: {', '.join(sorted(missing))}")


def save_json_output(
    filename: str,
    value: Mapping[str, Any],
    *,
    output_dir: str | Path = ANALYSIS_OUTPUT_DIR,
) -> Path:
    validate_output_schema(filename, value)
    destination = Path(output_dir).resolve() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def save_table_output(
    filename: str,
    value: pd.DataFrame,
    *,
    output_dir: str | Path = ANALYSIS_OUTPUT_DIR,
) -> Path:
    validate_output_schema(filename, value)
    destination = Path(output_dir).resolve() / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    value.to_csv(destination, index=filename == "correlation_matrix.csv")
    return destination


def warn_if_final_test_reused(
    locked_candidate: Mapping[str, Any],
    existing_result: Mapping[str, Any] | None,
) -> None:
    message = final_test_reuse_warning(locked_candidate, existing_result)
    if message:
        warnings.warn(message, UserWarning, stacklevel=2)

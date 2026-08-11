"""Real sklearn training and artifact handling for Chamber Resistance."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from app.services.config import CHAMBER_MODEL_DIR, ROOT_DIR, ensure_runtime_dirs


BASE_NUMERIC_FEATURES = [
    "use_time_total",
    "use_time_since_clean",
    "wafer_count_since_clean",
    "chamber_pressure",
    "pressure_delta",
    "source_rf_power",
    "source_rf_delta",
    "bias_rf_power",
    "bias_rf_delta",
    "gas_1_flow",
    "gas_2_flow",
    "gas_3_flow",
    "total_gas_flow",
    "gas_flow_delta",
    "gas_ratio",
    "gas_ratio_delta",
    "chamber_temperature",
    "temperature_delta",
    "esc_temperature",
    "esc_temperature_delta",
]

CATEGORICAL_FEATURES = [
    "equipment_id",
    "recipe_id",
    "gas_1_name",
    "gas_2_name",
    "gas_3_name",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numeric_features(include_seasoning: bool = False) -> list[str]:
    result = list(BASE_NUMERIC_FEATURES)
    if include_seasoning:
        result.append("seasoning_level")
    return result


def feature_frame(
    rows: Iterable[dict[str, Any]], *, include_seasoning: bool = False
) -> pd.DataFrame:
    columns = numeric_features(include_seasoning) + CATEGORICAL_FEATURES
    frame = pd.DataFrame(list(rows))
    for column in columns:
        if column not in frame:
            frame[column] = np.nan if column in numeric_features(include_seasoning) else "unknown"
    for column in numeric_features(include_seasoning):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].fillna("unknown").astype(str)
    return frame[columns]


def _encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # pragma: no cover - sklearn < 1.2 compatibility
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def make_pipeline(*, include_seasoning: bool = False, random_state: int = 42) -> Pipeline:
    numeric = numeric_features(include_seasoning)
    preprocessor = ColumnTransformer(
        transformers=[
            ("numeric", Pipeline([("imputer", SimpleImputer(strategy="median"))]), numeric),
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", _encoder()),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=True,
    )
    regressor = GradientBoostingRegressor(
        loss="huber",
        n_estimators=180,
        learning_rate=0.05,
        max_depth=3,
        min_samples_leaf=3,
        random_state=random_state,
    )
    return Pipeline([("preprocessor", preprocessor), ("model", regressor)])


def _metrics(actual: np.ndarray, expected: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(actual, expected)),
        "rmse": float(math.sqrt(mean_squared_error(actual, expected))),
    }


def _time_split(rows: list[dict[str, Any]], holdout_fraction: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (str(row.get("observed_at", "")), str(row.get("id", ""))))
    holdout_size = max(1, int(round(len(ordered) * holdout_fraction)))
    split_at = len(ordered) - holdout_size
    if split_at < 2:
        raise ValueError("Not enough rows for a time-based holdout")
    return ordered[:split_at], ordered[split_at:]


def _residual_threshold(actual: np.ndarray, expected: np.ndarray) -> float:
    errors = np.abs(actual - expected)
    median = float(np.median(errors))
    mad = float(np.median(np.abs(errors - median)))
    robust = median + 5.0 * max(1.4826 * mad, 1e-6)
    # A few recipe-transition rows can have large synthetic residuals. A high
    # quantile would let those isolated points inflate the online threshold, so
    # use the robust training-error center/spread instead.
    return float(max(0.25, robust))


def _context_thresholds(
    rows: list[dict[str, Any]],
    actual: np.ndarray,
    expected: np.ndarray,
    *,
    minimum_rows: int,
) -> dict[str, Any]:
    thresholds: dict[str, Any] = {
        "global": _residual_threshold(actual, expected),
        "equipment_recipe": {},
        "equipment": {},
        "recipe": {},
    }
    dimensions = {
        "equipment_recipe": lambda row: f"{row.get('equipment_id', '-')}|{row.get('recipe_id', '-')}",
        "equipment": lambda row: str(row.get("equipment_id") or "-"),
        "recipe": lambda row: str(row.get("recipe_id") or "-"),
    }
    for dimension, key_fn in dimensions.items():
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            groups.setdefault(key_fn(row), []).append(index)
        for key, indices in groups.items():
            if len(indices) < minimum_rows:
                continue
            thresholds[dimension][key] = _residual_threshold(actual[indices], expected[indices])
    return thresholds


def resolve_threshold(bundle: dict[str, Any], row: dict[str, Any]) -> tuple[float, str]:
    """Resolve equipment+recipe → equipment → recipe → global threshold."""
    thresholds = bundle.get("thresholds") or {"global": bundle.get("threshold", 0.25)}
    equipment = str(row.get("equipment_id") or "-")
    recipe = str(row.get("recipe_id") or "-")
    candidates = (
        ("equipment_recipe", f"{equipment}|{recipe}"),
        ("equipment", equipment),
        ("recipe", recipe),
    )
    for dimension, key in candidates:
        value = thresholds.get(dimension, {}).get(key)
        if value is not None:
            return float(value), f"{dimension}:{key}"
    return float(thresholds.get("global", bundle.get("threshold", 0.25))), "global"


def _group_metrics(
    rows: list[dict[str, Any]],
    actual: np.ndarray,
    expected: np.ndarray,
    *,
    minimum_rows: int,
) -> dict[str, dict[str, dict[str, float | int]]]:
    result: dict[str, dict[str, dict[str, float | int]]] = {}
    for dimension, field in (("equipment", "equipment_id"), ("recipe", "recipe_id"), ("lot", "lot_id")):
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            key = str(row.get(field) or "unassigned")
            groups.setdefault(key, []).append(index)
        result[dimension] = {}
        for key, indices in groups.items():
            if len(indices) < minimum_rows:
                continue
            metrics = _metrics(actual[indices], expected[indices])
            result[dimension][key] = {**metrics, "rows": len(indices)}
    return result


def _feature_importance(pipeline: Pipeline) -> tuple[list[str], list[dict[str, float]]]:
    names = [str(name) for name in pipeline.named_steps["preprocessor"].get_feature_names_out()]
    importances = pipeline.named_steps["model"].feature_importances_
    ranked = sorted(
        ({"feature": name.replace("numeric__", "").replace("categorical__", ""), "importance": float(value)} for name, value in zip(names, importances)),
        key=lambda item: item["importance"],
        reverse=True,
    )
    return names, ranked


def artifact_path(version: str) -> Path:
    return CHAMBER_MODEL_DIR / f"{version}.joblib"


def resolve_artifact_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT_DIR / candidate


def load_artifact(model: dict[str, Any] | str | Path) -> dict[str, Any]:
    path = model.get("artifact_path") if isinstance(model, dict) else model
    resolved = resolve_artifact_path(str(path))
    if not resolved.is_file():
        raise FileNotFoundError(f"Chamber model artifact not found: {resolved}")
    bundle = joblib.load(resolved)
    if not isinstance(bundle, dict) or "pipeline" not in bundle or "version" not in bundle:
        raise ValueError(f"Invalid Chamber model artifact: {resolved}")
    if not hasattr(bundle["pipeline"], "predict"):
        raise ValueError(f"Chamber artifact has no usable pipeline: {resolved}")
    return bundle


def predict_rows(bundle: dict[str, Any], rows: list[dict[str, Any]]) -> np.ndarray:
    frame = feature_frame(rows, include_seasoning=bool(bundle.get("include_seasoning", False)))
    return np.asarray(bundle["pipeline"].predict(frame), dtype=float)


def train_model(
    rows: list[dict[str, Any]],
    *,
    version: str,
    stage: str,
    config: dict[str, Any],
    production: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model_config = config["model"]
    min_rows = int(model_config.get("min_training_rows", 80))
    if len(rows) < min_rows:
        raise ValueError(f"Need at least {min_rows} clean running rows; got {len(rows)}")
    started_at = utc_now()
    include_seasoning = bool(model_config.get("include_seasoning_feature", False))
    train_rows, holdout_rows = _time_split(rows, float(model_config.get("holdout_fraction", 0.2)))
    train_x = feature_frame(train_rows, include_seasoning=include_seasoning)
    train_y = np.asarray([float(row["resistance"]) for row in train_rows])
    holdout_x = feature_frame(holdout_rows, include_seasoning=include_seasoning)
    holdout_y = np.asarray([float(row["resistance"]) for row in holdout_rows])

    evaluation_pipeline = make_pipeline(include_seasoning=include_seasoning)
    evaluation_pipeline.fit(train_x, train_y)
    holdout_expected = np.asarray(evaluation_pipeline.predict(holdout_x), dtype=float)
    candidate_metrics = _metrics(holdout_y, holdout_expected)
    group_min_rows = max(2, int(model_config.get("group_metric_min_rows", 5)))
    group_metrics = _group_metrics(
        holdout_rows,
        holdout_y,
        holdout_expected,
        minimum_rows=group_min_rows,
    )
    train_expected = np.asarray(evaluation_pipeline.predict(train_x), dtype=float)
    training_threshold = _residual_threshold(train_y, train_expected)
    holdout_threshold = _residual_threshold(holdout_y, holdout_expected)
    threshold = max(training_threshold, holdout_threshold)
    threshold_min_rows = max(3, int(model_config.get("context_threshold_min_rows", 12)))
    thresholds = _context_thresholds(
        train_rows,
        train_y,
        train_expected,
        minimum_rows=threshold_min_rows,
    )
    thresholds["global"] = threshold
    context_floor = threshold * float(model_config.get("context_threshold_floor_multiplier", 0.75))
    for dimension in ("equipment_recipe", "equipment", "recipe"):
        thresholds[dimension] = {
            key: max(float(value), context_floor)
            for key, value in thresholds[dimension].items()
        }

    baseline = GradientBoostingRegressor(loss="huber", n_estimators=120, random_state=42)
    baseline.fit(np.asarray([[float(row["use_time_total"])] for row in train_rows]), train_y)
    baseline_expected = baseline.predict(np.asarray([[float(row["use_time_total"])] for row in holdout_rows]))
    baseline_metrics = _metrics(holdout_y, baseline_expected)

    production_metrics: dict[str, float] | None = None
    production_error: str | None = None
    if production:
        try:
            production_bundle = load_artifact(production)
            production_expected = predict_rows(production_bundle, holdout_rows)
            production_metrics = _metrics(holdout_y, production_expected)
        except (FileNotFoundError, ValueError, OSError) as exc:
            production_error = str(exc)

    passes_comparison = production_metrics is None or (
        candidate_metrics["mae"] <= production_metrics["mae"] * 1.10
        and candidate_metrics["rmse"] <= production_metrics["rmse"] * 1.10
    )

    final_pipeline = make_pipeline(include_seasoning=include_seasoning)
    all_x = feature_frame(rows, include_seasoning=include_seasoning)
    all_y = np.asarray([float(row["resistance"]) for row in rows])
    final_pipeline.fit(all_x, all_y)
    feature_names, importance = _feature_importance(final_pipeline)
    finished_at = utc_now()
    ensure_runtime_dirs()
    path = artifact_path(version)
    bundle = {
        "version": version,
        "pipeline": final_pipeline,
        "threshold": threshold,
        "thresholds": thresholds,
        "include_seasoning": include_seasoning,
        "feature_names": feature_names,
        "feature_importance": importance,
        "fitted_at": finished_at,
    }
    joblib.dump(bundle, path)
    # Reload immediately so a corrupt/incomplete artifact never reaches registry.
    load_artifact(path)
    relative_path = str(path.relative_to(ROOT_DIR)).replace("\\", "/")
    return {
        "version": version,
        "stage": stage,
        "model_type": "sklearn.pipeline.GradientBoostingRegressor",
        "artifact_path": relative_path,
        "mae": candidate_metrics["mae"],
        "rmse": candidate_metrics["rmse"],
        "threshold": threshold,
        "training_rows": len(rows),
        "feature_names": feature_names,
        "feature_importance": importance,
        "training_started_at": started_at,
        "training_ended_at": finished_at,
        "training_cutoff_at": max(str(row["observed_at"]) for row in rows),
        "registered_at": finished_at,
        "promoted_at": finished_at if stage == "Production" else None,
        "metadata": {
            "candidate_holdout": candidate_metrics,
            "use_time_only_holdout": baseline_metrics,
            "production_holdout": production_metrics,
            "production_evaluation_error": production_error,
            "holdout_rows": len(holdout_rows),
            "group_metrics": group_metrics,
            "context_thresholds": thresholds,
            "data_time_range": {
                "start": min(str(row["observed_at"]) for row in rows),
                "end": max(str(row["observed_at"]) for row in rows),
            },
            "passes_comparison": passes_comparison,
            "residual_threshold": threshold,
            "training_residual_threshold": training_threshold,
            "holdout_residual_threshold": holdout_threshold,
            "synthetic_data": True,
        },
    }

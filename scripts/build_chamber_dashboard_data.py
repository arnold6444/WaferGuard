from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import KFold


RANDOM_STATE = 42
N_SPLITS = 5
MAD_K = 3.0
ROW_ALPHA = 0.01
GB_PARAMS = {
    "n_estimators": 200,
    "learning_rate": 0.04,
    "max_depth": 2,
    "min_samples_leaf": 15,
    "loss": "huber",
    "random_state": RANDOM_STATE,
}


def make_model() -> GradientBoostingRegressor:
    return GradientBoostingRegressor(**GB_PARAMS)


def preprocess(path: Path) -> pd.DataFrame:
    dataframe = pd.read_csv(path)
    dataframe.columns = [str(column).strip().upper().replace(" ", "_") for column in dataframe.columns]
    required = {"DATE", "EQP", "USE_TIME", "RESISTANCE"}
    missing = required - set(dataframe.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    dataframe["DATE"] = pd.to_datetime(dataframe["DATE"], errors="coerce")
    dataframe["EQP"] = dataframe["EQP"].astype(str).str.strip()
    dataframe["USE_TIME"] = pd.to_numeric(dataframe["USE_TIME"], errors="coerce")
    dataframe["RESISTANCE"] = pd.to_numeric(dataframe["RESISTANCE"], errors="coerce")
    return (
        dataframe.dropna(subset=["DATE", "EQP", "USE_TIME", "RESISTANCE"])
        .sort_values(["EQP", "DATE"])
        .reset_index(drop=True)
    )


def group_oof_predict(dataframe: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = dataframe.copy().reset_index(drop=True)
    equipment = np.array(sorted(frame["EQP"].unique()))
    splitter = KFold(n_splits=min(N_SPLITS, len(equipment)), shuffle=True, random_state=RANDOM_STATE)
    predictions = np.full(len(frame), np.nan)
    folds: list[dict[str, float | int]] = []

    for fold_number, (train_idx, valid_idx) in enumerate(splitter.split(equipment), start=1):
        train_equipment = set(equipment[train_idx])
        valid_equipment = set(equipment[valid_idx])
        train_mask = frame["EQP"].isin(train_equipment)
        valid_mask = frame["EQP"].isin(valid_equipment)
        model = make_model()
        model.fit(frame.loc[train_mask, ["USE_TIME"]], frame.loc[train_mask, "RESISTANCE"])
        fold_prediction = model.predict(frame.loc[valid_mask, ["USE_TIME"]])
        predictions[valid_mask] = fold_prediction
        folds.append(
            {
                "fold": fold_number,
                "trainEquipment": len(train_equipment),
                "validEquipment": len(valid_equipment),
                "mae": mean_absolute_error(frame.loc[valid_mask, "RESISTANCE"], fold_prediction),
                "rmse": mean_squared_error(frame.loc[valid_mask, "RESISTANCE"], fold_prediction) ** 0.5,
            }
        )

    result = frame.copy()
    result["EXPECTED"] = predictions
    result["RESIDUAL"] = result["RESISTANCE"] - result["EXPECTED"]
    result["ABS_ERROR"] = result["RESIDUAL"].abs()
    return result, pd.DataFrame(folds)


def scaled_mad(values: pd.Series) -> float:
    array = np.asarray(values.dropna(), dtype=float)
    median = np.median(array)
    return float(1.4826 * np.median(np.abs(array - median)))


def robust_upper_threshold(values: pd.Series, k: float = MAD_K) -> tuple[float, float, float]:
    series = values.dropna().astype(float)
    center = float(series.median())
    scale = scaled_mad(series)
    if scale == 0:
        q1, q3 = float(series.quantile(0.25)), float(series.quantile(0.75))
        scale = (q3 - q1) / 1.349 if q3 > q1 else 1e-9
    return center + k * scale, center, scale


def summarize_equipment(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.groupby("EQP", as_index=False).agg(
        ROWS=("ABS_ERROR", "size"),
        MEDIAN_ABS_ERROR=("ABS_ERROR", "median"),
        MEAN_ABS_ERROR=("ABS_ERROR", "mean"),
        Q95_ABS_ERROR=("ABS_ERROR", lambda values: values.quantile(0.95)),
        MAX_ABS_ERROR=("ABS_ERROR", "max"),
        MEAN_SIGNED_ERROR=("RESIDUAL", "mean"),
    )


def max_consecutive(frame: pd.DataFrame) -> dict[str, int]:
    result: dict[str, int] = {}
    for equipment, group in frame.sort_values(["EQP", "DATE"]).groupby("EQP"):
        current = maximum = 0
        for value in group["ROW_ANOMALY"].astype(int):
            current = current + 1 if value else 0
            maximum = max(maximum, current)
        result[str(equipment)] = maximum
    return result


def number(value: object, digits: int = 4) -> float:
    return round(float(value), digits)


def build_payload(source: Path) -> dict[str, object]:
    sample = preprocess(source)
    sample_oof, fold_metrics = group_oof_predict(sample)
    stage1 = summarize_equipment(sample_oof)
    stage1_median_threshold, stage1_median_center, stage1_median_scale = robust_upper_threshold(
        stage1["MEDIAN_ABS_ERROR"]
    )
    stage1_q95_threshold, stage1_q95_center, stage1_q95_scale = robust_upper_threshold(stage1["Q95_ABS_ERROR"])
    stage1["IS_CANDIDATE"] = (
        (stage1["MEDIAN_ABS_ERROR"] > stage1_median_threshold)
        | (stage1["Q95_ABS_ERROR"] > stage1_q95_threshold)
    )
    stage1["SCORE"] = np.maximum(
        (stage1["MEDIAN_ABS_ERROR"] - stage1_median_center) / max(stage1_median_scale, 1e-9),
        (stage1["Q95_ABS_ERROR"] - stage1_q95_center) / max(stage1_q95_scale, 1e-9),
    )
    candidates = set(stage1.loc[stage1["IS_CANDIDATE"], "EQP"])

    normal_sample = sample[~sample["EQP"].isin(candidates)].copy()
    normal_oof, normal_fold_metrics = group_oof_predict(normal_sample)
    clean_model = make_model()
    clean_model.fit(normal_sample[["USE_TIME"]], normal_sample["RESISTANCE"])

    candidate_eval = sample[sample["EQP"].isin(candidates)].copy()
    candidate_eval["EXPECTED"] = clean_model.predict(candidate_eval[["USE_TIME"]])
    candidate_eval["RESIDUAL"] = candidate_eval["RESISTANCE"] - candidate_eval["EXPECTED"]
    candidate_eval["ABS_ERROR"] = candidate_eval["RESIDUAL"].abs()
    final_eval = (
        pd.concat([normal_oof, candidate_eval], ignore_index=True)
        .sort_values(["EQP", "DATE"])
        .reset_index(drop=True)
    )
    row_threshold = float(normal_oof["ABS_ERROR"].quantile(1 - ROW_ALPHA))
    final_eval["ROW_ANOMALY"] = (final_eval["ABS_ERROR"] > row_threshold).astype(int)

    normal_reference = summarize_equipment(normal_oof)
    median_threshold, median_center, median_scale = robust_upper_threshold(normal_reference["MEDIAN_ABS_ERROR"])
    q95_threshold, q95_center, q95_scale = robust_upper_threshold(normal_reference["Q95_ABS_ERROR"])

    summary = final_eval.groupby("EQP", as_index=False).agg(
        ROWS=("ABS_ERROR", "size"),
        MEDIAN_ABS_ERROR=("ABS_ERROR", "median"),
        MEAN_ABS_ERROR=("ABS_ERROR", "mean"),
        Q95_ABS_ERROR=("ABS_ERROR", lambda values: values.quantile(0.95)),
        MAX_ABS_ERROR=("ABS_ERROR", "max"),
        MEAN_SIGNED_ERROR=("RESIDUAL", "mean"),
        ANOMALY_ROW_COUNT=("ROW_ANOMALY", "sum"),
        ANOMALY_ROW_RATE=("ROW_ANOMALY", "mean"),
    )
    summary["MAX_CONSECUTIVE"] = summary["EQP"].map(max_consecutive(final_eval)).fillna(0).astype(int)
    summary["IS_ANOMALY"] = (
        (summary["MEDIAN_ABS_ERROR"] > median_threshold)
        | (summary["Q95_ABS_ERROR"] > q95_threshold)
    )
    summary["SCORE"] = np.maximum(
        (summary["MEDIAN_ABS_ERROR"] - median_center) / max(median_scale, 1e-9),
        (summary["Q95_ABS_ERROR"] - q95_center) / max(q95_scale, 1e-9),
    )
    summary = summary.sort_values(["IS_ANOMALY", "SCORE"], ascending=[False, False]).reset_index(drop=True)

    binned = sample.copy()
    binned["USE_BIN"] = pd.cut(binned["USE_TIME"], bins=25)
    pattern = (
        binned.groupby("USE_BIN", observed=False)
        .agg(
            useTime=("USE_TIME", "median"),
            median=("RESISTANCE", "median"),
            q25=("RESISTANCE", lambda values: values.quantile(0.25)),
            q75=("RESISTANCE", lambda values: values.quantile(0.75)),
        )
        .dropna()
        .reset_index(drop=True)
    )
    pattern["expected"] = clean_model.predict(pattern[["useTime"]].rename(columns={"useTime": "USE_TIME"}))
    pattern["iqr"] = pattern["q75"] - pattern["q25"]

    anomaly_map = []
    for row in summary.itertuples(index=False):
        anomaly_map.append(
            {
                "eqp": row.EQP,
                "medianError": number(row.MEDIAN_ABS_ERROR, 3),
                "q95Error": number(row.Q95_ABS_ERROR, 3),
                "score": number(row.SCORE, 2),
                "anomaly": bool(row.IS_ANOMALY),
                "anomalyRows": int(row.ANOMALY_ROW_COUNT),
                "consecutive": int(row.MAX_CONSECUTIVE),
            }
        )

    top_equipment = anomaly_map[:12]
    detail_equipment = {row["eqp"] for row in top_equipment}
    series: dict[str, list[dict[str, object]]] = {}
    for equipment in detail_equipment:
        equipment_frame = final_eval[final_eval["EQP"] == equipment]
        series[equipment] = [
            {
                "date": row.DATE.strftime("%m-%d"),
                "useTime": int(row.USE_TIME),
                "actual": number(row.RESISTANCE, 2),
                "expected": number(row.EXPECTED, 2),
                "error": number(row.ABS_ERROR, 2),
                "anomaly": bool(row.ROW_ANOMALY),
            }
            for row in equipment_frame.itertuples(index=False)
        ]

    sensitivity = []
    for alpha in [0.005, 0.01, 0.02, 0.05]:
        threshold = float(normal_oof["ABS_ERROR"].quantile(1 - alpha))
        flags = final_eval["ABS_ERROR"] > threshold
        sensitivity.append(
            {
                "percent": alpha * 100,
                "threshold": number(threshold, 3),
                "rows": int(flags.sum()),
                "equipment": int(final_eval.loc[flags, "EQP"].nunique()),
            }
        )

    return {
        "source": {
            "label": "sample.csv",
            "period": f"{sample['DATE'].min():%Y-%m-%d} ~ {sample['DATE'].max():%Y-%m-%d}",
            "rows": len(sample),
            "equipment": int(sample["EQP"].nunique()),
            "days": int(sample["DATE"].nunique()),
            "useTimeMin": int(sample["USE_TIME"].min()),
            "useTimeMax": int(sample["USE_TIME"].max()),
            "resistanceMean": number(sample["RESISTANCE"].mean(), 2),
            "resistanceStd": number(sample["RESISTANCE"].std(), 2),
        },
        "model": {
            "name": "Gradient Boosting Regressor",
            "validation": "EQP-grouped 5-fold OOF",
            "mae": number(mean_absolute_error(sample_oof["RESISTANCE"], sample_oof["EXPECTED"]), 3),
            "rmse": number(mean_squared_error(sample_oof["RESISTANCE"], sample_oof["EXPECTED"]) ** 0.5, 3),
            "normalMae": number(mean_absolute_error(normal_oof["RESISTANCE"], normal_oof["EXPECTED"]), 3),
            "folds": [
                {
                    "fold": int(row.fold),
                    "mae": number(row.mae, 3),
                    "rmse": number(row.rmse, 3),
                }
                for row in fold_metrics.itertuples(index=False)
            ],
            "normalFolds": [
                {
                    "fold": int(row.fold),
                    "mae": number(row.mae, 3),
                    "rmse": number(row.rmse, 3),
                }
                for row in normal_fold_metrics.itertuples(index=False)
            ],
        },
        "thresholds": {
            "madK": MAD_K,
            "rowAlpha": ROW_ALPHA,
            "rowError": number(row_threshold, 3),
            "medianError": number(median_threshold, 3),
            "q95Error": number(q95_threshold, 3),
        },
        "summary": {
            "anomalyEquipment": int(summary["IS_ANOMALY"].sum()),
            "normalEquipment": int((~summary["IS_ANOMALY"]).sum()),
            "flaggedRows": int(final_eval["ROW_ANOMALY"].sum()),
            "flaggedRate": number(final_eval["ROW_ANOMALY"].mean() * 100, 1),
            "stage1Candidates": sorted(candidates),
        },
        "pattern": [
            {
                "useTime": int(row.useTime),
                "median": number(row.median, 2),
                "q25": number(row.q25, 2),
                "q75": number(row.q75, 2),
                "iqr": number(row.iqr, 2),
                "expected": number(row.expected, 2),
            }
            for row in pattern.itertuples(index=False)
        ],
        "anomalyMap": anomaly_map,
        "topEquipment": top_equipment,
        "series": series,
        "sensitivity": sensitivity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static Chamber AI dashboard sample payload.")
    parser.add_argument("--input", type=Path, required=True, help="sample.csv path")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/src/data/chamberSample.json"),
        help="Generated JSON path",
    )
    args = parser.parse_args()
    payload = build_payload(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "equipment": payload["source"]["equipment"],
                "anomalyEquipment": payload["summary"]["anomalyEquipment"],
                "stage1Candidates": payload["summary"]["stage1Candidates"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.fab_analysis import (
    BASELINE_PREDICTION_COLUMN,
    GROUND_TRUTH_COLUMN,
    build_workbench_dashboard_summary,
)
from app.services.fab_experiment import (
    CREATE_CANDIDATE_DEFAULT,
    RUN_EXPERIMENT_DEFAULT,
    RUN_FINAL_TEST_DEFAULT,
    audit_label_sources,
    binary_metrics,
    build_candidate_leaderboard,
    build_feature_sets,
    candidate_grid,
    context_false_positives,
    detection_delays,
    error_examples,
    final_test_reuse_warning,
    false_alarms_per_hour,
    grouped_chronological_split,
    lock_validation_winner,
    seed_robustness,
    select_validation_threshold,
    validate_feature_columns,
    validate_output_schema,
    validate_split_integrity,
)
from scripts.run_fab_analysis import parser


def _frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for run_index in range(1, 7):
        for tick in range(2):
            rows.append(
                {
                    "message_id": f"M-{run_index}-{tick}",
                    "process_run_id": f"R-{run_index}",
                    "wafer_id": f"W-{run_index}",
                    "equipment_id": "CMP-1" if run_index < 5 else "CMP-2",
                    "unit_id": "P-A",
                    "recipe_id": "CMP-A",
                    "observed_at": f"2026-01-{run_index:02d}T00:00:0{tick}+00:00",
                    "z:slurry_flow": float(run_index),
                    "delta:slurry_flow": float(tick),
                    "rel:slurry_motor": float(run_index - tick),
                    "roll_mean_delta:slurry_flow": float(run_index) / 2,
                    "roll_std:slurry_flow": 0.1,
                    "context:phase:polish": 1.0,
                    GROUND_TRUTH_COLUMN: run_index in {2, 5},
                    "ground_truth_fault_type": (
                        "cmp_slurry_degradation" if run_index in {2, 5} else None
                    ),
                    "ground_truth_started_at": (
                        f"2026-01-{run_index:02d}T00:00:00+00:00"
                        if run_index in {2, 5}
                        else None
                    ),
                    "ground_truth_source": "simulation_faults",
                    BASELINE_PREDICTION_COLUMN: run_index == 5 and tick == 1,
                    "baseline_prediction_source": "fab_detector_results",
                }
            )
    return pd.DataFrame(rows)


def test_workbench_defaults_do_not_execute_training_or_artifact_paths() -> None:
    assert RUN_EXPERIMENT_DEFAULT is False
    assert RUN_FINAL_TEST_DEFAULT is False
    assert CREATE_CANDIDATE_DEFAULT is False
    args = parser().parse_args([])
    assert args.run_experiment is False
    assert args.run_final_test is False
    assert args.create_candidate is False
    assert seed_robustness(pd.DataFrame({"synthetic_seed": [1]}), [False])["status"] == "ground-truth unavailable"


def test_label_sources_and_feature_sets_are_leakage_safe() -> None:
    frame = _frame()
    audit = audit_label_sources(frame)
    assert audit["ground_truth_source"] == ["simulation_faults"]
    assert audit["baseline_prediction_source"] == ["fab_detector_results"]
    assert audit["faulted_process_runs"] == 2

    features = build_feature_sets(
        [
            "z:slurry_flow",
            "delta:slurry_flow",
            "rel:slurry_motor",
            "roll_mean_delta:slurry_flow",
            "roll_std:slurry_flow",
            "context:phase:polish",
        ]
    )
    assert features["level_only"] == ["z:slurry_flow"]
    assert features["level_change"] == ["z:slurry_flow", "delta:slurry_flow"]
    assert "context:phase:polish" in features["all_features"]
    with pytest.raises(ValueError, match="cannot be features"):
        validate_feature_columns(["z:slurry_flow", GROUND_TRUTH_COLUMN])
    with pytest.raises(ValueError, match="not ground truth"):
        audit_label_sources(pd.DataFrame({"is_anomaly": [True]}))
    with pytest.raises(ValueError, match="explicit ground_truth_source"):
        audit_label_sources(pd.DataFrame({GROUND_TRUTH_COLUMN: [True]}))
    with pytest.raises(ValueError, match="cannot be features"):
        validate_feature_columns(["z:ground_truth_is_anomaly"])
    with pytest.raises(ValueError, match="cannot be features"):
        validate_feature_columns(["context:wafer_id"])
    with pytest.raises(ValueError, match="cannot be features"):
        validate_feature_columns(["z:baseline_detector_is_anomaly"])


def test_split_is_chronological_grouped_and_normal_only() -> None:
    splits = grouped_chronological_split(_frame())
    validate_split_integrity(splits.train, splits.validation, splits.test)

    assert set(splits.train["process_run_id"]) == {"R-1", "R-3"}
    assert set(splits.validation["process_run_id"]) == {"R-4"}
    assert set(splits.test["process_run_id"]) == {"R-5", "R-6"}
    excluded = splits.manifest.set_index("process_run_id").loc["R-2"]
    assert excluded["split"] == "train"
    assert bool(excluded["included_in_training"]) is False
    assert splits.normal_only_applied is True
    assert splits.manifest.loc[
        splits.manifest["split"].eq("test"), "ground_truth_is_anomaly"
    ].isna().all()

    leaking_validation = pd.concat([splits.validation, splits.train.head(1)])
    with pytest.raises(ValueError, match="process_run leakage"):
        validate_split_integrity(splits.train, leaking_validation, splits.test)

    overlapping = _frame()
    overlapping.loc[overlapping["wafer_id"].eq("W-1"), "observed_at"] = [
        "2026-01-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00"
    ]
    with pytest.raises(ValueError, match="non-overlapping chronological"):
        grouped_chronological_split(overlapping)


def test_predefined_scores_select_validation_threshold_and_metrics() -> None:
    scores = np.asarray([-2.0, -1.0, 0.0, 1.0])
    truth = np.asarray([False, False, True, True])

    selected = select_validation_threshold(scores, truth)

    assert selected["threshold"] == 0.0
    assert selected["f1"] == 1.0
    assert selected["f2"] == 1.0
    assert selected["false_positive_rate"] == 0.0
    assert binary_metrics(truth, scores >= selected["threshold"])["recall"] == 1.0
    with pytest.raises(ValueError, match="only on the validation"):
        select_validation_threshold(scores, truth, split_name="test")


def test_time_fault_and_context_metrics_use_predefined_predictions() -> None:
    frame = _frame()
    predicted = np.zeros(len(frame), dtype=bool)
    predicted[1] = True  # one normal false alarm in R-1
    predicted[frame.index[(frame["process_run_id"] == "R-5") & frame["observed_at"].str.endswith("01+00:00")]] = True

    rate = false_alarms_per_hour(frame, predicted)
    delays = detection_delays(frame, predicted)
    contexts = context_false_positives(frame, predicted)

    assert rate is not None and rate > 0
    fallback_rate = false_alarms_per_hour(
        frame.drop(columns="observed_at"),
        predicted,
        sampling_interval_seconds=1.0,
    )
    assert fallback_rate is not None and fallback_rate > 0
    r5 = delays.set_index("process_run_id").loc["R-5"]
    assert r5["delay_seconds"] == 1.0
    assert bool(r5["missed"]) is False
    assert set(contexts.groupby("context")["false_positives"].sum()) == {1}
    examples = error_examples(frame, predicted)
    assert set(examples["error_type"]) == {"false_positive", "false_negative"}


def test_candidate_configs_leaderboard_lock_and_output_contracts() -> None:
    assert {row["model"] for row in candidate_grid()} == {
        "robust_z_univariate",
        "mahalanobis_multivariate",
        "isolation_forest",
        "one_class_svm",
    }
    base = {
        "hyperparameters": {},
        "precision": 0.8,
        "recall": 0.8,
        "f1": 0.8,
        "false_positive_rate": 0.1,
        "false_alarms_per_hour": 1.0,
        "threshold": -0.5,
        "selection_split": "validation",
    }
    leaderboard = build_candidate_leaderboard(
        [
            {**base, "candidate_id": "a", "model": "robust_z_univariate", "feature_set": "level_only", "f2": 0.7},
            {**base, "candidate_id": "b", "model": "isolation_forest", "feature_set": "all_features", "f2": 0.9},
        ]
    )
    assert leaderboard.iloc[0]["candidate_id"] == "b"
    winner = leaderboard.iloc[0].to_dict()
    locked = lock_validation_winner(
        winner,
        feature_names=["z:slurry_flow"],
        feature_contract="fab-context-v1",
        config_fingerprint_value="config-sha",
        split_hash="split-sha",
    )
    validate_output_schema("locked_candidate.json", locked)
    validate_output_schema("candidate_leaderboard.csv", leaderboard)
    assert "already exists" in final_test_reuse_warning(
        locked,
        {
            "experiment_hash": locked["experiment_hash"],
            "split_hash": locked["split_hash"],
        },
    )
    assert "reuses the sealed Test set" in final_test_reuse_warning(
        locked,
        {"experiment_hash": "different-candidate", "split_hash": locked["split_hash"]},
    )


def test_workbench_dashboard_summary_preserves_legacy_panel_keys() -> None:
    summary = build_workbench_dashboard_summary(
        process_id="cmp",
        dataset={"rows": 12},
        baseline_metrics={"f2": 0.4},
        validation_winner=None,
        final_test_metrics=None,
        existing_summary={
            "feature_importance": [{"feature": "z:x", "importance": 0.5}],
            "top_correlations": [{"left": "z:x", "right": "z:y"}],
            "model": {"metrics": {"legacy": True}},
        },
    )

    assert summary["schema_version"] == "fab-analysis.v1"
    assert summary["model"]["metrics"]["legacy"] is True
    assert summary["feature_importance"][0]["feature"] == "z:x"
    assert summary["experiment_status"] == "not_run"


def test_notebook_is_valid_json_with_all_execution_switches_off() -> None:
    notebook_path = Path(__file__).resolve().parents[1] / "notebooks" / "fab_local_analysis.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell.get("cell_type") == "code"
    )

    assert "RUN_EXPERIMENT = False" in source
    assert "RUN_FINAL_TEST = False" in source
    assert "CREATE_CANDIDATE = False" in source
    assert ".fit(" not in source
    assert "grouped_chronological_split," in source
    assert "display(df.head())" not in source
    assert "Test values and labels remain sealed" in source
    validation_cells = [
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if "run_validation_experiment(" in "".join(cell.get("source", []))
    ]
    assert validation_cells and all("if RUN_EXPERIMENT:" in cell for cell in validation_cells)
    assert all(cell.get("execution_count") is None for cell in notebook["cells"] if cell.get("cell_type") == "code")

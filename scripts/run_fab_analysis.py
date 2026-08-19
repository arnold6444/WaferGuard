from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.fab_analysis import (  # noqa: E402
    ANALYSIS_SCHEMA_VERSION,
    BASELINE_PREDICTION_COLUMN,
    FAB_ANALYSIS_OUTPUT_DIR,
    GROUND_TRUTH_COLUMN,
    DEFAULT_INPUT_PATH,
    build_workbench_dashboard_summary,
    export_runtime_features,
    load_local_dataset,
    save_workbench_dashboard_summary,
)
from app.services.fab_experiment import (  # noqa: E402
    CANDIDATE_SEARCH_SPACE,
    baseline_metrics,
    build_candidate_artifact,
    build_feature_sets,
    candidate_grid,
    context_false_positives,
    evaluate_locked_test,
    experiment_manifest,
    file_sha256,
    grouped_chronological_split,
    lock_validation_winner,
    non_training_feature_analysis,
    per_fault_metrics,
    run_validation_experiment,
    save_candidate_artifact,
    save_json_output,
    save_table_output,
    warn_if_final_test_reused,
    workbench_dataset_profile,
)
from app.services.fab_schema import FAB_FEATURE_CONTRACT, config_fingerprint  # noqa: E402


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Prepare the leakage-safe FAB anomaly workbench (no fitting by default)."
    )
    value.add_argument("--input", default=str(DEFAULT_INPUT_PATH), help="Local CSV or Parquet file")
    value.add_argument("--output-dir", default=str(FAB_ANALYSIS_OUTPUT_DIR))
    value.add_argument(
        "--process",
        choices=("photo", "etch", "deposition", "cmp", "cleaning"),
        default="cmp",
    )
    value.add_argument("--seed", type=int, default=42)
    value.add_argument("--export-runtime", action="store_true")
    value.add_argument("--run-experiment", action="store_true")
    value.add_argument("--run-final-test", action="store_true")
    value.add_argument("--create-candidate", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.run_final_test and not args.run_experiment:
        raise ValueError("--run-final-test requires --run-experiment in the same explicit run")
    if args.create_candidate and not args.run_experiment:
        raise ValueError("--create-candidate requires --run-experiment")

    source = Path(args.input)
    if args.export_runtime:
        source = export_runtime_features(args.process, source)
    frame = load_local_dataset(source)
    runtime_prefixes = ("z:", "delta:", "rel:", "roll_mean_delta:", "roll_std:", "context:")
    runtime_features = [name for name in frame.columns if str(name).startswith(runtime_prefixes)]
    feature_sets = build_feature_sets(runtime_features)
    splits = grouped_chronological_split(frame)
    development = pd.concat([splits.train, splits.validation], ignore_index=True)
    profile = workbench_dataset_profile(frame, label_audit_frame=development)
    feature_analysis = non_training_feature_analysis(development, feature_sets)
    output_dir = Path(args.output_dir)

    outputs: dict[str, str] = {}
    outputs["dataset_profile"] = str(
        save_json_output("dataset_profile.json", profile, output_dir=output_dir)
    )
    outputs["split_manifest"] = str(
        save_table_output("split_manifest.csv", splits.manifest, output_dir=output_dir)
    )
    outputs["feature_importance"] = str(
        save_table_output(
            "feature_importance.csv",
            feature_analysis["feature_effects"],
            output_dir=output_dir,
        )
    )
    outputs["correlation_matrix"] = str(
        save_table_output(
            "correlation_matrix.csv",
            feature_analysis["correlation"],
            output_dir=output_dir,
        )
    )

    contract = (
        str(frame["feature_contract"].dropna().iloc[0])
        if "feature_contract" in frame and frame["feature_contract"].notna().any()
        else FAB_FEATURE_CONTRACT
    )
    feature_fp = config_fingerprint(
        {"feature_contract": contract, "feature_names": feature_sets["all_features"]}
    )
    config_fp = (
        str(frame["config_fingerprint"].dropna().iloc[0])
        if "config_fingerprint" in frame and frame["config_fingerprint"].notna().any()
        else "unavailable"
    )
    manifest = experiment_manifest(
        process_id=args.process,
        source_file=source,
        source_hash=file_sha256(source),
        feature_contract=contract,
        feature_fingerprint=feature_fp,
        config_fingerprint_value=config_fp,
        random_state=args.seed,
        split_definition={"ratios": [0.6, 0.2, 0.2], "split_hash": splits.split_hash},
        candidate_grid_value=candidate_grid(CANDIDATE_SEARCH_SPACE),
    )
    outputs["experiment_manifest"] = str(
        save_json_output("experiment_manifest.json", manifest, output_dir=output_dir)
    )

    validation_winner = None
    final_metrics = None
    fault_metrics = []
    experiment = None
    if args.run_experiment:
        experiment = run_validation_experiment(
            splits,
            feature_sets,
            search_space=CANDIDATE_SEARCH_SPACE,
            random_state=args.seed,
        )
        leaderboard = experiment["leaderboard"]
        outputs["candidate_leaderboard"] = str(
            save_table_output("candidate_leaderboard.csv", leaderboard, output_dir=output_dir)
        )
        outputs["validation_metrics"] = str(
            save_table_output("validation_metrics.csv", leaderboard, output_dir=output_dir)
        )
        winner = leaderboard.iloc[0].to_dict()
        validation_winner = lock_validation_winner(
            winner,
            feature_names=feature_sets[winner["feature_set"]],
            feature_contract=contract,
            config_fingerprint_value=config_fp,
            split_hash=splits.split_hash,
        )
        outputs["locked_candidate"] = str(
            save_json_output("locked_candidate.json", validation_winner, output_dir=output_dir)
        )
        model = experiment["fitted_candidates"][winner["candidate_id"]]
        validation_scores = -model.decision_function(
            splits.validation[validation_winner["feature_names"]]
        )
        validation_predicted = validation_scores >= validation_winner["threshold"]
        fault_table = per_fault_metrics(splits.validation, validation_predicted)
        context_table = context_false_positives(splits.validation, validation_predicted)
        fault_metrics = fault_table.to_dict("records")
        outputs["per_fault_metrics"] = str(
            save_table_output("per_fault_metrics.csv", fault_table, output_dir=output_dir)
        )
        outputs["context_false_positives"] = str(
            save_table_output(
                "context_false_positives.csv", context_table, output_dir=output_dir
            )
        )
        if args.run_final_test:
            existing_final = None
            existing_final_path = output_dir / "final_test_metrics.json"
            if existing_final_path.is_file():
                try:
                    existing_final = json.loads(existing_final_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing_final = None
            warn_if_final_test_reused(validation_winner, existing_final)
            final_metrics = evaluate_locked_test(model, splits.test, validation_winner)
            outputs["final_test_metrics"] = str(
                save_json_output("final_test_metrics.json", final_metrics, output_dir=output_dir)
            )
        if args.create_candidate:
            bundle = build_candidate_artifact(
                model,
                validation_winner,
                process_id=args.process,
                training_metadata={
                    "train_rows": len(splits.train),
                    "validation_rows": len(splits.validation),
                    "experiment_hash": validation_winner["experiment_hash"],
                },
            )
            outputs["candidate_artifact"] = str(
                save_candidate_artifact(
                    bundle,
                    output_dir / f"{args.process}-locked-candidate.joblib",
                    create_candidate=True,
                )
            )

    baseline_comparison = baseline_metrics(
        splits.test if final_metrics is not None else splits.validation
    )
    if baseline_comparison:
        baseline_comparison = {
            **baseline_comparison,
            "evaluation_split": "test" if final_metrics is not None else "validation",
        }
    dashboard = build_workbench_dashboard_summary(
        process_id=args.process,
        dataset=profile,
        baseline_metrics=baseline_comparison,
        validation_winner=validation_winner,
        final_test_metrics=final_metrics,
        per_fault_metrics=fault_metrics,
    )
    dashboard["schema_version"] = ANALYSIS_SCHEMA_VERSION
    dashboard["feature_importance"] = feature_analysis["feature_effects"].head(20).to_dict("records")
    dashboard["artifacts"] = outputs
    outputs["dashboard_summary"] = str(
        save_workbench_dashboard_summary(dashboard, output_dir / "dashboard_summary.json")
    )
    print(
        json.dumps(
            {
                "process_id": args.process,
                "experiment_status": "validated" if experiment else "not_run",
                "final_test_status": "executed" if final_metrics else "sealed",
                "candidate_status": "created" if args.create_candidate else "not_created",
                "ground_truth_column": GROUND_TRUTH_COLUMN,
                "baseline_prediction_column": BASELINE_PREDICTION_COLUMN,
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

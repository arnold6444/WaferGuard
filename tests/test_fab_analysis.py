from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.fab_analysis import (
    build_candidate_bundle,
    latest_dashboard_summary,
    run_local_analysis,
)
from app.services.fab_generator import load_fab_config
from app.services.fab_schema import FAB_FEATURE_CONTRACT, config_fingerprint


def _frame(rows: int = 48) -> pd.DataFrame:
    values = np.linspace(-2.0, 2.0, rows)
    return pd.DataFrame(
        {
            "feature_a": values,
            "feature_b": values * 0.75 + 0.1,
            "feature_c": np.sin(values),
            "is_anomaly": values > 1.0,
        }
    )


def test_local_analysis_writes_dashboard_contract(tmp_path: Path) -> None:
    source = tmp_path / "training.csv"
    output = tmp_path / "analysis"
    _frame().to_csv(source, index=False)

    summary = run_local_analysis(source, process_id="cmp", output_dir=output)

    assert summary["schema_version"] == "fab-analysis.v1"
    assert summary["status"] == "ready"
    assert summary["dataset"]["rows"] == 48
    assert summary["feature_importance"]
    assert summary["top_correlations"][0]["left"] in {"feature_a", "feature_b", "feature_c"}
    assert (output / "feature_importance.csv").is_file()
    assert (output / "correlation_matrix.csv").is_file()
    loaded = latest_dashboard_summary(output / "dashboard_summary.json")
    assert loaded["process_id"] == "cmp"


def test_candidate_rejects_arbitrary_csv_without_runtime_fingerprint() -> None:
    with pytest.raises(ValueError, match="feature_contract is required"):
        build_candidate_bundle(
            _frame(),
            process_id="cmp",
            feature_columns=["feature_a", "feature_b", "feature_c"],
        )


def test_candidate_uses_exact_runtime_contract() -> None:
    frame = _frame()
    names = ["feature_a", "feature_b", "feature_c"]
    frame["feature_contract"] = FAB_FEATURE_CONTRACT
    frame["feature_fingerprint"] = config_fingerprint(
        {"feature_contract": FAB_FEATURE_CONTRACT, "feature_names": names}
    )
    frame["config_fingerprint"] = config_fingerprint(load_fab_config())

    bundle, metrics = build_candidate_bundle(
        frame,
        process_id="cmp",
        feature_columns=names,
        contamination=0.1,
        version="fab-cmp-local-test",
    )

    assert bundle["feature_names"] == names
    assert bundle["version"] == "fab-cmp-local-test"
    assert len(bundle["model"].decision_function(frame[names].head(2))) == 2
    assert set(metrics) == {"precision", "recall", "f2", "false_positive_rate"}

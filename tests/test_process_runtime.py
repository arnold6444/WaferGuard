from __future__ import annotations

import numpy as np

from app.services.process_runtime import (
    _best_threshold,
    _profile,
    _vision_image,
    evaluate_candidates,
    load_config,
    vision_features,
)
from app.services.process_stream_live import _live_metrics
from app.services.process_temporal import (
    TEMPORAL_GENERATOR_VERSION,
    TemporalProcessGenerator,
    evaluate_temporal_candidates,
)


def test_profiles_cover_core_processes():
    config = load_config()
    assert {"photo", "etch", "deposition", "cmp", "cleaning"}.issubset(config["profiles"])
    for profile in config["profiles"].values():
        assert profile["timeseries"]["tags"]
        assert profile["timeseries"]["anomalies"]
        assert profile["vision"]["defects"]


def test_vision_defect_changes_features():
    config, profile = _profile("cmp")
    rng = np.random.default_rng(7)
    size = int(config["runtime"]["image_size"])
    normal, _ = _vision_image("cmp", profile, rng, size)
    scratch, related = _vision_image("cmp", profile, rng, size, "scratch")
    delta = np.abs(vision_features(normal) - vision_features(scratch)).sum()
    assert delta > 0.05
    assert "down_force" in related


def test_threshold_search_prefers_separated_scores():
    scores = np.asarray([-0.5, -0.4, -0.3, 1.2, 1.4, 1.8])
    labels = np.asarray([0, 0, 0, 1, 1, 1])
    threshold, metrics = _best_threshold(scores, labels, beta=2.0)
    assert -0.3 < threshold <= 1.4
    assert metrics["recall"] >= 2 / 3
    assert metrics["f2"] > 0.8


def test_candidate_training_runs_for_both_modalities():
    time_result = evaluate_temporal_candidates("deposition", seed=13)
    vision_result = evaluate_candidates("photo", "vision", seed=13)
    assert time_result["winner"]["f2"] > 0.45
    assert vision_result["winner"]["f2"] > 0.7
    assert time_result["generator_version"] == TEMPORAL_GENERATOR_VERSION
    assert time_result["window_size"] == 8
    assert {row["name"] for row in time_result["candidates"]} == {
        "robust_z_univariate",
        "mahalanobis_multivariate",
        "isolation_forest",
        "one_class_svm",
    }


def test_temporal_generator_is_continuous_phase_aware_and_windowed():
    generator = TemporalProcessGenerator("cmp", seed=19)
    rows = [generator.next() for _ in range(75)]
    motor = np.asarray([row[1]["motor_current"] for row in rows])
    deltas = np.abs(np.diff(motor))
    assert np.median(deltas) < 0.35
    phases = {row[3]["phase"] for row in rows}
    assert {"load", "ramp", "polish", "rinse"}.issubset(phases)
    assert any(name.startswith("roll_mean_delta:") for name in generator.feature_names)
    assert any(name.startswith("roll_std:") for name in generator.feature_names)


def test_temporal_generator_preserves_configured_relationship_and_breaks_it_on_anomaly():
    normal = TemporalProcessGenerator("cmp", seed=23)
    normal_rows = [normal.next()[1] for _ in range(140)]
    normal_force = np.asarray([row["down_force"] for row in normal_rows])
    normal_current = np.asarray([row["motor_current"] for row in normal_rows])
    normal_corr = float(np.corrcoef(normal_force, normal_current)[0, 1])

    broken = TemporalProcessGenerator("cmp", seed=23)
    [broken.next() for _ in range(70)]
    anomaly_rows = [broken.next("correlation_break")[1] for _ in range(70)]
    anomaly_force = np.asarray([row["down_force"] for row in anomaly_rows])
    anomaly_current = np.asarray([row["motor_current"] for row in anomaly_rows])
    anomaly_corr = float(np.corrcoef(anomaly_force, anomaly_current)[0, 1])

    assert normal_corr > 0.45
    assert anomaly_corr < normal_corr - 0.15


def test_temporal_anomaly_types_change_runtime_features():
    for process_id in ("photo", "deposition", "cmp"):
        _, profile = _profile(process_id)
        anomaly_names = list(profile["timeseries"]["anomalies"])
        for anomaly in anomaly_names:
            normal = TemporalProcessGenerator(process_id, seed=31)
            anomalous = TemporalProcessGenerator(process_id, seed=31)
            [normal.next() for _ in range(30)]
            [anomalous.next() for _ in range(30)]
            normal_vector = normal.next()[0]
            anomaly_vector = anomalous.next(anomaly)[0]
            assert np.linalg.norm(anomaly_vector - normal_vector) > 1e-6, (process_id, anomaly)


def test_live_metrics_use_only_supplied_history():
    history = [
        {"timeseries": {"ground_truth": False, "flag": False}, "vision": {"ground_truth": False, "flag": False}},
        {"timeseries": {"ground_truth": True, "flag": True}, "vision": {"ground_truth": True, "flag": False}},
    ]
    metrics = _live_metrics(history)
    assert metrics["rows"] == 4
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 0.5
    assert metrics["scope"].startswith("current live history")

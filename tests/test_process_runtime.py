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


def test_profiles_cover_core_processes():
    config = load_config()
    assert {"photo", "etch", "deposition", "cmp"}.issubset(config["profiles"])
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
    time_result = evaluate_candidates("deposition", "timeseries", seed=13)
    vision_result = evaluate_candidates("photo", "vision", seed=13)
    assert time_result["winner"]["f2"] > 0.8
    assert vision_result["winner"]["f2"] > 0.7
    assert {row["name"] for row in time_result["candidates"]} == {"isolation_forest", "one_class_svm"}

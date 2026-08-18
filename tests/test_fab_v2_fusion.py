from __future__ import annotations

from collections.abc import Mapping

from app.services.fab_fusion import fuse_detector_results
from app.services.fab_rca import build_rca, evaluate_rca
from app.services.fab_schema import DetectorResult


def _result(modality: str, raw_score: float, threshold: float, tags=()):
    return DetectorResult.from_score(
        process_run_id="PR-1",
        modality=modality,
        model_version=f"{modality}-v1",
        raw_score=raw_score,
        threshold=threshold,
        related_tags=tags,
        observed_at="2026-01-01T00:10:00+00:00",
    ).to_dict()


def test_signed_margin_handles_negative_model_scores():
    anomalous = _result("timeseries", -0.10, -0.20)
    normal = _result("timeseries", -0.30, -0.20)
    assert anomalous["is_anomaly"] is True
    assert anomalous["margin"] > 0
    assert normal["is_anomaly"] is False
    assert normal["margin"] < 0


def test_missing_modality_fusion_renormalizes_available_weights():
    config = {
        "threshold": 0.65,
        "calibration_scale": 1.0,
        "weights": {"timeseries": 0.5, "vision": 0.3, "metrology": 0.2},
    }
    fused = fuse_detector_results([
        _result("timeseries", 1.2, 0.2, ["slurry_flow"]),
        _result("metrology", 2.0, 1.0, ["slurry_flow"]),
    ], config)
    assert fused["missing_modalities"] == ["vision"]
    assert abs(sum(fused["normalized_weights"].values()) - 1.0) < 1e-8
    assert fused["normalized_weights"] == {
        "timeseries": 0.71428571,
        "metrology": 0.28571429,
    }
    assert fused["margin"] == fused["raw_score"] - fused["threshold"]


class TruthTrap(Mapping):
    """Mapping that fails if RCA tries to read simulator-truth fields."""

    def __init__(self, value):
        self.value = value

    def __getitem__(self, key):
        if key in {"simulation_faults", "_simulation_truth", "ground_truth", "fault_id"}:
            raise AssertionError(f"RCA accessed simulator truth: {key}")
        return self.value[key]

    def __iter__(self):
        return iter(self.value)

    def __len__(self):
        return len(self.value)

    def get(self, key, default=None):
        if key in {"simulation_faults", "_simulation_truth", "ground_truth", "fault_id"}:
            raise AssertionError(f"RCA accessed simulator truth: {key}")
        return self.value.get(key, default)


def test_rca_uses_persisted_evidence_with_list_metrology_and_separate_evaluation():
    detectors = [
        _result("timeseries", 2.0, 0.2, ["slurry_flow", "motor_current"]),
        _result("vision", 1.5, 1.0, ["slurry_flow"]),
        _result("metrology", 2.4, 1.0, ["slurry_flow", "motor_current"]),
    ]
    fusion = fuse_detector_results(detectors)
    detail = TruthTrap({
        "identity": {"process_run_id": "PR-1", "process_id": "cmp"},
        "telemetry": [
            {"detector_context": {"normalized_values": {"slurry_flow": -3.2, "motor_current": 2.4}}}
        ],
        "metrology": [{
            "metrics": {"remaining_film_nm": 46.0, "within_wafer_nonuniformity_percent": 5.0},
            "quality_targets": {
                "remaining_film_nm": {"target": 42.0, "tolerance": 2.5},
                "within_wafer_nonuniformity_percent": {"target": 3.0, "tolerance": 1.2},
            },
        }],
        "inspections": [{"image_key": "fab/PR-1/inspection.png"}],
        "simulation_faults": "must not be read",
    })
    rca = build_rca(detail, detectors, fusion)
    assert rca["uses_simulation_ground_truth"] is False
    assert rca["source"] == "persisted_observed_evidence"
    assert rca["candidates"][0]["cause_id"] == "cmp_slurry_degradation"
    assert all(item["explanation"].startswith("Candidate root cause:") for item in rca["candidates"])
    evaluation = evaluate_rca(rca, "cmp_slurry_degradation")
    assert evaluation["top_1"] is True
    assert evaluation["top_3"] is True
    assert evaluation["evaluation_only"] is True

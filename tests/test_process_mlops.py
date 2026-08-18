import pytest

from app.services import process_mlops


def test_health_marks_runtime_degradation(monkeypatch):
    monkeypatch.setattr(
        process_mlops,
        "production_model",
        lambda *_: {"f2": 0.90, "false_positive_rate": 0.05},
    )
    monkeypatch.setattr(
        process_mlops,
        "runtime_metrics",
        lambda *_, **__: {"rows": 50, "f2": 0.72, "false_positive_rate": 0.08},
    )
    result = process_mlops.model_health("cmp", "vision")
    assert result["degraded"] is True
    assert result["reason"] == "runtime_metric_degradation"


def test_promotion_rejects_weaker_candidate(monkeypatch):
    monkeypatch.setattr(
        process_mlops,
        "list_models",
        lambda *_: [
            {"version": "v2", "stage": "Staging", "f2": 0.85, "false_positive_rate": 0.04}
        ],
    )
    monkeypatch.setattr(
        process_mlops,
        "production_model",
        lambda *_: {"version": "v1", "f2": 0.90, "false_positive_rate": 0.04},
    )
    with pytest.raises(ValueError, match="below Production"):
        process_mlops.promote_candidate("cmp", "vision", "v2")


def test_rollback_promotes_latest_archived(monkeypatch):
    monkeypatch.setattr(
        process_mlops,
        "list_models",
        lambda *_: [{"version": "v1", "stage": "Archived"}],
    )
    monkeypatch.setattr(
        process_mlops,
        "promote_model",
        lambda process_id, modality, version: {"version": version, "stage": "Production"},
    )
    assert process_mlops.rollback_model("cmp", "vision")["version"] == "v1"

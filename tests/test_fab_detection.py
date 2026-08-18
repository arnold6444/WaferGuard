from __future__ import annotations

from app.services.fab_detection import detect_envelope, detector_result, normalized_risk


def _identity(state: str = "RUNNING") -> dict:
    return {
        "lot_id": "LOT-1",
        "wafer_id": "LOT-1-W01",
        "process_run_id": "RUN-1",
        "process_id": "cmp",
        "process_step": "CMP",
        "equipment_id": "CMP_EQ_01",
        "unit_id": "PLATEN_A",
        "unit_type": "platen",
        "recipe_id": "CMP_RECIPE_A",
        "cycle_id": "CMP_EQ_01-1",
        "cycle_index": 1,
        "cycle_index_since_maintenance": 1,
        "machine_state": state,
        "phase": "POLISH",
        "phase_progress": 0.5,
        "observed_at": "2026-08-18T00:00:00+00:00",
    }


def test_detector_contract_allows_negative_raw_scores() -> None:
    result = detector_result(
        message_id="M1",
        process_run_id="R1",
        modality="timeseries",
        model_version="v1",
        raw_score=-0.32,
        threshold=-0.51,
        observed_at="2026-08-18T00:00:00+00:00",
    )

    assert result["margin"] == 0.19
    assert result["is_anomaly"] is True


def test_non_running_telemetry_is_not_scored() -> None:
    message = {
        "message_id": "M1",
        "message_type": "telemetry",
        "identity": _identity("MAINTENANCE"),
        "payload": {"tags": {"slurry_flow": 180.0}},
    }

    assert detect_envelope(message) is None


def test_metrology_is_scored_against_quality_targets() -> None:
    message = {
        "message_id": "M2",
        "message_type": "metrology",
        "identity": _identity(),
        "payload": {
            "metrics": {"uniformity": 0.8},
            "quality_targets": {"uniformity": {"target": 1.0, "tolerance": 0.1}},
        },
    }

    result = detect_envelope(message)

    assert result is not None
    assert result["modality"] == "metrology"
    assert result["margin"] > 0
    assert result["is_anomaly"] is True


def test_normalized_risk_is_monotonic_and_bounded() -> None:
    low = normalized_risk({"margin": -2.0}, 1.0)
    high = normalized_risk({"margin": 2.0}, 1.0)

    assert 0.0 < low < 0.5 < high < 1.0


def test_embedded_observed_feature_result_keeps_signed_margin() -> None:
    message = {
        "message_id": "M3",
        "message_type": "inspection",
        "identity": _identity(),
        "payload": {
            "image_key": "fab/RUN-1/inspection.png",
            "detector_result": {
                "modality": "vision",
                "model_version": "vision-v1",
                "raw_score": 0.4,
                "threshold": 1.0,
                "margin": -0.6,
                "is_anomaly": False,
                "related_tags": [],
                "observed_at": "2026-08-18T00:10:00+00:00",
            },
        },
    }

    result = detect_envelope(message)

    assert result is not None
    assert result["margin"] == -0.6
    assert result["is_anomaly"] is False

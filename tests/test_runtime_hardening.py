from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from app.services import db
from app.services.chamber_data_quality import ChamberDataQualityGate, REJECT
from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config
from app.services.model_ops import summarize_prediction_rows
from app.services.pipeline import _utc_iso
from app.services.process_episode import build_anomaly_episodes
from scripts.run_chamber_stream import _restore_generator_progress


def _config_with_wafer_samples(samples: int) -> dict:
    config = copy.deepcopy(load_chamber_config())
    config["lot"]["startup_samples"] = 0
    config["lot"]["wafer_process_samples"] = {"min": samples, "max": samples}
    config["lot"]["hold_probability_per_wafer"] = 0.0
    return config


def test_data_quality_rejects_nonnumeric_stuck_sensor_without_crashing() -> None:
    config = _config_with_wafer_samples(4)
    sample = EtchTelemetryGenerator(config, equipment_count=1, seed=11).next_sample(
        "ETCH-001", machine_state="running"
    )
    sample["chamber_pressure"] = "not-a-number"

    result = ChamberDataQualityGate(config).validate(sample)

    assert result.status == REJECT
    assert any(
        issue["code"] == "invalid_numeric" and issue.get("field") == "chamber_pressure"
        for issue in result.issues
    )


def test_data_quality_state_can_restore_recent_timestamp() -> None:
    config = _config_with_wafer_samples(4)
    generator = EtchTelemetryGenerator(config, equipment_count=1, seed=13)
    first = generator.next_sample("ETCH-001", machine_state="running")
    gate = ChamberDataQualityGate(config)
    assert gate.restore_from_rows([first]) == 1

    duplicate = dict(first)
    result = gate.validate(duplicate)
    assert result.status == REJECT
    assert any(issue["code"] == "duplicate_sample" for issue in result.issues)


def test_stream_restore_does_not_reprocess_completed_wafer() -> None:
    config = _config_with_wafer_samples(2)
    original = EtchTelemetryGenerator(config, equipment_count=1, seed=7)
    history = [
        original.next_sample("ETCH-001", machine_state="running"),
        original.next_sample("ETCH-001", machine_state="running"),
    ]
    assert history[-1]["wafer_count_since_clean"] == 1

    restored = EtchTelemetryGenerator(config, equipment_count=1, seed=7)
    _restore_generator_progress(restored, {"ETCH-001": history})
    state = restored.states["ETCH-001"]

    assert state.lot_wafer_completed == 1
    assert state.wafer_id is None
    next_sample = restored.next_sample("ETCH-001", machine_state="running")
    assert next_sample["wafer_id"] == "W02"


def test_stream_restore_preserves_mid_wafer_progress() -> None:
    config = _config_with_wafer_samples(4)
    original = EtchTelemetryGenerator(config, equipment_count=1, seed=17)
    history = [
        original.next_sample("ETCH-001", machine_state="running"),
        original.next_sample("ETCH-001", machine_state="running"),
    ]

    restored = EtchTelemetryGenerator(config, equipment_count=1, seed=17)
    _restore_generator_progress(restored, {"ETCH-001": history})
    state = restored.states["ETCH-001"]

    assert state.wafer_id == "W01"
    assert state.wafer_sample_index == 2
    assert state.wafer_target_samples == 4


def test_inspection_timestamp_is_normalized_to_utc() -> None:
    kst = timezone(timedelta(hours=9))
    value = datetime(2026, 8, 11, 11, 5, tzinfo=kst)
    assert _utc_iso(value) == "2026-08-11T02:05:00+00:00"


def test_process_points_are_grouped_into_episodes() -> None:
    events = [
        {
            "id": "P1",
            "process_step": "Etch",
            "equipment_id": "ETCH-001",
            "lot_id": "LOT-1",
            "wafer_id": "W01",
            "event_type": "rf_power_drift",
            "severity": "warning",
            "source": "chamber_synthetic_runtime",
            "observed_at": "2026-08-11T02:00:00+00:00",
            "metadata": {"anomaly_score": 1.2},
        },
        {
            "id": "P2",
            "process_step": "Etch",
            "equipment_id": "ETCH-001",
            "lot_id": "LOT-1",
            "wafer_id": "W01",
            "event_type": "rf_power_drift",
            "severity": "critical",
            "source": "chamber_synthetic_runtime",
            "observed_at": "2026-08-11T02:00:02+00:00",
            "metadata": {"anomaly_score": 2.4},
        },
        {
            "id": "P3",
            "process_step": "Etch",
            "equipment_id": "ETCH-001",
            "lot_id": "LOT-1",
            "wafer_id": "W02",
            "event_type": "rf_power_drift",
            "severity": "warning",
            "source": "chamber_synthetic_runtime",
            "observed_at": "2026-08-11T02:00:20+00:00",
            "metadata": {"anomaly_score": 1.1},
        },
    ]

    episodes = build_anomaly_episodes(events, gap_seconds=5)

    assert len(episodes) == 2
    assert episodes[0]["sample_count"] == 1
    assert episodes[1]["sample_count"] == 2
    assert episodes[1]["severity"] == "critical"
    assert episodes[1]["max_anomaly_score"] == 2.4


def test_runtime_prediction_metrics_are_separate_from_holdout_metrics() -> None:
    rows = [
        {"equipment_id": "E1", "recipe_id": "R1", "lot_id": "L1", "model_version": "v1", "residual": 1.0, "is_anomaly": False},
        {"equipment_id": "E1", "recipe_id": "R1", "lot_id": "L1", "model_version": "v1", "residual": -3.0, "is_anomaly": True},
    ]

    metrics = summarize_prediction_rows(rows)

    assert metrics["overall"]["rows"] == 2
    assert metrics["overall"]["mae"] == 2.0
    assert metrics["overall"]["rmse"] == round((5.0) ** 0.5, 6)
    assert metrics["by_equipment"]["E1"]["anomaly_rate"] == 0.5


def test_postgres_pool_limits_are_validated(monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_MIN", "4")
    monkeypatch.setenv("DB_POOL_MAX", "2")
    try:
        db._pool_limits()
    except db.DatabaseConfigurationError as exc:
        assert "DB_POOL_MIN" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid pool bounds must fail")

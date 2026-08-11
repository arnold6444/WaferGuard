from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from app.services import db
from app.services.chamber_data_quality import ChamberDataQualityGate, REJECT
from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config
from app.services.pipeline import _utc_iso
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


def test_postgres_pool_limits_are_validated(monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_MIN", "4")
    monkeypatch.setenv("DB_POOL_MAX", "2")
    try:
        db._pool_limits()
    except db.DatabaseConfigurationError as exc:
        assert "DB_POOL_MIN" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid pool bounds must fail")

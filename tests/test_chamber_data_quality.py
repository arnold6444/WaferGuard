from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from app.services import chamber_storage, config, db, storage
from app.services.chamber_data_quality import ChamberDataQualityGate, REJECT, VALID, WARNING
from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config
from app.services.chamber_runtime import ChamberRuntime


START = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _sample(config_data: dict, *, seed: int = 31) -> dict:
    generator = EtchTelemetryGenerator(config_data, equipment_count=1, seed=seed, start_time=START)
    return generator.next_sample(machine_state="running")


def test_gate_rejects_missing_duplicate_reversal_and_invalid_contracts():
    cfg = copy.deepcopy(load_chamber_config())

    missing = _sample(cfg)
    missing.pop("resistance")
    assert ChamberDataQualityGate(cfg).validate(missing).status == REJECT

    duplicate_gate = ChamberDataQualityGate(cfg)
    sample = _sample(cfg)
    assert duplicate_gate.validate(sample).status == VALID
    duplicate = duplicate_gate.validate(dict(sample))
    assert duplicate.status == REJECT
    assert {issue["code"] for issue in duplicate.issues} == {"duplicate_sample"}

    reversal_gate = ChamberDataQualityGate(cfg)
    first = _sample(cfg)
    assert reversal_gate.validate(first).status == VALID
    reversed_sample = dict(first, observed_at=(START - timedelta(seconds=1)).isoformat())
    reversed_result = reversal_gate.validate(reversed_sample)
    assert reversed_result.status == REJECT
    assert any(issue["code"] == "timestamp_reversal" for issue in reversed_result.issues)

    invalid = _sample(cfg)
    invalid.update(machine_state="teleporting", recipe_id="UNKNOWN", chamber_pressure=-999)
    invalid_result = ChamberDataQualityGate(cfg).validate(invalid)
    assert invalid_result.status == REJECT
    assert {issue["code"] for issue in invalid_result.issues} >= {
        "invalid_state", "invalid_recipe", "impossible_physical_range"
    }


def test_gate_warns_on_gap_and_aggregates_stuck_sensor_once():
    cfg = copy.deepcopy(load_chamber_config())
    cfg["data_quality"].update({"stuck_consecutive_samples": 3, "aggregate_event_after": 2})
    gate = ChamberDataQualityGate(cfg)
    base = _sample(cfg)
    assert gate.validate(base).status == VALID

    gap = dict(base, observed_at=(START + timedelta(seconds=10)).isoformat())
    gap["chamber_pressure"] += 0.1
    gap["chamber_temperature"] += 0.1
    gap_result = gate.validate(gap)
    assert gap_result.status == WARNING
    assert any(issue["code"] == "timestamp_gap" for issue in gap_result.issues)

    stuck_events = []
    for index in range(11, 16):
        stuck = dict(gap, observed_at=(START + timedelta(seconds=index)).isoformat())
        result = gate.validate(stuck)
        stuck_events.extend(event for event in result.aggregate_events if event["code"] == "stuck_sensor")
    assert len(stuck_events) == 2
    assert {event["field"] for event in stuck_events} == {"chamber_pressure", "chamber_temperature"}
    assert all(event["count"] == 2 for event in stuck_events)


def test_runtime_persists_warning_but_blocks_prediction_and_training(tmp_path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "waferguard.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    storage.init_db()

    cfg = copy.deepcopy(load_chamber_config())
    cfg["maintenance"]["wafers_between_clean"] = 10_000
    cfg["model"]["bootstrap_min_rows"] = 10_000
    cfg["data_quality"].update({"stuck_consecutive_samples": 3, "aggregate_event_after": 2})
    generator = EtchTelemetryGenerator(cfg, equipment_count=1, seed=41, start_time=START)
    runtime = ChamberRuntime(cfg)

    samples = [generator.next_sample(machine_state="running") for _ in range(5)]
    frozen_pressure = samples[0]["chamber_pressure"]
    frozen_temperature = samples[0]["chamber_temperature"]
    for sample in samples[1:]:
        sample["chamber_pressure"] = frozen_pressure
        sample["chamber_temperature"] = frozen_temperature
    results = [runtime.process_sample(sample) for sample in samples]

    assert results[0]["state"] == "WARMING_UP"
    assert results[-1]["state"] == "DATA_QUALITY_WARNING"
    assert chamber_storage.count_rows("chamber_telemetry") == 5
    assert chamber_storage.count_rows("chamber_predictions") == 0
    assert len(chamber_storage.clean_training_rows()) == 2
    quality_events = [event for event in storage.list_process_events(limit=100) if event["event_type"] == "data_quality"]
    assert len(quality_events) == 2  # one aggregate event per configured stuck sensor
    assert {event["metadata"]["subtype"] for event in quality_events} == {"stuck_sensor"}

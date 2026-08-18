from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import fab_storage
from app.services.fab_mqtt import (
    DirectFabTransport,
    FabMessageError,
    FabMessageRouter,
    topic_for,
    validate_envelope,
)


def _message(message_id: str = "MSG-1") -> dict:
    return {
        "schema_version": "fab.v2",
        "message_id": message_id,
        "message_type": "telemetry",
        "identity": {
            "schema_version": "fab.v2",
            "message_id": message_id,
            "lot_id": "LOT-0001",
            "wafer_id": "LOT-0001-W01",
            "process_run_id": "LOT-0001-W01-CMP",
            "process_id": "cmp",
            "process_step": "CMP",
            "equipment_id": "CMP_EQ_01",
            "unit_id": "PLATEN_A",
            "unit_type": "platen",
            "recipe_id": "CMP_RECIPE_A",
            "cycle_id": "CMP_EQ_01-1",
            "cycle_index": 1,
            "cycle_index_since_maintenance": 1,
            "machine_state": "RUNNING",
            "phase": "POLISH",
            "phase_progress": 0.5,
            "observed_at": datetime(2026, 8, 18, tzinfo=timezone.utc).isoformat(),
        },
        "payload": {"tags": {"slurry_flow": 180.0}},
    }


def test_envelope_validation_normalizes_utc_and_topic() -> None:
    message = _message()
    message["identity"]["observed_at"] = "2026-08-18T09:00:00+09:00"

    normalized = validate_envelope(message)

    assert normalized["identity"]["observed_at"].startswith("2026-08-18T00:00:00")
    assert topic_for(normalized) == "fab/cmp/CMP_EQ_01/PLATEN_A/telemetry"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "fab.v1"),
        (("message_id",), ""),
        (("message_type",), "unknown"),
        (("identity", "phase_progress"), 1.2),
        (("identity", "equipment_id"), ""),
        (("identity", "observed_at"), "2026-08-18T00:00:00"),
        (("identity", "cycle_index"), 0),
        (("identity", "cycle_index_since_maintenance"), -1),
    ],
)
def test_envelope_rejects_malformed_contract(path: tuple[str, ...], value: object) -> None:
    message = _message()
    target = message
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(FabMessageError):
        validate_envelope(message)


def test_direct_transport_uses_consumer_receipts_for_duplicate_protection(monkeypatch) -> None:
    claims: set[tuple[str, str]] = set()
    received: list[str] = []

    def claim(message_id: str, consumer: str) -> bool:
        key = (message_id, consumer)
        if key in claims:
            return False
        claims.add(key)
        return True

    router = FabMessageRouter({"db_writer": lambda item: received.append(item["message_id"])})
    monkeypatch.setattr(router, "_claim", claim)
    monkeypatch.setattr(fab_storage, "mark_message_processed", lambda *_: {"status": "processed"})
    transport = DirectFabTransport(router)

    first = transport.publish(_message())
    second = transport.publish(_message())

    assert first["delivered"] == ["db_writer"]
    assert second["duplicates"] == ["db_writer"]
    assert received == ["MSG-1"]

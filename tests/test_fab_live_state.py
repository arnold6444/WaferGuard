from __future__ import annotations

from pathlib import Path

import pytest

from app.services import config, db, fab_storage, storage


@pytest.fixture()
def fab_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "fab-live.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    storage.init_db()
    return database


def _identity(run_id: str, observed_at: str, *, cycle: int = 1) -> dict[str, object]:
    return {
        "schema_version": "fab.v2",
        "message_id": f"MSG-{run_id}",
        "lot_id": "LOT-LIVE-001",
        "wafer_id": f"LOT-LIVE-001-W{cycle:02d}",
        "process_run_id": run_id,
        "process_id": "cmp",
        "process_step": "CMP",
        "equipment_id": "CMP-001",
        "unit_id": "PLATEN-A",
        "unit_type": "platen",
        "recipe_id": "CMP_DEMO_A",
        "cycle_id": f"CMP-001-{cycle}",
        "cycle_index": cycle,
        "cycle_index_since_maintenance": cycle,
        "machine_state": "RUNNING",
        "phase": "POLISH",
        "phase_progress": 0.5,
        "observed_at": observed_at,
    }


def _telemetry(identity: dict[str, object]) -> None:
    fab_storage.insert_process_telemetry(
        {
            **identity,
            "tags": {"slurry_flow": 175.0},
            "detector_context": {},
        }
    )


def test_process_run_uses_first_observed_time_as_started_at(fab_db: Path) -> None:
    identity = _identity("RUN-TIME-001", "2026-01-01T00:00:00Z")

    _telemetry(identity)

    run = fab_storage.get_process_run("RUN-TIME-001")
    assert run is not None
    assert run["started_at"] == "2026-01-01T00:00:00+00:00"


def test_additive_migration_repairs_impossible_legacy_run_range(fab_db: Path) -> None:
    identity = _identity("RUN-TIME-LEGACY", "2026-01-01T00:00:00Z")
    _telemetry(identity)
    with db.connect() as conn:
        conn.execute(
            "UPDATE process_runs SET started_at = ?, completed_at = ?, status = ? "
            "WHERE process_run_id = ?",
            (
                "2026-08-18T00:00:00+00:00",
                "2026-01-01T00:01:00+00:00",
                "completed",
                "RUN-TIME-LEGACY",
            ),
        )

    fab_storage.init_fab_db()

    run = fab_storage.get_process_run("RUN-TIME-LEGACY")
    assert run is not None
    assert run["started_at"] == "2026-01-01T00:00:00+00:00"


def test_process_live_exposes_latest_completed_run_instead_of_empty_current(fab_db: Path) -> None:
    identity = _identity("RUN-COMPLETE-001", "2026-01-01T00:00:00Z")
    _telemetry(identity)
    fab_storage.upsert_process_run(
        {
            **identity,
            "status": "completed",
            "completed_at": "2026-01-01T00:01:00Z",
        }
    )

    live = fab_storage.process_live("cmp")

    assert live["run_state"] == "latest_completed"
    assert live["process_run"]["process_run_id"] == "RUN-COMPLETE-001"
    assert live["process_run"]["status"] == "completed"
    assert live["current"]["process_run_id"] == "RUN-COMPLETE-001"


def test_process_live_prioritizes_running_run_over_newer_completed_history(fab_db: Path) -> None:
    running = _identity("RUN-ACTIVE-001", "2026-01-01T00:00:00Z", cycle=1)
    completed = _identity("RUN-COMPLETE-002", "2026-01-01T01:00:00Z", cycle=2)
    _telemetry(running)
    _telemetry(completed)
    fab_storage.upsert_process_run(
        {
            **completed,
            "status": "completed",
            "completed_at": "2026-01-01T01:01:00Z",
        }
    )

    live = fab_storage.process_live("cmp")

    assert live["run_state"] == "running"
    assert live["process_run"]["process_run_id"] == "RUN-ACTIVE-001"
    assert {row["process_run_id"] for row in live["telemetry"]} == {"RUN-ACTIVE-001"}

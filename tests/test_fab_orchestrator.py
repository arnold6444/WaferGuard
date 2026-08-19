from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.services import config, db, fab_storage, storage
from app.services.fab_generator import VirtualFabGenerator, load_fab_config, load_fault_catalog
from app.services.fab_orchestrator import envelopes_for_process_run
from app.services.fab_orchestrator import FabStreamOrchestrator
from scripts.run_fab_stream import parse_args


def _result() -> dict[str, object]:
    identity = {
        "schema_version": "fab.v2",
        "message_id": "BASE",
        "lot_id": "LOT-1",
        "wafer_id": "LOT-1-W01",
        "process_run_id": "RUN-1",
        "process_id": "cmp",
        "process_step": "CMP",
        "equipment_id": "CMP-001",
        "unit_id": "PLATEN-A",
        "unit_type": "platen",
        "recipe_id": "CMP-A",
        "cycle_id": "CMP-001-1",
        "cycle_index": 1,
        "cycle_index_since_maintenance": 1,
        "machine_state": "RUNNING",
        "phase": "POLISH",
        "phase_progress": 0.5,
        "observed_at": "2026-08-18T00:00:00+00:00",
    }
    return {
        "identity": identity,
        "telemetry": [
            {
                **identity,
                "tags": {"slurry_flow": 175.0},
                "detector_context": {
                    "normalized_values": {"slurry_flow": -0.2},
                    "fault_id": "MUST-NOT-CROSS",
                },
            }
        ],
        "metrology": {
            "metrics": {"remaining_film_nm": 42.0},
            "measurements": [{"metric": "remaining_film_nm", "value": 42.0, "unit": "nm"}],
            "instrument_class": "spectroscopic_reflectometer",
            "sampling_level": "inline_proxy",
            "metrology_context": {"measurement_type": "film_thickness"},
            "quality_targets": {
                "remaining_film_nm": {"target": 42.0, "tolerance": 2.5}
            },
            "available_at": "2026-08-18T00:05:00+00:00",
        },
        "inspection": {
            "image_key": "fab/RUN-1/inspection.png",
            "features": {"mean": 0.5},
            "inspection_modality": "optical_surface_inspection",
            "instrument_class": "wafer_surface_scanner",
            "image_type": "surface_intensity_map",
            "sampling_level": "inline_proxy",
            "inspection_context": {"review_role": "surface_defect_screening"},
            "available_at": "2026-08-18T00:10:00+00:00",
            "synthetic_debug": {"mask_key": "secret.png", "bbox": [1, 1, 2, 2]},
        },
    }


def test_envelopes_keep_identity_and_exclude_simulator_truth() -> None:
    envelopes = envelopes_for_process_run(_result())
    serialized = repr(envelopes)

    assert {item["identity"]["process_run_id"] for item in envelopes} == {"RUN-1"}
    assert {item["identity"]["wafer_id"] for item in envelopes} == {"LOT-1-W01"}
    assert "MUST-NOT-CROSS" not in serialized
    assert "mask_key" not in serialized
    assert "synthetic_debug" not in serialized
    assert [item["message_type"] for item in envelopes].count("telemetry") == 1
    metrology = next(item for item in envelopes if item["message_type"] == "metrology")
    inspection = next(item for item in envelopes if item["message_type"] == "inspection")
    assert metrology["payload"]["instrument_class"] == "spectroscopic_reflectometer"
    assert metrology["payload"]["measurements"][0]["unit"] == "nm"
    assert inspection["payload"]["inspection_modality"] == "optical_surface_inspection"
    assert inspection["payload"]["image_type"] == "surface_intensity_map"


def test_cli_defaults_to_mqtt_and_accepts_direct_debug() -> None:
    assert parse_args([]).transport == "mqtt"
    args = parse_args(["--process", "cmp", "--transport", "direct", "--seed", "7"])
    assert (args.process, args.transport, args.seed) == ("cmp", "direct", 7)
    cleaning = parse_args([
        "--process", "cleaning", "--fault", "cleaning_chemical_concentration_drift",
    ])
    assert cleaning.process == "cleaning"
    assert cleaning.fault == "cleaning_chemical_concentration_drift"


@pytest.mark.parametrize(
    ("process_id", "fault_id"),
    [
        ("cmp", "cmp_slurry_degradation"),
        ("cleaning", "cleaning_chemical_concentration_drift"),
    ],
)
def test_one_small_direct_process_run_reaches_fusion_and_rca(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_id: str,
    fault_id: str,
) -> None:
    database = tmp_path / "fab-orchestrator.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    storage.init_db()

    fab_config = copy.deepcopy(load_fab_config())
    fab_config["processes"][process_id]["samples_per_run"] = 3
    fault_catalog = load_fault_catalog()
    generator = VirtualFabGenerator(fab_config, fault_catalog, seed=17)
    generator._inspection.persist_images = False
    orchestrator = FabStreamOrchestrator(
        fab_config,
        fault_catalog,
        seed=17,
        transport="direct",
        generator=generator,
    )

    result = orchestrator.run(
        lots=1,
        wafers_per_lot=1,
        process=process_id,
        fault=fault_id,
    )

    assert len(result["process_runs"]) == 1
    assert result["process_runs"][0]["process_id"] == process_id
    assert result["process_runs"][0]["fusion"]["process_run_id"]
    assert result["process_runs"][0]["rca"]["result_label"] == "Candidate root cause"
    assert result["process_runs"][0]["evaluation"]["evaluated"] is True
    detail = fab_storage.process_run_detail(result["process_runs"][0]["process_run_id"])
    assert detail and detail["completed_at"]

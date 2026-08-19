from __future__ import annotations

from pathlib import Path

import pytest

from app.services import config, db, fab_storage, storage


@pytest.fixture()
def fab_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "fab.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    storage.init_db()
    storage.init_db()
    return database


def identity(
    *,
    process_step: str = "CMP",
    process_run_id: str = "RUN-CMP-001",
    message_time: str = "2026-08-18T01:00:00Z",
) -> dict[str, object]:
    prefix = process_step.upper()
    unit_type = {
        "PHOTO": "module",
        "ETCH": "chamber",
        "DEPOSITION": "chamber",
        "CMP": "platen",
    }[prefix]
    return {
        "lot_id": "LOT-001",
        "wafer_id": "LOT-001-W01",
        "process_run_id": process_run_id,
        "process_id": process_step.lower(),
        "process_step": process_step,
        "equipment_id": f"{prefix}_EQ_01",
        "unit_id": f"{unit_type.upper()}_A",
        "unit_type": unit_type,
        "recipe_id": f"{prefix}_RECIPE_A",
        "cycle_id": f"{prefix}_EQ_01-1",
        "cycle_index": 1,
        "cycle_index_since_maintenance": 1,
        "machine_state": "RUNNING",
        "phase": "PROCESS",
        "phase_progress": 0.5,
        "observed_at": message_time,
    }


def envelope(
    message_id: str,
    *,
    message_type: str = "telemetry",
    identity_value: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "fab.v2",
        "message_id": message_id,
        "message_type": message_type,
        "identity": identity_value or identity(),
        "payload": payload or {},
    }


def test_schema_is_additive_and_equipment_unit_key_is_composite(fab_db: Path) -> None:
    with db.connect() as conn:
        for table in (
            "wafers",
            "process_runs",
            "equipment_units",
            "process_telemetry",
            "metrology_results",
            "inspection_assets",
            "simulation_faults",
            "fusion_results",
            "rca_results",
            "fab_message_receipts",
        ):
            assert db.table_columns(conn, table)
        assert "process_run_id" in db.table_columns(conn, "process_events")
        assert "process_run_id" in db.table_columns(conn, "inspections")
        assert "detector_context_json" in db.table_columns(conn, "process_telemetry")
        assert {"measurements_json", "metrology_context_json"}.issubset(
            db.table_columns(conn, "metrology_results")
        )
        assert {
            "inspection_modality", "instrument_class", "image_type",
            "sampling_level", "inspection_context_json",
        }.issubset(db.table_columns(conn, "inspection_assets"))

    first = fab_storage.upsert_equipment_unit(
        {**identity(), "equipment_id": "CMP_EQ_01", "unit_id": "PLATEN_A"}
    )
    second = fab_storage.upsert_equipment_unit(
        {**identity(), "equipment_id": "CMP_EQ_02", "unit_id": "PLATEN_A"}
    )
    assert first["unit_id"] == second["unit_id"]
    overview = fab_storage.equipment_overview("cmp")
    assert {
        "CMP_EQ_01",
        "CMP_EQ_02",
    }.issubset({item["equipment_id"] for item in overview})
    assert all(item["has_runtime_data"] for item in overview[:2])
    configured_only = [item for item in overview if not item["has_runtime_data"]]
    assert configured_only
    assert all(
        item["runtime_supported"] and not item["connected"] and item["status"] == "not_connected"
        for item in configured_only
    )
    assert configured_only[0]["equipment_class"]["id"] == "rotary_cmp_system"
    assert "slurry_flow" in configured_only[0]["sensor_tags"]


def test_envelope_dedupe_detector_fusion_trace_and_gt_isolation(fab_db: Path) -> None:
    storage.upsert_lot(
        {
            "lot_id": "LOT-001",
            "product_id": "PRODUCT-A",
            "recipe_route": "Photo>Etch>Deposition>CMP",
            "status": "running",
            "started_at": "2026-08-18T00:00:00+00:00",
            "current_process_step": "CMP",
            "wafer_count": 1,
        }
    )
    route = ("Photo", "Etch", "Deposition", "CMP")
    for index, step in enumerate(route, start=1):
        run_identity = identity(
            process_step=step,
            process_run_id=f"RUN-{step.upper()}-001",
            message_time=f"2026-08-18T0{index}:00:00Z",
        )
        row = fab_storage.persist_envelope(
            envelope(
                f"MSG-{step.upper()}-TEL",
                identity_value=run_identity,
                payload={
                    "tags": {"signal": float(index), "fault_id": "must-not-leak"},
                    "detector_context": {
                        "normalized_values": {"signal": 0.2},
                        "feature_vector": [0.2, 0.5],
                        "feature_names": ["signal", "phase_progress"],
                        "ground_truth": "must-not-leak",
                    },
                },
            )
        )
        assert row["process_run_id"] == run_identity["process_run_id"]
        assert "ground_truth" not in row["detector_context"]
        assert "fault_id" not in row["tags"]

    duplicate = fab_storage.persist_envelope(
        envelope(
            "MSG-CMP-TEL",
            identity_value=identity(),
            payload={"tags": {"signal": 999.0}},
        )
    )
    assert duplicate["deduplicated"] is True
    assert duplicate["tags"] == {"signal": 4.0}

    detector = fab_storage.save_detector_result(
        {
            "id": "DET-CMP-001",
            "message_id": "MSG-CMP-TEL",
            "process_run_id": "RUN-CMP-001",
            "modality": "timeseries",
            "model_version": "ts-v2",
            "raw_score": -2.0,
            "threshold": -1.0,
            "observed_at": "2026-08-18T04:00:00Z",
            "related_tags": ["slurry_flow"],
        }
    )
    assert detector["raw_score"] == -2.0
    assert detector["margin"] == -1.0
    assert detector["is_anomaly"] is False
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM fab_detector_results").fetchone()["n"] == 1
        assert db.table_columns(conn, "process_runtime_samples") == []
        conn.execute(
            "UPDATE fab_detector_results SET margin = ? WHERE id = ?",
            (-0.9999997, "DET-CMP-001"),
        )
        conn.commit()
    round_tripped = fab_storage.detector_results("RUN-CMP-001")[0]
    assert round_tripped["margin"] == -1.0
    assert round_tripped["is_anomaly"] is False

    fab_storage.persist_envelope(
        envelope(
            "MSG-CMP-MET",
            message_type="metrology",
            payload={
                "metrics": {"removal_rate": 91.2},
                "measurements": [
                    {
                        "metric_id": "removal_rate",
                        "display_name": "Removal Rate",
                        "value": 91.2,
                        "unit": "nm/min",
                        "modality": "spectroscopic_reflectometry",
                        "instrument_class": "film_thickness_metrology_system",
                        "method": "derived_rate",
                        "sampling_level": "inline_sample",
                        "synthetic_proxy": True,
                    }
                ],
                "metrology_context": {
                    "process_id": "cmp",
                    "measurement_scope": "post_process_inline_or_atline",
                    "synthetic_proxy": True,
                },
                "available_at": "2026-08-18T04:05:00Z",
                "detector_result": {
                    "model_version": "met-v1",
                    "raw_score": 1.2,
                    "threshold": 1.0,
                    "related_tags": ["removal_rate"],
                },
            },
        )
    )
    fab_storage.persist_envelope(
        envelope(
            "MSG-CMP-VIS",
            message_type="inspection",
            payload={
                "image_key": "inspection/cmp-001.png",
                "defect_type": "scratch",
                "defect_severity": 0.8,
                "inspection_modality": "darkfield_optical_surface_inspection",
                "instrument_class": "patterned_wafer_surface_inspector",
                "image_type": "top_down_darkfield_grayscale_proxy",
                "sampling_level": "process_run",
                "inspection_context": {
                    "measurement_scope": "post_process_review",
                    "synthetic_proxy": True,
                },
                "features": {"mean": 0.42},
                "available_at": "2026-08-18T04:10:00Z",
                "synthetic_debug": {
                    "mask_key": "debug/cmp-001-mask.png",
                    "bbox": [2, 3, 10, 12],
                },
                "detector_result": {
                    "model_version": "vision-v1",
                    "raw_score": 0.9,
                    "threshold": 0.5,
                },
            },
        )
    )
    fusion = fab_storage.save_fusion_result(
        {
            "process_run_id": "RUN-CMP-001",
            "overall_risk": 0.82,
            "threshold": 0.5,
            "fusion_version": "fusion-v2",
            "modality_risks": {"timeseries": 0.4, "vision": 0.9, "metrology": 0.8},
        }
    )
    assert fusion["margin"] == pytest.approx(0.32)

    fab_storage.save_simulation_fault(
        {
            "fault_id": "FAULT-CMP-001",
            "process_run_id": "RUN-CMP-001",
            "fault_type": "cmp_slurry_degradation",
            "severity": 0.8,
            "ground_truth_tags": ["slurry_flow"],
            "ground_truth_defect": "scratch",
        }
    )
    with pytest.raises(PermissionError):
        fab_storage.list_simulation_faults("RUN-CMP-001")

    rca = fab_storage.save_rca_result(
        {
            "id": "RCA-CMP-001",
            "wafer_id": "LOT-001-W01",
            "process_run_id": "RUN-CMP-001",
            "candidate_causes": [
                {
                    "candidate": "slurry delivery degradation",
                    "score": 0.88,
                    "evidence": ["slurry_flow", "removal_rate"],
                    "injected_fault": "cmp_slurry_degradation",
                }
            ],
            "evidence": {"ground_truth": "forbidden", "tags": ["slurry_flow"]},
            "confidence": 0.88,
            "overall_risk": 0.82,
            "fusion_version": "fusion-v2",
            "model_or_rule_version": "rca-v2",
        }
    )
    assert rca["result_label"] == "Candidate root cause"
    assert "injected_fault" not in rca["candidate_causes"][0]
    assert "ground_truth" not in rca["evidence"]

    trace = fab_storage.wafer_trace("LOT-001-W01")
    assert trace is not None
    assert trace["route"] == list(route)
    cmp_run = trace["process_runs"][-1]
    assert cmp_run["fusion"]["fusion_version"] == "fusion-v2"
    assert cmp_run["metrology"][0]["metrics"]["removal_rate"] == 91.2
    assert cmp_run["metrology"][0]["measurements"][0]["unit"] == "nm/min"
    assert cmp_run["metrology"][0]["metrology_context"]["process_id"] == "cmp"
    assert cmp_run["inspections"][0]["inspection_modality"] == (
        "darkfield_optical_surface_inspection"
    )
    assert cmp_run["inspections"][0]["inspection_context"]["synthetic_proxy"] is True
    assert cmp_run["inspections"][0]["metadata"]["features"]["mean"] == 0.42
    assert cmp_run["equipment_context"]["equipment_class"]["id"] == "rotary_cmp_system"
    assert cmp_run["post_process_plan"]["inspection"]["inspection_modality"] == (
        "darkfield_optical_surface_inspection"
    )
    assert "bbox" not in cmp_run["inspections"][0]
    assert "mask_key" not in cmp_run["inspections"][0]
    assert "fault_id" not in str(trace)

    debug_detail = fab_storage.process_run_detail("RUN-CMP-001", include_debug=True)
    assert debug_detail is not None
    assert debug_detail["inspections"][0]["mask_key"] == "debug/cmp-001-mask.png"
    assert debug_detail["inspections"][0]["bbox"] == [2, 3, 10, 12]
    assert debug_detail["synthetic_debug"]["simulation_faults"][0]["fault_id"] == "FAULT-CMP-001"


def test_consumer_receipts_are_atomic_and_wait_for_completion(fab_db: Path) -> None:
    assert fab_storage.claim_message("MSG-001", "db_writer") is True
    assert fab_storage.claim_message("MSG-001", "db_writer") is False
    assert fab_storage.message_processed("MSG-001", ("db_writer",)) is False
    assert fab_storage.mark_message_processed("MSG-001", "db_writer")["status"] == "processed"
    assert fab_storage.message_processed("MSG-001", ("db_writer",)) is True

    assert fab_storage.claim_message("MSG-002", "detector") is True
    assert fab_storage.release_message("MSG-002", "detector") is True
    assert fab_storage.claim_message("MSG-002", "detector") is True

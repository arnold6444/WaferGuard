from __future__ import annotations

import copy
from datetime import datetime
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from app.services import object_store
from app.services.fab_generator import (
    SUPPORTED_PROCESSES,
    VirtualFabGenerator,
    load_fab_config,
    load_fault_catalog,
    validate_fab_config,
)
from app.services.fab_schema import FAB_FEATURE_CONTRACT, public_projection


@pytest.fixture
def local_images(monkeypatch, tmp_path):
    monkeypatch.setattr(object_store, "OUTPUT_DIR", tmp_path / "outputs")
    return tmp_path / "outputs"


def test_config_contract_rejects_executable_or_unknown_relations():
    config = load_fab_config()
    assert config["route"] == list(SUPPORTED_PROCESSES)
    assert set(load_fault_catalog()["faults"]) == {
        "photo_focus_drift",
        "etch_chamber_contamination",
        "deposition_precursor_instability",
        "cmp_slurry_degradation",
    }
    invalid = copy.deepcopy(config)
    invalid["processes"]["cmp"]["relations"][0]["kind"] = "eval"
    with pytest.raises(ValueError, match="Unsupported relation kind"):
        validate_fab_config(invalid)


def test_process_specific_equipment_sensor_and_measurement_contracts():
    config = load_fab_config()
    expected_classes = {
        "photo": "arf_immersion_lithography_cell",
        "etch": "icp_rie_etch_system",
        "deposition": "pecvd_deposition_system",
        "cmp": "rotary_cmp_system",
    }
    for process_id, expected_class in expected_classes.items():
        process = config["processes"][process_id]
        assert process["equipment_class"]["id"] == expected_class
        assert process["sensor_tags"]
        assert all(
            {"display_name", "unit", "sensor_type", "role"}.issubset(sensor)
            for sensor in process["sensor_tags"].values()
        )
        assert all(
            {"display_name", "unit", "modality", "instrument_class", "method", "sampling_level"}
            .issubset(metric)
            for metric in process["metrology"].values()
        )

    etch = config["processes"]["etch"]
    assert etch["metrology"]["post_etch_linewidth_nm"]["modality"] == "cd_sem"
    assert etch["metrology"]["etch_depth_nm"]["modality"] == "optical_cd_scatterometry"
    assert etch["inspection"]["inspection_modality"] == "top_down_review_sem"
    assert {item["modality"] for item in etch["inspection"]["escalation_modalities"]} == {
        "fib_sem_cross_section", "tem_stem_cross_section",
    }
    assert config["processes"]["deposition"]["metrology"]["film_thickness_nm"]["modality"] == (
        "spectroscopic_ellipsometry"
    )
    assert config["processes"]["cmp"]["inspection"]["inspection_modality"] == (
        "darkfield_optical_surface_inspection"
    )


def test_generated_etch_run_exposes_public_equipment_and_metrology_context():
    config = load_fab_config()
    config["processes"]["etch"]["samples_per_run"] = 2
    generator = VirtualFabGenerator(config=config, seed=13, simulation_id="SIM-CONTRACT")
    generator._inspection.persist_images = False
    run = generator.generate_process_run("LOT-C", 1, "etch")

    assert run["equipment_context"]["equipment_class"]["id"] == "icp_rie_etch_system"
    assert run["equipment_context"]["unit_class"] == "vacuum_process_chamber"
    telemetry_context = run["telemetry"][0]["detector_context"]
    assert telemetry_context["equipment_context"]["sensor_tags"]["chamber_pressure"]["unit"] == "mTorr"
    measurements = {item["metric_id"]: item for item in run["metrology"]["measurements"]}
    assert measurements["post_etch_linewidth_nm"]["instrument_class"] == "critical_dimension_sem"
    assert measurements["etch_depth_nm"]["modality"] == "optical_cd_scatterometry"
    assert run["inspection"]["inspection_modality"] == "top_down_review_sem"
    assert run["inspection"]["inspection_context"]["escalation_policy"] == (
        "candidate_evidence_review_only"
    )
    assert "fault_id" not in str(run["metrology"])
    assert "ground_truth" not in str(run["inspection"])


def test_fixed_seed_reproduces_identity_payload_and_feature_contract(local_images):
    left = VirtualFabGenerator(seed=17, simulation_id="SIM-REPRO")
    right = VirtualFabGenerator(seed=17, simulation_id="SIM-REPRO")
    a = left.generate_process_run("LOT-R", 1, "photo")
    b = right.generate_process_run("LOT-R", 1, "photo")

    assert a["identity"] == b["identity"]
    assert a["identity"]["schema_version"] == "fab.v2"
    assert a["identity"]["process_id"] == "photo"
    assert a["identity"]["process_step"] == "Photo"
    assert a["identity"]["wafer_id"] == "LOT-R-W01"
    assert [row["tags"] for row in a["telemetry"]] == [row["tags"] for row in b["telemetry"]]
    context = a["telemetry"][0]["detector_context"]
    assert context["feature_contract"] == FAB_FEATURE_CONTRACT
    assert len(context["feature_fingerprint"]) == 64
    assert "context:phase_progress" in context["feature_names"]
    assert "context:cycle_position" in context["feature_names"]
    assert "context:cycle_since_maintenance" in context["feature_names"]
    assert len(context["feature_names"]) == len(context["feature_vector"])
    assert set(context["normalized_values"]) == set(a["telemetry"][0]["tags"])
    assert all(row["identity"]["machine_state"] == "RUNNING" for row in a["telemetry"])
    assert len({row["identity"]["process_run_id"] for row in a["telemetry"]}) == 1
    assert {row["identity"]["phase"] for row in a["telemetry"]} == {
        "idle", "coat_track", "expose", "develop",
    }
    assert [event["identity"]["machine_state"] for event in a["state_events"]] == [
        "LOAD", "STARTUP", "RUNNING", "UNLOAD",
    ]


def test_four_faults_change_sensor_and_metrology_without_public_truth(local_images):
    cases = {
        "photo_focus_drift": ("photo", "focus_offset", "critical_dimension_nm"),
        "etch_chamber_contamination": ("etch", "chamber_pressure", "etch_depth_nm"),
        "deposition_precursor_instability": ("deposition", "precursor_flow", "film_thickness_nm"),
        "cmp_slurry_degradation": ("cmp", "slurry_flow", "remaining_film_nm"),
    }
    for fault_id, (process_id, tag, metric) in cases.items():
        normal = VirtualFabGenerator(seed=23, simulation_id=f"SIM-{process_id}").generate_process_run(
            "LOT-F", 1, process_id
        )
        faulty = VirtualFabGenerator(seed=23, simulation_id=f"SIM-{process_id}").generate_process_run(
            "LOT-F", 1, process_id, fault_id
        )
        normal_mean = np.mean([row["tags"][tag] for row in normal["telemetry"]])
        faulty_mean = np.mean([row["tags"][tag] for row in faulty["telemetry"]])
        assert abs(faulty_mean - normal_mean) > 0.05, fault_id
        target = faulty["metrology"]["quality_target"][metric]["target"]
        faulty_error = abs(faulty["metrology"]["metrics"][metric] - target)
        normal_error = abs(normal["metrology"]["metrics"][metric] - target)
        assert faulty_error > normal_error, fault_id
        assert "fault_id" not in str(faulty["telemetry"])
        public = public_projection(faulty)
        assert "_simulation_truth" not in public
        assert "synthetic_debug" not in str(public)


def test_balanced_context_and_maintenance_reset(local_images):
    config = load_fab_config()
    config["processes"]["cmp"]["maintenance_every_cycles"] = 1
    generator = VirtualFabGenerator(config=config, seed=31, simulation_id="SIM-MAINT")
    runs = [generator.generate_process_run("LOT-M", index, "cmp") for index in range(1, 6)]

    contexts = [
        (run["identity"]["equipment_id"], run["identity"]["unit_id"], run["identity"]["recipe_id"])
        for run in runs[:4]
    ]
    assert len({(equipment, unit) for equipment, unit, _ in contexts}) == 4
    assert len({recipe for _, _, recipe in contexts}) == 2
    assert runs[0]["identity"]["equipment_id"] == runs[4]["identity"]["equipment_id"]
    assert runs[0]["identity"]["unit_id"] == runs[4]["identity"]["unit_id"]
    assert runs[0]["identity"]["cycle_index"] == 1
    assert runs[4]["identity"]["cycle_index"] == 2
    assert runs[4]["identity"]["cycle_index_since_maintenance"] == 1
    assert runs[4]["state_events"][0]["identity"]["machine_state"] == "MAINTENANCE"


def test_inspection_mask_bbox_and_simulated_lags(local_images):
    catalog = load_fault_catalog()
    catalog["faults"]["cmp_slurry_degradation"]["vision"]["base_probability"] = 1.0
    catalog["faults"]["cmp_slurry_degradation"]["vision"]["max_probability"] = 1.0
    run = VirtualFabGenerator(
        fault_catalog=catalog, seed=41, simulation_id="SIM-MASK"
    ).generate_process_run("LOT-I", 1, "cmp", "cmp_slurry_degradation")

    debug = run["inspection"]["synthetic_debug"]
    mask_bytes = object_store.read_bytes(debug["mask_key"])
    assert mask_bytes is not None
    mask = np.asarray(Image.open(BytesIO(mask_bytes))) > 0
    ys, xs = np.where(mask)
    assert debug["bbox"] == [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    assert run["inspection"]["features"]
    completed = datetime.fromisoformat(run["outcome"]["completed_at"])
    measured = datetime.fromisoformat(run["metrology"]["available_at"])
    inspected = datetime.fromisoformat(run["inspection"]["available_at"])
    assert (measured - completed).total_seconds() == 300
    assert (inspected - completed).total_seconds() == 600

    normal = VirtualFabGenerator(seed=41, simulation_id="SIM-NORMAL").generate_process_run(
        "LOT-I", 1, "cmp"
    )
    assert "synthetic_debug" not in normal["inspection"]

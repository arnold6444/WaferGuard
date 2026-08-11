from __future__ import annotations

from pathlib import Path

import pytest

from app.services import config, db, process_ops, storage


@pytest.fixture()
def process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "waferguard.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(config, "DB_PATH", database)
    monkeypatch.setattr(storage, "DB_PATH", database)
    storage.init_db()
    return database


def _inspection(
    inspection_id: str,
    wafer_id: str,
    risk_level: str,
    risk_score: float,
    created_at: str,
    *,
    defect_type: str = "Edge-Loc",
) -> dict[str, object]:
    return {
        "id": inspection_id,
        "lot_id": "LOT-042",
        "wafer_id": wafer_id,
        "line_id": "LINE-7",
        "equipment_id": "ETCH-02",
        "process_step": "Etch",
        "recipe_id": "RCP-ETCH-EDGE-02",
        "image_source": "synthetic_wafer",
        "proxy_dataset": "wm811k",
        "proxy_status": "proxy fixture",
        "defect_type": defect_type,
        "confidence": 0.88,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "hotspot_ratio": 0.12,
        "image_url": "images/sample.png",
        "heatmap_url": "images/sample_heatmap.png",
        "overlay_url": "images/sample_overlay.png",
        "roi_url": "images/sample_roi.png",
        "roi_bbox": [180, 80, 220, 120],
        "report": "fixture",
        "cases": [],
        "process_context": {"lot_id": "LOT-042", "wafer_id": wafer_id},
        "metrology": {
            "cd_nm": 32.0,
            "overlay_nm": 4.2,
            "film_thickness_nm": 88.0,
            "roughness_nm": 1.2,
            "defect_count": 8,
            "yield_proxy": 0.98,
        },
        "cd_nm": 32.0,
        "overlay_nm": 4.2,
        "film_thickness_nm": 88.0,
        "roughness_nm": 1.2,
        "action_card": {"defect_type": defect_type},
        "model_version": "v-test",
        "status": "review_required",
        "created_at": created_at,
    }


def test_process_profiles_cover_eight_processes_and_mark_sources(process_env):
    profiles = process_ops.process_profiles()

    assert [profile["process_id"] for profile in profiles] == [
        "oxidation", "photo", "etch", "deposition", "implant", "metal", "cmp", "inspection"
    ]
    etch = next(profile for profile in profiles if profile["process_id"] == "etch")
    assert etch["connection"] == "runtime"
    assert {item["id"] for item in etch["parameters"]} >= {"source_rf_power", "resistance"}
    assert all(profile["data_source"] != "fab_runtime" for profile in profiles)


def test_process_event_window_uses_numeric_equipment_alias(process_env):
    storage.insert_process_event(
        {
            "id": "PROC-1",
            "process_step": "Etch",
            "equipment_id": "ETCH-002",
            "recipe_id": "RCP-1",
            "observed_at": "2026-08-11T01:45:00+00:00",
            "event_type": "rf_power_drift",
            "severity": "warning",
            "metadata": {"residual": 2.3},
            "source": "chamber_synthetic_runtime",
        }
    )
    storage.insert_process_event(
        {
            "id": "PROC-OLD",
            "process_step": "Etch",
            "equipment_id": "ETCH-002",
            "observed_at": "2026-08-10T23:00:00+00:00",
            "event_type": "old_signal",
            "severity": "warning",
            "metadata": {},
            "source": "chamber_synthetic_runtime",
        }
    )

    related = process_ops.related_process_events("ETCH-02", "2026-08-11T02:00:00+00:00")

    assert [event["id"] for event in related] == ["PROC-1"]
    assert related[0]["metadata"]["residual"] == 2.3


def test_quality_read_model_groups_latest_wafer_records(process_env):
    storage.insert_inspection(_inspection("INS-W01-OLD", "W01", "Low", 0.1, "2026-08-11T01:00:00+00:00"))
    storage.insert_inspection(_inspection("INS-W01", "W01", "High", 0.8, "2026-08-11T02:00:00+00:00"))
    storage.insert_inspection(_inspection("INS-W02", "W02", "Medium", 0.5, "2026-08-11T02:01:00+00:00"))

    lots = process_ops.quality_lots()
    detail = process_ops.quality_lot("LOT-042")

    assert lots["data_source"] == "runtime_inspection_db"
    assert lots["items"][0]["inspected"] == 2
    assert lots["items"][0]["critical"] == 1
    assert lots["items"][0]["warning"] == 1
    assert detail is not None
    assert [wafer["wafer_id"] for wafer in detail["wafers"]] == ["W01", "W02"]
    assert detail["wafers"][0]["inspection"]["id"] == "INS-W01"
    assert detail["wafers"][0]["defect_point"] is not None


def test_fab_overview_marks_demo_and_runtime_sources(process_env):
    storage.insert_inspection(_inspection("INS-W15", "W15", "High", 0.82, "2026-08-11T02:00:00+00:00"))

    overview = process_ops.fab_overview()

    inspection = next(item for item in overview["process_status"] if item["process_id"] == "inspection")
    photo = next(item for item in overview["process_status"] if item["process_id"] == "photo")
    assert overview["metrics"]["active_lots"]["data_source"] == "runtime_inspection_db"
    assert inspection["status"] == "critical"
    assert inspection["data_source"] == "proxy_runtime"
    assert photo["data_source"] == "demo_profile"
    assert "not connected" in overview["disclaimer"].lower()

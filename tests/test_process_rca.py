from __future__ import annotations

from app.services import process_rca


def test_rca_evidence_uses_exact_persisted_equipment_and_exposes_tags(monkeypatch):
    captured = {}

    def fake_events(**kwargs):
        captured.update(kwargs)
        return [
            {
                "id": "PROC-1",
                "process_step": "CMP",
                "equipment_id": "CMP-01",
                "recipe_id": "CMP_DEMO_A",
                "lot_id": "LOT-1",
                "wafer_id": "W01",
                "observed_at": "2026-08-14T04:30:00+00:00",
                "event_type": "vision_scratch",
                "severity": "critical",
                "source": "process_multimodal_synthetic_runtime",
                "metadata": {
                    "modality": "vision",
                    "anomaly_score": 1.2,
                    "threshold": 0.4,
                    "related_tags": ["down_force", "slurry_flow", "motor_current"],
                    "image_key": "process_runtime/cmp/W01.png",
                    "injected_anomaly": "scratch",
                },
            }
        ]

    monkeypatch.setattr(process_rca.storage, "list_process_events", fake_events)
    monkeypatch.setattr(process_rca.object_store, "presign", lambda key: f"/outputs/{key}")

    evidence = process_rca.build_process_rca_evidence(
        process_step="CMP",
        equipment_id="CMP-01",
        lot_id="LOT-1",
        wafer_id="W01",
        observed_at="2026-08-14T04:35:00+00:00",
    )

    assert captured["equipment_ids"] == ["CMP-01"]
    assert captured["lot_id"] == "LOT-1"
    assert captured["wafer_id"] == "W01"
    assert evidence["risk_level"] == "High"
    assert evidence["process_context"]["multimodal_rca"]["related_tags"] == [
        "down_force", "slurry_flow", "motor_current"
    ]
    event = evidence["process_context"]["related_process_events"][0]
    assert "related tags: down_force, slurry_flow, motor_current" in event["event_type"]
    assert "anomaly_score=1.2" in event["metadata"]["residual"]
    assert evidence["image_urls"] == ["/outputs/process_runtime/cmp/W01.png"]

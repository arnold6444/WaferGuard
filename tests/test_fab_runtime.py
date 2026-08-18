from __future__ import annotations

from app.services.fab_runtime import _one_per_modality


def test_run_fusion_selects_strongest_signed_margin_per_modality() -> None:
    selected = _one_per_modality(
        [
            {"id": "t1", "modality": "timeseries", "margin": -0.3},
            {"id": "t2", "modality": "timeseries", "margin": 0.2},
            {"id": "v1", "modality": "vision", "margin": -0.1},
        ]
    )

    assert [item["id"] for item in selected] == ["t2", "v1"]

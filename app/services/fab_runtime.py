"""Consumers and process-run finalization for the Virtual FAB runtime."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services import fab_storage
from app.services.fab_detection import detect_envelope
from app.services.fab_fusion import fuse_detector_results
from app.services.fab_mqtt import FabMessageRouter
from app.services.fab_rca import build_rca


def persist_fab_message(envelope: dict[str, Any]) -> dict[str, Any]:
    return fab_storage.persist_envelope(envelope)


def detect_fab_message(envelope: dict[str, Any]) -> dict[str, Any] | None:
    result = detect_envelope(envelope)
    if result is None:
        return None
    return fab_storage.save_detector_result(result)


def default_router() -> FabMessageRouter:
    # Ordering matters for direct transport and is retained by Python mappings:
    # identity/raw evidence must exist before a detector result is persisted.
    return FabMessageRouter(
        {
            "db_writer": persist_fab_message,
            "detector": detect_fab_message,
        }
    )


def _one_per_modality(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Use the strongest signed margin per modality for run-level fusion."""
    selected: dict[str, dict[str, Any]] = {}
    for raw in results:
        item = dict(raw)
        modality = str(item.get("modality") or "")
        if modality not in {"timeseries", "vision", "metrology"}:
            continue
        current = selected.get(modality)
        if current is None or float(item.get("margin") or 0.0) > float(current.get("margin") or 0.0):
            selected[modality] = item
    return [selected[name] for name in ("timeseries", "vision", "metrology") if name in selected]


def finalize_process_run(process_run_id: str, *, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    detail = fab_storage.process_run_detail(process_run_id)
    if detail is None:
        raise ValueError(f"Unknown process_run_id: {process_run_id}")
    selected = _one_per_modality(fab_storage.detector_results(process_run_id))
    if not selected:
        raise ValueError(f"No detector results for process run: {process_run_id}")
    fusion = fuse_detector_results(selected, config=config)
    saved_fusion = fab_storage.save_fusion_result(
        {
            **fusion,
            "wafer_id": detail["wafer_id"],
            "fusion_version": fusion["model_version"],
            "overall_risk": fusion["raw_score"],
            "weights": fusion.get("normalized_weights", {}),
            "evidence": {
                "detector_result_ids": [item.get("id") for item in selected],
                "related_tags": fusion.get("related_tags", []),
                "missing_modalities": fusion.get("missing_modalities", []),
            },
        }
    )
    # Re-query so RCA consumes exactly what the read API exposes, not simulator
    # objects or hidden fault state retained by the orchestrator.
    persisted_detail = fab_storage.process_run_detail(process_run_id)
    if persisted_detail is None:  # pragma: no cover - guarded by first query
        raise RuntimeError("process run disappeared during finalization")
    rca = build_rca(persisted_detail, selected, fusion)
    candidates = list(rca.get("candidates") or [])
    top = candidates[0] if candidates else {}
    recommended_checks = [
        "Review recent maintenance and cleaning history for the selected unit.",
        "Compare the same recipe on peer equipment and units.",
        "Confirm metrology and inspection evidence before any equipment action.",
    ]
    saved_rca = fab_storage.save_rca_result(
        {
            "process_run_id": process_run_id,
            "wafer_id": detail["wafer_id"],
            "candidate_causes": candidates,
            "evidence": {
                "summary": rca.get("summary"),
                "input_modalities": rca.get("input_modalities", []),
                "source": rca.get("source"),
                "uses_simulation_ground_truth": False,
            },
            "recommended_checks": recommended_checks,
            "top_candidate": top.get("cause_id") or top.get("label"),
            "confidence": float(top.get("confidence") or 0.0),
            "overall_risk": fusion["raw_score"],
            "fusion_version": fusion["model_version"],
            "model_or_rule_version": rca["model_version"],
            "created_at": rca.get("observed_at"),
        }
    )
    return {
        "process_run_id": process_run_id,
        "detector_results": selected,
        "fusion": saved_fusion,
        "rca": saved_rca,
    }

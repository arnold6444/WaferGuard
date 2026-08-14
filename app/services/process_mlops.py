"""MLOps policy layer for the generic process multimodal runtime.

The core runtime owns artifacts/inference. This module adds safer lifecycle policy:
health comparison, Staging candidate training, guarded promotion, and rollback.
"""
from __future__ import annotations

from typing import Any

from app.services.process_runtime import (
    list_models,
    production_model,
    promote_model,
    runtime_metrics,
)
from app.services.process_temporal import train_process_model


def model_health(process_id: str, modality: str, *, limit: int = 500) -> dict[str, Any]:
    production = production_model(process_id, modality)
    runtime = runtime_metrics(process_id, modality, limit=limit)
    if production is None:
        return {
            "process_id": process_id,
            "modality": modality,
            "production": None,
            "runtime": runtime,
            "degraded": True,
            "reason": "no_production_model",
        }

    runtime_f2 = runtime.get("f2")
    runtime_fp = runtime.get("false_positive_rate")
    f2_floor = max(0.0, float(production["f2"]) - 0.10)
    fp_ceiling = min(1.0, float(production["false_positive_rate"]) + 0.10)
    enough_runtime = int(runtime.get("rows") or 0) >= 30 and runtime_f2 is not None
    degraded = bool(
        enough_runtime
        and (
            float(runtime_f2) < f2_floor
            or (runtime_fp is not None and float(runtime_fp) > fp_ceiling)
        )
    )
    return {
        "process_id": process_id,
        "modality": modality,
        "production": production,
        "runtime": runtime,
        "degraded": degraded,
        "reason": "runtime_metric_degradation" if degraded else (
            "insufficient_runtime_gt" if not enough_runtime else "healthy"
        ),
        "policy": {
            "runtime_min_rows": 30,
            "f2_floor": f2_floor,
            "false_positive_rate_ceiling": fp_ceiling,
            "scope": "synthetic runtime GT policy; replace with real validation/drift signals for Fab data",
        },
    }


def train_candidate(process_id: str, modality: str, *, force: bool = False) -> dict[str, Any]:
    health = model_health(process_id, modality)
    if not force and not health["degraded"] and health["reason"] != "insufficient_runtime_gt":
        return {"accepted": False, "reason": "production_healthy", "health": health}
    candidate = train_process_model(process_id, modality, stage="Staging")
    production = production_model(process_id, modality)
    comparison = {
        "candidate_f2": float(candidate["f2"]),
        "production_f2": float(production["f2"]) if production else None,
        "candidate_fp_rate": float(candidate["false_positive_rate"]),
        "production_fp_rate": float(production["false_positive_rate"]) if production else None,
    }
    comparison["passes"] = bool(
        production is None
        or (
            comparison["candidate_f2"] >= comparison["production_f2"]
            and comparison["candidate_fp_rate"] <= comparison["production_fp_rate"] + 0.02
        )
    )
    return {
        "accepted": True,
        "candidate": candidate,
        "comparison": comparison,
        "promotion_recommended": comparison["passes"],
        "health_at_trigger": health,
    }


def promote_candidate(process_id: str, modality: str, version: str) -> dict[str, Any]:
    models = list_models(process_id, modality)
    target = next((model for model in models if model["version"] == version), None)
    if target is None:
        raise KeyError(f"Unknown model: {process_id}/{modality}/{version}")
    if target["stage"] != "Staging":
        raise ValueError(f"Only Staging models can be promoted; {version} is {target['stage']}")

    production = production_model(process_id, modality)
    if production is not None:
        if float(target["f2"]) < float(production["f2"]):
            raise ValueError(
                f"Candidate F2 {target['f2']:.4f} is below Production F2 {production['f2']:.4f}"
            )
        if float(target["false_positive_rate"]) > float(production["false_positive_rate"]) + 0.02:
            raise ValueError(
                "Candidate false-positive rate is more than 0.02 above Production"
            )
    return promote_model(process_id, modality, version)


def rollback_model(process_id: str, modality: str) -> dict[str, Any]:
    models = list_models(process_id, modality)
    archived = [model for model in models if model["stage"] == "Archived"]
    if not archived:
        raise RuntimeError(f"No Archived model available for {process_id}/{modality}")
    target = archived[0]
    return promote_model(process_id, modality, str(target["version"]))

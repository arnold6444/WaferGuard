"""Calibrated late fusion for FAB time-series, vision, and metrology results."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from app.services.fab_schema import DetectorResult, FAB_FEATURE_CONTRACT, as_utc


FUSION_MODEL_VERSION = "late-fusion-sigmoid-v1"
EXPECTED_MODALITIES = ("timeseries", "vision", "metrology")


def _fusion_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if config is None:
        # Lazy import keeps the schema/fusion layer free of generator startup
        # state and avoids a module cycle.
        from app.services.fab_generator import load_fab_config  # noqa: PLC0415

        config = load_fab_config()
    return config.get("fusion", config)


def calibrated_risk(result: DetectorResult, *, scale: float = 1.0) -> float:
    """Map boundary distance to [0, 1] without assuming scores are positive."""
    safe_scale = max(abs(float(scale)), 1e-9)
    exponent = max(-60.0, min(60.0, -result.margin / safe_scale))
    return 1.0 / (1.0 + math.exp(exponent))


def fuse_detector_results(
    results: Iterable[Mapping[str, Any] | DetectorResult],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse available modalities, renormalizing weights when one is missing."""
    parsed = [item if isinstance(item, DetectorResult) else DetectorResult.from_dict(item) for item in results]
    if not parsed:
        raise ValueError("At least one detector result is required for fusion")
    run_ids = {item.process_run_id for item in parsed}
    if len(run_ids) != 1:
        raise ValueError("Detector results from different process runs cannot be fused")
    by_modality: dict[str, DetectorResult] = {}
    for item in parsed:
        if item.modality not in EXPECTED_MODALITIES:
            raise ValueError(f"Unsupported fusion modality: {item.modality}")
        if item.modality in by_modality:
            raise ValueError(f"Duplicate detector result for modality: {item.modality}")
        by_modality[item.modality] = item

    settings = _fusion_config(config)
    configured_weights = {str(key): float(value) for key, value in settings.get("weights", {}).items()}
    available_weight = sum(max(0.0, configured_weights.get(name, 0.0)) for name in by_modality)
    if available_weight <= 0:
        raise ValueError("Fusion weights for available modalities must sum to a positive value")
    normalized_weights = {
        name: max(0.0, configured_weights.get(name, 0.0)) / available_weight
        for name in by_modality
    }
    scale = float(settings.get("calibration_scale", 1.0))
    risks = {name: calibrated_risk(item, scale=scale) for name, item in by_modality.items()}
    fused_risk = sum(risks[name] * normalized_weights[name] for name in by_modality)
    threshold = float(settings.get("threshold", 0.65))
    observed_at = max((item.observed_at for item in parsed), key=as_utc)
    related_tags: list[str] = []
    for item in sorted(parsed, key=lambda row: (not row.is_anomaly, row.modality)):
        for tag in item.related_tags:
            if tag not in related_tags:
                related_tags.append(tag)

    fused = DetectorResult.from_score(
        process_run_id=parsed[0].process_run_id,
        modality="fusion",
        model_version=FUSION_MODEL_VERSION,
        raw_score=fused_risk,
        threshold=threshold,
        related_tags=related_tags,
        observed_at=observed_at,
    ).to_dict()
    return {
        **fused,
        "modality_risks": {name: round(value, 8) for name, value in risks.items()},
        "normalized_weights": {name: round(value, 8) for name, value in normalized_weights.items()},
        "missing_modalities": [name for name in EXPECTED_MODALITIES if name not in by_modality],
        "feature_contract": FAB_FEATURE_CONTRACT,
    }

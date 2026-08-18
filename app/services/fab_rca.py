"""Evidence-ranked candidate RCA for FAB v2.

RCA consumes persisted telemetry and detector evidence only.  The optional
fault catalog supplies the universe of plausible causes and expected tag
directions; it never supplies which fault was injected for a particular run.
Simulation truth comparison lives in the explicitly separate evaluation helper.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np

from app.services.fab_schema import DetectorResult, FAB_FEATURE_CONTRACT


RCA_MODEL_VERSION = "evidence-ranked-rca-v1"


def _catalog(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        from app.services.fab_generator import load_fault_catalog  # noqa: PLC0415

        value = load_fault_catalog()
    return value.get("faults", value)


def _process_id(detail: Mapping[str, Any]) -> str:
    identity = detail.get("identity") or {}
    process_run = detail.get("process_run") or detail.get("run") or {}
    process_id = identity.get("process_id") or detail.get("process_id") or process_run.get("process_id")
    if process_id:
        return str(process_id).lower()
    step = str(identity.get("process_step") or detail.get("process_step") or process_run.get("process_step") or "").lower()
    return "photo" if step == "lithography" else step


def _run_id(detail: Mapping[str, Any]) -> str:
    identity = detail.get("identity") or {}
    process_run = detail.get("process_run") or detail.get("run") or {}
    value = identity.get("process_run_id") or detail.get("process_run_id") or process_run.get("process_run_id") or process_run.get("id")
    if not value:
        raise ValueError("process_run_detail must include process_run_id")
    return str(value)


def _normalized_telemetry(detail: Mapping[str, Any]) -> dict[str, float]:
    samples: dict[str, list[float]] = {}
    for row in detail.get("telemetry") or []:
        context = row.get("detector_context") or {}
        normalized = context.get("normalized_values") or {}
        for tag, value in normalized.items():
            samples.setdefault(str(tag), []).append(float(value))
    return {tag: float(np.mean(values)) for tag, values in samples.items() if values}


def _metrology_deviation(detail: Mapping[str, Any]) -> dict[str, float]:
    deviations: dict[str, float] = {}
    raw = detail.get("metrology") or detail.get("metrology_results") or []
    rows = raw if isinstance(raw, list) else [raw]
    for metrology in rows:
        if not isinstance(metrology, Mapping):
            continue
        metrics = metrology.get("metrics") or {}
        targets = metrology.get("quality_targets") or metrology.get("quality_target") or {}
        for name, target in targets.items():
            if name not in metrics:
                continue
            tolerance = max(abs(float(target.get("tolerance", 1.0))), 1e-9)
            deviations[str(name)] = (float(metrics[name]) - float(target.get("target", 0.0))) / tolerance
    return deviations


def _detectors(values: Iterable[Mapping[str, Any] | DetectorResult]) -> list[DetectorResult]:
    return [value if isinstance(value, DetectorResult) else DetectorResult.from_dict(value) for value in values]


def _label(cause_id: str) -> str:
    return cause_id.replace("_", " ").title()


def build_rca(
    process_run_detail: Mapping[str, Any],
    detector_results: Iterable[Mapping[str, Any] | DetectorResult],
    fusion_result: Mapping[str, Any],
    fault_catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank candidate causes without reading simulator ground-truth storage."""
    process_id = _process_id(process_run_detail)
    process_run_id = _run_id(process_run_detail)
    detectors = _detectors(detector_results)
    if any(result.process_run_id != process_run_id for result in detectors):
        raise ValueError("RCA detector results must belong to process_run_detail")
    if str(fusion_result.get("process_run_id")) != process_run_id:
        raise ValueError("RCA fusion result must belong to process_run_detail")

    normalized = _normalized_telemetry(process_run_detail)
    metrology = _metrology_deviation(process_run_detail)
    related: dict[str, float] = {}
    modalities: list[str] = []
    for detector in detectors:
        modalities.append(detector.modality)
        detector_strength = 1.0 + min(detector.margin, 4.0) if detector.is_anomaly else 0.25
        for tag in detector.related_tags:
            related[tag] = related.get(tag, 0.0) + detector_strength

    candidates: list[dict[str, Any]] = []
    for cause_id, definition in _catalog(fault_catalog).items():
        if str(definition.get("process_id", "")).lower() != process_id:
            continue
        score = 0.10
        evidence: list[str] = []
        effects = definition.get("sensor_effects") or {}
        for tag, effect in effects.items():
            tag = str(tag)
            observed = float(normalized.get(tag, 0.0))
            coefficient = float(effect.get("coefficient", 0.0))
            attribution = float(related.get(tag, 0.0))
            if attribution:
                contribution = min(attribution, 4.0) * 0.32
                score += contribution
                evidence.append(f"detector attribution includes {tag}")
            if observed and coefficient and observed * coefficient > 0:
                contribution = min(abs(observed), 5.0) * 0.24
                score += contribution
                evidence.append(f"{tag} residual direction matches the candidate pattern")

        for metric, expected_offset in (definition.get("quality_effects") or {}).items():
            observed = float(metrology.get(str(metric), 0.0))
            expected = float(expected_offset)
            if observed and expected and observed * expected > 0:
                score += min(abs(observed), 4.0) * 0.28
                evidence.append(f"{metric} deviation has the expected direction")

        vision = next((item for item in detectors if item.modality == "vision"), None)
        if vision and vision.is_anomaly and definition.get("vision"):
            score += 0.20 + min(vision.margin, 3.0) * 0.08
            evidence.append("inspection features are anomalous")

        confidence = score / (score + 1.5)
        candidates.append({
            "cause_id": str(cause_id),
            "label": _label(str(cause_id)),
            "score": round(score, 8),
            "confidence": round(confidence, 8),
            "evidence": evidence or ["process context match; direct supporting evidence is limited"],
            "explanation": f"Candidate root cause: {_label(str(cause_id))}.",
        })

    max_residual = max((abs(value) for value in normalized.values()), default=0.0)
    generic = [
        (
            "equipment_context_shift",
            0.18 + min(max_residual, 4.0) * 0.12,
            ["equipment/unit normalized residuals differ from the contextual baseline"],
        ),
        (
            "recipe_or_setup_deviation",
            0.16 + (0.18 if related else 0.0),
            ["detector attribution is compatible with a recipe or setup deviation"],
        ),
        (
            "measurement_or_inspection_shift",
            0.12 + (0.25 if any(item.modality == "vision" and item.is_anomaly for item in detectors) else 0.0),
            ["inspection or measurement evidence should be confirmed independently"],
        ),
    ]
    for cause_id, score, evidence in generic:
        candidates.append({
            "cause_id": cause_id,
            "label": _label(cause_id),
            "score": round(score, 8),
            "confidence": round(score / (score + 1.5), 8),
            "evidence": evidence,
            "explanation": f"Candidate root cause: {_label(cause_id)}.",
        })

    candidates.sort(key=lambda item: (-float(item["score"]), str(item["cause_id"])))
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    fused_risk = float(fusion_result.get("raw_score", 0.0))
    top = candidates[0] if candidates else None
    return {
        "process_run_id": process_run_id,
        "process_id": process_id,
        "model_version": RCA_MODEL_VERSION,
        "feature_contract": FAB_FEATURE_CONTRACT,
        "fusion_risk": fused_risk,
        "summary": (
            f"Candidate root cause: {top['label']}. Confirm with engineering review before action."
            if top else "Candidate root cause: insufficient observed evidence."
        ),
        "candidates": candidates,
        "input_modalities": sorted(set(modalities)),
        "source": "persisted_observed_evidence",
        "uses_simulation_ground_truth": False,
        "observed_at": str(fusion_result.get("observed_at") or ""),
    }


def evaluate_rca(rca_result: Mapping[str, Any], fault_id: str) -> dict[str, Any]:
    """Evaluation-only Top-1/Top-3 comparison performed after RCA completes."""
    ranked = [str(item.get("cause_id")) for item in rca_result.get("candidates") or []]
    try:
        rank = ranked.index(str(fault_id)) + 1
    except ValueError:
        rank = None
    return {
        "process_run_id": rca_result.get("process_run_id"),
        "fault_id": str(fault_id),
        "rank": rank,
        "top_1": rank == 1,
        "top_3": rank is not None and rank <= 3,
        "evaluation_only": True,
    }

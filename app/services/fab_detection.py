"""FAB v2 detector adapter with a common score/margin contract.

Existing process artifacts remain authoritative when their feature contract
matches the incoming event. A transparent context baseline is used only while
a FAB-compatible artifact is not yet available; it is versioned and surfaced
as such rather than being presented as a trained model.
"""
from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.services import process_runtime
from app.services.fab_schema import FAB_FEATURE_CONTRACT


FAB_CONTEXT_BASELINE_VERSION = "fab-context-baseline-v1"
FAB_METROLOGY_RULE_VERSION = "fab-metrology-risk-v1"


def detector_result(
    *,
    message_id: str,
    process_run_id: str,
    modality: str,
    model_version: str,
    raw_score: float,
    threshold: float,
    observed_at: str,
    related_tags: Sequence[str] = (),
    context_key: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw = float(raw_score)
    boundary = float(threshold)
    margin = raw - boundary
    return {
        "id": f"FABDET-{uuid.uuid5(uuid.NAMESPACE_URL, f'{message_id}:{modality}').hex}",
        "message_id": message_id,
        "process_run_id": process_run_id,
        "modality": modality,
        "model_version": model_version,
        "raw_score": raw,
        "threshold": boundary,
        "margin": margin,
        "is_anomaly": margin >= 0.0,
        "related_tags": list(dict.fromkeys(str(tag) for tag in related_tags)),
        "context_key": context_key,
        "observed_at": observed_at,
        "metadata": dict(metadata or {}),
    }


def _context_key(identity: Mapping[str, Any]) -> str:
    return "/".join(
        str(identity.get(field) or "-")
        for field in ("process_id", "equipment_id", "unit_id", "recipe_id", "phase")
    )


def _process_id(identity: Mapping[str, Any]) -> str:
    value = identity.get("process_id") or identity.get("process_step")
    process_id = str(value or "").strip().lower()
    return "photo" if process_id == "lithography" else process_id


def _trained_score(process_id: str, modality: str, payload: Mapping[str, Any]) -> tuple[float, float, str] | None:
    record = process_runtime.production_model(process_id, modality)
    if record is None:
        return None
    context = payload.get("detector_context")
    if not isinstance(context, Mapping):
        context = payload
    raw_vector = context.get("feature_vector") if isinstance(context, Mapping) else None
    raw_names = context.get("feature_names") if isinstance(context, Mapping) else None
    if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes)):
        return None
    bundle = process_runtime._load_bundle(record)
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    incoming_contract = context.get("feature_contract")
    incoming_feature_fingerprint = context.get("feature_fingerprint")
    incoming_config_fingerprint = context.get("config_fingerprint")
    artifact_contract = bundle.get("feature_contract") or metadata.get("feature_contract")
    artifact_feature_fingerprint = (
        bundle.get("feature_fingerprint") or metadata.get("feature_fingerprint")
    )
    artifact_config_fingerprint = (
        bundle.get("config_fingerprint") or metadata.get("config_fingerprint")
    )
    # Legacy artifacts remain registered, but a FAB event may use them only
    # when the artifact explicitly declares the same v2 contract.  Otherwise
    # inference falls back to the transparent context baseline until a new
    # candidate is manually promoted.
    if (
        incoming_contract != FAB_FEATURE_CONTRACT
        or artifact_contract != FAB_FEATURE_CONTRACT
        or not incoming_feature_fingerprint
        or incoming_feature_fingerprint != artifact_feature_fingerprint
        or not incoming_config_fingerprint
        or incoming_config_fingerprint != artifact_config_fingerprint
    ):
        return None
    expected = list(bundle.get("feature_names") or [])
    if raw_names is not None and list(raw_names) != expected:
        return None
    vector = np.asarray(list(raw_vector), dtype=float).reshape(1, -1)
    if expected and vector.shape[1] != len(expected):
        return None
    score = float(process_runtime._scores(bundle["model"], vector)[0])
    return score, float(bundle["threshold"]), str(record["version"])


def _baseline_score(process_id: str, payload: Mapping[str, Any]) -> tuple[float, float, list[str]]:
    context = payload.get("detector_context")
    if isinstance(context, Mapping):
        normalized = context.get("normalized_values")
        if isinstance(normalized, Mapping) and normalized:
            ordered = sorted((str(tag), abs(float(value))) for tag, value in normalized.items())
            ordered.sort(key=lambda item: item[1], reverse=True)
            return ordered[0][1], 4.0, [tag for tag, value in ordered if value >= 3.0][:4]
    tags = payload.get("tags")
    if not isinstance(tags, Mapping):
        tags = payload
    try:
        _, profile = process_runtime._profile(process_id)
    except KeyError:
        return 0.0, 4.0, []
    deviations: list[tuple[str, float]] = []
    for tag, spec in profile.get("timeseries", {}).get("tags", {}).items():
        if tag not in tags:
            continue
        std = max(abs(float(spec.get("std", 1.0))), 1e-9)
        deviations.append((str(tag), abs((float(tags[tag]) - float(spec.get("mean", 0.0))) / std)))
    deviations.sort(key=lambda item: item[1], reverse=True)
    return (
        deviations[0][1] if deviations else 0.0,
        4.0,
        [tag for tag, value in deviations if value >= 3.0][:4],
    )


def detect_timeseries(envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    identity = envelope["identity"]
    if str(identity.get("machine_state")).upper() != "RUNNING":
        return None
    process_id = _process_id(identity)
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), Mapping) else {}
    context = payload.get("detector_context")
    if not isinstance(context, Mapping):
        context = {}
    trained = _trained_score(process_id, "timeseries", payload)
    if trained:
        score, threshold, version = trained
        related = context.get("related_tags") or payload.get("related_tags") or []
        source = "production_artifact"
    else:
        score, threshold, related = _baseline_score(process_id, payload)
        version = FAB_CONTEXT_BASELINE_VERSION
        source = "hierarchical_context_baseline"
    return detector_result(
        message_id=str(envelope["message_id"]),
        process_run_id=str(identity["process_run_id"]),
        modality="timeseries",
        model_version=version,
        raw_score=score,
        threshold=threshold,
        related_tags=related,
        context_key=_context_key(identity),
        observed_at=str(identity["observed_at"]),
        metadata={
            "source": source,
            "machine_state": identity["machine_state"],
            "phase": identity["phase"],
            "phase_progress": identity["phase_progress"],
            "cycle_index_since_maintenance": identity["cycle_index_since_maintenance"],
            "contract": "fab-context-v1",
            "feature_fingerprint": context.get("feature_fingerprint"),
            "config_fingerprint": context.get("config_fingerprint"),
        },
    )


def detect_vision(envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    identity = envelope["identity"]
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), Mapping) else {}
    features = payload.get("features")
    if not isinstance(features, Mapping) or not features:
        return None
    process_id = _process_id(identity)
    record = process_runtime.production_model(process_id, "vision")
    score: float
    threshold: float
    version: str
    source: str
    if record is not None:
        bundle = process_runtime._load_bundle(record)
        names = list(bundle.get("feature_names") or [])
        if names and all(name in features for name in names):
            vector = np.asarray([float(features[name]) for name in names], dtype=float).reshape(1, -1)
            score = float(process_runtime._scores(bundle["model"], vector)[0])
            threshold = float(bundle["threshold"])
            version = str(record["version"])
            source = "production_artifact"
        else:
            record = None
    if record is None:
        score = float(max((abs(float(value)) for value in features.values()), default=0.0))
        threshold = 1.0
        version = "fab-vision-feature-baseline-v1"
        source = "vision_feature_baseline"
    return detector_result(
        message_id=str(envelope["message_id"]),
        process_run_id=str(identity["process_run_id"]),
        modality="vision",
        model_version=version,
        raw_score=score,
        threshold=threshold,
        related_tags=payload.get("related_tags") or [],
        context_key=_context_key(identity),
        observed_at=str(identity["observed_at"]),
        metadata={"source": source, "image_key": payload.get("image_key")},
    )


def detect_metrology(envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    identity = envelope["identity"]
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), Mapping) else {}
    metrics = payload.get("metrics")
    targets = payload.get("quality_targets")
    if not isinstance(metrics, Mapping) or not isinstance(targets, Mapping):
        return None
    deviations: list[tuple[str, float]] = []
    for name, target in targets.items():
        if name not in metrics:
            continue
        if isinstance(target, Mapping):
            center = float(target.get("target", target.get("mean", 0.0)))
            tolerance = max(abs(float(target.get("tolerance", target.get("std", 1.0)))), 1e-9)
        else:
            center = float(target)
            tolerance = max(abs(center) * 0.05, 1e-9)
        deviations.append((str(name), abs((float(metrics[name]) - center) / tolerance)))
    deviations.sort(key=lambda item: item[1], reverse=True)
    score = deviations[0][1] if deviations else 0.0
    return detector_result(
        message_id=str(envelope["message_id"]),
        process_run_id=str(identity["process_run_id"]),
        modality="metrology",
        model_version=FAB_METROLOGY_RULE_VERSION,
        raw_score=score,
        threshold=1.0,
        related_tags=[name for name, value in deviations if value >= 1.0],
        context_key=_context_key(identity),
        observed_at=str(identity["observed_at"]),
        metadata={"source": "quality_target_deviation", "deviations": dict(deviations)},
    )


def normalized_risk(result: Mapping[str, Any], scale: float) -> float:
    safe_scale = max(abs(float(scale)), 1e-9)
    value = float(result["margin"]) / safe_scale
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-min(value, 700.0)))
    exp_value = math.exp(max(value, -700.0))
    return exp_value / (1.0 + exp_value)


def detect_envelope(envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    message_type = str(envelope.get("message_type") or "").lower()
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), Mapping) else {}
    embedded = payload.get("detector_result") if isinstance(payload, Mapping) else None
    if isinstance(embedded, Mapping) and message_type in {"metrology", "inspection"}:
        identity = envelope["identity"]
        expected_modality = "vision" if message_type == "inspection" else "metrology"
        if str(embedded.get("modality") or expected_modality) != expected_modality:
            raise ValueError("embedded detector modality does not match message_type")
        return detector_result(
            message_id=str(envelope["message_id"]),
            process_run_id=str(identity["process_run_id"]),
            modality=expected_modality,
            model_version=str(embedded["model_version"]),
            raw_score=float(embedded.get("raw_score", embedded.get("anomaly_score", 0.0))),
            threshold=float(embedded["threshold"]),
            related_tags=embedded.get("related_tags") or (),
            context_key=_context_key(identity),
            observed_at=str(embedded.get("observed_at") or identity["observed_at"]),
            metadata={
                "source": "post_process_observed_features",
                "image_key": payload.get("image_key"),
            },
        )
    if message_type == "telemetry":
        return detect_timeseries(envelope)
    if message_type == "metrology":
        return detect_metrology(envelope)
    if message_type == "inspection":
        return detect_vision(envelope)
    return None

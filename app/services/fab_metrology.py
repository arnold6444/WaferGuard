"""Synthetic post-process metrology and inspection for FAB v2.

Both simulators consume only the run identity and telemetry evidence.  Latent
quality offsets and defect choices are accepted as private injection inputs and
are never copied into the returned detector evidence.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image

from app.services import object_store, process_runtime
from app.services.fab_schema import DetectorResult, FabEventIdentity, as_utc, utc_iso


METROLOGY_MODEL_VERSION = "synthetic-metrology-v1"
VISION_MODEL_VERSION = "synthetic-vision-stat-v1"
VISION_FEATURE_NAMES = (
    "mean", "std", "center_mean", "mid_mean", "edge_mean",
    "grad_x", "grad_y", "high_ratio", "low_ratio", "symmetry_error",
)


def _identity(value: FabEventIdentity | Mapping[str, Any]) -> FabEventIdentity:
    return value if isinstance(value, FabEventIdentity) else FabEventIdentity.from_dict(value)


def _mean_normalized(telemetry: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for row in telemetry:
        context = row.get("detector_context") or {}
        normalized = context.get("normalized_values") or {}
        for tag, value in normalized.items():
            values.setdefault(str(tag), []).append(float(value))
    return {tag: float(np.mean(rows)) for tag, rows in values.items() if rows}


class FabMetrologySimulator:
    """Create one delayed metrology result from a completed process run."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config

    def generate(
        self,
        identity: FabEventIdentity | Mapping[str, Any],
        telemetry: Sequence[Mapping[str, Any]],
        *,
        rng: np.random.Generator,
        latent_quality_effects: Mapping[str, float] | None = None,
        available_at: str | None = None,
    ) -> dict[str, Any]:
        base = _identity(identity)
        process = self.config["processes"][base.process_id]
        specifications = process["metrology"]
        normalized = _mean_normalized(telemetry)
        hidden_offsets = latent_quality_effects or {}
        metrics: dict[str, float] = {}
        quality_targets: dict[str, dict[str, float]] = {}
        metric_related: dict[str, list[str]] = {}
        measurements: list[dict[str, Any]] = []

        for name, spec in specifications.items():
            target = float(spec["target"])
            tolerance = float(spec["tolerance"])
            contributions = {
                str(tag): float(coefficient) * float(normalized.get(str(tag), 0.0))
                for tag, coefficient in spec.get("drivers", {}).items()
            }
            value = (
                target
                + sum(contributions.values())
                + float(hidden_offsets.get(name, 0.0))
                + float(rng.normal(0.0, float(spec.get("noise_std", 0.0))))
            )
            metrics[name] = round(value, 6)
            quality_targets[name] = {
                "target": target,
                "tolerance": tolerance,
                "lower": target - tolerance,
                "upper": target + tolerance,
                "display_name": str(spec["display_name"]),
                "unit": str(spec["unit"]),
                "modality": str(spec["modality"]),
                "instrument_class": str(spec["instrument_class"]),
                "method": str(spec["method"]),
                "sampling_level": str(spec["sampling_level"]),
            }
            metric_related[name] = [
                tag for tag, _ in sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
            ]
            measurements.append({
                "metric_id": str(name),
                "display_name": str(spec["display_name"]),
                "value": metrics[name],
                "unit": str(spec["unit"]),
                "modality": str(spec["modality"]),
                "instrument_class": str(spec["instrument_class"]),
                "method": str(spec["method"]),
                "sampling_level": str(spec["sampling_level"]),
                "target": target,
                "tolerance": tolerance,
                "synthetic_proxy": True,
            })

        normalized_errors = {
            name: abs(metrics[name] - target["target"]) / max(target["tolerance"], 1e-9)
            for name, target in quality_targets.items()
        }
        worst_metric = max(normalized_errors, key=normalized_errors.get)
        raw_score = float(normalized_errors[worst_metric])
        timestamp = available_at or utc_iso(
            as_utc(base.observed_at) + timedelta(seconds=float(self.config["clock"]["metrology_lag_seconds"]))
        )
        detector = DetectorResult.from_score(
            process_run_id=base.process_run_id,
            modality="metrology",
            model_version=METROLOGY_MODEL_VERSION,
            raw_score=raw_score,
            threshold=1.0,
            related_tags=metric_related[worst_metric],
            observed_at=timestamp,
        )
        modalities = list(dict.fromkeys(item["modality"] for item in measurements))
        instruments = list(dict.fromkeys(item["instrument_class"] for item in measurements))
        return {
            "process_run_id": base.process_run_id,
            "metrics": metrics,
            "measurements": measurements,
            # Keep both spellings during the v2 additive rollout. The envelope
            # persistence contract uses plural; API callers requested singular.
            "quality_target": quality_targets,
            "quality_targets": quality_targets,
            "metrology_context": {
                "process_id": base.process_id,
                "measurement_scope": "post_process_inline_or_atline",
                "modalities": modalities,
                "instrument_classes": instruments,
                "synthetic_proxy": True,
                "disclaimer": "Synthetic metrology proxy; not instrument data or Fab control limits.",
            },
            "available_at": utc_iso(timestamp),
            "detector_result": detector.to_dict(),
            "related_tags": list(detector.related_tags),
        }


class FabInspectionSimulator:
    """Create at most one procedural inspection image for a process run."""

    def __init__(self, config: Mapping[str, Any], *, persist_images: bool = True) -> None:
        self.config = config
        self.persist_images = bool(persist_images)

    def generate(
        self,
        identity: FabEventIdentity | Mapping[str, Any],
        telemetry: Sequence[Mapping[str, Any]],
        *,
        rng: np.random.Generator,
        defect_probability: float,
        defect: str | None,
        available_at: str | None = None,
    ) -> dict[str, Any]:
        base = _identity(identity)
        inspection_config = dict(self.config["processes"][base.process_id]["inspection"])
        size = int(process_runtime.load_config().get("runtime", {}).get("image_size", 96))
        profile = process_runtime._profile(base.process_id)[1]
        clean = process_runtime._base_image(base.process_id, size, rng)
        image = clean.copy()
        injected = bool(defect and rng.random() < min(1.0, max(0.0, float(defect_probability))))
        if injected:
            image = process_runtime._inject_defect(image, str(defect), rng)

        difference = np.abs(image - clean)
        mask = difference > 0.02
        bbox: list[int] | None = None
        if mask.any():
            ys, xs = np.where(mask)
            bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]

        image_object = Image.fromarray(np.uint8(np.clip(image * 255.0, 0.0, 255.0)), mode="L")
        prefix = f"fab/{base.process_run_id}"
        image_key = f"{prefix}/inspection.png"
        if self.persist_images:
            image_key = object_store.save_image(image_object, image_key)

        synthetic_debug: dict[str, Any] = {}
        if bbox is not None:
            mask_object = Image.fromarray(np.uint8(mask) * 255, mode="L")
            mask_key = f"{prefix}/ground-truth-mask.png"
            if self.persist_images:
                mask_key = object_store.save_image(mask_object, mask_key)
            synthetic_debug = {"mask_key": mask_key, "bbox": bbox}

        actual_features = process_runtime.vision_features(image)
        clean_features = process_runtime.vision_features(clean)
        # Feature-specific proxy scales make the simple score useful while
        # retaining the existing handcrafted vision feature contract.
        scales = np.asarray([0.03, 0.03, 0.04, 0.04, 0.04, 0.025, 0.025, 0.03, 0.03, 0.035])
        raw_score = float(np.max(np.abs(actual_features - clean_features) / scales))
        timestamp = available_at or utc_iso(
            as_utc(base.observed_at) + timedelta(seconds=float(self.config["clock"]["inspection_lag_seconds"]))
        )
        related_tags = self.config["processes"][base.process_id].get("vision_related_tags", []) if raw_score >= 1.0 else []
        detector = DetectorResult.from_score(
            process_run_id=base.process_run_id,
            modality="vision",
            model_version=VISION_MODEL_VERSION,
            raw_score=raw_score,
            threshold=1.0,
            related_tags=related_tags,
            observed_at=timestamp,
        )
        result = {
            "process_run_id": base.process_run_id,
            "image_key": image_key,
            "inspection_modality": str(inspection_config["inspection_modality"]),
            "instrument_class": str(inspection_config["instrument_class"]),
            "image_type": str(inspection_config["image_type"]),
            "sampling_level": str(inspection_config["sampling_level"]),
            "inspection_context": {
                **inspection_config,
                "measurement_scope": "post_process_review",
                "synthetic_proxy": True,
                "disclaimer": "Procedural image proxy; not SEM, TEM, or optical instrument output.",
            },
            "features": {
                name: round(float(value), 8)
                for name, value in zip(VISION_FEATURE_NAMES, actual_features, strict=True)
            },
            "available_at": utc_iso(timestamp),
            "detector_result": detector.to_dict(),
            "related_tags": list(detector.related_tags),
        }
        if synthetic_debug:
            result["synthetic_debug"] = synthetic_debug
        return result

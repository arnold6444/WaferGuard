"""Stateful synthetic time-series runtime for Photo, Deposition, and CMP.

Generic process telemetry is continuous rather than independent random rows:
process phases, AR continuity, configured tag relationships, equipment bias,
persistent faults, and short rolling-window behavior are part of the feature
contract used by both training and live inference.

All values and relationships are synthetic proxies, not Fab control limits.
"""
from __future__ import annotations

import math
import uuid
from collections import deque
from typing import Any

import joblib
import numpy as np
from sklearn.covariance import LedoitWolf

from app.services import db
from app.services import process_runtime as legacy

TEMPORAL_GENERATOR_VERSION = "temporal-correlated-window-v2"
TEMPORAL_PROCESSES = {"photo", "deposition", "cmp"}
DEFAULT_WINDOW_SIZE = 8


class RobustZScorer:
    """Normal-only univariate baseline over the temporal feature vector."""

    def fit(self, matrix: np.ndarray):
        x = np.asarray(matrix, dtype=float)
        self.center_ = np.median(x, axis=0)
        mad = np.median(np.abs(x - self.center_), axis=0)
        self.scale_ = np.maximum(1.4826 * mad, 1e-6)
        return self

    def decision_function(self, matrix: np.ndarray) -> np.ndarray:
        x = np.asarray(matrix, dtype=float)
        outlier = np.max(np.abs((x - self.center_) / self.scale_), axis=1)
        return -outlier


class MahalanobisScorer:
    """Multivariate covariance baseline using shrinkage for stable inversion."""

    def fit(self, matrix: np.ndarray):
        self.covariance_ = LedoitWolf().fit(np.asarray(matrix, dtype=float))
        return self

    def decision_function(self, matrix: np.ndarray) -> np.ndarray:
        return -np.asarray(self.covariance_.mahalanobis(np.asarray(matrix, dtype=float)), dtype=float)


def _temporal_candidate_models(seed: int) -> dict[str, Any]:
    candidates = {
        "robust_z_univariate": RobustZScorer(),
        "mahalanobis_multivariate": MahalanobisScorer(),
    }
    candidates.update(legacy._candidate_models(seed))
    return candidates


class TemporalProcessGenerator:
    """Generate one continuous synthetic equipment stream for one process."""

    def __init__(self, process_id: str, *, seed: int = 42, window_size: int = DEFAULT_WINDOW_SIZE):
        config, profile = legacy._profile(process_id)
        if process_id not in TEMPORAL_PROCESSES:
            raise ValueError(f"Temporal generic runtime is not enabled for {process_id}")
        self.process_id = process_id
        self.config = config
        self.profile = profile
        self.tags = list(profile["timeseries"]["tags"])
        self.specs = profile["timeseries"]["tags"]
        self.temporal = profile["timeseries"].get("temporal", {})
        self.rng = np.random.default_rng(seed)
        self.step = 0
        self.ar = float(self.temporal.get("ar", 0.90))
        self.noise_scale = float(self.temporal.get("noise_scale", 0.55))
        self.window_size = max(3, int(window_size))
        self._noise = np.zeros(len(self.tags), dtype=float)
        self._equipment_bias = self.rng.normal(0.0, 0.10, len(self.tags))
        self._prev_z: np.ndarray | None = None
        self._history: deque[np.ndarray] = deque(maxlen=self.window_size - 1)
        self._active_anomaly: str | None = None
        self._anomaly_age = 0
        self._stuck_value: float | None = None
        self._corr = self._correlation_matrix()
        self._pairs = list(self.temporal.get("correlations", []))
        self.feature_names = (
            [f"z:{tag}" for tag in self.tags]
            + [f"delta:{tag}" for tag in self.tags]
            + [f"rel:{pair['a']}:{pair['b']}" for pair in self._pairs]
            + [f"roll_mean_delta:{tag}" for tag in self.tags]
            + [f"roll_std:{tag}" for tag in self.tags]
        )

    @property
    def cycle_length(self) -> int:
        phases = self.temporal.get("phases") or [{"length": 60}]
        return sum(max(1, int(phase.get("length", 1))) for phase in phases)

    def _correlation_matrix(self) -> np.ndarray:
        matrix = np.eye(len(self.tags), dtype=float)
        index = {tag: idx for idx, tag in enumerate(self.tags)}
        for pair in self.temporal.get("correlations", []):
            if pair.get("a") not in index or pair.get("b") not in index:
                continue
            i, j = index[pair["a"]], index[pair["b"]]
            rho = float(np.clip(pair.get("rho", 0.0), -0.90, 0.90))
            matrix[i, j] = rho
            matrix[j, i] = rho
        values, vectors = np.linalg.eigh(matrix)
        matrix = vectors @ np.diag(np.maximum(values, 1e-6)) @ vectors.T
        scale = np.sqrt(np.diag(matrix))
        return matrix / np.outer(scale, scale)

    def _phase(self) -> tuple[str, float, dict[str, float]]:
        phases = self.temporal.get("phases") or [{"name": "steady", "length": 60, "offsets": {}}]
        lengths = [max(1, int(phase.get("length", 1))) for phase in phases]
        position = self.step % sum(lengths)
        cursor = 0
        current_index = 0
        for idx, length in enumerate(lengths):
            if position < cursor + length:
                current_index = idx
                break
            cursor += length
        current = phases[current_index]
        previous = phases[(current_index - 1) % len(phases)]
        local = position - cursor
        progress = local / max(1, lengths[current_index] - 1)
        smooth = progress * progress * (3.0 - 2.0 * progress)
        current_offsets = current.get("offsets", {})
        previous_offsets = previous.get("offsets", {})
        offsets = {
            tag: float(previous_offsets.get(tag, 0.0))
            + (float(current_offsets.get(tag, 0.0)) - float(previous_offsets.get(tag, 0.0))) * smooth
            for tag in self.tags
        }
        return str(current.get("name", "steady")), float(progress), offsets

    def _apply_anomaly(self, z: np.ndarray, anomaly: str | None) -> tuple[np.ndarray, list[str]]:
        if anomaly != self._active_anomaly:
            self._active_anomaly = anomaly
            self._anomaly_age = 0
            self._stuck_value = None
        if not anomaly:
            return z, []

        rule = self.profile["timeseries"]["anomalies"].get(anomaly)
        if rule is None:
            raise KeyError(f"Unknown timeseries anomaly: {anomaly}")
        self._anomaly_age += 1
        kind = str(rule.get("kind", "change_point"))
        related = list(rule.get("tags") or ([rule["tag"]] if rule.get("tag") else []))
        index = {tag: idx for idx, tag in enumerate(self.tags)}
        target = rule.get("tag") or (related[-1] if related else None)
        if target not in index:
            return z, related
        idx = index[target]

        if kind == "drift":
            cap = float(rule.get("shift_sigma", 6.0))
            per_step = float(rule.get("drift_per_step", math.copysign(0.4, cap or 1.0)))
            shift = math.copysign(min(abs(cap), abs(per_step) * self._anomaly_age), cap or per_step)
            z[idx] += shift
        elif kind in {"change_point", "spike"}:
            z[idx] += float(rule.get("shift_sigma", 7.0))
        elif kind == "stuck":
            if self._stuck_value is None:
                self._stuck_value = float(self._prev_z[idx]) if self._prev_z is not None else float(z[idx])
            z[idx] = self._stuck_value
        elif kind == "oscillation":
            amplitude = float(rule.get("amplitude_sigma", 5.0))
            frequency = float(rule.get("frequency", 0.8))
            z[idx] += amplitude * math.sin(self._anomaly_age * frequency)
        elif kind == "noise_increase":
            z[idx] += float(self.rng.normal(0.0, float(rule.get("noise_sigma", 4.0))))
        elif kind == "correlation_break":
            z[idx] += float(self.rng.normal(0.0, float(rule.get("break_sigma", 4.5))))
        else:
            z[idx] += float(rule.get("shift_sigma", 6.0))
        return z, related

    def next(self, anomaly: str | None = None) -> tuple[np.ndarray, dict[str, float], list[str], dict[str, Any]]:
        phase_name, phase_progress, phase_offsets = self._phase()
        innovation = self.rng.multivariate_normal(np.zeros(len(self.tags)), self._corr)
        residual_scale = math.sqrt(max(1e-6, 1.0 - self.ar * self.ar))
        self._noise = self.ar * self._noise + residual_scale * innovation
        z = np.asarray([phase_offsets[tag] for tag in self.tags], dtype=float)
        z += self.noise_scale * self._noise + self._equipment_bias
        z, related = self._apply_anomaly(z, anomaly)

        previous = self._prev_z if self._prev_z is not None else z.copy()
        delta = z - previous
        index = {tag: idx for idx, tag in enumerate(self.tags)}
        relationship_errors = np.asarray([
            float(z[index[pair["b"]]] - float(pair.get("rho", 0.0)) * z[index[pair["a"]]])
            for pair in self._pairs
        ], dtype=float)
        window_rows = [*self._history, z]
        window = np.vstack(window_rows)
        rolling_mean_delta = z - np.mean(window, axis=0)
        rolling_std = np.std(window, axis=0)
        vector = np.concatenate([z, delta, relationship_errors, rolling_mean_delta, rolling_std])
        payload = {
            tag: float(self.specs[tag]["mean"] + self.specs[tag]["std"] * z[idx])
            for idx, tag in enumerate(self.tags)
        }
        self._history.append(z.copy())
        self._prev_z = z.copy()
        self.step += 1
        return vector, payload, related, {
            "phase": phase_name,
            "phase_progress": phase_progress,
            "generator_version": TEMPORAL_GENERATOR_VERSION,
            "window_size": self.window_size,
        }


def _sequence(generator: TemporalProcessGenerator, count: int, anomaly: str | None = None) -> list[np.ndarray]:
    return [generator.next(anomaly)[0] for _ in range(max(0, count))]


def evaluate_temporal_candidates(process_id: str, *, seed: int | None = None) -> dict[str, Any]:
    config, profile = legacy._profile(process_id)
    runtime = config.get("runtime", {})
    resolved_seed = int(seed if seed is not None else runtime.get("seed", 42))
    beta = float(runtime.get("fbeta", 2.0))
    train_n = int(runtime.get("bootstrap_normal_samples", 240))
    normal_n = int(runtime.get("validation_normal_samples", 120))
    anomaly_n = int(runtime.get("validation_anomaly_samples", 100))
    warmup = 24

    train_gen = TemporalProcessGenerator(process_id, seed=resolved_seed)
    _sequence(train_gen, warmup)
    train_x = np.vstack(_sequence(train_gen, train_n))

    normal_gen = TemporalProcessGenerator(process_id, seed=resolved_seed + 1000)
    _sequence(normal_gen, warmup)
    validation: list[np.ndarray] = _sequence(normal_gen, normal_n)
    labels = [0] * normal_n

    anomaly_names = list(profile["timeseries"]["anomalies"])
    per_anomaly = max(1, math.ceil(anomaly_n / max(1, len(anomaly_names))))
    for idx, name in enumerate(anomaly_names):
        anomaly_gen = TemporalProcessGenerator(process_id, seed=resolved_seed + 2000 + idx)
        _sequence(anomaly_gen, warmup)
        rows = _sequence(anomaly_gen, per_anomaly, name)
        validation.extend(rows)
        labels.extend([1] * len(rows))
        if len(labels) >= normal_n + anomaly_n:
            break
    validation = validation[: normal_n + anomaly_n]
    labels = labels[: normal_n + anomaly_n]
    val_x = np.vstack(validation)
    label_array = np.asarray(labels, dtype=int)

    candidates = []
    for name, model in _temporal_candidate_models(resolved_seed).items():
        model.fit(train_x)
        threshold, metrics = legacy._best_threshold(legacy._scores(model, val_x), label_array, beta)
        candidates.append({
            "name": name,
            "model": model,
            "threshold": threshold,
            **metrics,
            "false_positive_per_hour_at_1hz": float(metrics["false_positive_rate"] * 3600.0),
        })
    candidates.sort(key=lambda item: (item["f2"], -item["false_positive_rate"], item["precision"]), reverse=True)
    return {
        "winner": candidates[0],
        "candidates": [{key: value for key, value in item.items() if key != "model"} for item in candidates],
        "feature_names": train_gen.feature_names,
        "seed": resolved_seed,
        "train_rows": len(train_x),
        "validation_rows": len(val_x),
        "generator_version": TEMPORAL_GENERATOR_VERSION,
        "window_size": train_gen.window_size,
    }


def train_temporal_model(process_id: str, *, stage: str = "Staging", seed: int | None = None) -> dict[str, Any]:
    if stage not in {"Production", "Staging"}:
        raise ValueError("stage must be Production or Staging")
    legacy._ensure_schema()
    result = evaluate_temporal_candidates(process_id, seed=seed)
    winner = result["winner"]
    version = legacy._next_version(process_id, "timeseries")
    path = legacy._artifact_path(process_id, "timeseries", version)
    bundle = {
        "process_id": process_id,
        "modality": "timeseries",
        "version": version,
        "model_name": winner["name"],
        "model": winner["model"],
        "threshold": winner["threshold"],
        "feature_names": result["feature_names"],
        "candidate_metrics": result["candidates"],
        "generator_version": TEMPORAL_GENERATOR_VERSION,
        "window_size": result["window_size"],
    }
    joblib.dump(bundle, path)
    if stage == "Production":
        with db.connect() as conn:
            conn.execute(
                "UPDATE process_model_registry SET stage='Archived' WHERE process_id=? AND modality='timeseries' AND stage='Production'",
                (process_id,),
            )
    metadata = {
        "candidate_metrics": result["candidates"],
        "feature_names": result["feature_names"],
        "train_rows": result["train_rows"],
        "validation_rows": result["validation_rows"],
        "data_source": "synthetic_temporal_proxy",
        "generator_version": TEMPORAL_GENERATOR_VERSION,
        "window_size": result["window_size"],
        "note": "Synthetic temporal/correlated validation only; not Fab-calibrated performance.",
    }
    record = {
        "id": str(uuid.uuid4()), "process_id": process_id, "modality": "timeseries",
        "version": version, "stage": stage, "model_name": winner["name"],
        "artifact_path": str(path.relative_to(legacy.ROOT_DIR)),
        "precision": winner["precision"], "recall": winner["recall"], "f2": winner["f2"],
        "false_positive_rate": winner["false_positive_rate"], "threshold": winner["threshold"],
        "metadata_json": legacy._json(metadata), "registered_at": legacy.utc_now(),
    }
    columns = list(record)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO process_model_registry (" + ", ".join(columns) + ") VALUES (" + ", ".join(["?"] * len(columns)) + ")",
            tuple(record[column] for column in columns),
        )
    return {**record, "metadata": metadata}


def train_process_model(process_id: str, modality: str, *, stage: str = "Staging", seed: int | None = None) -> dict[str, Any]:
    if modality == "timeseries" and process_id in TEMPORAL_PROCESSES:
        return train_temporal_model(process_id, stage=stage, seed=seed)
    return legacy.train_model(process_id, modality, stage=stage, seed=seed)


def ensure_temporal_production_model(process_id: str) -> dict[str, Any]:
    production = legacy.production_model(process_id, "timeseries")
    if production and production.get("metadata", {}).get("generator_version") == TEMPORAL_GENERATOR_VERSION:
        return production
    return train_temporal_model(process_id, stage="Production")


def simulate_temporal_sample(
    process_id: str,
    generator: TemporalProcessGenerator | None = None,
    *,
    modality: str = "both",
    anomaly: str | None = None,
    equipment_id: str | None = None,
    lot_id: str = "LOT-MM-DEMO-001",
    wafer_id: str = "W01",
    observed_at: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    if process_id not in TEMPORAL_PROCESSES:
        return legacy.simulate_sample(process_id, modality=modality, anomaly=anomaly, equipment_id=equipment_id, lot_id=lot_id, wafer_id=wafer_id, observed_at=observed_at, seed=seed)
    if modality not in {"timeseries", "vision", "both"}:
        raise ValueError("modality must be timeseries, vision, or both")

    legacy._ensure_schema()
    config, profile = legacy._profile(process_id)
    timestamp = observed_at or legacy.utc_now()
    equipment = equipment_id or f"{profile['equipment_prefix']}-01"
    results: dict[str, Any] = {}

    if modality in {"timeseries", "both"}:
        ts_generator = generator or TemporalProcessGenerator(process_id, seed=int(seed or config.get("runtime", {}).get("seed", 42)))
        applied = anomaly if anomaly in profile["timeseries"]["anomalies"] else None
        if anomaly and applied is None and modality == "timeseries":
            raise KeyError(f"Unknown timeseries anomaly: {anomaly}")
        model = ensure_temporal_production_model(process_id)
        bundle = legacy._load_bundle(model)
        vector, payload, related, temporal_meta = ts_generator.next(applied)
        if list(bundle.get("feature_names", [])) != list(ts_generator.feature_names):
            raise ValueError("Temporal runtime feature contract does not match Production artifact")
        score = float(legacy._scores(bundle["model"], vector.reshape(1, -1))[0])
        threshold = float(bundle["threshold"])
        flag = score >= threshold
        record = {
            "id": f"PMM-{uuid.uuid4().hex}", "process_id": process_id, "process_step": profile["process_step"],
            "modality": "timeseries", "equipment_id": equipment, "recipe_id": profile["recipe_id"],
            "lot_id": lot_id, "wafer_id": wafer_id, "observed_at": timestamp, "model_version": model["version"],
            "anomaly_score": score, "threshold": threshold, "is_anomaly": int(flag), "injected_anomaly": applied,
            "ground_truth": int(bool(applied)), "payload_json": legacy._json(payload), "image_key": None,
            "created_at": legacy.utc_now(),
        }
        legacy._save_sample(record)
        event = legacy._project_event(record, related)
        row = {**record, "is_anomaly": flag, "ground_truth": bool(applied), "payload": payload, "related_tags": related, "event": event, "margin": score - threshold, **temporal_meta}
        row.pop("payload_json", None)
        results["timeseries"] = row

    if modality in {"vision", "both"}:
        vision_anomaly = anomaly if anomaly in profile["vision"]["defects"] else None
        if anomaly and vision_anomaly is None and modality == "vision":
            raise KeyError(f"Unknown vision defect: {anomaly}")
        vision_result = legacy.simulate_sample(process_id, modality="vision", anomaly=vision_anomaly, equipment_id=equipment, lot_id=lot_id, wafer_id=wafer_id, observed_at=timestamp, seed=seed)
        vision_row = vision_result["results"]["vision"]
        vision_row["margin"] = float(vision_row["anomaly_score"] - vision_row["threshold"])
        results["vision"] = vision_row

    return {
        "process_id": process_id, "process_step": profile["process_step"], "equipment_id": equipment,
        "lot_id": lot_id, "wafer_id": wafer_id, "observed_at": timestamp, "results": results,
        "data_source": "synthetic_temporal_multimodal_runtime",
        "disclaimer": "Synthetic/proxy data and metrics; not Fab control limits.",
    }

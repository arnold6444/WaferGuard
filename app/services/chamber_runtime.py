"""Online Chamber telemetry -> inference -> retraining lifecycle orchestration."""
from __future__ import annotations

import statistics
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import chamber_storage, storage
from app.services.chamber_generator import load_chamber_config
from app.services.chamber_data_quality import ChamberDataQualityGate, VALID
from app.services.chamber_training import (
    load_artifact,
    predict_rows,
    resolve_artifact_path,
    resolve_threshold,
    train_model,
)
from app.services.process_ops import project_chamber_anomaly


logger = logging.getLogger(__name__)


class ChamberRuntime:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_chamber_config()
        self._lock = threading.RLock()
        self._bundle: dict[str, Any] | None = None
        self._bundle_version: str | None = None
        self.data_quality = ChamberDataQualityGate(self.config)
        self._ewma_abs_error: dict[str, float] = {}

    def reset_cache(self) -> None:
        with self._lock:
            self._bundle = None
            self._bundle_version = None

    def _production_bundle(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        model = chamber_storage.production_model()
        if model is None:
            self.reset_cache()
            return None, None
        version = str(model["version"])
        if self._bundle is None or self._bundle_version != version:
            bundle = load_artifact(model)
            if str(bundle["version"]) != version:
                raise ValueError(
                    f"Registry/artifact version mismatch: {version} != {bundle['version']}"
                )
            self._bundle = bundle
            self._bundle_version = version
        return model, self._bundle

    def process_sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            quality_result = self.data_quality.validate(sample)
            audited_sample = {
                **sample,
                "data_quality_status": quality_result.status,
                "data_quality_issues": quality_result.issues,
            }
            self._persist_lifecycle_events(sample)
            self._persist_data_quality_events(sample, quality_result.aggregate_events)
            telemetry = chamber_storage.insert_telemetry(audited_sample) if quality_result.persistable else None
            if quality_result.status != VALID:
                return {
                    "state": f"DATA_QUALITY_{quality_result.status}",
                    "reason": "data_quality_gate",
                    "data_quality": {
                        "status": quality_result.status,
                        "issues": quality_result.issues,
                    },
                    "telemetry": telemetry,
                    "prediction": None,
                }
            if telemetry is None:
                return {
                    "state": "DATA_QUALITY_REJECT",
                    "reason": "unpersistable_sample",
                    "data_quality": {"status": quality_result.status, "issues": quality_result.issues},
                    "telemetry": None,
                    "prediction": None,
                }
            if telemetry["machine_state"] != "running" or telemetry["quality"] != "good":
                return {"state": "SKIPPED", "reason": "non_running_or_bad_quality", "telemetry": telemetry, "prediction": None}

            production = chamber_storage.production_model()
            if production is None and bool(self.config["model"].get("auto_train", True)):
                clean_rows = chamber_storage.clean_training_rows()
                bootstrap_min = int(self.config["model"].get("bootstrap_min_rows", 120))
                if len(clean_rows) >= bootstrap_min:
                    version = chamber_storage.next_model_version()
                    trained = train_model(
                        clean_rows,
                        version=version,
                        stage="Production",
                        config=self.config,
                    )
                    chamber_storage.register_model(trained)
                    # The just-created Production artifact must be the model used
                    # for the first inference after warm-up.
                    self.reset_cache()
                    production = chamber_storage.production_model()

            if production is None:
                return {"state": "WARMING_UP", "telemetry": telemetry, "prediction": None}

            production, bundle = self._production_bundle()
            if production is None or bundle is None:
                return {"state": "MODEL_UNAVAILABLE", "telemetry": telemetry, "prediction": None}
            expected = float(predict_rows(bundle, [telemetry])[0])
            actual = float(telemetry["resistance"])
            residual = actual - expected
            threshold, threshold_context = resolve_threshold(bundle, telemetry)
            abs_error = abs(residual)
            prediction = chamber_storage.insert_prediction(
                {
                    "telemetry_id": telemetry["id"],
                    "equipment_id": telemetry["equipment_id"],
                    "observed_at": telemetry["observed_at"],
                    "model_version": production["version"],
                    "actual_resistance": actual,
                    "expected_resistance": expected,
                    "residual": residual,
                    "abs_error": abs_error,
                    "anomaly_score": abs_error / max(threshold, 1e-9),
                    "threshold": threshold,
                    "is_anomaly": abs_error > threshold,
                }
            )
            equipment_id = str(telemetry["equipment_id"])
            alpha = float(self.config["model"].get("ewma_alpha", 0.30))
            previous_ewma = self._ewma_abs_error.get(equipment_id, abs_error)
            ewma_score = alpha * abs_error + (1.0 - alpha) * previous_ewma
            self._ewma_abs_error[equipment_id] = ewma_score
            ewma_threshold = threshold * float(self.config["model"].get("ewma_threshold_multiplier", 0.90))
            detections = [
                chamber_storage.insert_anomaly_detection(
                    {
                        "prediction_id": prediction["id"],
                        "detector_name": "robust_mad",
                        "score": abs_error,
                        "threshold": threshold,
                        "is_anomaly": abs_error > threshold,
                        "context_key": threshold_context,
                        "observed_at": telemetry["observed_at"],
                        "metadata": {"primary": True, "residual": residual},
                    }
                ),
                chamber_storage.insert_anomaly_detection(
                    {
                        "prediction_id": prediction["id"],
                        "detector_name": "ewma_abs_residual",
                        "score": ewma_score,
                        "threshold": ewma_threshold,
                        "is_anomaly": ewma_score > ewma_threshold,
                        "context_key": threshold_context,
                        "observed_at": telemetry["observed_at"],
                        "metadata": {"primary": False, "alpha": alpha},
                    }
                ),
            ]
            try:
                project_chamber_anomaly(telemetry, prediction, detections=detections)
            except Exception:  # noqa: BLE001
                # The normalized event layer must never interrupt authoritative
                # Chamber telemetry/prediction persistence.
                logger.exception("Failed to project Chamber anomaly into process_events")
            return {"state": "LIVE", "telemetry": telemetry, "prediction": prediction, "detections": detections}

    def _persist_data_quality_events(
        self,
        sample: dict[str, Any],
        aggregate_events: list[dict[str, Any]],
    ) -> None:
        for event in aggregate_events:
            observed_at = str(sample.get("observed_at") or chamber_storage.utc_now())
            timestamp_key = "".join(character for character in observed_at if character.isdigit())
            code = str(event.get("code") or "unknown")
            field = str(event.get("field") or "all")
            storage.insert_process_event(
                {
                    "id": f"PROC-DQ-{sample.get('equipment_id') or 'UNKNOWN'}-{code}-{field}-{timestamp_key}",
                    "process_step": "Etch",
                    "equipment_id": sample.get("equipment_id") or "ETCH-UNKNOWN",
                    "recipe_id": sample.get("recipe_id"),
                    "lot_id": sample.get("lot_id"),
                    "wafer_id": sample.get("wafer_id"),
                    "observed_at": observed_at,
                    "event_type": "data_quality",
                    "severity": "critical" if event.get("severity") == "REJECT" else "warning",
                    "source": "chamber_data_quality_gate",
                    "metadata": {
                        "subtype": code,
                        "detail": event.get("detail"),
                        "field": event.get("field"),
                        "consecutive_count": event.get("count"),
                        "interpretation": "Input quality issue; not a process anomaly.",
                    },
                }
            )

    def _persist_lifecycle_events(self, sample: dict[str, Any]) -> None:
        for event in sample.get("lifecycle_events", []):
            event_type = str(event.get("type") or "")
            lot_id = str(event.get("lot_id") or "")
            if not event_type or not lot_id:
                continue
            metadata = {
                "current_wafer": event.get("wafer_id"),
                "completed_wafers": int(event.get("completed_wafers") or 0),
                "equipment_id": event.get("equipment_id"),
                "recipe_id": event.get("recipe_id"),
                "synthetic": True,
            }
            lot_payload: dict[str, Any] = {
                "lot_id": lot_id,
                "product_id": event.get("product_id") or "PRODUCT-DEMO",
                "recipe_route": f"Etch:{event.get('recipe_id') or 'unknown'}",
                "status": "completed" if event_type == "lot_completed" else "running",
                "started_at": event.get("observed_at") if event_type == "lot_started" else None,
                "completed_at": event.get("observed_at") if event_type == "lot_completed" else None,
                "current_process_step": "Inspection" if event_type == "lot_completed" else "Etch",
                "wafer_count": int(event.get("wafer_count") or self.config.get("lot", {}).get("wafer_count", 25)),
                "metadata": metadata,
                "updated_at": event.get("observed_at"),
            }
            storage.upsert_lot(lot_payload)
            if event_type not in {"lot_started", "wafer_completed", "lot_completed"}:
                continue
            timestamp_key = "".join(character for character in str(event.get("observed_at") or "") if character.isdigit())
            storage.insert_process_event(
                {
                    "id": f"PROC-LIFECYCLE-{lot_id}-{event_type}-{event.get('wafer_id') or 'LOT'}-{timestamp_key}",
                    "process_step": "Etch",
                    "equipment_id": event.get("equipment_id") or sample.get("equipment_id") or "ETCH-UNKNOWN",
                    "recipe_id": event.get("recipe_id") or sample.get("recipe_id"),
                    "lot_id": lot_id,
                    "wafer_id": event.get("wafer_id"),
                    "observed_at": event.get("observed_at") or sample.get("observed_at"),
                    "event_type": event_type,
                    "severity": "info",
                    "source": "chamber_lifecycle_simulator",
                    "metadata": {**metadata, "sample_count": event.get("sample_count")},
                }
            )

    def readiness(self) -> dict[str, Any]:
        model_config = self.config["model"]
        production = chamber_storage.production_model()
        min_predictions = int(model_config.get("recent_prediction_count", 30))
        min_new_rows = int(model_config.get("retrain_min_new_rows", 40))
        if production is None:
            clean_count = len(chamber_storage.clean_training_rows())
            bootstrap_min = int(model_config.get("bootstrap_min_rows", 120))
            return {
                "ready": False,
                "reason": "no_production_model",
                "clean_rows": clean_count,
                "required_clean_rows": bootstrap_min,
                "recent_prediction_count": 0,
            }

        new_clean_rows = chamber_storage.clean_training_rows(since=str(production["training_cutoff_at"]))
        recent_errors = chamber_storage.recent_abs_errors(min_predictions)
        median_abs_error = statistics.median(recent_errors) if recent_errors else None
        drift_limit = float(production["mae"]) * float(
            model_config.get("drift_median_abs_error_multiplier", 1.5)
        )
        drift = len(recent_errors) >= min_predictions and median_abs_error is not None and median_abs_error > drift_limit
        anchor_raw = production.get("promoted_at") or production["registered_at"]
        anchor = datetime.fromisoformat(str(anchor_raw).replace("Z", "+00:00"))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        elapsed_hours = (datetime.now(timezone.utc) - anchor).total_seconds() / 3600.0
        interval_due = elapsed_hours >= float(model_config.get("retrain_interval_hours", 24))
        enough_rows = len(new_clean_rows) >= min_new_rows
        ready = enough_rows and (drift or interval_due)
        reason = "ready" if ready else (
            "insufficient_new_clean_rows" if not enough_rows else "no_sustained_drift_or_interval"
        )
        return {
            "ready": ready,
            "reason": reason,
            "new_clean_rows": len(new_clean_rows),
            "required_new_clean_rows": min_new_rows,
            "recent_prediction_count": len(recent_errors),
            "required_prediction_count": min_predictions,
            "median_abs_error": median_abs_error,
            "drift_limit": drift_limit,
            "drift": drift,
            "interval_due": interval_due,
            "elapsed_hours": elapsed_hours,
        }

    def retrain(self, *, force: bool = False, trigger_type: str = "manual") -> dict[str, Any]:
        with self._lock:
            readiness = self.readiness()
            production = chamber_storage.production_model()
            if production is None:
                return {"accepted": False, "reason": "no_production_model", "readiness": readiness}
            if not force and not readiness["ready"]:
                return {"accepted": False, "reason": readiness["reason"], "readiness": readiness}

            rows = chamber_storage.clean_training_rows()
            version = chamber_storage.next_model_version()
            trained = train_model(
                rows,
                version=version,
                stage="Staging",
                config=self.config,
                production=production,
            )
            trained["metadata"]["trigger_type"] = trigger_type
            trained["metadata"]["readiness_at_trigger"] = readiness
            model = chamber_storage.register_model(trained)
            comparison_passed = bool(model["metadata"].get("passes_comparison"))
            return {
                "accepted": True,
                "reason": "candidate_ready" if comparison_passed else "candidate_below_production",
                "comparison_passed": comparison_passed,
                "candidate": model,
                "readiness": readiness,
            }

    def maybe_auto_retrain(self) -> dict[str, Any]:
        """Train at most one automatic Staging candidate; promotion stays manual."""
        if not bool(self.config["model"].get("auto_candidate_training", True)):
            return {"accepted": False, "reason": "automatic_candidate_training_disabled"}
        existing = chamber_storage.staging_model()
        if existing is not None:
            return {
                "accepted": False,
                "reason": "staging_candidate_already_exists",
                "candidate": existing,
            }
        readiness = self.readiness()
        if not bool(readiness.get("ready")):
            return {"accepted": False, "reason": readiness.get("reason"), "readiness": readiness}
        result = self.retrain(force=False, trigger_type="automatic")
        if result.get("accepted") and result.get("candidate", {}).get("stage") != "Staging":
            raise RuntimeError("Automatic retraining must only create a Staging candidate")
        return result

    def promote(self, version: str) -> dict[str, Any]:
        with self._lock:
            model = chamber_storage.get_model(version)
            if model is None:
                raise KeyError(f"Unknown Chamber model: {version}")
            artifact = resolve_artifact_path(str(model["artifact_path"]))
            if not artifact.is_file():
                raise FileNotFoundError(f"Cannot promote without artifact: {artifact}")
            bundle = load_artifact(model)
            if str(bundle["version"]) != version:
                raise ValueError(f"Cannot promote mismatched artifact for {version}")
            promoted = chamber_storage.promote_model(version)
            if promoted is None:
                raise KeyError(f"Unknown Chamber model: {version}")
            self.reset_cache()
            # Validate that inference will now load the selected Production model.
            loaded_model, _ = self._production_bundle()
            if loaded_model is None or loaded_model["version"] != version:
                raise RuntimeError(f"Promotion did not activate {version}")
            return promoted

    def status(self) -> dict[str, Any]:
        production = chamber_storage.production_model()
        telemetry_count = chamber_storage.count_rows("chamber_telemetry")
        prediction_count = chamber_storage.count_rows("chamber_predictions")
        clean_count = len(chamber_storage.clean_training_rows())
        bootstrap_min = int(self.config["model"].get("bootstrap_min_rows", 120))
        artifact_ok = False
        model_error: str | None = None
        if production:
            try:
                artifact_ok = Path(resolve_artifact_path(str(production["artifact_path"]))).is_file()
                if artifact_ok:
                    load_artifact(production)
            except (OSError, ValueError) as exc:
                model_error = str(exc)
        return {
            "state": "LIVE" if production and artifact_ok else ("MODEL_ERROR" if production else "WARMING_UP"),
            "telemetry_rows": telemetry_count,
            "prediction_rows": prediction_count,
            "detection_rows": chamber_storage.count_rows("anomaly_detections"),
            "detectors": chamber_storage.detector_summary(),
            "data_quality": chamber_storage.data_quality_summary(),
            "clean_running_rows": clean_count,
            "bootstrap_required_rows": bootstrap_min,
            "bootstrap_progress": min(1.0, clean_count / max(bootstrap_min, 1)),
            "production_model": production,
            "artifact_ok": artifact_ok,
            "model_error": model_error,
            "readiness": self.readiness(),
            "synthetic_data": True,
        }


chamber_runtime = ChamberRuntime()

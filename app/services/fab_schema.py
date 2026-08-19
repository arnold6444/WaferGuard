"""Stable, transport-neutral contracts for the synthetic FAB v2 runtime.

The FAB generator, direct handler, and MQTT handler share these contracts.  The
module deliberately has no database dependency so schema validation can happen
before a message reaches persistence or inference.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


FAB_SCHEMA_VERSION = "fab.v2"
FAB_FEATURE_CONTRACT = "fab-context-v1"
_PRIVATE_KEYS = {
    "fault_id",
    "ground_truth",
    "ground_truth_mask",
    "injected_anomaly",
    "simulation_truth",
    "synthetic_debug",
}


def as_utc(value: str | datetime) -> datetime:
    """Return an aware UTC datetime, rejecting ambiguous naive timestamps."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("FAB timestamps must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def utc_iso(value: str | datetime, *, milliseconds: bool = True) -> str:
    timespec = "milliseconds" if milliseconds else "seconds"
    return as_utc(value).isoformat(timespec=timespec)


def stable_message_id(*parts: object) -> str:
    """Build a replay-stable UUID without depending on process hash randomization."""
    material = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"waferguard://fab/{material}"))


def config_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FabEventIdentity:
    """Identity carried by every FAB state, telemetry, and result message."""

    schema_version: str
    message_id: str
    lot_id: str
    wafer_id: str
    process_run_id: str
    process_id: str
    process_step: str
    equipment_id: str
    unit_id: str
    unit_type: str
    recipe_id: str
    cycle_id: str
    cycle_index: int
    cycle_index_since_maintenance: int
    machine_state: str
    phase: str
    phase_progress: float
    observed_at: str

    def __post_init__(self) -> None:
        required = (
            "schema_version", "message_id", "lot_id", "wafer_id",
            "process_run_id", "process_id", "process_step", "equipment_id",
            "unit_id", "unit_type", "recipe_id", "cycle_id",
            "machine_state", "phase", "observed_at",
        )
        for name in required:
            if not str(getattr(self, name)).strip():
                raise ValueError(f"FAB identity field {name} must not be empty")
        if self.schema_version != FAB_SCHEMA_VERSION:
            raise ValueError(f"FAB identity schema_version must be {FAB_SCHEMA_VERSION}")
        if self.cycle_index < 1:
            raise ValueError("cycle_index must be at least 1")
        if self.cycle_index_since_maintenance < 1:
            raise ValueError("cycle_index_since_maintenance must be at least 1")
        if not 0.0 <= float(self.phase_progress) <= 1.0:
            raise ValueError("phase_progress must be between 0 and 1")
        object.__setattr__(self, "machine_state", str(self.machine_state).upper())
        object.__setattr__(self, "observed_at", utc_iso(self.observed_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "lot_id": self.lot_id,
            "wafer_id": self.wafer_id,
            "process_run_id": self.process_run_id,
            "process_id": self.process_id,
            "process_step": self.process_step,
            "equipment_id": self.equipment_id,
            "unit_id": self.unit_id,
            "unit_type": self.unit_type,
            "recipe_id": self.recipe_id,
            "cycle_id": self.cycle_id,
            "cycle_index": self.cycle_index,
            "cycle_index_since_maintenance": self.cycle_index_since_maintenance,
            "machine_state": self.machine_state,
            "phase": self.phase,
            "phase_progress": float(self.phase_progress),
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FabEventIdentity":
        fields = cls.__dataclass_fields__
        return cls(**{name: value[name] for name in fields})

    def for_message(
        self,
        *,
        message_id: str,
        observed_at: str | datetime,
        machine_state: str | None = None,
        phase: str | None = None,
        phase_progress: float | None = None,
    ) -> "FabEventIdentity":
        return replace(
            self,
            message_id=message_id,
            observed_at=utc_iso(observed_at),
            machine_state=machine_state or self.machine_state,
            phase=phase or self.phase,
            phase_progress=self.phase_progress if phase_progress is None else phase_progress,
        )


@dataclass(frozen=True, slots=True)
class FabEquipmentContext:
    """Public, vendor-neutral equipment and sensor context for one process run."""

    process_id: str
    equipment_id: str
    equipment_display_name: str
    equipment_class: Mapping[str, Any]
    unit_id: str
    unit_display_name: str
    unit_class: str
    recipe_id: str
    recipe_display_name: str
    recipe_class: str
    sensor_tags: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        for name in (
            "process_id", "equipment_id", "equipment_display_name", "unit_id",
            "unit_display_name", "unit_class", "recipe_id", "recipe_display_name",
            "recipe_class",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"FAB equipment context field {name} must not be empty")
        if not self.equipment_class.get("id") or not self.equipment_class.get("display_name"):
            raise ValueError("equipment_class requires id and display_name")
        if not self.sensor_tags:
            raise ValueError("sensor_tags must not be empty")

    @classmethod
    def from_config(
        cls,
        *,
        process_id: str,
        process: Mapping[str, Any],
        equipment: Mapping[str, Any],
        unit: Mapping[str, Any],
        recipe: Mapping[str, Any],
    ) -> "FabEquipmentContext":
        return cls(
            process_id=process_id,
            equipment_id=str(equipment["id"]),
            equipment_display_name=str(equipment["display_name"]),
            equipment_class=copy.deepcopy(dict(process["equipment_class"])),
            unit_id=str(unit["id"]),
            unit_display_name=str(unit["display_name"]),
            unit_class=str(unit["unit_class"]),
            recipe_id=str(recipe["id"]),
            recipe_display_name=str(recipe["display_name"]),
            recipe_class=str(recipe["recipe_class"]),
            sensor_tags=copy.deepcopy(dict(process["sensor_tags"])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "equipment_id": self.equipment_id,
            "equipment_display_name": self.equipment_display_name,
            "equipment_class": copy.deepcopy(dict(self.equipment_class)),
            "unit_id": self.unit_id,
            "unit_display_name": self.unit_display_name,
            "unit_class": self.unit_class,
            "recipe_id": self.recipe_id,
            "recipe_display_name": self.recipe_display_name,
            "recipe_class": self.recipe_class,
            "sensor_tags": copy.deepcopy(dict(self.sensor_tags)),
            "synthetic_proxy": True,
            "disclaimer": "Vendor-neutral synthetic equipment context; not connected to Fab control systems.",
        }


@dataclass(frozen=True, slots=True)
class DetectorResult:
    """Common high-score-is-anomalous result shared by all modalities.

    ``margin`` is the signed distance ``raw_score - threshold``.  This supports
    models whose raw score and threshold are both negative without losing the
    decision direction.
    """

    process_run_id: str
    modality: str
    model_version: str
    raw_score: float
    threshold: float
    margin: float
    is_anomaly: bool
    related_tags: tuple[str, ...]
    observed_at: str

    def __post_init__(self) -> None:
        for name in ("process_run_id", "modality", "model_version"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"Detector result field {name} must not be empty")
        for name in ("raw_score", "threshold", "margin"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"Detector result field {name} must be finite")
        expected_margin = float(self.raw_score) - float(self.threshold)
        if not math.isclose(float(self.margin), expected_margin, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("margin must equal raw_score - threshold")
        if bool(self.is_anomaly) != (expected_margin >= 0.0):
            raise ValueError("is_anomaly must match the signed margin")
        object.__setattr__(self, "observed_at", utc_iso(self.observed_at))
        object.__setattr__(self, "related_tags", tuple(dict.fromkeys(map(str, self.related_tags))))

    @classmethod
    def from_score(
        cls,
        *,
        process_run_id: str,
        modality: str,
        model_version: str,
        raw_score: float,
        threshold: float,
        related_tags: Sequence[str] = (),
        observed_at: str | datetime,
    ) -> "DetectorResult":
        raw = float(raw_score)
        boundary = float(threshold)
        return cls(
            process_run_id=process_run_id,
            modality=modality,
            model_version=model_version,
            raw_score=raw,
            threshold=boundary,
            margin=raw - boundary,
            is_anomaly=raw >= boundary,
            related_tags=tuple(related_tags),
            observed_at=utc_iso(observed_at),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DetectorResult":
        raw = float(value.get("raw_score", value.get("anomaly_score", 0.0)))
        threshold = float(value.get("threshold", 0.0))
        is_anomaly = bool(value.get("is_anomaly", raw >= threshold))
        margin = float(value.get("margin", raw - threshold))
        return cls(
            process_run_id=str(value["process_run_id"]),
            modality=str(value["modality"]),
            model_version=str(value["model_version"]),
            raw_score=raw,
            threshold=threshold,
            margin=margin,
            is_anomaly=is_anomaly,
            related_tags=tuple(value.get("related_tags") or ()),
            observed_at=value["observed_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_run_id": self.process_run_id,
            "modality": self.modality,
            "model_version": self.model_version,
            "raw_score": float(self.raw_score),
            "threshold": float(self.threshold),
            "margin": float(self.margin),
            "is_anomaly": bool(self.is_anomaly),
            "related_tags": list(self.related_tags),
            "observed_at": self.observed_at,
        }


def public_projection(value: Any, *, include_synthetic_debug: bool = False) -> Any:
    """Recursively remove simulator truth before returning an API response."""
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name.startswith("_"):
                continue
            if name in _PRIVATE_KEYS and not (include_synthetic_debug and name == "synthetic_debug"):
                continue
            projected[name] = public_projection(item, include_synthetic_debug=include_synthetic_debug)
        return projected
    if isinstance(value, (list, tuple)):
        return [public_projection(item, include_synthetic_debug=include_synthetic_debug) for item in value]
    return value

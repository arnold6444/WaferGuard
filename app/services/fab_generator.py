"""Deterministic four-process Virtual FAB generator.

Photo, Deposition, and CMP retain ``TemporalProcessGenerator`` behavior. Etch
retains the stateful Chamber generator through a small adapter. FAB identity,
context baselines, maintenance, latent faults, and delayed modalities are added
without changing either legacy runtime.
"""
from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config
from app.services.config import ROOT_DIR
from app.services.fab_metrology import FabInspectionSimulator, FabMetrologySimulator
from app.services.fab_schema import (
    FAB_FEATURE_CONTRACT,
    FAB_SCHEMA_VERSION,
    FabEquipmentContext,
    FabEventIdentity,
    as_utc,
    config_fingerprint,
    stable_message_id,
    utc_iso,
)
from app.services.process_runtime import load_config as load_process_config
from app.services.process_temporal import TEMPORAL_GENERATOR_VERSION, TemporalProcessGenerator


FAB_CONFIG_PATH = ROOT_DIR / "configs" / "fab_simulator.yaml"
FAULT_CATALOG_PATH = ROOT_DIR / "configs" / "fault_catalog.yaml"
FAB_GENERATOR_VERSION = "virtual-fab-v2"
ETCH_ADAPTER_VERSION = "etch-chamber-adapter-v1"
SUPPORTED_PROCESSES = ("photo", "etch", "deposition", "cmp")
ALLOWED_RELATIONS = {"linear", "ratio", "difference", "correlation"}
ALLOWED_EFFECTS = {"linear", "step", "oscillation"}


def _load_yaml(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    with resolved.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be an object: {resolved}")
    return value


def _finite(value: Any, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _tag_specs() -> dict[str, dict[str, Mapping[str, Any]]]:
    profiles = load_process_config()["profiles"]
    specs = {name: dict(profile["timeseries"]["tags"]) for name, profile in profiles.items()}
    specs["etch"].update({
        "esc_temperature": {"mean": 18.0, "std": 0.6},
        "resistance": {"mean": 86.0, "std": 1.2},
    })
    return specs


def validate_fab_config(config: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(config))
    if value.get("schema_version") != "waferguard.fab.v2":
        raise ValueError("FAB simulator config schema_version must be waferguard.fab.v2")
    route = list(value.get("route") or [])
    if route != list(SUPPORTED_PROCESSES):
        raise ValueError(f"FAB route must be {list(SUPPORTED_PROCESSES)}")
    lifecycle = value.get("lifecycle") or {}
    supported_states = {str(item).upper() for item in lifecycle.get("supported_states") or []}
    required_states = {"IDLE", "LOAD", "STARTUP", "RUNNING", "HOLD", "ALARM", "UNLOAD", "CLEANING", "MAINTENANCE", "SHUTDOWN"}
    if not required_states.issubset(supported_states):
        raise ValueError("FAB lifecycle must declare all required machine states")
    configured_states = [
        *list(lifecycle.get("pre_run_states") or []),
        lifecycle.get("running_state"),
        lifecycle.get("post_run_state"),
        lifecycle.get("maintenance_state"),
    ]
    if any(str(state).upper() not in supported_states for state in configured_states):
        raise ValueError("FAB lifecycle sequence references an unsupported state")
    if str(lifecycle.get("running_state")).upper() != "RUNNING":
        raise ValueError("FAB lifecycle running_state must be RUNNING")
    clock = value.get("clock") or {}
    as_utc(clock.get("start_utc"))
    for key in ("sample_interval_seconds", "metrology_lag_seconds", "inspection_lag_seconds"):
        if _finite(clock.get(key), f"clock.{key}") < 0:
            raise ValueError(f"clock.{key} must not be negative")

    specs = _tag_specs()
    processes = value.get("processes") or {}
    for process_id in SUPPORTED_PROCESSES:
        process = processes.get(process_id)
        if not isinstance(process, dict):
            raise ValueError(f"Missing FAB process config: {process_id}")
        if int(process.get("samples_per_run", 0)) < 1:
            raise ValueError(f"{process_id}.samples_per_run must be positive")
        if int(process.get("maintenance_every_cycles", 0)) < 1:
            raise ValueError(f"{process_id}.maintenance_every_cycles must be positive")
        known_tags = set(specs[process_id])
        equipment_class = process.get("equipment_class") or {}
        if not equipment_class.get("id") or not equipment_class.get("display_name"):
            raise ValueError(f"{process_id}.equipment_class requires id and display_name")
        if not str(process.get("unit_class") or ""):
            raise ValueError(f"{process_id}.unit_class must not be empty")
        sensor_tags = process.get("sensor_tags") or {}
        if set(sensor_tags) != known_tags:
            raise ValueError(
                f"{process_id}.sensor_tags must describe exactly {sorted(known_tags)}"
            )
        for tag, definition in sensor_tags.items():
            for field in ("display_name", "unit", "sensor_type", "role"):
                if not str(definition.get(field) or ""):
                    raise ValueError(f"{process_id}.sensor_tags.{tag}.{field} must not be empty")
        equipment = process.get("equipment") or []
        if not equipment:
            raise ValueError(f"{process_id}.equipment must not be empty")
        composite_units: set[tuple[str, str]] = set()
        for tool in equipment:
            equipment_id = str(tool.get("id") or "")
            if not equipment_id or not tool.get("units"):
                raise ValueError(f"{process_id} equipment requires id and units")
            if tool.get("equipment_class_id") != equipment_class["id"] or not tool.get("display_name"):
                raise ValueError(f"{process_id} equipment must expose its class and display_name")
            unknown = set(tool.get("bias") or {}) - known_tags
            if unknown:
                raise ValueError(f"Unknown {process_id} equipment bias tags: {sorted(unknown)}")
            for unit in tool["units"]:
                unit_id = str(unit.get("id") or "")
                key = (equipment_id, unit_id)
                if not unit_id or key in composite_units:
                    raise ValueError(f"Duplicate or empty FAB unit key: {key}")
                if unit.get("unit_class") != process["unit_class"] or not unit.get("display_name"):
                    raise ValueError(f"{process_id} unit must expose its class and display_name")
                composite_units.add(key)
                unknown = set(unit.get("bias") or {}) - known_tags
                if unknown:
                    raise ValueError(f"Unknown {process_id} unit bias tags: {sorted(unknown)}")
        recipes = process.get("recipes") or []
        if not recipes or len({str(item.get("id")) for item in recipes}) != len(recipes):
            raise ValueError(f"{process_id}.recipes must contain unique recipes")
        for recipe in recipes:
            if not recipe.get("recipe_class") or not recipe.get("display_name"):
                raise ValueError(f"{process_id} recipes must expose recipe_class and display_name")
            unknown = set(recipe.get("offsets") or {}) - known_tags
            if unknown:
                raise ValueError(f"Unknown {process_id} recipe offset tags: {sorted(unknown)}")
        if not process.get("phases"):
            raise ValueError(f"{process_id}.phases must not be empty")
        for relation in process.get("relations") or []:
            if relation.get("kind") not in ALLOWED_RELATIONS:
                raise ValueError(f"Unsupported relation kind: {relation.get('kind')}")
            if relation.get("a") not in known_tags or relation.get("b") not in known_tags:
                raise ValueError(f"Relation references an unknown {process_id} tag")
            _finite(relation.get("coefficient"), "relation.coefficient")
        for metric, metric_spec in (process.get("metrology") or {}).items():
            for field in (
                "display_name", "unit", "modality", "instrument_class", "method", "sampling_level",
            ):
                if not str(metric_spec.get(field) or ""):
                    raise ValueError(f"{process_id}.metrology.{metric}.{field} must not be empty")
            _finite(metric_spec.get("target"), f"{metric}.target")
            if _finite(metric_spec.get("tolerance"), f"{metric}.tolerance") <= 0:
                raise ValueError(f"{metric}.tolerance must be positive")
            unknown = set(metric_spec.get("drivers") or {}) - known_tags
            if unknown:
                raise ValueError(f"Unknown {process_id} metrology driver tags: {sorted(unknown)}")
        inspection = process.get("inspection") or {}
        for field in ("display_name", "inspection_modality", "instrument_class", "image_type", "sampling_level"):
            if not str(inspection.get(field) or ""):
                raise ValueError(f"{process_id}.inspection.{field} must not be empty")
    return value


def load_fab_config(path: str | Path | None = None) -> dict[str, Any]:
    return validate_fab_config(_load_yaml(path or FAB_CONFIG_PATH))


def validate_fault_catalog(
    catalog: Mapping[str, Any],
    fab_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = copy.deepcopy(dict(catalog))
    if value.get("schema_version") != "waferguard.fault-catalog.v1":
        raise ValueError("Fault catalog schema_version must be waferguard.fault-catalog.v1")
    config = fab_config or load_fab_config()
    specs = _tag_specs()
    faults = value.get("faults") or {}
    if set(faults) != {
        "photo_focus_drift", "etch_chamber_contamination",
        "deposition_precursor_instability", "cmp_slurry_degradation",
    }:
        raise ValueError("Fault catalog must define the four FAB v2 latent faults")
    for fault_id, fault in faults.items():
        process_id = str(fault.get("process_id") or "")
        if process_id not in config["processes"]:
            raise ValueError(f"Unknown process for fault {fault_id}: {process_id}")
        for tag, effect in (fault.get("sensor_effects") or {}).items():
            if tag not in specs[process_id]:
                raise ValueError(f"Unknown sensor effect tag for {fault_id}: {tag}")
            if effect.get("kind") not in ALLOWED_EFFECTS:
                raise ValueError(f"Unsupported sensor effect kind: {effect.get('kind')}")
            _finite(effect.get("coefficient"), f"{fault_id}.{tag}.coefficient")
            if "frequency" in effect:
                _finite(effect["frequency"], f"{fault_id}.{tag}.frequency")
        metrics = set(config["processes"][process_id]["metrology"])
        if set(fault.get("quality_effects") or {}) - metrics:
            raise ValueError(f"Fault {fault_id} references an unknown metrology metric")
        vision = fault.get("vision") or {}
        low = _finite(vision.get("base_probability"), f"{fault_id}.vision.base_probability")
        high = _finite(vision.get("max_probability"), f"{fault_id}.vision.max_probability")
        if not 0 <= low <= high <= 1:
            raise ValueError(f"Fault {fault_id} vision probabilities must satisfy 0 <= base <= max <= 1")
    return value


def load_fault_catalog(path: str | Path | None = None) -> dict[str, Any]:
    return validate_fault_catalog(_load_yaml(path or FAULT_CATALOG_PATH))


@dataclass(slots=True)
class _UnitState:
    total_cycles: int = 0
    cycles_since_maintenance: int = 0


class VirtualFabGenerator:
    """Generate deterministic wafer process runs for the four-process route."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        fault_catalog: Mapping[str, Any] | None = None,
        seed: int = 42,
        simulation_id: str | None = None,
    ) -> None:
        self.config = validate_fab_config(config) if config is not None else load_fab_config()
        self.fault_catalog = validate_fault_catalog(
            fault_catalog if fault_catalog is not None else load_fault_catalog(), self.config
        )
        self.seed = int(seed)
        self.simulation_id = simulation_id or f"SIM-{self.seed:08d}"
        self.config_fingerprint = config_fingerprint(self.config)
        self.feature_contract = FAB_FEATURE_CONTRACT
        self._clock = as_utc(self.config["clock"]["start_utc"])
        self._unit_states: dict[tuple[str, str, str], _UnitState] = {}
        self._temporal: dict[tuple[str, str, str], TemporalProcessGenerator] = {}
        self._metrology = FabMetrologySimulator(self.config)
        self._inspection = FabInspectionSimulator(self.config)
        etch_equipment = len(self.config["processes"]["etch"]["equipment"])
        self._etch = EtchTelemetryGenerator(
            load_chamber_config(),
            equipment_count=etch_equipment,
            interval_seconds=float(self.config["clock"]["sample_interval_seconds"]),
            seed=self._derived_seed("etch-adapter"),
            start_time=self._clock,
        )

    def _derived_seed(self, *parts: object) -> int:
        material = "|".join([str(self.seed), self.simulation_id, *map(str, parts)])
        return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big") % (2**32 - 1)

    def _rng(self, *parts: object) -> np.random.Generator:
        return np.random.default_rng(self._derived_seed(*parts))

    def _context(self, lot_id: str, wafer_index: int, process_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        process = self.config["processes"][process_id]
        units = [(tool, unit) for tool in process["equipment"] for unit in tool["units"]]
        offset = self._derived_seed("assignment", lot_id, process_id) % len(units)
        tool, unit = units[(offset + wafer_index - 1) % len(units)]
        recipes = process["recipes"]
        recipe_offset = self._derived_seed("recipe", lot_id, process_id) % len(recipes)
        recipe = recipes[(recipe_offset + wafer_index - 1) % len(recipes)]
        return process, tool, unit | {"recipe": recipe}

    @staticmethod
    def _effect_sigma(effect: Mapping[str, Any], progress: float) -> float:
        coefficient = float(effect.get("coefficient", 0.0))
        kind = str(effect.get("kind"))
        if kind == "linear":
            return coefficient * progress
        if kind == "step":
            return coefficient
        if kind == "oscillation":
            frequency = float(effect.get("frequency", 1.0))
            return coefficient * math.sin(2.0 * math.pi * frequency * progress)
        raise ValueError(f"Unsupported sensor effect kind: {kind}")

    @staticmethod
    def _phase_for_tick(phases: list[str], tick: int, count: int) -> tuple[str, float]:
        scaled = tick * len(phases) / max(count, 1)
        phase_index = min(len(phases) - 1, int(scaled))
        local_start = phase_index * count / len(phases)
        local_end = (phase_index + 1) * count / len(phases)
        progress = (tick - local_start) / max(local_end - local_start - 1.0, 1.0)
        return phases[phase_index], min(1.0, max(0.0, float(progress)))

    def _temporal_generator(self, process_id: str, equipment_id: str, unit_id: str) -> TemporalProcessGenerator:
        key = (process_id, equipment_id, unit_id)
        if key not in self._temporal:
            self._temporal[key] = TemporalProcessGenerator(
                process_id, seed=self._derived_seed("temporal", *key)
            )
        return self._temporal[key]

    def _base_identity(
        self,
        *,
        lot_id: str,
        wafer_id: str,
        process_id: str,
        process: Mapping[str, Any],
        equipment_id: str,
        unit_id: str,
        recipe_id: str,
        cycle_index: int,
        cycle_since: int,
        run_id: str,
        observed_at: datetime,
    ) -> FabEventIdentity:
        return FabEventIdentity(
            schema_version=FAB_SCHEMA_VERSION,
            message_id=stable_message_id(run_id, "run"),
            lot_id=lot_id,
            wafer_id=wafer_id,
            process_run_id=run_id,
            process_id=process_id,
            process_step=str(process["process_step"]),
            equipment_id=equipment_id,
            unit_id=unit_id,
            unit_type=str(process["unit_type"]),
            recipe_id=recipe_id,
            cycle_id=f"{equipment_id}-{unit_id}-C{cycle_index:06d}",
            cycle_index=cycle_index,
            cycle_index_since_maintenance=cycle_since,
            machine_state="RUNNING",
            phase=str(process["phases"][0]),
            phase_progress=0.0,
            observed_at=utc_iso(observed_at),
        )

    def _state_event(
        self,
        base: FabEventIdentity,
        *,
        index: int,
        state: str,
        phase: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        identity = base.for_message(
            message_id=stable_message_id(base.process_run_id, "state", index, state),
            observed_at=observed_at,
            machine_state=state,
            phase=phase,
            phase_progress=0.0 if state != "COMPLETE" else 1.0,
        )
        return {"identity": identity.to_dict(), "event_type": f"process_{state.lower()}"}

    def generate_process_run(
        self,
        lot_id: str,
        wafer_index: int,
        process_id: str,
        fault_id: str | None = None,
    ) -> dict[str, Any]:
        process_id = str(process_id).lower()
        if process_id not in SUPPORTED_PROCESSES:
            raise KeyError(f"Unknown FAB process: {process_id}")
        if int(wafer_index) < 1:
            raise ValueError("wafer_index must be at least 1")
        if not str(lot_id).strip():
            raise ValueError("lot_id must not be empty")
        fault = None
        if fault_id:
            fault = self.fault_catalog["faults"].get(fault_id)
            if fault is None:
                raise KeyError(f"Unknown FAB fault: {fault_id}")
            if fault["process_id"] != process_id:
                raise ValueError(f"Fault {fault_id} belongs to {fault['process_id']}, not {process_id}")

        process, tool, unit_context = self._context(str(lot_id), int(wafer_index), process_id)
        unit = {key: value for key, value in unit_context.items() if key != "recipe"}
        recipe = unit_context["recipe"]
        equipment_id, unit_id, recipe_id = str(tool["id"]), str(unit["id"]), str(recipe["id"])
        state_key = (process_id, equipment_id, unit_id)
        unit_state = self._unit_states.setdefault(state_key, _UnitState())
        maintenance = unit_state.cycles_since_maintenance >= int(process["maintenance_every_cycles"])
        if maintenance:
            unit_state.cycles_since_maintenance = 0
        unit_state.total_cycles += 1
        unit_state.cycles_since_maintenance += 1
        cycle_index, cycle_since = unit_state.total_cycles, unit_state.cycles_since_maintenance
        wafer_id = f"{lot_id}-W{int(wafer_index):02d}"
        run_id = "PR-" + stable_message_id(
            self.simulation_id, lot_id, wafer_id, process_id, equipment_id, unit_id, cycle_index
        ).replace("-", "")[:24]
        run_start = self._clock
        base = self._base_identity(
            lot_id=str(lot_id), wafer_id=wafer_id, process_id=process_id, process=process,
            equipment_id=equipment_id, unit_id=unit_id, recipe_id=recipe_id,
            cycle_index=cycle_index, cycle_since=cycle_since, run_id=run_id, observed_at=run_start,
        )
        equipment_context = FabEquipmentContext.from_config(
            process_id=process_id,
            process=process,
            equipment=tool,
            unit=unit,
            recipe=recipe,
        ).to_dict()
        interval = float(self.config["clock"]["sample_interval_seconds"])
        state_events: list[dict[str, Any]] = []
        state_index = 0
        lifecycle = self.config["lifecycle"]
        if maintenance:
            state_events.append(self._state_event(
                base, index=state_index, state=str(lifecycle["maintenance_state"]),
                phase="maintenance", observed_at=run_start
            ))
            state_index += 1
        for offset, machine_state in enumerate(lifecycle["pre_run_states"]):
            state_events.append(self._state_event(
                base, index=state_index + offset, state=str(machine_state),
                phase=str(machine_state).lower(), observed_at=run_start,
            ))
        state_index += len(lifecycle["pre_run_states"])
        state_events.append(self._state_event(
            base, index=state_index, state=str(lifecycle["running_state"]),
            phase=str(process["phases"][0]), observed_at=run_start,
        ))

        count = int(process["samples_per_run"])
        specs = _tag_specs()[process_id]
        run_rng = self._rng("run", run_id)
        lot_rng = self._rng("lot", lot_id, process_id)
        wafer_rng = self._rng("wafer", lot_id, wafer_id, process_id)
        lot_sigma = float(self.config.get("variation", {}).get("lot_sigma", 0.0))
        wafer_sigma = float(self.config.get("variation", {}).get("wafer_sigma", 0.0))
        lot_z = {tag: float(lot_rng.normal(0.0, lot_sigma)) for tag in specs}
        wafer_z = {tag: float(wafer_rng.normal(0.0, wafer_sigma)) for tag in specs}
        telemetry: list[dict[str, Any]] = []
        for tick in range(count):
            timestamp = run_start + timedelta(seconds=tick * interval)
            if process_id == "etch":
                row = self._etch_row(
                    base, tick, count, timestamp, process, tool, unit, recipe, specs,
                    lot_z, wafer_z, fault,
                )
            else:
                row = self._temporal_row(
                    base, tick, count, timestamp, process, tool, unit, recipe, specs,
                    lot_z, wafer_z, fault,
                )
            telemetry.append(row)

        completed_at = run_start + timedelta(seconds=count * interval)
        state_events.append(self._state_event(
            base, index=state_index + 1, state=str(lifecycle["post_run_state"]),
            phase=str(lifecycle["post_run_state"]).lower(), observed_at=completed_at
        ))
        metrology_at = completed_at + timedelta(seconds=float(self.config["clock"]["metrology_lag_seconds"]))
        inspection_at = completed_at + timedelta(seconds=float(self.config["clock"]["inspection_lag_seconds"]))
        quality_effects = dict(fault.get("quality_effects") or {}) if fault else {}
        metrology = self._metrology.generate(
            base, telemetry, rng=run_rng, latent_quality_effects=quality_effects,
            available_at=utc_iso(metrology_at),
        )
        vision = dict(fault.get("vision") or {}) if fault else {}
        inspection = self._inspection.generate(
            base, telemetry, rng=run_rng,
            defect_probability=float(vision.get("max_probability", 0.0)),
            defect=vision.get("defect"), available_at=utc_iso(inspection_at),
        )
        tag_means = {
            tag: round(float(np.mean([row["tags"][tag] for row in telemetry])), 6)
            for tag in specs if all(tag in row["tags"] for row in telemetry)
        }
        attention = bool(metrology["detector_result"]["is_anomaly"] or inspection["detector_result"]["is_anomaly"])
        result: dict[str, Any] = {
            "identity": base.to_dict(),
            "equipment_context": equipment_context,
            "state_events": state_events,
            "telemetry": telemetry,
            "outcome": {
                "process_run_id": run_id,
                "completed_at": utc_iso(completed_at),
                "tag_means": tag_means,
                "quality_status": "attention" if attention else "within_proxy_limits",
            },
            "metrology": metrology,
            "inspection": inspection,
            "generator_metadata": {
                "generator_version": FAB_GENERATOR_VERSION,
                "feature_contract": FAB_FEATURE_CONTRACT,
                "config_fingerprint": self.config_fingerprint,
                "simulation_id": self.simulation_id,
                "equipment_class_id": equipment_context["equipment_class"]["id"],
                "unit_class": equipment_context["unit_class"],
                "recipe_class": equipment_context["recipe_class"],
                "synthetic": True,
            },
        }
        if fault_id:
            result["_simulation_truth"] = {
                "simulation_id": self.simulation_id,
                "process_run_id": run_id,
                "fault_id": fault_id,
                "injected_at": utc_iso(run_start),
                "affected_tags": list(fault.get("sensor_effects") or {}),
            }
        self._clock = inspection_at + timedelta(seconds=interval)
        return result

    def _external_offsets(
        self,
        process: Mapping[str, Any],
        tool: Mapping[str, Any],
        unit: Mapping[str, Any],
        recipe: Mapping[str, Any],
        cycle_since: int,
    ) -> dict[str, float]:
        tags = set(tool.get("bias") or {}) | set(unit.get("bias") or {}) | set(recipe.get("offsets") or {}) | set(process.get("degradation") or {})
        return {
            tag: (
                float((tool.get("bias") or {}).get(tag, 0.0))
                + float((unit.get("bias") or {}).get(tag, 0.0))
                + float((recipe.get("offsets") or {}).get(tag, 0.0))
                + float((process.get("degradation") or {}).get(tag, 0.0)) * max(0, cycle_since - 1)
            )
            for tag in tags
        }

    def _context_features(
        self,
        *,
        feature_names: list[str],
        vector: list[float],
        phase: str,
        phases: list[str],
        phase_progress: float,
        tick: int,
        count: int,
        cycle_since: int,
        maintenance_every: int,
    ) -> tuple[list[str], list[float]]:
        names = list(feature_names) + [f"context:phase:{name}" for name in phases] + [
            "context:phase_progress", "context:cycle_position", "context:cycle_since_maintenance",
        ]
        values = list(map(float, vector)) + [1.0 if phase == name else 0.0 for name in phases] + [
            float(phase_progress), tick / max(1, count - 1), cycle_since / max(1, maintenance_every),
        ]
        return names, values

    def _telemetry_row(
        self,
        base: FabEventIdentity,
        *,
        tick: int,
        observed_at: datetime,
        phase: str,
        phase_progress: float,
        tags: Mapping[str, float],
        normalized: Mapping[str, float],
        expected: Mapping[str, float],
        feature_names: list[str],
        feature_vector: list[float],
        related_tags: list[str],
        generator_version: str,
    ) -> dict[str, Any]:
        identity = base.for_message(
            message_id=stable_message_id(base.process_run_id, "telemetry", tick),
            observed_at=observed_at, machine_state="RUNNING", phase=phase,
            phase_progress=phase_progress,
        )
        process = self.config["processes"][base.process_id]
        equipment = next(item for item in process["equipment"] if str(item["id"]) == base.equipment_id)
        unit = next(item for item in equipment["units"] if str(item["id"]) == base.unit_id)
        recipe = next(item for item in process["recipes"] if str(item["id"]) == base.recipe_id)
        equipment_context = FabEquipmentContext.from_config(
            process_id=base.process_id,
            process=process,
            equipment=equipment,
            unit=unit,
            recipe=recipe,
        ).to_dict()
        detector_context = {
            "feature_contract": FAB_FEATURE_CONTRACT,
            "config_fingerprint": self.config_fingerprint,
            "feature_fingerprint": config_fingerprint({
                "feature_contract": FAB_FEATURE_CONTRACT,
                "feature_names": feature_names,
            }),
            "feature_names": feature_names,
            "feature_vector": feature_vector,
            "normalized_values": {key: float(value) for key, value in normalized.items()},
            "expected_baseline": {key: float(value) for key, value in expected.items()},
            "equipment_context": equipment_context,
            "sensor_catalog": equipment_context["sensor_tags"],
            "related_tags": list(dict.fromkeys(related_tags)),
            "generator_version": generator_version,
        }
        return {
            "identity": identity.to_dict(),
            "tags": {key: round(float(value), 8) for key, value in tags.items()},
            "detector_context": detector_context,
            # Temporary flat aliases ease integration with existing direct
            # runtime callers; persistence may keep only detector_context.
            "feature_names": feature_names,
            "feature_vector": feature_vector,
            "related_tags": detector_context["related_tags"],
            "generator_version": generator_version,
        }

    def _temporal_row(
        self,
        base: FabEventIdentity,
        tick: int,
        count: int,
        timestamp: datetime,
        process: Mapping[str, Any],
        tool: Mapping[str, Any],
        unit: Mapping[str, Any],
        recipe: Mapping[str, Any],
        specs: Mapping[str, Mapping[str, Any]],
        lot_z: Mapping[str, float],
        wafer_z: Mapping[str, float],
        fault: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        generator = self._temporal_generator(base.process_id, base.equipment_id, base.unit_id)
        _, _, phase_offsets = generator._phase()
        anomaly = str(fault.get("detector_anomaly")) if fault else None
        raw_vector, payload, related, metadata = generator.next(anomaly)
        progress = tick / max(1, count - 1)
        external = self._external_offsets(process, tool, unit, recipe, base.cycle_index_since_maintenance)
        expected: dict[str, float] = {}
        normalized: dict[str, float] = {}
        effects = (fault or {}).get("sensor_effects") or {}
        for index, (tag, spec) in enumerate(specs.items()):
            sigma = max(abs(float(spec["std"])), 1e-9)
            normal_offset = float(external.get(tag, 0.0))
            random_offset = sigma * (float(lot_z[tag]) + float(wafer_z[tag]))
            fault_offset = sigma * self._effect_sigma(effects[tag], progress) if tag in effects else 0.0
            payload[tag] = float(payload[tag]) + normal_offset + random_offset + fault_offset
            expected[tag] = (
                float(spec["mean"])
                + sigma * float(phase_offsets.get(tag, 0.0))
                + sigma * float(generator._equipment_bias[index])
                + normal_offset
            )
            normalized[tag] = (payload[tag] - expected[tag]) / sigma
        vector = list(map(float, raw_vector))
        for index, tag in enumerate(specs):
            vector[index] = normalized[tag]
        feature_names, feature_vector = self._context_features(
            feature_names=list(generator.feature_names), vector=vector,
            phase=str(metadata["phase"]), phases=list(process["phases"]),
            phase_progress=float(metadata["phase_progress"]), tick=tick, count=count,
            cycle_since=base.cycle_index_since_maintenance,
            maintenance_every=int(process["maintenance_every_cycles"]),
        )
        return self._telemetry_row(
            base, tick=tick, observed_at=timestamp, phase=str(metadata["phase"]),
            phase_progress=float(metadata["phase_progress"]), tags=payload,
            normalized=normalized, expected=expected, feature_names=feature_names,
            feature_vector=feature_vector,
            related_tags=[*related, *effects], generator_version=TEMPORAL_GENERATOR_VERSION,
        )

    def _etch_row(
        self,
        base: FabEventIdentity,
        tick: int,
        count: int,
        timestamp: datetime,
        process: Mapping[str, Any],
        tool: Mapping[str, Any],
        unit: Mapping[str, Any],
        recipe: Mapping[str, Any],
        specs: Mapping[str, Mapping[str, Any]],
        lot_z: Mapping[str, float],
        wafer_z: Mapping[str, float],
        fault: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        state = self._etch.states[base.equipment_id]
        if base.recipe_id in self._etch.recipe_ids:
            state.recipe_index = self._etch.recipe_ids.index(base.recipe_id)
        anomaly = str(fault.get("detector_anomaly")) if fault else None
        sample = self._etch.next_sample(base.equipment_id, anomaly=anomaly, machine_state="running")
        phases = list(process["phases"])
        phase, phase_progress = self._phase_for_tick(phases, tick, count)
        progress = tick / max(1, count - 1)
        external = self._external_offsets(process, tool, unit, recipe, base.cycle_index_since_maintenance)
        effects = (fault or {}).get("sensor_effects") or {}
        setpoint_keys = {
            "chamber_pressure": "chamber_pressure_setpoint",
            "source_rf_power": "source_rf_setpoint",
            "bias_rf_power": "bias_rf_setpoint",
            "total_gas_flow": "total_gas_setpoint",
            "chamber_temperature": "chamber_temperature_setpoint",
            "esc_temperature": "esc_temperature_setpoint",
            "resistance": None,
        }
        tags: dict[str, float] = {}
        expected: dict[str, float] = {}
        normalized: dict[str, float] = {}
        for tag, spec in specs.items():
            sigma = max(abs(float(spec["std"])), 1e-9)
            normal_offset = float(external.get(tag, 0.0))
            random_offset = sigma * (float(lot_z[tag]) + float(wafer_z[tag]))
            fault_offset = sigma * self._effect_sigma(effects[tag], progress) if tag in effects else 0.0
            value = float(sample[tag]) + normal_offset + random_offset + fault_offset
            setpoint_key = setpoint_keys.get(tag)
            baseline = float(sample[setpoint_key]) if setpoint_key else float(spec["mean"])
            baseline += normal_offset
            tags[tag], expected[tag] = value, baseline
            normalized[tag] = (value - baseline) / sigma
        # Preserve useful Chamber evidence without copying its synthetic label.
        for tag in ("gas_ratio", "gas_flow_delta", "pressure_delta", "temperature_delta"):
            if tag in sample:
                tags[tag] = float(sample[tag])
        base_names = [f"z:{tag}" for tag in specs]
        feature_names, feature_vector = self._context_features(
            feature_names=base_names, vector=[normalized[tag] for tag in specs],
            phase=phase, phases=phases, phase_progress=phase_progress, tick=tick, count=count,
            cycle_since=base.cycle_index_since_maintenance,
            maintenance_every=int(process["maintenance_every_cycles"]),
        )
        return self._telemetry_row(
            base, tick=tick, observed_at=timestamp, phase=phase,
            phase_progress=phase_progress, tags=tags, normalized=normalized,
            expected=expected, feature_names=feature_names, feature_vector=feature_vector,
            related_tags=list(effects), generator_version=ETCH_ADAPTER_VERSION,
        )

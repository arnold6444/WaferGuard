"""Stateful synthetic Etch telemetry for the Chamber Resistance demo.

The relationships in this module are intentionally synthetic. They exercise a
multivariate prediction and MLOps pipeline; they are not Fab-calibrated process
limits or claims about the physical definition of ``RESISTANCE``.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.config import ROOT_DIR


SUPPORTED_ANOMALIES = {
    "resistance_spike",
    "resistance_drift",
    "pressure_drift",
    "rf_power_drift",
    "gas_flow_drift",
    "temperature_drift",
    "stuck_sensor",
    "step_change",
}
SUPPORTED_MACHINE_STATES = {"idle", "running", "cleaning", "maintenance", "alarm"}


def load_chamber_config(path: str | Path | None = None) -> dict[str, Any]:
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency error is explicit
        raise RuntimeError("PyYAML is required to load configs/chamber.yaml") from exc

    config_path = Path(path) if path else ROOT_DIR / "configs" / "chamber.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict) or not config.get("recipes"):
        raise ValueError(f"Invalid Chamber config: {config_path}")
    return config


@dataclass
class EquipmentState:
    equipment_id: str
    recipe_index: int
    use_time_total: float
    use_time_since_clean: float = 0.0
    wafer_count_since_clean: int = 0
    seasoning_level: float = 0.0
    sample_index: int = 0
    cleaning_remaining: int = 0
    previous_values: dict[str, float] = field(default_factory=dict)
    drifts: dict[str, float] = field(default_factory=dict)
    equipment_bias: dict[str, float] = field(default_factory=dict)
    resistance_noise: float = 0.0
    active_anomaly: str | None = None
    anomaly_age: int = 0
    stuck_values: dict[str, float] = field(default_factory=dict)


class EtchTelemetryGenerator:
    """Generate correlated, continuous telemetry while preserving per-tool state."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        config_path: str | Path | None = None,
        equipment_count: int | None = None,
        interval_seconds: float | None = None,
        seed: int | None = None,
        start_time: datetime | None = None,
    ) -> None:
        self.config = config or load_chamber_config(config_path)
        stream = self.config["stream"]
        self.seed = int(stream.get("seed", 42) if seed is None else seed)
        self.random = random.Random(self.seed)
        self.interval_seconds = float(
            stream.get("sample_interval_seconds", 1.0) if interval_seconds is None else interval_seconds
        )
        self.recipe_duration = max(1, int(stream.get("recipe_duration_samples", 40)))
        self.recipe_ids = list(self.config["recipes"])
        count = int(stream.get("equipment_count", 10) if equipment_count is None else equipment_count)
        if count < 1:
            raise ValueError("equipment_count must be at least 1")
        self.current_time = start_time or datetime.now(timezone.utc).replace(microsecond=0)
        if self.current_time.tzinfo is None:
            self.current_time = self.current_time.replace(tzinfo=timezone.utc)
        self.states: dict[str, EquipmentState] = {}
        for index in range(count):
            equipment_id = f"ETCH-{index + 1:03d}"
            equipment_rng = random.Random(self.seed * 1009 + index)
            self.states[equipment_id] = EquipmentState(
                equipment_id=equipment_id,
                recipe_index=index % len(self.recipe_ids),
                # Keep the initial total-use clock aligned across demo tools.
                # Otherwise USE_TIME alone becomes an accidental equipment ID
                # proxy and defeats the intended multivariate comparison.
                use_time_total=600.0,
                equipment_bias={
                    "pressure": equipment_rng.uniform(-0.25, 0.25),
                    "source_rf": equipment_rng.uniform(-8.0, 8.0),
                    "bias_rf": equipment_rng.uniform(-4.0, 4.0),
                    "temperature": equipment_rng.uniform(-0.7, 0.7),
                    "esc_temperature": equipment_rng.uniform(-0.35, 0.35),
                    "gas": equipment_rng.uniform(-0.8, 0.8),
                    "resistance": equipment_rng.uniform(-2.0, 2.0),
                },
                drifts={key: 0.0 for key in ("pressure", "source_rf", "bias_rf", "temperature", "esc_temperature", "gas_1", "gas_2", "gas_3")},
            )

    @property
    def equipment_ids(self) -> list[str]:
        return list(self.states)

    def restore_from_rows(self, rows: list[dict[str, Any]]) -> None:
        """Resume observable state from each equipment's latest persisted row."""
        latest_time = self.current_time
        for row in rows:
            equipment_id = str(row.get("equipment_id", ""))
            state = self.states.get(equipment_id)
            if state is None:
                continue
            recipe_id = str(row.get("recipe_id", self.recipe_ids[0]))
            if recipe_id in self.recipe_ids:
                state.recipe_index = self.recipe_ids.index(recipe_id)
            state.use_time_total = float(row.get("use_time_total") or state.use_time_total)
            state.use_time_since_clean = float(row.get("use_time_since_clean") or 0.0)
            state.wafer_count_since_clean = int(row.get("wafer_count_since_clean") or 0)
            state.seasoning_level = float(row.get("seasoning_level") or 0.0)
            state.previous_values.update(
                {
                    "pressure": float(row["chamber_pressure"]),
                    "source_rf": float(row["source_rf_power"]),
                    "bias_rf": float(row["bias_rf_power"]),
                    "temperature": float(row["chamber_temperature"]),
                    "esc_temperature": float(row["esc_temperature"]),
                    "gas_1": float(row["gas_1_flow"]),
                    "gas_2": float(row["gas_2_flow"]),
                    "gas_3": float(row["gas_3_flow"]),
                }
            )
            observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            latest_time = max(latest_time, observed + timedelta(seconds=self.interval_seconds))
        self.current_time = latest_time

    def next_sample(
        self,
        equipment_id: str | None = None,
        *,
        anomaly: str | None = None,
        machine_state: str | None = None,
    ) -> dict[str, Any]:
        if anomaly is not None and anomaly not in SUPPORTED_ANOMALIES:
            supported = ", ".join(sorted(SUPPORTED_ANOMALIES))
            raise ValueError(f"Unsupported anomaly '{anomaly}'. Supported: {supported}")
        if machine_state is not None and machine_state not in SUPPORTED_MACHINE_STATES:
            supported = ", ".join(sorted(SUPPORTED_MACHINE_STATES))
            raise ValueError(f"Unsupported machine state '{machine_state}'. Supported: {supported}")

        selected = equipment_id or self.equipment_ids[0]
        if selected not in self.states:
            raise KeyError(f"Unknown equipment: {selected}")
        state = self.states[selected]
        observed_at = self.current_time
        self.current_time += timedelta(seconds=self.interval_seconds)

        if anomaly == state.active_anomaly:
            state.anomaly_age += 1
        else:
            state.active_anomaly = anomaly
            state.anomaly_age = 1 if anomaly else 0
            state.stuck_values = {}

        resolved_state = self._resolve_machine_state(state, machine_state)
        recipe_id = self.recipe_ids[state.recipe_index]
        recipe = self.config["recipes"][recipe_id]
        values = self._process_values(state, recipe, resolved_state, anomaly)

        interval_hours = self.interval_seconds / 3600.0
        if resolved_state != "maintenance":
            state.use_time_total += interval_hours
        if resolved_state == "running":
            state.use_time_since_clean += interval_hours
            state.wafer_count_since_clean += 1
            seasoning_wafers = max(1, int(self.config["maintenance"].get("seasoning_wafers", 12)))
            state.seasoning_level = min(1.8, state.seasoning_level + 1.0 / seasoning_wafers)
        elif resolved_state == "cleaning":
            state.use_time_since_clean = 0.0
            state.wafer_count_since_clean = 0
            state.seasoning_level = 0.0

        resistance = self._resistance(state, recipe, values, resolved_state, anomaly)
        gas_payload = [
            {
                "name": values[f"gas_{index}_name"],
                "setpoint": values[f"gas_{index}_setpoint"],
                "flow": values[f"gas_{index}_flow"],
            }
            for index in (1, 2, 3)
        ]
        state.sample_index += 1
        if state.sample_index % self.recipe_duration == 0:
            state.recipe_index = (state.recipe_index + 1) % len(self.recipe_ids)

        quality = "bad" if resolved_state == "alarm" else "good"
        return {
            "equipment_id": selected,
            "observed_at": observed_at.isoformat(timespec="milliseconds"),
            "recipe_id": recipe_id,
            "machine_state": resolved_state,
            "use_time_total": round(state.use_time_total, 6),
            "use_time_since_clean": round(state.use_time_since_clean, 6),
            "wafer_count_since_clean": state.wafer_count_since_clean,
            "seasoning_level": round(state.seasoning_level, 6),
            **{key: round(value, 6) if isinstance(value, float) else value for key, value in values.items()},
            "gas_json": json.dumps(gas_payload, ensure_ascii=False, separators=(",", ":")),
            "resistance": round(resistance, 6),
            "quality": quality,
            "is_synthetic": True,
            "synthetic_anomaly_type": anomaly,
        }

    def _resolve_machine_state(self, state: EquipmentState, requested: str | None) -> str:
        if requested:
            if requested == "cleaning":
                state.cleaning_remaining = 0
            return requested
        maintenance = self.config["maintenance"]
        if state.cleaning_remaining > 0:
            state.cleaning_remaining -= 1
            return "cleaning"
        if maintenance.get("cleaning_enabled", True) and state.wafer_count_since_clean >= int(maintenance.get("wafers_between_clean", 120)):
            state.cleaning_remaining = max(0, int(maintenance.get("cleaning_samples", 1)) - 1)
            return "cleaning"
        return "running"

    def _smooth_value(
        self,
        state: EquipmentState,
        key: str,
        target: float,
        *,
        noise: float,
        drift_noise: float,
        reversion: float = 0.28,
    ) -> float:
        state.drifts[key] = 0.94 * state.drifts.get(key, 0.0) + self.random.gauss(0.0, drift_noise)
        desired = target + state.drifts[key]
        previous = state.previous_values.get(key, desired)
        value = previous + reversion * (desired - previous) + self.random.gauss(0.0, noise)
        state.previous_values[key] = value
        return value

    def _process_values(
        self,
        state: EquipmentState,
        recipe: dict[str, Any],
        machine_state: str,
        anomaly: str | None,
    ) -> dict[str, Any]:
        running = machine_state == "running"
        age = state.anomaly_age
        anomaly_cfg = self.config.get("anomalies", {})
        pressure_sp = float(recipe["pressure_setpoint"])
        source_sp = float(recipe["source_rf_setpoint"])
        bias_sp = float(recipe["bias_rf_setpoint"])
        chamber_temp_sp = float(recipe["chamber_temperature_setpoint"])
        esc_temp_sp = float(recipe["esc_temperature_setpoint"])

        pressure_target = pressure_sp + state.equipment_bias["pressure"]
        source_target = source_sp + state.equipment_bias["source_rf"]
        bias_target = bias_sp + state.equipment_bias["bias_rf"]
        chamber_temp_target = chamber_temp_sp + state.equipment_bias["temperature"]
        esc_temp_target = esc_temp_sp + state.equipment_bias["esc_temperature"]
        if not running:
            pressure_target = 1.0 if machine_state == "idle" else 4.0
            source_target = 0.0
            bias_target = 0.0
            chamber_temp_target = 35.0 if machine_state == "cleaning" else 28.0
            esc_temp_target = 22.0

        if anomaly == "pressure_drift":
            pressure_target += float(anomaly_cfg.get("pressure_drift_per_sample", 0.16)) * age
        elif anomaly == "rf_power_drift":
            bias_target += float(anomaly_cfg.get("rf_drift_per_sample", 7.5)) * age
        elif anomaly == "temperature_drift":
            chamber_temp_target += float(anomaly_cfg.get("temperature_drift_per_sample", 0.22)) * age
        elif anomaly == "step_change":
            source_target += 90.0
            chamber_temp_target += 3.5

        pressure = self._smooth_value(state, "pressure", pressure_target, noise=0.070, drift_noise=0.014)
        source_rf = self._smooth_value(state, "source_rf", source_target, noise=3.2, drift_noise=0.60)
        bias_rf = self._smooth_value(state, "bias_rf", bias_target, noise=1.5, drift_noise=0.30)
        chamber_temp = self._smooth_value(state, "temperature", chamber_temp_target, noise=0.090, drift_noise=0.020)
        esc_temp = self._smooth_value(state, "esc_temperature", esc_temp_target, noise=0.055, drift_noise=0.012)

        gas_values: dict[str, Any] = {}
        for index, gas in enumerate(recipe["gases"], start=1):
            gas_sp = float(gas["flow_setpoint"])
            gas_target = gas_sp + state.equipment_bias["gas"]
            # Keep the recipe setpoint unchanged: anomaly affects actual flow.
            if anomaly == "gas_flow_drift" and index == 1:
                gas_target += float(anomaly_cfg.get("gas_drift_per_sample", 1.8)) * age
            if not running:
                gas_target = 0.0
            gas_flow = self._smooth_value(state, f"gas_{index}", gas_target, noise=0.32, drift_noise=0.060)
            gas_values.update(
                {
                    f"gas_{index}_name": str(gas["name"]),
                    f"gas_{index}_setpoint": gas_sp,
                    f"gas_{index}_flow": gas_flow,
                }
            )

        if anomaly == "stuck_sensor":
            if not state.stuck_values:
                state.stuck_values = {"chamber_pressure": pressure, "chamber_temperature": chamber_temp}
            pressure = state.stuck_values["chamber_pressure"]
            chamber_temp = state.stuck_values["chamber_temperature"]

        total_flow = sum(gas_values[f"gas_{index}_flow"] for index in (1, 2, 3))
        total_setpoint = sum(gas_values[f"gas_{index}_setpoint"] for index in (1, 2, 3))
        gas_ratio = gas_values["gas_1_flow"] / max(gas_values["gas_2_flow"], 1e-6)
        gas_ratio_setpoint = gas_values["gas_1_setpoint"] / max(gas_values["gas_2_setpoint"], 1e-6)
        return {
            "chamber_pressure": pressure,
            "chamber_pressure_setpoint": pressure_sp,
            "pressure_delta": pressure - pressure_sp,
            "source_rf_power": source_rf,
            "source_rf_setpoint": source_sp,
            "source_rf_delta": source_rf - source_sp,
            "bias_rf_power": bias_rf,
            "bias_rf_setpoint": bias_sp,
            "bias_rf_delta": bias_rf - bias_sp,
            **gas_values,
            "total_gas_flow": total_flow,
            "total_gas_setpoint": total_setpoint,
            "gas_flow_delta": total_flow - total_setpoint,
            "gas_ratio": gas_ratio,
            "gas_ratio_delta": gas_ratio - gas_ratio_setpoint,
            "chamber_temperature": chamber_temp,
            "chamber_temperature_setpoint": chamber_temp_sp,
            "temperature_delta": chamber_temp - chamber_temp_sp,
            "esc_temperature": esc_temp,
            "esc_temperature_setpoint": esc_temp_sp,
            "esc_temperature_delta": esc_temp - esc_temp_sp,
        }

    def _resistance(
        self,
        state: EquipmentState,
        recipe: dict[str, Any],
        values: dict[str, Any],
        machine_state: str,
        anomaly: str | None,
    ) -> float:
        if machine_state != "running":
            base = 72.0 if machine_state == "cleaning" else 78.0
            state.resistance_noise = 0.75 * state.resistance_noise + self.random.gauss(0.0, 0.08)
            return base + state.equipment_bias["resistance"] + state.resistance_noise

        pressure_n = values["pressure_delta"] / 0.35
        source_n = values["source_rf_delta"] / 12.0
        bias_n = values["bias_rf_delta"] / 7.0
        gas_n = values["gas_flow_delta"] / 3.0
        ratio_n = values["gas_ratio_delta"] / 0.08
        temp_n = values["temperature_delta"] / 1.1
        esc_n = values["esc_temperature_delta"] / 0.6
        aging = math.log1p(max(state.wafer_count_since_clean, 0))
        seasoning = state.seasoning_level

        # Synthetic, deliberately nonlinear signal used only to validate the
        # multivariate pipeline. No coefficient below is a measured Fab value.
        expected = (
            86.0
            + state.equipment_bias["resistance"]
            + float(recipe.get("resistance_offset", 0.0))
            + 0.18 * aging
            + 0.55 * (seasoning - 1.0) ** 2
            + 0.34 * pressure_n
            - 0.27 * source_n
            + 0.43 * bias_n
            + 0.22 * gas_n
            + 0.30 * ratio_n
            + 0.31 * temp_n
            - 0.16 * esc_n
            + 0.13 * pressure_n * source_n
            + 0.11 * ratio_n * bias_n
            + 0.08 * temp_n * min(aging, 5.0)
        )
        state.resistance_noise = 0.82 * state.resistance_noise + self.random.gauss(0.0, 0.11)
        direct = 0.0
        anomaly_cfg = self.config.get("anomalies", {})
        if anomaly == "resistance_spike":
            direct = float(anomaly_cfg.get("resistance_spike", 8.0))
        elif anomaly == "resistance_drift":
            direct = float(anomaly_cfg.get("resistance_drift_per_sample", 0.14)) * state.anomaly_age
        return expected + state.resistance_noise + direct

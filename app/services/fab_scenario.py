"""Deterministic synthetic Fab flow composed above Generator and Inspection.

This module is deliberately an orchestrator: neither the Chamber generator nor
the inspection pipeline imports the other. A production adapter can replace
either side while preserving the lot/time correlation contract.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from app.services import chamber_storage, storage
from app.services.chamber_generator import EtchTelemetryGenerator, load_chamber_config
from app.services.chamber_runtime import ChamberRuntime
from app.services.pipeline import run_inspection
from app.services.schemas import InspectRequest


class FabScenarioOrchestrator:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        seed: int = 42,
        equipment_id: str = "ETCH-001",
        inspection_lag_minutes: int = 5,
    ) -> None:
        self.config = copy.deepcopy(config or load_chamber_config())
        self.equipment_id = equipment_id
        self.inspection_lag = timedelta(minutes=max(1, inspection_lag_minutes))
        self.generator = EtchTelemetryGenerator(
            self.config,
            equipment_count=1,
            seed=seed,
        )
        if equipment_id != self.generator.equipment_ids[0]:
            raise ValueError(
                f"Scenario equipment_id must be {self.generator.equipment_ids[0]!r}; got {equipment_id!r}"
            )
        self.runtime = ChamberRuntime(self.config)

    def run(
        self,
        *,
        anomaly_wafers: tuple[int, ...] = (13, 14, 15),
        max_samples: int = 5000,
    ) -> dict[str, Any]:
        target = set(anomaly_wafers)
        if not target or min(target) < 1:
            raise ValueError("anomaly_wafers must contain positive wafer numbers")

        inspections: list[dict[str, Any]] = []
        processed = 0
        completed_targets: set[int] = set()
        scenario_lot_id: str | None = None
        while processed < max_samples and completed_targets != target:
            state = self.generator.states[self.equipment_id]
            active_sequence = state.lot_wafer_completed + 1 if state.lot_id else 0
            anomaly = "rf_power_drift" if active_sequence in target else None
            sample = self.generator.next_sample(self.equipment_id, anomaly=anomaly)
            result = self.runtime.process_sample(sample)
            processed += 1

            for event in sample.get("lifecycle_events", []):
                if event.get("type") != "wafer_completed":
                    continue
                wafer_sequence = int(event.get("completed_wafers") or 0)
                if wafer_sequence not in target:
                    continue
                scenario_lot_id = str(event["lot_id"])
                completed_targets.add(wafer_sequence)
                inspections.append(self._inspect_completed_wafer(event, result))

        if completed_targets != target:
            missing = sorted(target - completed_targets)
            raise RuntimeError(
                f"Scenario did not complete target wafers {missing} within {max_samples} samples"
            )

        process_events = storage.list_process_events(lot_id=scenario_lot_id, limit=1000)
        anomaly_events = [
            event
            for event in process_events
            if event.get("source") == "chamber_synthetic_runtime"
        ]
        return {
            "scenario": "rf_drift_to_edge_degradation",
            "lot_id": scenario_lot_id,
            "equipment_id": self.equipment_id,
            "generated_samples": processed,
            "target_wafers": [f"W{index:02d}" for index in sorted(target)],
            "inspections": inspections,
            "process_events": process_events,
            "anomaly_events": anomaly_events,
            "production_model": chamber_storage.production_model(),
            "boundary": (
                "Synthetic temporal correlation only; the RF drift is not asserted as the inspection root cause."
            ),
        }

    def _inspect_completed_wafer(
        self,
        event: dict[str, Any],
        runtime_result: dict[str, Any],
    ) -> dict[str, Any]:
        sequence = int(event.get("completed_wafers") or 0)
        observed_at = datetime.fromisoformat(str(event["observed_at"]).replace("Z", "+00:00"))
        severity_step = max(0, sequence - 13)
        request = InspectRequest(
            lot_id=str(event["lot_id"]),
            wafer_id=str(event["wafer_id"]),
            line_id="LINE-SYNTHETIC-ETCH",
            equipment_id=str(event["equipment_id"]),
            process_step="Etch",
            recipe_id=str(event["recipe_id"]),
            defect_hint="Edge-Loc",
            cd_nm=35.4 + 0.8 * severity_step,
            overlay_nm=5.7 + 0.7 * severity_step,
            film_thickness_nm=82.0 - 2.5 * severity_step,
            roughness_nm=2.7 + 0.5 * severity_step,
            defect_count=360 + 150 * severity_step,
            yield_proxy=max(0.90, 0.964 - 0.012 * severity_step),
            operator_note=(
                "Synthetic scenario: inspect RF-drift temporal correlation; do not treat it as causal proof."
            ),
            process_timestamp=observed_at,
            inspection_timestamp=observed_at + self.inspection_lag,
            use_llm=False,
        )
        inspection = run_inspection(request)
        inspection["scenario_prediction"] = runtime_result.get("prediction")
        return inspection

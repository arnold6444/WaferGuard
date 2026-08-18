"""Small, transport-neutral orchestrator for the synthetic FAB v2 stream.

The generator owns simulation state.  This module only turns one completed
process run into public FAB envelopes, sends them through the shared handler,
and finalizes persisted multimodal evidence.  Simulator truth is written to
the debug-only repository and is never copied into an envelope.
"""
from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from app.services import fab_storage, storage
from app.services.fab_generator import (
    VirtualFabGenerator,
    load_fab_config,
    load_fault_catalog,
)
from app.services.fab_evaluation import evaluate_process_run
from app.services.fab_mqtt import (
    DirectFabTransport,
    MqttFabPublisher,
    MqttFabSubscriber,
    SCHEMA_VERSION,
)
from app.services.fab_runtime import default_router, finalize_process_run
from app.services.fab_schema import public_projection, stable_message_id


_IDENTITY_FIELDS = {
    "schema_version",
    "message_id",
    "lot_id",
    "wafer_id",
    "process_run_id",
    "process_id",
    "process_step",
    "equipment_id",
    "unit_id",
    "unit_type",
    "recipe_id",
    "cycle_id",
    "cycle_index",
    "cycle_index_since_maintenance",
    "machine_state",
    "phase",
    "phase_progress",
    "observed_at",
}


def _mapping(value: Any, *, name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    raise TypeError(f"{name} must be a mapping or expose to_dict()")


def _public(value: Any) -> Any:
    """Apply the public projection and fail closed for simulator-only keys."""
    return public_projection(value, include_synthetic_debug=False)


def _synthetic_debug_enabled() -> bool:
    return os.environ.get("FAB_SYNTHETIC_DEBUG", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _row_value(row: Mapping[str, Any], key: str, fallback: Any = None) -> Any:
    if key in row:
        return row[key]
    identity = row.get("identity")
    if isinstance(identity, Mapping) and key in identity:
        return identity[key]
    return fallback


def _identity_for_message(
    base: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    message_type: str,
    ordinal: int,
    machine_state: str | None = None,
    phase: str | None = None,
    phase_progress: float | None = None,
) -> tuple[str, dict[str, Any]]:
    nested = row.get("identity")
    identity = {**dict(base), **(dict(nested) if isinstance(nested, Mapping) else {})}
    identity.update({key: row[key] for key in _IDENTITY_FIELDS if key in row})
    if machine_state is not None:
        identity["machine_state"] = machine_state
    if phase is not None:
        identity["phase"] = phase
    if phase_progress is not None:
        identity["phase_progress"] = float(phase_progress)
    identity["schema_version"] = SCHEMA_VERSION
    message_id = stable_message_id(
        identity["process_run_id"],
        message_type,
        ordinal,
        identity["observed_at"],
    )
    identity["message_id"] = message_id
    return message_id, _public(identity)


def _envelope(
    base: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    message_type: str,
    ordinal: int,
    payload: Mapping[str, Any],
    machine_state: str | None = None,
    phase: str | None = None,
    phase_progress: float | None = None,
) -> dict[str, Any]:
    message_id, identity = _identity_for_message(
        base,
        row,
        message_type=message_type,
        ordinal=ordinal,
        machine_state=machine_state,
        phase=phase,
        phase_progress=phase_progress,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id,
        "message_type": message_type,
        "identity": identity,
        "payload": _public(dict(payload)),
        "source": "virtual_fab_v2",
    }


def envelopes_for_process_run(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert generator output into the only objects allowed onto transport."""
    run = dict(result)
    base = _mapping(run["identity"], name="result.identity")
    telemetry_value = run.get("telemetry") or []
    if not isinstance(telemetry_value, Sequence) or isinstance(telemetry_value, (str, bytes)):
        raise TypeError("result.telemetry must be a sequence")
    telemetry = [_mapping(item, name="telemetry row") for item in telemetry_value]
    if not telemetry:
        raise ValueError("a process run must contain at least one telemetry row")

    first = telemetry[0]
    last = telemetry[-1]
    state_values = run.get("state_events") or []
    if not isinstance(state_values, Sequence) or isinstance(state_values, (str, bytes)):
        raise TypeError("result.state_events must be a sequence")
    state_events = [_mapping(item, name="state event") for item in state_values]

    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    if state_events:
        first_observed = str(_row_value(first, "observed_at", base["observed_at"]))
        for index, row in enumerate(state_events):
            machine_state = str(_row_value(row, "machine_state", "RUNNING")).upper()
            event_type = str(row.get("event_type") or "machine_state_changed")
            completed = event_type in {"process_complete", "process_run_completed"} or machine_state in {
                "COMPLETE",
                "UNLOAD",
                "IDLE",
            }
            completed_at = _row_value(row, "observed_at", first_observed) if completed else None
            target = before if str(_row_value(row, "observed_at", first_observed)) <= first_observed else after
            target.append(
                _envelope(
                    base,
                    row,
                    message_type="state",
                    ordinal=index,
                    payload={
                        "run_status": "completed" if completed else "running",
                        **({"completed_at": completed_at} if completed_at else {}),
                    },
                )
            )
            target.append(
                _envelope(
                    base,
                    row,
                    message_type="event",
                    ordinal=index,
                    payload={
                        "event_type": event_type,
                        "severity": str(row.get("severity") or "info"),
                        "run_status": "completed" if completed else "running",
                        **({"completed_at": completed_at} if completed_at else {}),
                        "metadata": {"process_id": base.get("process_id")},
                    },
                )
            )
    else:
        before = [
            _envelope(
                base,
                first,
                message_type="state",
                ordinal=0,
                payload={"run_status": "running"},
            ),
            _envelope(
                base,
                first,
                message_type="event",
                ordinal=0,
                payload={
                    "event_type": "process_run_started",
                    "severity": "info",
                    "run_status": "running",
                    "metadata": {"process_id": base.get("process_id")},
                },
            ),
        ]
        last_observed = _row_value(last, "observed_at", base["observed_at"])
        after = [
            _envelope(
                base,
                last,
                message_type="event",
                ordinal=1,
                payload={
                    "event_type": "process_run_completed",
                    "severity": "info",
                    "run_status": "completed",
                    "completed_at": last_observed,
                    "metadata": {"process_id": base.get("process_id")},
                },
            ),
            _envelope(
                base,
                last,
                message_type="state",
                ordinal=1,
                payload={"run_status": "completed", "completed_at": last_observed},
                machine_state="UNLOAD",
                phase="UNLOAD",
                phase_progress=1.0,
            ),
        ]

    envelopes: list[dict[str, Any]] = list(before)

    for index, row in enumerate(telemetry, start=1):
        tags = row.get("tags", row.get("values", {}))
        context = row.get("detector_context", {})
        payload = {
            "tags": tags if isinstance(tags, Mapping) else {},
            "detector_context": context if isinstance(context, Mapping) else {},
        }
        for key in (
            "related_tags",
            "generator_version",
            "feature_fingerprint",
            "config_fingerprint",
            "data_quality_status",
        ):
            if key in row:
                payload[key] = row[key]
        envelopes.append(
            _envelope(
                base,
                row,
                message_type="telemetry",
                ordinal=index,
                payload=payload,
            )
        )

    envelopes.extend(after)

    for message_type in ("metrology", "inspection"):
        value = run.get(message_type)
        if value is None:
            continue
        item = _mapping(value, name=f"result.{message_type}")
        observed_at = item.get("available_at") or item.get("observed_at")
        row = {
            **last,
            "observed_at": observed_at or _row_value(last, "observed_at", base["observed_at"]),
        }
        if message_type == "metrology":
            payload = {
                key: item[key]
                for key in (
                    "metrics",
                    "quality_target",
                    "quality_targets",
                    "available_at",
                    "detector_result",
                    "related_tags",
                )
                if key in item
            }
        else:
            # Images stay in object storage; only their key and observed
            # features cross MQTT.  synthetic_debug (mask/bbox) is omitted.
            payload = {
                key: item[key]
                for key in (
                    "image_key",
                    "features",
                    "available_at",
                    "detector_result",
                    "related_tags",
                    "metadata",
                )
                if key in item
            }
        envelopes.append(
            _envelope(
                base,
                row,
                message_type=message_type,
                ordinal=0,
                payload=payload,
                machine_state="UNLOAD",
                phase=message_type.upper(),
                phase_progress=1.0,
            )
        )
    return envelopes


class FabStreamOrchestrator:
    """Run a bounded synthetic lot/wafer route through direct or MQTT transport."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        fault_catalog: Mapping[str, Any] | None = None,
        *,
        seed: int = 42,
        transport: str = "mqtt",
        generator: Any | None = None,
        receipt_timeout_seconds: float = 10.0,
    ) -> None:
        self.config = dict(config or load_fab_config())
        self.fault_catalog = dict(fault_catalog or load_fault_catalog())
        self.seed = int(seed)
        self.transport_name = str(transport).strip().lower()
        if self.transport_name not in {"mqtt", "direct"}:
            raise ValueError("transport must be 'mqtt' or 'direct'")
        self.generator = generator or VirtualFabGenerator(
            self.config,
            self.fault_catalog,
            seed=self.seed,
        )
        self.receipt_timeout_seconds = max(0.1, float(receipt_timeout_seconds))
        self.router = default_router()
        self.publisher: DirectFabTransport | MqttFabPublisher | None = None
        self.subscriber: MqttFabSubscriber | None = None

    def _open(self) -> None:
        if self.publisher is not None:
            return
        if self.transport_name == "direct":
            self.publisher = DirectFabTransport(self.router)
            return
        self.subscriber = MqttFabSubscriber(self.router)
        self.subscriber.start()
        self.publisher = MqttFabPublisher()

    def close(self) -> None:
        if self.publisher is not None:
            self.publisher.close()
            self.publisher = None
        if self.subscriber is not None:
            self.subscriber.close()
            self.subscriber = None

    def _publish(self, envelope: Mapping[str, Any]) -> None:
        if self.publisher is None:
            raise RuntimeError("FAB transport is not open")
        self.publisher.publish(envelope)
        if self.transport_name == "direct":
            return
        deadline = time.monotonic() + self.receipt_timeout_seconds
        while time.monotonic() < deadline:
            if fab_storage.message_processed(str(envelope["message_id"])):
                return
            time.sleep(0.02)
        raise TimeoutError(f"FAB message was not consumed: {envelope['message_id']}")

    def _processes(self, process: str) -> list[str]:
        route = [str(item).lower() for item in self.config.get("route", ())]
        selected = str(process).strip().lower()
        if selected == "all":
            return route
        if selected not in route:
            raise ValueError(f"process must be 'all' or one of: {', '.join(route)}")
        return [selected]

    def _fault_definition(self, fault: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if fault is None:
            return None, None
        name = str(fault).strip()
        definitions = self.fault_catalog.get("faults", self.fault_catalog)
        if not isinstance(definitions, Mapping) or name not in definitions:
            choices = ", ".join(sorted(map(str, definitions))) if isinstance(definitions, Mapping) else ""
            raise ValueError(f"unknown fault {name!r}; expected one of: {choices}")
        definition = _mapping(definitions[name], name="fault definition")
        return name, definition

    def _save_fault_truth(
        self,
        *,
        fault_name: str,
        fault_definition: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        identity = _mapping(result["identity"], name="result.identity")
        telemetry = list(result.get("telemetry") or ())
        first = _mapping(telemetry[0], name="telemetry row") if telemetry else identity
        vision = fault_definition.get("vision")
        fab_storage.save_simulation_fault(
            {
                "fault_id": f"SIMFLT-{identity['process_run_id']}",
                "process_run_id": identity["process_run_id"],
                "fault_type": fault_name,
                "fault_scope": "process_run",
                "started_at": first.get("observed_at", identity["observed_at"]),
                "severity": 1.0,
                "ground_truth_tags": list((fault_definition.get("sensor_effects") or {}).keys()),
                "ground_truth_defect": vision.get("defect") if isinstance(vision, Mapping) else None,
                "metadata": {"synthetic_seed": self.seed},
            }
        )

    def run(
        self,
        *,
        lots: int = 1,
        wafers_per_lot: int = 1,
        process: str = "all",
        fault: str | None = None,
        fault_after_cycle: int = 0,
        interval_seconds: float = 0.0,
    ) -> dict[str, Any]:
        lot_count = int(lots)
        wafer_count = int(wafers_per_lot)
        if lot_count < 1 or wafer_count < 1:
            raise ValueError("lots and wafers_per_lot must be at least 1")
        if int(fault_after_cycle) < 0:
            raise ValueError("fault_after_cycle must be non-negative")
        if float(interval_seconds) < 0:
            raise ValueError("interval_seconds must be non-negative")
        processes = self._processes(process)
        fault_name, fault_definition = self._fault_definition(fault)
        if fault_definition is not None:
            fault_process = str(fault_definition.get("process_id") or "").lower()
            if fault_process not in processes:
                raise ValueError(f"fault {fault_name!r} applies to process {fault_process!r}")

        summaries: list[dict[str, Any]] = []
        self._open()
        try:
            ordinal_by_process = {name: 0 for name in processes}
            for lot_index in range(1, lot_count + 1):
                lot_id = f"FAB-{self.seed:06d}-LOT-{lot_index:03d}"
                storage.upsert_lot(
                    {
                        "lot_id": lot_id,
                        "product_id": "WAFERGUARD-SYNTHETIC",
                        "recipe_route": ">".join(processes),
                        "status": "running",
                        "current_process_step": processes[0],
                        "wafer_count": wafer_count,
                        "metadata": {"source": "virtual_fab_v2", "synthetic_seed": self.seed},
                    }
                )
                for wafer_index in range(1, wafer_count + 1):
                    for process_id in processes:
                        ordinal_by_process[process_id] += 1
                        inject = bool(
                            fault_name
                            and fault_definition
                            and str(fault_definition.get("process_id")).lower() == process_id
                            and ordinal_by_process[process_id] > int(fault_after_cycle)
                        )
                        generated = self.generator.generate_process_run(
                            lot_id=lot_id,
                            wafer_index=wafer_index,
                            process_id=process_id,
                            fault_id=fault_name if inject else None,
                        )
                        result = _mapping(generated, name="generated process run")
                        envelopes = envelopes_for_process_run(result)
                        for envelope in envelopes:
                            self._publish(envelope)
                            if interval_seconds > 0:
                                time.sleep(float(interval_seconds))
                        finalized = finalize_process_run(str(_mapping(result["identity"], name="identity")["process_run_id"]), config=self.config)
                        evaluation = None
                        if _synthetic_debug_enabled():
                            inspection = result.get("inspection")
                            debug = inspection.get("synthetic_debug") if isinstance(inspection, Mapping) else None
                            if isinstance(debug, Mapping):
                                fab_storage.save_inspection_debug(
                                    finalized["process_run_id"],
                                    mask_key=str(debug.get("mask_key")) if debug.get("mask_key") else None,
                                    bbox=list(debug.get("bbox")) if debug.get("bbox") is not None else None,
                                    debug=True,
                                )
                        if inject and fault_name and fault_definition:
                            # Evaluation is intentionally sequenced after RCA:
                            # inference cannot consult truth that is not yet in
                            # the repository.
                            self._save_fault_truth(
                                fault_name=fault_name,
                                fault_definition=fault_definition,
                                result=result,
                            )
                            evaluation = evaluate_process_run(finalized["process_run_id"])
                        summaries.append(
                            {
                                "lot_id": lot_id,
                                "wafer_id": _mapping(result["identity"], name="identity")["wafer_id"],
                                "process_id": process_id,
                                "process_run_id": finalized["process_run_id"],
                                "message_count": len(envelopes),
                                "fusion": finalized["fusion"],
                                "rca": finalized["rca"],
                                "evaluation": evaluation,
                            }
                        )
                storage.upsert_lot(
                    {
                        "lot_id": lot_id,
                        "status": "completed",
                        "current_process_step": processes[-1],
                        "wafer_count": wafer_count,
                    }
                )
        finally:
            self.close()
        return {
            "transport": self.transport_name,
            "lots": lot_count,
            "wafers_per_lot": wafer_count,
            "processes": processes,
            "process_runs": summaries,
        }

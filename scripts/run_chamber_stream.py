from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.chamber_generator import (  # noqa: E402
    SUPPORTED_ANOMALIES,
    SUPPORTED_MACHINE_STATES,
    EtchTelemetryGenerator,
)
from app.services.chamber_runtime import ChamberRuntime  # noqa: E402
from app.services import chamber_storage  # noqa: E402
from app.services.storage import init_db  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic multivariate Chamber telemetry locally.")
    parser.add_argument("--equipment-count", type=int, default=3)
    parser.add_argument("--interval", type=float, default=1.0, help="Wall-clock delay between rounds; 0 enables accelerated mode.")
    parser.add_argument("--duration", type=float, default=None, help="Optional wall-clock run duration in seconds.")
    parser.add_argument("--samples", type=int, default=180, help="Rounds to generate when --duration is omitted.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--anomaly", choices=sorted(SUPPORTED_ANOMALIES), default=None)
    parser.add_argument("--anomaly-after", type=int, default=140)
    parser.add_argument("--machine-state", choices=sorted(SUPPORTED_MACHINE_STATES), default=None)
    return parser.parse_args()


def _load_recent_history(generator: EtchTelemetryGenerator, limit: int = 200) -> dict[str, list[dict[str, Any]]]:
    return {
        equipment_id: chamber_storage.telemetry_rows(equipment_id=equipment_id, limit=limit)
        for equipment_id in generator.equipment_ids
    }


def _wafer_number(wafer_id: object) -> int:
    digits = "".join(character for character in str(wafer_id or "") if character.isdigit())
    return int(digits) if digits else 0


def _restore_generator_progress(
    generator: EtchTelemetryGenerator,
    histories: dict[str, list[dict[str, Any]]],
) -> None:
    """Restore lifecycle progress from recent persisted telemetry.

    A completion sample still carries the wafer id that was active before the
    generator advanced its internal state. Detect that boundary from the
    wafer_count_since_clean increment instead of blindly treating the last wafer
    id as an in-progress wafer. For a mid-wafer restart, retain the number of
    already processed running samples rather than restarting its whole cycle.
    """
    latest_rows = [rows[-1] for rows in histories.values() if rows]
    generator.restore_from_rows(latest_rows)

    maintenance = generator.config.get("maintenance", {})
    cleaning_enabled = bool(maintenance.get("cleaning_enabled", True))
    cleaning_every = max(1, int(maintenance.get("wafers_between_clean", 120)))
    cleaning_samples = max(1, int(maintenance.get("cleaning_samples", 1)))
    midpoint_target = max(1, round((generator.wafer_samples_min + generator.wafer_samples_max) / 2))

    for equipment_id, rows in histories.items():
        if not rows:
            continue
        state = generator.states[equipment_id]
        latest = rows[-1]
        lot_id = str(latest.get("lot_id") or "") or None
        wafer_id = str(latest.get("wafer_id") or "") or None
        state.lot_id = lot_id
        state.wafer_id = wafer_id

        if lot_id:
            same_lot = [row for row in rows if str(row.get("lot_id") or "") == lot_id]
            state.lot_sample_index = sum(1 for row in same_lot if row.get("machine_state") == "running")

        if not wafer_id:
            continue

        contiguous: list[dict[str, Any]] = []
        for row in reversed(rows):
            if str(row.get("lot_id") or "") != str(lot_id or ""):
                break
            if str(row.get("wafer_id") or "") != wafer_id:
                break
            contiguous.append(row)
        contiguous.reverse()
        running_rows = [row for row in contiguous if row.get("machine_state") == "running"]
        processed_samples = len(running_rows)
        wafer_number = _wafer_number(wafer_id)

        completed_on_latest = False
        if len(contiguous) >= 2:
            previous_count = int(contiguous[-2].get("wafer_count_since_clean") or 0)
            latest_count = int(contiguous[-1].get("wafer_count_since_clean") or 0)
            completed_on_latest = latest_count > previous_count

        if completed_on_latest:
            state.lot_wafer_completed = max(state.lot_wafer_completed, wafer_number)
            state.wafer_id = None
            state.wafer_sample_index = 0
            state.wafer_target_samples = 0
            state.wafer_bias = {}
            if cleaning_enabled and int(latest.get("wafer_count_since_clean") or 0) >= cleaning_every:
                state.cleaning_remaining = cleaning_samples
            if wafer_number >= generator.lot_wafer_count:
                state.lot_id = None
                state.lot_started_at = None
                state.lot_bias = {}
                state.between_lots_remaining = generator.idle_between_lots
                state.recipe_index = (state.recipe_index + 1) % len(generator.recipe_ids)
            continue

        state.lot_wafer_completed = max(0, wafer_number - 1)
        state.wafer_sample_index = processed_samples
        # The historical random target was not persisted in older rows. Preserve
        # already-consumed progress and use a bounded midpoint target rather than
        # granting a brand-new full random cycle after every restart.
        state.wafer_target_samples = min(
            generator.wafer_samples_max,
            max(processed_samples + 1, midpoint_target),
        )


def main() -> int:
    args = parse_args()
    if args.interval < 0 or args.samples < 1 or args.equipment_count < 1:
        raise SystemExit("interval must be >= 0 and counts must be >= 1")
    init_db()
    generator = EtchTelemetryGenerator(
        equipment_count=args.equipment_count,
        interval_seconds=max(args.interval, 1.0),
        seed=args.seed,
    )
    histories = _load_recent_history(generator)
    _restore_generator_progress(generator, histories)
    runtime = ChamberRuntime(generator.config)
    runtime.data_quality.restore_from_rows([row for rows in histories.values() for row in rows])

    started = time.monotonic()
    rounds = 0
    states: dict[str, int] = {}
    auto_retraining: dict | None = None
    retrain_check_samples = max(1, int(generator.config["model"].get("auto_retrain_check_samples", 30)))
    while True:
        if args.duration is not None:
            if time.monotonic() - started >= args.duration:
                break
        elif rounds >= args.samples:
            break
        for index, equipment_id in enumerate(generator.equipment_ids):
            anomaly = args.anomaly if args.anomaly and rounds >= args.anomaly_after and index == 0 else None
            sample = generator.next_sample(
                equipment_id,
                anomaly=anomaly,
                machine_state=args.machine_state,
            )
            result = runtime.process_sample(sample)
            states[result["state"]] = states.get(result["state"], 0) + 1
        rounds += 1
        if rounds % retrain_check_samples == 0:
            auto_retraining = runtime.maybe_auto_retrain()
        if args.interval:
            time.sleep(args.interval)

    full_status = runtime.status()
    production = full_status.get("production_model")
    compact_production = None if production is None else {
        "version": production["version"],
        "stage": production["stage"],
        "mae": production["mae"],
        "rmse": production["rmse"],
        "threshold": production["threshold"],
        "artifact_path": production["artifact_path"],
        "holdout": production.get("metadata", {}).get("candidate_holdout"),
        "use_time_only_holdout": production.get("metadata", {}).get("use_time_only_holdout"),
    }
    result = {
        "rounds": rounds,
        "equipment_count": args.equipment_count,
        "generated_rows": rounds * args.equipment_count,
        "processing_states": states,
        "status": {
            "state": full_status["state"],
            "telemetry_rows": full_status["telemetry_rows"],
            "prediction_rows": full_status["prediction_rows"],
            "clean_running_rows": full_status["clean_running_rows"],
            "production_model": compact_production,
            "readiness": full_status["readiness"],
        },
        "auto_retraining": auto_retraining,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

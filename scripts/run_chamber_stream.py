from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

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
    latest_rows = []
    for equipment_id in generator.equipment_ids:
        persisted = chamber_storage.telemetry_rows(equipment_id=equipment_id, limit=1)
        if persisted:
            latest_rows.append(persisted[-1])
    generator.restore_from_rows(latest_rows)
    runtime = ChamberRuntime(generator.config)
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

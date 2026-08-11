from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import storage  # noqa: E402
from app.services.chamber_generator import load_chamber_config  # noqa: E402
from app.services.fab_scenario import FabScenarioOrchestrator  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the synthetic Lot -> Chamber -> Inspection scenario.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--lag-minutes", type=int, default=5)
    parser.add_argument("--wafer-samples", type=int, default=4, help="Samples per wafer in accelerated mode.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    storage.init_db()
    config = load_chamber_config()
    config["lot"].update(
        startup_samples=1,
        idle_samples_between_lots=1,
        wafer_process_samples={"min": args.wafer_samples, "max": args.wafer_samples},
        hold_probability_per_wafer=0,
    )
    config["model"].update(bootstrap_min_rows=30, min_training_rows=24)
    config["anomalies"]["rf_drift_per_sample"] = 18.0
    result = FabScenarioOrchestrator(
        config,
        seed=args.seed,
        inspection_lag_minutes=args.lag_minutes,
    ).run()
    compact = {
        "scenario": result["scenario"],
        "lot_id": result["lot_id"],
        "generated_samples": result["generated_samples"],
        "target_wafers": result["target_wafers"],
        "inspection_ids": [item["id"] for item in result["inspections"]],
        "anomaly_event_count": len(result["anomaly_events"]),
        "boundary": result["boundary"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

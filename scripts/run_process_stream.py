from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.process_runtime import process_profiles, runtime_metrics, simulate_sample


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic multi-process telemetry/vision inference")
    parser.add_argument("--process", choices=[p["process_id"] for p in process_profiles()], default="deposition")
    parser.add_argument("--modality", choices=["timeseries", "vision", "both"], default="both")
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--anomaly", default=None, help="Process-specific telemetry anomaly or vision defect")
    parser.add_argument("--anomaly-after", type=int, default=40)
    parser.add_argument("--equipment-id", default=None)
    parser.add_argument("--lot-id", default="LOT-MM-DEMO-001")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for index in range(max(1, args.samples)):
        anomaly = args.anomaly if args.anomaly and index >= args.anomaly_after else None
        result = simulate_sample(
            args.process,
            modality=args.modality,
            anomaly=anomaly,
            equipment_id=args.equipment_id,
            lot_id=args.lot_id,
            wafer_id=f"W{index + 1:02d}",
        )
        summary = {
            "index": index + 1,
            "process": args.process,
            "anomaly": anomaly,
            "detections": {
                name: {
                    "flag": row["is_anomaly"],
                    "score": round(row["anomaly_score"], 5),
                    "threshold": round(row["threshold"], 5),
                    "model": row["model_version"],
                }
                for name, row in result["results"].items()
            },
        }
        print(json.dumps(summary, ensure_ascii=False))
        if args.interval > 0:
            time.sleep(args.interval)
    print(json.dumps({"metrics": runtime_metrics(args.process)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

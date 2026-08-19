from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.process_runtime import process_profiles, runtime_metrics
from app.services.process_stream_live import GENERIC_DASHBOARD_PROCESSES, stream_sample
from app.services.process_temporal import TemporalProcessGenerator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic temporal process telemetry/vision inference")
    choices = [p["process_id"] for p in process_profiles()]
    parser.add_argument("--process", choices=[*choices, "all"], default="deposition")
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
    process_ids = list(GENERIC_DASHBOARD_PROCESSES) if args.process == "all" else [args.process]
    if any(process_id not in GENERIC_DASHBOARD_PROCESSES for process_id in process_ids):
        raise SystemExit(
            "Use run_chamber_stream.py for Etch. Generic temporal runtime supports "
            "photo/deposition/cmp/cleaning."
        )

    histories = {process_id: [] for process_id in process_ids}
    generators = {
        process_id: TemporalProcessGenerator(process_id, seed=42 + idx * 1000)
        for idx, process_id in enumerate(process_ids)
    }

    for index in range(1, max(1, args.samples) + 1):
        for process_id in process_ids:
            anomaly = args.anomaly if args.anomaly and index > args.anomaly_after else None
            result = stream_sample(
                process_id,
                generators[process_id],
                index=index,
                modality=args.modality,
                anomaly=anomaly,
                equipment_id=args.equipment_id if args.process != "all" else None,
                lot_id=args.lot_id,
                history=histories[process_id],
            )
            summary = {
                "index": index,
                "process": process_id,
                "anomaly": anomaly,
                "phase": result["results"].get("timeseries", {}).get("phase"),
                "detections": {},
            }
            for name, row in result["results"].items():
                score = float(row["anomaly_score"])
                threshold = float(row["threshold"])
                summary["detections"][name] = {
                    "flag": bool(row["is_anomaly"]),
                    "score": round(score, 5),
                    "threshold": round(threshold, 5),
                    "margin": round(float(row.get("margin", score - threshold)), 5),
                    "model": row["model_version"],
                }
            print(json.dumps(summary, ensure_ascii=False))
        if args.interval > 0:
            time.sleep(args.interval)

    print(json.dumps({"metrics": {pid: runtime_metrics(pid) for pid in process_ids}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

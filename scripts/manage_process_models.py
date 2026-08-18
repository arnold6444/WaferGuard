from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.process_mlops import model_health, promote_candidate, rollback_model, train_candidate
from app.services.process_runtime import list_models, process_profiles
from app.services.process_temporal import train_process_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage process-specific time-series and vision models")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--process", default=None)
    list_cmd.add_argument("--modality", choices=["timeseries", "vision"], default=None)

    health = sub.add_parser("health")
    health.add_argument("--process", required=True)
    health.add_argument("--modality", choices=["timeseries", "vision"], required=True)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--process", choices=[p["process_id"] for p in process_profiles()], required=True)
    bootstrap.add_argument("--modality", choices=["timeseries", "vision"], required=True)

    retrain = sub.add_parser("retrain")
    retrain.add_argument("--process", choices=[p["process_id"] for p in process_profiles()], required=True)
    retrain.add_argument("--modality", choices=["timeseries", "vision"], required=True)
    retrain.add_argument("--force", action="store_true")

    promote = sub.add_parser("promote")
    promote.add_argument("--process", required=True)
    promote.add_argument("--modality", choices=["timeseries", "vision"], required=True)
    promote.add_argument("--version", required=True)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--process", required=True)
    rollback.add_argument("--modality", choices=["timeseries", "vision"], required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "list":
        result = list_models(args.process, args.modality)
    elif args.command == "health":
        result = model_health(args.process, args.modality)
    elif args.command == "bootstrap":
        result = train_process_model(args.process, args.modality, stage="Production")
    elif args.command == "retrain":
        result = train_candidate(args.process, args.modality, force=args.force)
    elif args.command == "promote":
        result = promote_candidate(args.process, args.modality, args.version)
    else:
        result = rollback_model(args.process, args.modality)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

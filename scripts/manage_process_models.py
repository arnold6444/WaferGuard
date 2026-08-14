from __future__ import annotations

import argparse
import json

from app.services.process_runtime import list_models, process_profiles, promote_model, train_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage process-specific time-series and vision models")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--process", default=None)
    list_cmd.add_argument("--modality", choices=["timeseries", "vision"], default=None)

    train = sub.add_parser("train")
    train.add_argument("--process", choices=[p["process_id"] for p in process_profiles()], required=True)
    train.add_argument("--modality", choices=["timeseries", "vision"], required=True)
    train.add_argument("--production", action="store_true")

    promote = sub.add_parser("promote")
    promote.add_argument("--process", required=True)
    promote.add_argument("--modality", choices=["timeseries", "vision"], required=True)
    promote.add_argument("--version", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "list":
        result = list_models(args.process, args.modality)
    elif args.command == "train":
        result = train_model(
            args.process,
            args.modality,
            stage="Production" if args.production else "Staging",
        )
    else:
        result = promote_model(args.process, args.modality, args.version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

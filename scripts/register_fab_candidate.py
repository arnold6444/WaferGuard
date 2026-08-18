from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import storage  # noqa: E402
from app.services.fab_models import register_fab_candidate  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register an externally trained FAB artifact as Staging.")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--process", choices=("photo", "etch", "deposition", "cmp"), required=True)
    parser.add_argument("--precision", type=float, required=True)
    parser.add_argument("--recall", type=float, required=True)
    parser.add_argument("--f2", type=float, required=True)
    parser.add_argument("--false-positive-rate", type=float, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    storage.init_db()
    result = register_fab_candidate(
        args.artifact,
        process_id=args.process,
        metrics={
            "precision": args.precision,
            "recall": args.recall,
            "f2": args.f2,
            "false_positive_rate": args.false_positive_rate,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

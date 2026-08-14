from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.process_rca import run_process_rca


STEP = {
    "photo": "Lithography",
    "etch": "Etch",
    "deposition": "Deposition",
    "cmp": "CMP",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PostgreSQL-backed RCA over persisted process events")
    parser.add_argument("--process", choices=sorted(STEP), required=True)
    parser.add_argument("--equipment-id", required=True)
    parser.add_argument("--lot-id", default=None)
    parser.add_argument("--wafer-id", default=None)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--no-llm", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_process_rca(
        process_step=STEP[args.process],
        equipment_id=args.equipment_id,
        lot_id=args.lot_id,
        wafer_id=args.wafer_id,
        minutes=args.minutes,
        use_llm=not args.no_llm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

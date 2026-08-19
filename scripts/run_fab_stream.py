from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import storage  # noqa: E402
from app.services.fab_orchestrator import FabStreamOrchestrator  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the synthetic WaferGuard FAB v2 stream.")
    parser.add_argument("--lots", type=int, default=1)
    parser.add_argument("--wafers-per-lot", type=int, default=1)
    parser.add_argument(
        "--process",
        choices=("all", "photo", "etch", "deposition", "cmp", "cleaning"),
        default="all",
    )
    parser.add_argument(
        "--fault",
        choices=(
            "cmp_slurry_degradation",
            "etch_chamber_contamination",
            "deposition_precursor_instability",
            "photo_focus_drift",
            "cleaning_chemical_concentration_drift",
        ),
    )
    parser.add_argument("--fault-after-cycle", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--transport", choices=("mqtt", "direct"), default="mqtt")
    parser.add_argument("--interval", type=float, default=0.0, help="Seconds between messages.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    storage.init_db()
    orchestrator = FabStreamOrchestrator(seed=args.seed, transport=args.transport)
    result = orchestrator.run(
        lots=args.lots,
        wafers_per_lot=args.wafers_per_lot,
        process=args.process,
        fault=args.fault,
        fault_after_cycle=args.fault_after_cycle,
        interval_seconds=args.interval,
    )
    compact = {
        "transport": result["transport"],
        "lots": result["lots"],
        "wafers_per_lot": result["wafers_per_lot"],
        "processes": result["processes"],
        "process_run_ids": [item["process_run_id"] for item in result["process_runs"]],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

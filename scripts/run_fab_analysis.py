from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.fab_analysis import (  # noqa: E402
    DATA_OUTPUT_DIR,
    DEFAULT_INPUT_PATH,
    export_runtime_features,
    run_local_analysis,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run local FAB EDA and model analysis without Jupyter.")
    value.add_argument("--input", default=str(DEFAULT_INPUT_PATH), help="Local CSV or Parquet file")
    value.add_argument("--output-dir", default=str(DATA_OUTPUT_DIR))
    value.add_argument("--process", choices=("photo", "etch", "deposition", "cmp"), default="cmp")
    value.add_argument("--target", default="is_anomaly", help="Target column; use an empty value for unsupervised")
    value.add_argument("--seed", type=int, default=42)
    value.add_argument("--contamination", type=float, default=0.05)
    value.add_argument("--export-runtime", action="store_true", help="Export current DB runtime features before analysis")
    value.add_argument("--create-candidate", action="store_true", help="Save a fingerprint-compatible Staging candidate bundle")
    return value


def main() -> int:
    args = parser().parse_args()
    source = Path(args.input)
    if args.export_runtime:
        source = export_runtime_features(args.process, source)
    summary = run_local_analysis(
        source,
        process_id=args.process,
        target_column=args.target or None,
        output_dir=args.output_dir,
        create_candidate=args.create_candidate,
        contamination=args.contamination,
        random_state=args.seed,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

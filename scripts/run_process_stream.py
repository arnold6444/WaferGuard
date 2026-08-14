from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services import object_store
from app.services.process_runtime import process_profiles, runtime_metrics, simulate_sample


MAX_LIVE_HISTORY = 180


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


def _deviation_map(image_key: str | None, process_id: str, wafer_id: str) -> str | None:
    """Create a simple spatial deviation preview for the dashboard.

    This is intentionally not described as model localization: the current generic
    vision model is feature-based, so this image only highlights pixel deviation
    from the wafer median to help inspect the synthetic input visually.
    """
    if not image_key:
        return None
    raw = object_store.read_bytes(image_key)
    if not raw:
        return None
    image = Image.open(io.BytesIO(raw)).convert("L")
    arr = np.asarray(image, dtype=np.float32) / 255.0
    wafer = arr > 0.02
    if not np.any(wafer):
        return None
    baseline = float(np.median(arr[wafer]))
    deviation = np.zeros_like(arr)
    deviation[wafer] = np.abs(arr[wafer] - baseline)
    scale = float(np.quantile(deviation[wafer], 0.98))
    if scale <= 1e-9:
        scale = 1.0
    heat = np.clip(deviation / scale, 0.0, 1.0)
    rgb = np.zeros((*arr.shape, 3), dtype=np.uint8)
    gray = np.uint8(np.clip(arr * 150, 0, 150))
    rgb[..., 0] = np.maximum(gray, np.uint8(heat * 255))
    rgb[..., 1] = np.uint8(gray * (1.0 - heat * 0.70))
    rgb[..., 2] = np.uint8(gray * (1.0 - heat * 0.85))
    rgb[~wafer] = 0
    key = f"process_runtime/{process_id}/{wafer_id}-deviation.png"
    object_store.save_image(Image.fromarray(rgb, mode="RGB"), key)
    return object_store.presign(key)


def _live_row(index: int, result: dict, anomaly: str | None) -> dict:
    row = {
        "index": index,
        "observed_at": result["observed_at"],
        "equipment_id": result["equipment_id"],
        "lot_id": result["lot_id"],
        "wafer_id": result["wafer_id"],
        "requested_anomaly": anomaly,
        "timeseries": None,
        "vision": None,
    }
    ts = result["results"].get("timeseries")
    if ts:
        row["timeseries"] = {
            "payload": ts.get("payload", {}),
            "flag": bool(ts.get("is_anomaly")),
            "ground_truth": bool(ts.get("ground_truth")),
            "injected_anomaly": ts.get("injected_anomaly"),
            "score": float(ts.get("anomaly_score") or 0.0),
            "threshold": float(ts.get("threshold") or 0.0),
            "model": ts.get("model_version"),
            "related_tags": ts.get("related_tags", []),
        }
    vision = result["results"].get("vision")
    if vision:
        image_url = vision.get("image_url")
        row["vision"] = {
            "image_url": image_url,
            "deviation_url": _deviation_map(vision.get("image_key"), result["process_id"], result["wafer_id"]),
            "features": vision.get("payload", {}),
            "flag": bool(vision.get("is_anomaly")),
            "ground_truth": bool(vision.get("ground_truth")),
            "injected_anomaly": vision.get("injected_anomaly"),
            "score": float(vision.get("anomaly_score") or 0.0),
            "threshold": float(vision.get("threshold") or 0.0),
            "model": vision.get("model_version"),
            "related_tags": vision.get("related_tags", []),
        }
    return row


def _write_live_snapshot(process_id: str, history: list[dict]) -> None:
    payload = {
        "process_id": process_id,
        "data_source": "synthetic_multimodal_runtime",
        "generated_at": history[-1]["observed_at"] if history else None,
        "history": history[-MAX_LIVE_HISTORY:],
        "metrics": runtime_metrics(process_id),
        "note": "Vision deviation map is a visual diagnostic, not model localization.",
    }
    object_store.put_bytes(
        f"process_runtime/{process_id}/live.json",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )


def main() -> None:
    args = parse_args()
    history: list[dict] = []
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
        history.append(_live_row(index + 1, result, anomaly))
        history = history[-MAX_LIVE_HISTORY:]
        _write_live_snapshot(args.process, history)
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

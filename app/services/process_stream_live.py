from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
from PIL import Image

from app.services import object_store
from app.services.process_runtime import runtime_metrics
from app.services.process_temporal import TemporalProcessGenerator, simulate_temporal_sample

MAX_LIVE_HISTORY = 180
GENERIC_DASHBOARD_PROCESSES = ("photo", "deposition", "cmp")


def deviation_map(image_key: str | None, process_id: str, wafer_id: str) -> str | None:
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


def live_row(index: int, result: dict[str, Any], anomaly: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
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
        score = float(ts.get("anomaly_score") or 0.0)
        threshold = float(ts.get("threshold") or 0.0)
        row["timeseries"] = {
            "payload": ts.get("payload", {}),
            "flag": bool(ts.get("is_anomaly")),
            "ground_truth": bool(ts.get("ground_truth")),
            "injected_anomaly": ts.get("injected_anomaly"),
            "score": score,
            "threshold": threshold,
            "margin": float(ts.get("margin", score - threshold)),
            "model": ts.get("model_version"),
            "related_tags": ts.get("related_tags", []),
            "phase": ts.get("phase"),
            "phase_progress": ts.get("phase_progress"),
            "generator_version": ts.get("generator_version"),
        }
    vision = result["results"].get("vision")
    if vision:
        score = float(vision.get("anomaly_score") or 0.0)
        threshold = float(vision.get("threshold") or 0.0)
        row["vision"] = {
            "image_url": vision.get("image_url"),
            "deviation_url": deviation_map(vision.get("image_key"), result["process_id"], result["wafer_id"]),
            "features": vision.get("payload", {}),
            "flag": bool(vision.get("is_anomaly")),
            "ground_truth": bool(vision.get("ground_truth")),
            "injected_anomaly": vision.get("injected_anomaly"),
            "score": score,
            "threshold": threshold,
            "margin": float(vision.get("margin", score - threshold)),
            "model": vision.get("model_version"),
            "related_tags": vision.get("related_tags", []),
        }
    return row


def write_live_snapshot(process_id: str, history: list[dict[str, Any]]) -> None:
    payload = {
        "process_id": process_id,
        "data_source": "synthetic_temporal_multimodal_runtime",
        "generated_at": history[-1]["observed_at"] if history else None,
        "history": history[-MAX_LIVE_HISTORY:],
        "metrics": runtime_metrics(process_id),
        "note": "Vision deviation map is diagnostic only. Margin = score - threshold; margin >= 0 is anomalous.",
    }
    object_store.put_bytes(
        f"process_runtime/{process_id}/live.json",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )


def stream_sample(
    process_id: str,
    generator: TemporalProcessGenerator,
    *,
    index: int,
    modality: str,
    anomaly: str | None,
    equipment_id: str | None,
    lot_id: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    result = simulate_temporal_sample(
        process_id,
        generator,
        modality=modality,
        anomaly=anomaly,
        equipment_id=equipment_id,
        lot_id=lot_id,
        wafer_id=f"W{index:02d}",
    )
    history.append(live_row(index, result, anomaly))
    del history[:-MAX_LIVE_HISTORY]
    write_live_snapshot(process_id, history)
    return result

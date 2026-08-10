from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - local analyzer URL
        return json.load(response)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - local analyzer URL
        destination.write_bytes(response.read())


def number(value: Any, digits: int = 3) -> float:
    return round(float(value or 0), digits)


def chamber_slug(chamber_id: str) -> str:
    match = re.search(r"(\d+)", chamber_id)
    return f"chamber-{int(match.group(1)):03d}" if match else chamber_id.lower().replace("_", "-")


def artifact_url(base_url: str, dataset_id: str, kind: str, image_id: str = "") -> str:
    query = {"detector_mode": "comparison"}
    if image_id:
        query["image_id"] = image_id
    return f"{base_url}/api/artifacts/{dataset_id}/{kind}?{urllib.parse.urlencode(query)}"


def build_payload(base_url: str, dataset_id: str, public_dir: Path) -> dict[str, Any]:
    result_url = f"{base_url}/api/results/{dataset_id}?detector_mode=comparison"
    result = fetch_json(result_url)
    ai_result = result.get("ai") or {}
    ai_chambers = {item["chamber_id"]: item for item in ai_result.get("chambers", [])}
    ai_images = {item["image_id"]: item for item in ai_result.get("images", [])}

    grid: list[dict[str, Any]] = []
    for chamber in sorted(result["chambers"], key=lambda item: item["chamber_id"]):
        ai_chamber = ai_chambers.get(chamber["chamber_id"], {})
        grid.append(
            {
                "id": chamber["chamber_id"],
                "label": chamber["chamber_id"].replace("Chamber_", "CH-"),
                "statStatus": chamber["status"],
                "statScore": number(chamber["peak_score"], 2),
                "statAnomalyCount": int(chamber["anomaly_count"]),
                "aiStatus": ai_chamber.get("status", "normal"),
                "aiScore": number(ai_chamber.get("peak_score"), 2),
                "aiAnomalyCount": int(ai_chamber.get("anomaly_count", 0)),
                "firstAnomaly": chamber.get("first_anomaly"),
                "direction": chamber.get("dominant_direction"),
                "rank": chamber.get("rank"),
            }
        )

    stat_images_by_chamber: dict[str, list[dict[str, Any]]] = {}
    for image in result["images"]:
        stat_images_by_chamber.setdefault(image["chamber_id"], []).append(image)

    anomaly_chambers = [item for item in grid if item["statStatus"] == "anomaly"]
    anomaly_chambers.sort(key=lambda item: item["rank"] or 999)
    series: dict[str, list[dict[str, Any]]] = {}
    details: dict[str, dict[str, Any]] = {}

    for chamber in anomaly_chambers:
        chamber_id = chamber["id"]
        rows = sorted(stat_images_by_chamber[chamber_id], key=lambda item: item["sequence_index"])
        points: list[dict[str, Any]] = []
        for item in rows:
            ai_item = ai_images.get(item["image_id"], {})
            points.append(
                {
                    "day": item["sequence_label"].replace("Day ", "D"),
                    "sequence": int(item["sequence_index"]),
                    "statScore": number(item["score"], 2),
                    "aiScore": number(ai_item.get("score"), 2),
                    "statStatus": item["status"],
                    "aiStatus": ai_item.get("status", "normal"),
                    "statDirection": item.get("direction"),
                    "aiDirection": ai_item.get("direction"),
                    "statArea": number(item.get("anomaly_area_ratio") * 100, 2),
                    "aiArea": number(ai_item.get("anomaly_area_ratio", 0) * 100, 2),
                    "brightnessDelta": number(item.get("brightness_delta"), 1),
                    "imageId": item["image_id"],
                }
            )
        series[chamber_id] = points

        selected = next((item for item in rows if item["status"] == "anomaly"), rows[-1])
        ai_selected = ai_images.get(selected["image_id"], {})
        slug = chamber_slug(chamber_id)
        day_slug = selected["sequence_label"].lower().replace(" ", "-")
        asset_stem = f"{slug}-{day_slug}"
        asset_names = {
            "original": f"{asset_stem}-original.png",
            "statHeatmap": f"{asset_stem}-stat-heatmap.png",
            "aiHeatmap": f"{asset_stem}-ai-heatmap.png",
        }
        download(artifact_url(base_url, dataset_id, "original", selected["image_id"]), public_dir / asset_names["original"])
        download(artifact_url(base_url, dataset_id, "heatmap", selected["image_id"]), public_dir / asset_names["statHeatmap"])
        download(artifact_url(base_url, dataset_id, "ai-heatmap", selected["image_id"]), public_dir / asset_names["aiHeatmap"])
        details[chamber_id] = {
            "imageId": selected["image_id"],
            "sequenceLabel": selected["sequence_label"],
            "direction": selected.get("direction"),
            "polarity": selected.get("polarity"),
            "brightnessDelta": number(selected.get("brightness_delta"), 1),
            "statScore": number(selected.get("score"), 2),
            "aiScore": number(ai_selected.get("score"), 2),
            "statArea": number(selected.get("anomaly_area_ratio", 0) * 100, 2),
            "aiArea": number(ai_selected.get("anomaly_area_ratio", 0) * 100, 2),
            "assets": {key: f"/wafer-vision/{value}" for key, value in asset_names.items()},
        }

    shared_assets = {
        "reference": "reference.png",
        "statAggregate": "stat-aggregate.png",
        "aiAggregate": "ai-aggregate.png",
    }
    download(artifact_url(base_url, dataset_id, "reference"), public_dir / shared_assets["reference"])
    download(artifact_url(base_url, dataset_id, "aggregate"), public_dir / shared_assets["statAggregate"])
    download(artifact_url(base_url, dataset_id, "ai-aggregate"), public_dir / shared_assets["aiAggregate"])

    model = ai_result.get("model") or {}
    return {
        "source": {
            "label": result["dataset"]["root_name"],
            "datasetId": result["dataset"]["dataset_id"],
            "images": int(result["dataset"]["valid_files"]),
            "chambers": int(result["dataset"]["chamber_count"]),
            "imageSize": result["dataset"]["image_size"],
            "warnings": int(result["dataset"]["warning_count"]),
        },
        "summary": result["summary"],
        "thresholds": {
            "statCaution": number(result["thresholds"]["caution"], 2),
            "statAnomaly": number(result["thresholds"]["anomaly"], 2),
            "aiCaution": number(ai_result["thresholds"]["caution"], 2),
            "aiAnomaly": number(ai_result["thresholds"]["anomaly"], 2),
        },
        "evaluation": {
            "stat": result.get("evaluation"),
            "ai": ai_result.get("evaluation"),
            "comparison": result.get("comparison"),
        },
        "model": {
            "architecture": model.get("architecture"),
            "backbone": model.get("backbone"),
            "trainingMode": model.get("training_mode"),
            "normalLabelled": model.get("normal_labelled_count"),
            "normalUnique": model.get("normal_unique_count"),
            "effectiveTrainingSamples": model.get("effective_training_samples"),
            "featureGrid": model.get("feature_grid"),
            "featureDimensions": model.get("feature_dimensions"),
            "retrainedForCurrentFolder": bool(ai_result.get("retrained_for_current_folder")),
        },
        "grid": grid,
        "anomalyChambers": anomaly_chambers,
        "series": series,
        "details": details,
        "assets": {key: f"/wafer-vision/{value}" for key, value in shared_assets.items()},
        "limitations": list(dict.fromkeys((result.get("limitations") or []) + (ai_result.get("limitations") or []))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build WaferGuard's static wafer vision sample payload.")
    parser.add_argument("--base-url", required=True, help="Running wafer_particle API base URL")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("frontend/src/data/waferVisionSample.json"))
    parser.add_argument("--public-dir", type=Path, default=Path("frontend/public/wafer-vision"))
    args = parser.parse_args()

    payload = build_payload(args.base_url.rstrip("/"), args.dataset_id, args.public_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "images": payload["source"]["images"],
                "chambers": payload["source"]["chambers"],
                "anomalyChambers": [item["id"] for item in payload["anomalyChambers"]],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

"""Synthetic multi-process telemetry + vision anomaly runtime with real model artifacts.

Photo/Etch/Deposition/CMP profiles share one experiment path:
normal-only training -> candidate comparison -> artifact -> Production inference
-> process_events -> existing Inspection/RCA evidence.

Generated limits/images/metrics are synthetic proxies, not Fab control limits.
"""
from __future__ import annotations

import json
import math
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import yaml
from PIL import Image
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from app.services import db, object_store, storage
from app.services.config import ROOT_DIR, ensure_runtime_dirs

CONFIG_PATH = ROOT_DIR / "configs" / "process_runtime.yaml"
MODEL_DIR = ROOT_DIR / "runtime" / "models" / "process"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    with Path(path or CONFIG_PATH).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not config.get("profiles"):
        raise ValueError("process runtime profiles are empty")
    return config


def _profile(process_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_config()
    if process_id not in config["profiles"]:
        raise KeyError(f"Unknown process profile: {process_id}")
    return config, config["profiles"][process_id]


def process_profiles() -> list[dict[str, Any]]:
    config = load_config()
    return [
        {
            "process_id": pid,
            "display_name": p["display_name"],
            "process_step": p["process_step"],
            "timeseries_tags": list(p["timeseries"]["tags"]),
            "timeseries_anomalies": list(p["timeseries"]["anomalies"]),
            "vision_defects": list(p["vision"]["defects"]),
            "data_source": "synthetic_multimodal_runtime",
        }
        for pid, p in config["profiles"].items()
    ]


def _ensure_schema() -> None:
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS process_model_registry (
                id TEXT PRIMARY KEY, process_id TEXT NOT NULL, modality TEXT NOT NULL,
                version TEXT NOT NULL, stage TEXT NOT NULL, model_name TEXT NOT NULL,
                artifact_path TEXT NOT NULL, precision REAL NOT NULL, recall REAL NOT NULL,
                f2 REAL NOT NULL, false_positive_rate REAL NOT NULL, threshold REAL NOT NULL,
                metadata_json TEXT NOT NULL, registered_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS process_runtime_samples (
                id TEXT PRIMARY KEY, process_id TEXT NOT NULL, process_step TEXT NOT NULL,
                modality TEXT NOT NULL, equipment_id TEXT NOT NULL, recipe_id TEXT,
                lot_id TEXT, wafer_id TEXT, observed_at TEXT NOT NULL, model_version TEXT NOT NULL,
                anomaly_score REAL NOT NULL, threshold REAL NOT NULL, is_anomaly INTEGER NOT NULL,
                injected_anomaly TEXT, ground_truth INTEGER NOT NULL, payload_json TEXT NOT NULL,
                image_key TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_process_model_lookup
              ON process_model_registry(process_id, modality, stage, registered_at);
            CREATE INDEX IF NOT EXISTS idx_process_runtime_lookup
              ON process_runtime_samples(process_id, modality, observed_at);
            """
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _artifact_path(process_id: str, modality: str, version: str) -> Path:
    ensure_runtime_dirs()
    path = MODEL_DIR / process_id / modality / f"{version}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_artifact(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def _next_version(process_id: str, modality: str) -> str:
    _ensure_schema()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM process_model_registry WHERE process_id = ? AND modality = ?",
            (process_id, modality),
        ).fetchone()
    return f"{process_id}-{modality}-v{int(row['c']) + 1}"


def list_models(process_id: str | None = None, modality: str | None = None) -> list[dict[str, Any]]:
    _ensure_schema()
    where, params = [], []
    if process_id:
        where.append("process_id = ?"); params.append(process_id)
    if modality:
        where.append("modality = ?"); params.append(modality)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM process_model_registry {clause} ORDER BY registered_at DESC", tuple(params)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        result.append(item)
    return result


def production_model(process_id: str, modality: str) -> dict[str, Any] | None:
    models = list_models(process_id, modality)
    return next((m for m in models if m["stage"] == "Production"), None)


def promote_model(process_id: str, modality: str, version: str) -> dict[str, Any]:
    _ensure_schema()
    with db.connect() as conn:
        target = conn.execute(
            "SELECT * FROM process_model_registry WHERE process_id = ? AND modality = ? AND version = ?",
            (process_id, modality, version),
        ).fetchone()
        if target is None:
            raise KeyError(f"Unknown model: {process_id}/{modality}/{version}")
        artifact = _resolve_artifact(str(target["artifact_path"]))
        if not artifact.is_file():
            raise FileNotFoundError(f"Model artifact missing: {artifact}")
        conn.execute(
            "UPDATE process_model_registry SET stage='Archived' WHERE process_id=? AND modality=? AND stage='Production'",
            (process_id, modality),
        )
        conn.execute("UPDATE process_model_registry SET stage='Production' WHERE id=?", (target["id"],))
    model = production_model(process_id, modality)
    if model is None:
        raise RuntimeError("Production model missing after promotion")
    return model


def _timeseries_vector(profile: dict[str, Any], rng: np.random.Generator, anomaly: str | None = None):
    tags = profile["timeseries"]["tags"]
    values = {name: float(rng.normal(spec["mean"], spec["std"])) for name, spec in tags.items()}
    related: list[str] = []
    if anomaly:
        rule = profile["timeseries"]["anomalies"].get(anomaly)
        if rule is None:
            raise KeyError(f"Unknown timeseries anomaly: {anomaly}")
        tag = rule["tag"]
        values[tag] += float(rule["shift_sigma"]) * float(tags[tag]["std"])
        related = [tag]
    names = list(tags)
    return np.asarray([values[n] for n in names], dtype=float), values, related


def _base_image(process_id: str, size: int, rng: np.random.Generator) -> np.ndarray:
    yy, xx = np.mgrid[:size, :size]
    c, radius = (size - 1) / 2, size * 0.46
    rr = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / radius
    mask = rr <= 1
    base = 0.48 + rng.normal(0, 0.018, (size, size))
    if process_id == "photo": base += 0.025 * (np.sin(xx / 3.2) + np.sin(yy / 3.2))
    elif process_id == "etch": base += 0.025 * rr
    elif process_id == "deposition": base += 0.015 * np.cos(rr * math.pi)
    elif process_id == "cmp": base += 0.010 * np.sin((xx + yy) / 6)
    image = np.zeros((size, size), dtype=float)
    image[mask] = np.clip(base[mask], 0.05, 0.95)
    return image


def _inject_defect(image: np.ndarray, defect: str, rng: np.random.Generator) -> np.ndarray:
    arr = image.copy(); size = arr.shape[0]
    yy, xx = np.mgrid[:size, :size]; c = (size - 1) / 2
    rr = np.sqrt((xx - c) ** 2 + (yy - c) ** 2); wafer = rr <= size * 0.46
    if defect in {"bridge", "scratch"}:
        slope = 0.45 if defect == "bridge" else -0.65
        line = np.abs(yy - (slope * (xx - c) + c)) <= (2 if defect == "bridge" else 1)
        arr[line & wafer] = 0.95
    elif defect == "missing_pattern":
        arr[int(size*.35):int(size*.62), int(size*.38):int(size*.58)] *= 0.25
    elif defect == "misalignment":
        arr[(np.abs((xx - yy) - size*.08) < 2) & wafer] = 0.9
    elif defect in {"residue", "particle", "pinhole"}:
        count = 30 if defect == "residue" else 18 if defect == "particle" else 24
        value, radius = (0.05, 1) if defect == "pinhole" else (0.98, 2)
        for _ in range(count):
            x, y = int(rng.integers(size*.2, size*.8)), int(rng.integers(size*.2, size*.8))
            spot = (xx-x)**2 + (yy-y)**2 <= radius**2
            arr[spot & wafer] = value
    elif defect in {"non_uniformity", "thickness_non_uniformity"}:
        grad = np.clip((xx - size*.25) / (size*.6), 0, 1)
        arr[wafer] = np.clip(arr[wafer] + 0.22 * grad[wafer], 0, 1)
    elif defect == "over_etch": arr[(rr < size*.28) & wafer] *= 0.55
    elif defect == "under_etch":
        region = (rr < size*.30) & wafer; arr[region] = np.clip(arr[region] + 0.25, 0, 1)
    elif defect == "dishing": arr[(rr < size*.20) & wafer] *= 0.45
    elif defect == "erosion": arr[(rr > size*.26) & (rr < size*.38) & wafer] *= 0.55
    else: raise KeyError(f"Unknown vision defect: {defect}")
    return np.clip(arr, 0, 1)


def _vision_image(process_id: str, profile: dict[str, Any], rng: np.random.Generator, size: int, defect: str | None = None):
    image = _base_image(process_id, size, rng); related: list[str] = []
    if defect:
        if defect not in profile["vision"]["defects"]:
            raise KeyError(f"Unknown vision defect: {defect}")
        image = _inject_defect(image, defect, rng)
        related = list(profile["vision"].get("root_cause_tags", {}).get(defect, []))
    return image, related


def vision_features(image: np.ndarray) -> np.ndarray:
    size = image.shape[0]; yy, xx = np.mgrid[:size, :size]; c = (size - 1) / 2
    rr = np.sqrt((xx-c)**2 + (yy-c)**2) / (size*.46); mask = rr <= 1
    center, mid, edge = mask & (rr <= .35), mask & (rr > .35) & (rr <= .70), mask & (rr > .70)
    values = image[mask]
    left, right = image[:, :size//2], np.fliplr(image[:, -size//2:])
    return np.asarray([
        values.mean(), values.std(), image[center].mean(), image[mid].mean(), image[edge].mean(),
        np.abs(np.diff(image, axis=1)).mean(), np.abs(np.diff(image, axis=0)).mean(),
        np.mean(values >= .80), np.mean(values <= .20), np.mean(np.abs(left[:, :right.shape[1]] - right)),
    ], dtype=float)


def _candidate_models(seed: int) -> dict[str, Any]:
    return {
        "isolation_forest": Pipeline([
            ("scale", StandardScaler()),
            ("model", IsolationForest(n_estimators=220, contamination="auto", random_state=seed, n_jobs=1)),
        ]),
        "one_class_svm": Pipeline([
            ("scale", StandardScaler()),
            ("model", OneClassSVM(kernel="rbf", gamma="scale", nu=.05)),
        ]),
    }


def _scores(model: Any, matrix: np.ndarray) -> np.ndarray:
    return -np.asarray(model.decision_function(matrix), dtype=float).reshape(-1)


def _metrics(labels: np.ndarray, predicted: np.ndarray, beta: float = 2.0) -> dict[str, float]:
    p, r, _, _ = precision_recall_fscore_support(labels, predicted, average="binary", zero_division=0)
    b2 = beta * beta; denom = b2*p + r; f = (1+b2)*p*r/denom if denom else 0.0
    normal = labels == 0; fp = float(np.mean(predicted[normal] == 1)) if np.any(normal) else 0.0
    return {"precision": float(p), "recall": float(r), "f2": float(f), "false_positive_rate": fp}


def _best_threshold(scores: np.ndarray, labels: np.ndarray, beta: float):
    best_t, best = float(np.median(scores)), {"precision":0.0,"recall":0.0,"f2":-1.0,"false_positive_rate":1.0}
    for threshold in np.unique(np.quantile(scores, np.linspace(.05, .995, 160))):
        current = _metrics(labels, (scores >= threshold).astype(int), beta)
        if (current["f2"], -current["false_positive_rate"], current["precision"]) > (best["f2"], -best["false_positive_rate"], best["precision"]):
            best_t, best = float(threshold), current
    return best_t, best


def _matrices(process_id: str, modality: str, seed: int):
    config, profile = _profile(process_id); rt = config.get("runtime", {}); rng = np.random.default_rng(seed)
    train_n, normal_n, anomaly_n = int(rt.get("bootstrap_normal_samples",240)), int(rt.get("validation_normal_samples",120)), int(rt.get("validation_anomaly_samples",100))
    train, val, labels = [], [], []
    if modality == "timeseries":
        names = list(profile["timeseries"]["tags"]); anomalies = list(profile["timeseries"]["anomalies"])
        train = [_timeseries_vector(profile, rng)[0] for _ in range(train_n)]
        val = [_timeseries_vector(profile, rng)[0] for _ in range(normal_n)]; labels = [0]*normal_n
        for i in range(anomaly_n): val.append(_timeseries_vector(profile, rng, anomalies[i % len(anomalies)])[0]); labels.append(1)
    elif modality == "vision":
        names = ["mean","std","center_mean","mid_mean","edge_mean","grad_x","grad_y","high_ratio","low_ratio","symmetry_error"]
        size = int(rt.get("image_size",96)); defects = list(profile["vision"]["defects"])
        train = [vision_features(_vision_image(process_id, profile, rng, size)[0]) for _ in range(train_n)]
        val = [vision_features(_vision_image(process_id, profile, rng, size)[0]) for _ in range(normal_n)]; labels = [0]*normal_n
        for i in range(anomaly_n): val.append(vision_features(_vision_image(process_id, profile, rng, size, defects[i % len(defects)])[0])); labels.append(1)
    else: raise ValueError("modality must be timeseries or vision")
    return np.vstack(train), np.vstack(val), np.asarray(labels, dtype=int), names


def evaluate_candidates(process_id: str, modality: str, seed: int | None = None) -> dict[str, Any]:
    config, _ = _profile(process_id); rt = config.get("runtime", {}); seed = int(seed if seed is not None else rt.get("seed",42)); beta = float(rt.get("fbeta",2.0))
    train_x, val_x, labels, names = _matrices(process_id, modality, seed); candidates = []
    for name, model in _candidate_models(seed).items():
        model.fit(train_x); threshold, metrics = _best_threshold(_scores(model, val_x), labels, beta)
        candidates.append({"name":name,"model":model,"threshold":threshold,**metrics})
    candidates.sort(key=lambda x:(x["f2"],-x["false_positive_rate"],x["precision"]), reverse=True)
    return {"winner":candidates[0],"candidates":[{k:v for k,v in c.items() if k!="model"} for c in candidates],"feature_names":names,"seed":seed,"train_rows":len(train_x),"validation_rows":len(val_x)}


def train_model(process_id: str, modality: str, *, stage: str = "Staging", seed: int | None = None) -> dict[str, Any]:
    if stage not in {"Production","Staging"}: raise ValueError("stage must be Production or Staging")
    result = evaluate_candidates(process_id, modality, seed); winner = result["winner"]; version = _next_version(process_id, modality)
    path = _artifact_path(process_id, modality, version)
    bundle = {"process_id":process_id,"modality":modality,"version":version,"model_name":winner["name"],"model":winner["model"],"threshold":winner["threshold"],"feature_names":result["feature_names"],"candidate_metrics":result["candidates"]}
    joblib.dump(bundle, path)
    if stage == "Production":
        with db.connect() as conn:
            conn.execute("UPDATE process_model_registry SET stage='Archived' WHERE process_id=? AND modality=? AND stage='Production'", (process_id, modality))
    record = {
        "id":str(uuid.uuid4()),"process_id":process_id,"modality":modality,"version":version,"stage":stage,"model_name":winner["name"],
        "artifact_path":str(path.relative_to(ROOT_DIR)),"precision":winner["precision"],"recall":winner["recall"],"f2":winner["f2"],"false_positive_rate":winner["false_positive_rate"],"threshold":winner["threshold"],
        "metadata_json":_json({"candidate_metrics":result["candidates"],"feature_names":result["feature_names"],"train_rows":result["train_rows"],"validation_rows":result["validation_rows"],"data_source":"synthetic_proxy","note":"Synthetic validation only; not Fab-calibrated performance."}),"registered_at":utc_now(),
    }
    columns = list(record)
    with db.connect() as conn:
        conn.execute("INSERT INTO process_model_registry ("+", ".join(columns)+") VALUES ("+", ".join(["?"]*len(columns))+")", tuple(record[c] for c in columns))
    return {**record,"metadata":json.loads(record["metadata_json"])}


def ensure_production_model(process_id: str, modality: str) -> dict[str, Any]:
    return production_model(process_id, modality) or train_model(process_id, modality, stage="Production")


def _load_bundle(record: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_artifact(record["artifact_path"])
    if not path.is_file(): raise FileNotFoundError(f"Model artifact missing: {path}")
    bundle = joblib.load(path)
    if bundle.get("version") != record["version"]: raise ValueError("Registry/artifact version mismatch")
    return bundle


def _save_sample(record: dict[str, Any]) -> None:
    columns = list(record)
    with db.connect() as conn:
        conn.execute("INSERT INTO process_runtime_samples ("+", ".join(columns)+") VALUES ("+", ".join(["?"]*len(columns))+")", tuple(record[c] for c in columns))


def _project_event(record: dict[str, Any], related_tags: list[str]):
    if not record["is_anomaly"]: return None
    injected = record.get("injected_anomaly") or "model_anomaly"; margin = record["anomaly_score"] - record["threshold"]
    return storage.insert_process_event({
        "id":f"PROC-MM-{record['id']}","process_step":record["process_step"],"equipment_id":record["equipment_id"],"recipe_id":record["recipe_id"],"lot_id":record["lot_id"],"wafer_id":record["wafer_id"],"observed_at":record["observed_at"],
        "event_type":f"{record['modality']}_{injected}","severity":"critical" if margin >= max(.25, abs(record["threshold"])*.5) else "warning","source":"process_multimodal_synthetic_runtime",
        "metadata":{"modality":record["modality"],"model_version":record["model_version"],"anomaly_score":record["anomaly_score"],"threshold":record["threshold"],"ground_truth":bool(record["ground_truth"]),"injected_anomaly":record.get("injected_anomaly"),"related_tags":related_tags,"image_key":record.get("image_key"),"synthetic":True,"interpretation":"Synthetic anomaly evidence; temporal association is not causality."},
    })


def simulate_sample(process_id: str, *, modality: str = "both", anomaly: str | None = None, equipment_id: str | None = None, lot_id: str = "LOT-MM-DEMO-001", wafer_id: str = "W01", observed_at: str | None = None, seed: int | None = None) -> dict[str, Any]:
    _ensure_schema(); config, profile = _profile(process_id); rt = config.get("runtime", {}); resolved_seed = int(seed if seed is not None else rt.get("seed",42))
    if seed is None: resolved_seed += random.SystemRandom().randint(0,10_000_000)
    rng = np.random.default_rng(resolved_seed); timestamp = observed_at or utc_now(); equipment = equipment_id or f"{profile['equipment_prefix']}-01"; requested = ["timeseries","vision"] if modality == "both" else [modality]; results = {}
    for current in requested:
        model = ensure_production_model(process_id, current); bundle = _load_bundle(model); injected = anomaly; image_key = None; related = []
        if current == "timeseries":
            applied = injected if injected in profile["timeseries"]["anomalies"] else None
            vector, payload, related = _timeseries_vector(profile, rng, applied)
            if injected and applied is None and modality != "both": raise KeyError(f"Unknown timeseries anomaly: {injected}")
        elif current == "vision":
            applied = injected if injected in profile["vision"]["defects"] else None
            image, related = _vision_image(process_id, profile, rng, int(rt.get("image_size",96)), applied); vector = vision_features(image)
            payload = {name:float(value) for name,value in zip(bundle["feature_names"],vector)}
            image_key = object_store.save_image(Image.fromarray(np.uint8(np.clip(image*255,0,255)), mode="L"), f"process_runtime/{process_id}/{wafer_id}-{uuid.uuid4().hex[:10]}.png")
            if injected and applied is None and modality != "both": raise KeyError(f"Unknown vision defect: {injected}")
        else: raise ValueError("modality must be timeseries, vision, or both")
        score = float(_scores(bundle["model"], vector.reshape(1,-1))[0]); threshold = float(bundle["threshold"]); flag = score >= threshold
        record = {"id":f"PMM-{uuid.uuid4().hex}","process_id":process_id,"process_step":profile["process_step"],"modality":current,"equipment_id":equipment,"recipe_id":profile["recipe_id"],"lot_id":lot_id,"wafer_id":wafer_id,"observed_at":timestamp,"model_version":model["version"],"anomaly_score":score,"threshold":threshold,"is_anomaly":int(flag),"injected_anomaly":applied,"ground_truth":int(bool(applied)),"payload_json":_json(payload),"image_key":image_key,"created_at":utc_now()}
        _save_sample(record); event = _project_event(record, related)
        row = {**record,"is_anomaly":flag,"ground_truth":bool(applied),"payload":payload,"related_tags":related,"event":event}; row.pop("payload_json",None)
        if image_key: row["image_url"] = object_store.presign(image_key)
        results[current] = row
    return {"process_id":process_id,"process_step":profile["process_step"],"equipment_id":equipment,"lot_id":lot_id,"wafer_id":wafer_id,"observed_at":timestamp,"results":results,"data_source":"synthetic_multimodal_runtime","disclaimer":"Synthetic/proxy data and metrics; not Fab control limits."}


def runtime_metrics(process_id: str | None = None, modality: str | None = None, limit: int = 2000) -> dict[str, Any]:
    _ensure_schema(); where, params = [], []
    if process_id: where.append("process_id = ?"); params.append(process_id)
    if modality: where.append("modality = ?"); params.append(modality)
    clause = f"WHERE {' AND '.join(where)}" if where else ""; params.append(max(1,min(int(limit),10000)))
    with db.connect() as conn:
        rows = conn.execute(f"SELECT ground_truth,is_anomaly FROM process_runtime_samples {clause} ORDER BY observed_at DESC LIMIT ?", tuple(params)).fetchall()
    if not rows: return {"rows":0,"precision":None,"recall":None,"f2":None,"false_positive_rate":None}
    labels = np.asarray([int(r["ground_truth"]) for r in rows]); predicted = np.asarray([int(r["is_anomaly"]) for r in rows])
    return {"rows":len(rows),**_metrics(labels,predicted),"scope":"synthetic runtime ground truth"}

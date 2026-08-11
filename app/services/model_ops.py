"""Operational metrics computed from persisted Chamber production predictions.

Training holdout metrics answer whether a model fitted well before deployment.
This module answers a different question: how well is the active model matching
Actual Resistance on rows it has seen in operation?
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.services import db


def summarize_prediction_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute overall and grouped MAE/RMSE from joined runtime predictions."""

    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        if not group:
            return {"rows": 0, "mae": None, "rmse": None, "anomaly_rate": None}
        errors = [abs(float(row.get("residual") or 0.0)) for row in group]
        squared = [float(row.get("residual") or 0.0) ** 2 for row in group]
        return {
            "rows": len(group),
            "mae": round(sum(errors) / len(errors), 6),
            "rmse": round(math.sqrt(sum(squared) / len(squared)), 6),
            "anomaly_rate": round(
                sum(1 for row in group if bool(row.get("is_anomaly"))) / len(group), 6
            ),
        }

    grouped: dict[str, defaultdict[str, list[dict[str, Any]]]] = {
        "equipment": defaultdict(list),
        "recipe": defaultdict(list),
        "lot": defaultdict(list),
        "model": defaultdict(list),
    }
    for row in rows:
        grouped["equipment"][str(row.get("equipment_id") or "UNKNOWN")].append(row)
        grouped["recipe"][str(row.get("recipe_id") or "UNKNOWN")].append(row)
        grouped["lot"][str(row.get("lot_id") or "UNASSIGNED")].append(row)
        grouped["model"][str(row.get("model_version") or "UNKNOWN")].append(row)

    return {
        "overall": summarize(rows),
        "by_equipment": {key: summarize(value) for key, value in grouped["equipment"].items()},
        "by_recipe": {key: summarize(value) for key, value in grouped["recipe"].items()},
        "by_lot": {key: summarize(value) for key, value in grouped["lot"].items()},
        "by_model": {key: summarize(value) for key, value in grouped["model"].items()},
        "metric_scope": "runtime Actual vs Expected Resistance; not a Fab yield/quality claim",
    }


def chamber_operational_metrics(*, limit: int = 1000, model_version: str | None = None) -> dict[str, Any]:
    """Read recent prediction rows and report post-deployment error metrics."""
    safe_limit = max(1, min(int(limit), 10000))
    where = "WHERE p.model_version = ?" if model_version else ""
    params: tuple[object, ...] = (model_version, safe_limit) if model_version else (safe_limit,)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.model_version, p.residual, p.is_anomaly, p.observed_at, "
            "t.equipment_id, t.recipe_id, t.lot_id "
            "FROM chamber_predictions p "
            "JOIN chamber_telemetry t ON t.id = p.telemetry_id "
            f"{where} ORDER BY p.observed_at DESC LIMIT ?",
            params,
        ).fetchall()
    return summarize_prediction_rows([dict(row) for row in reversed(rows)])

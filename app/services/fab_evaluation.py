"""Evaluation-only RCA comparison against synthetic simulation truth.

Inference, fusion, and RCA intentionally do not import this module.  Keeping
the truth repository behind this explicit service makes ground-truth access an
offline/debug concern rather than part of the runtime decision path.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.services import fab_storage
from app.services.fab_rca import evaluate_rca


TruthReader = Callable[[str], Sequence[Mapping[str, Any]]]


def _default_truth_reader(process_run_id: str) -> Sequence[Mapping[str, Any]]:
    return fab_storage.list_simulation_faults(process_run_id, debug=True)


def evaluate_process_run(
    process_run_id: str,
    *,
    truth_reader: TruthReader | None = None,
) -> dict[str, Any]:
    """Evaluate persisted RCA after inference has completed.

    ``truth_reader`` is injectable so tests can prove that the inference/RCA
    path does not need access to simulator truth.
    """
    rca = fab_storage.rca_for_run(process_run_id)
    if rca is None:
        raise ValueError(f"No persisted RCA result for process run: {process_run_id}")

    rows = list((truth_reader or _default_truth_reader)(process_run_id))
    if not rows:
        return {
            "process_run_id": process_run_id,
            "evaluated": False,
            "reason": "no_simulation_fault",
            "evaluation_only": True,
        }

    candidates = rca.get("candidates") or rca.get("candidate_causes") or []
    normalized_rca = {
        **rca,
        "process_run_id": process_run_id,
        "candidates": candidates,
    }
    evaluations = [
        evaluate_rca(
            normalized_rca,
            str(row.get("fault_type") or row.get("fault_id")),
        )
        for row in rows
    ]
    return {
        "process_run_id": process_run_id,
        "evaluated": True,
        "top_1": any(item["top_1"] for item in evaluations),
        "top_3": any(item["top_3"] for item in evaluations),
        "results": evaluations,
        "evaluation_only": True,
    }

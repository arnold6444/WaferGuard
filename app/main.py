from __future__ import annotations

import json
import logging
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.services.config import IMAGE_BACKEND, OUTPUT_DIR, ensure_runtime_dirs
from app.services.automation import automation_status, run_automation_tick
from app.services.copilot import ops_copilot_summary
from app.services.data_registry import metrology_threshold_basis, proxy_dataset_manifest
from app.services.demo import seed_demo_data
from app.services.evaluation import wm811k_evaluation_report
from app.services.handoff import generate_handoff_report, get_latest_handoff_report
from app.services.mlops import pipeline_state, promote_latest, rollback, simulate_drift, simulate_retraining
from app.services.pipeline import run_inspection
from app.services.process_ops import fab_overview, process_profiles, quality_lot, quality_lots
from app.services.rag import browse_cases, index_stats
from app.services.rag_eval import rag_evaluation_set
from app.services.defect_chat import chat_about_inspection, stream_chat_about_inspection
from app.services.schemas import (
    AgentStreamRequest,
    ApprovalResolveRequest,
    AutomationTickRequest,
    ChamberRetrainRequest,
    DemoSeedRequest,
    DriftRequest,
    HandoffReportRequest,
    InspectRequest,
    InspectionChatRequest,
    MlopsAgentRequest,
    MlopsChatRequest,
    PromoteRequest,
    RetrainRequest,
    ReviewRequest,
    RollbackRequest,
)
from app.services.storage import (
    browse_table,
    db_overview,
    get_agent_trace_for_inspection,
    get_inspection,
    init_db,
    insert_alert,
    list_inspections,
    list_lots,
    list_pending_approvals,
    list_process_events,
    metrics,
    record_review,
    resolve_approval,
)
from app.services import chamber_storage, db, fab_analysis, fab_storage
from app.services.chamber_runtime import chamber_runtime


FAB_SYNTHETIC_DEBUG = os.environ.get("FAB_SYNTHETIC_DEBUG", "0").strip().lower() in {
    "1", "true", "yes", "on",
}

ensure_runtime_dirs()
try:
    DATABASE_STARTUP_STATUS = db.validate_connection()
    init_db()
    DATABASE_STARTUP_STATUS = {**DATABASE_STARTUP_STATUS, "schema": "ready"}
except (db.DatabaseConfigurationError, db.DatabaseConnectionError):
    logging.getLogger(__name__).critical("Database startup validation failed", exc_info=True)
    raise
except Exception as exc:  # noqa: BLE001
    selected = os.environ.get("STORAGE_BACKEND", "<missing>")
    raise RuntimeError(
        f"Database schema initialization failed for STORAGE_BACKEND={selected!r}: {exc}"
    ) from exc


def _seed_rag_index_background() -> None:
    """Seed the vector RAG corpus once at startup, off the request path.

    Embedding the corpus needs network calls, so it runs in a daemon thread —
    the app boots immediately and retrieval falls back to the deterministic
    library until the index is ready.
    """
    try:
        from app.services.rag import ensure_rag_index  # noqa: PLC0415

        result = ensure_rag_index()
        logging.getLogger(__name__).info("RAG index seed: %s", result)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("RAG index seed failed: %s", exc)


threading.Thread(target=_seed_rag_index_background, daemon=True).start()

app = FastAPI(
    title="WaferGuard Agent Simulation API",
    version="0.1.0",
    description="Local MVP for simulated wafer/process anomaly response with Agent decisions, RAG evidence, action cards, and handoff reports.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local images are served from disk; in S3 mode they're fetched via presigned URLs.
if IMAGE_BACKEND == "local":
    app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "waferguard-api",
        "database": DATABASE_STARTUP_STATUS,
        "fab": fab_storage.fab_metrics(),
    }


@app.post("/api/v1/inspect")
def inspect(request: InspectRequest) -> dict[str, object]:
    return run_inspection(request)


@app.get("/api/v1/inspections")
def inspections(limit: int = 20) -> list[dict[str, object]]:
    return list_inspections(limit=limit)


@app.get("/api/v1/inspect/{inspection_id}")
def inspection_detail(inspection_id: str) -> dict[str, object]:
    record = get_inspection(inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    return record


@app.get("/api/v1/inspect/{inspection_id}/trace")
def inspection_trace(inspection_id: str) -> dict[str, object]:
    """Latest Agent trace (final action, tool calls, mode) for an inspection."""
    trace = get_agent_trace_for_inspection(inspection_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No agent trace for this inspection")
    return trace


@app.post("/api/v1/inspect/{inspection_id}/chat")
def inspection_chat(inspection_id: str, request: InspectionChatRequest) -> dict[str, object]:
    """Evidence-grounded LLM chat about one inspection."""
    record = get_inspection(inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    trace = get_agent_trace_for_inspection(inspection_id)
    return chat_about_inspection(
        record, request.message, request.history, trace,
        use_llm=request.use_llm, extra_context=request.extra_context,
    )


@app.post("/api/v1/inspect/{inspection_id}/chat/stream")
def inspection_chat_stream(inspection_id: str, request: InspectionChatRequest) -> StreamingResponse:
    """Agentic, streaming chat: emits tool_call / tool_result / token / done events
    as Server-Sent Events so the UI can show tool activity and stream the answer."""
    record = get_inspection(inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    trace = get_agent_trace_for_inspection(inspection_id)

    def event_source():
        try:
            for event in stream_chat_about_inspection(
                record, request.message, request.history, trace,
                use_llm=request.use_llm, extra_context=request.extra_context,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "done", "reply": f"오류: 응답 생성 실패 ({exc})", "agent_mode": "error", "tool_calls": []}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/review/{inspection_id}")
def review(inspection_id: str, request: ReviewRequest) -> dict[str, object]:
    record = record_review(inspection_id, request.decision, request.reviewer, request.note)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    # RAG learning loop (Gap 3): an engineer-confirmed decision becomes retrievable
    # knowledge, so the corpus gets smarter as the line operates.
    record["knowledge_saved"] = _ingest_reviewed_case(record, request)
    return record


def _ingest_reviewed_case(record: dict[str, object], request: ReviewRequest) -> dict[str, object] | None:
    # Only confirmed outcomes become retrievable knowledge. "needs_review" is an
    # open state, not a resolved decision — writing it back would teach the RAG
    # corpus from cases the engineer hasn't actually concluded.
    if request.decision not in ("approved", "false_alarm"):
        return {"skipped": True, "reason": "decision_not_confirmed"}
    try:
        from app.services.tools import save_case_to_knowledge  # noqa: PLC0415

        defect = record.get("defect_type", "결함")
        equip = record.get("equipment_id", "?")
        note = (request.note or "").strip()
        decision_label = {
            "approved": "조치 완료",
            "needs_review": "추가 리뷰 필요",
            "false_alarm": "오탐 처리",
        }.get(request.decision, request.decision)
        title = f"{defect} 대응 — {equip}"
        summary = (
            f"{record.get('risk_level')} 리스크(score {float(record.get('risk_score') or 0):.2f}) "
            f"{defect} 검사를 {decision_label}로 처리."
        )
        action = note or decision_label
        return save_case_to_knowledge(
            title=title,
            summary=summary,
            action=action,
            defect_type=str(defect),
            metadata={
                "inspection_id": record.get("id"),
                "decision": request.decision,
                "reviewer": request.reviewer,
                "equipment_id": equip,
            },
        )
    except Exception:  # noqa: BLE001
        return None


@app.post("/api/v1/demo/seed")
def demo_seed(request: DemoSeedRequest) -> dict[str, object]:
    return seed_demo_data(request)


@app.get("/api/v1/metrics")
def service_metrics() -> dict[str, object]:
    return metrics()


@app.get("/api/v1/evaluation/wm811k")
def wm811k_evaluation() -> dict[str, object]:
    return wm811k_evaluation_report()


@app.get("/api/v1/rag/evaluation")
def rag_evaluation() -> dict[str, object]:
    return rag_evaluation_set()


@app.get("/api/v1/rag/index")
def rag_index() -> dict[str, object]:
    """Live RAG index stats (indexed count, retrieval mode, per-type counts)."""
    return index_stats()


@app.get("/api/v1/rag/search")
def rag_search(
    defect_type: str = "Edge-Ring",
    q: str = "",
    line_id: str = "LINE-7",
    k: int = 6,
) -> dict[str, object]:
    """Top-k similar past cases for a defect type, optionally refined by keyword."""
    return browse_cases(defect_type, query=q, line_id=line_id, k=k)


@app.get("/api/v1/db/overview")
def database_overview() -> dict[str, object]:
    """Tables, row counts, and columns of the workflow SQLite DB."""
    return db_overview()


@app.get("/api/v1/db/tables/{table_name}")
def database_table(table_name: str, limit: int = 50, offset: int = 0) -> dict[str, object]:
    """Paginated rows from one workflow table (read-only)."""
    result = browse_table(table_name, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown table")
    return result


@app.get("/api/v1/fab/overview")
def fab_status_overview() -> dict[str, object]:
    return fab_storage.enrich_overview(fab_overview())


@app.get("/api/v1/fab/equipment")
def fab_equipment(process_id: str | None = None) -> list[dict[str, object]]:
    return fab_storage.equipment_overview(process_id)


@app.get("/api/v1/fab/process-definitions")
def fab_process_definitions(process_id: str | None = None) -> list[dict[str, object]]:
    """Vendor-neutral synthetic equipment, sensor, and post-process measurement catalog."""
    return fab_storage.process_definitions(process_id)


@app.get("/api/v1/fab/wafers/{wafer_id}/trace")
def fab_wafer_trace(wafer_id: str, telemetry_limit_per_run: int = 500) -> dict[str, object]:
    result = fab_storage.wafer_trace(
        wafer_id,
        telemetry_limit_per_run=max(1, min(telemetry_limit_per_run, 2000)),
        include_debug=FAB_SYNTHETIC_DEBUG,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Wafer not found")
    return result


@app.get("/api/v1/fab/process-runs/{process_run_id}")
def fab_process_run(process_run_id: str, telemetry_limit: int = 2000) -> dict[str, object]:
    result = fab_storage.process_run_detail(
        process_run_id,
        telemetry_limit=max(1, min(telemetry_limit, 10_000)),
        include_debug=FAB_SYNTHETIC_DEBUG,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Process run not found")
    return result


@app.get("/api/v1/process/{process_id}/live")
def fab_process_live(
    process_id: str,
    equipment_id: str | None = None,
    unit_id: str | None = None,
    recipe_id: str | None = None,
    limit: int = 200,
) -> dict[str, object]:
    return fab_storage.process_live(
        process_id,
        equipment_id=equipment_id,
        unit_id=unit_id,
        recipe_id=recipe_id,
        limit=limit,
    )


@app.get("/api/v1/fab/rca/{process_run_id}")
def fab_rca(process_run_id: str) -> dict[str, object]:
    result = fab_storage.rca_for_run(process_run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="RCA result not found")
    return result


@app.get("/api/v1/fab/metrics")
def fab_runtime_metrics() -> dict[str, object]:
    return fab_storage.fab_metrics()


@app.get("/api/v1/fab/analysis/latest")
def fab_latest_analysis() -> dict[str, object]:
    """Latest local notebook/CLI summary; raw training data is never exposed."""
    try:
        return fab_analysis.latest_dashboard_summary()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/process/profiles")
def process_profile_list() -> list[dict[str, object]]:
    return process_profiles()


@app.get("/api/v1/process/events")
def process_event_list(
    process_step: str | None = None,
    equipment_id: str | None = None,
    lot_id: str | None = None,
    wafer_id: str | None = None,
    recipe_id: str | None = None,
    observed_after: str | None = None,
    observed_before: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    return list_process_events(
        process_step=process_step,
        equipment_ids=[equipment_id] if equipment_id else None,
        lot_id=lot_id,
        wafer_id=wafer_id,
        recipe_id=recipe_id,
        observed_after=observed_after,
        observed_before=observed_before,
        limit=limit,
    )


@app.get("/api/v1/lots")
def lot_list(status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
    return fab_storage.enrich_lots(list_lots(status=status, limit=limit))


@app.get("/api/v1/quality/lots")
def quality_lot_list(limit: int = 500) -> dict[str, object]:
    return quality_lots(limit=limit)


@app.get("/api/v1/quality/lots/{lot_id}")
def quality_lot_detail(lot_id: str) -> dict[str, object]:
    result = quality_lot(lot_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    fab_trace = fab_storage.lot_trace(lot_id)
    if fab_trace is not None:
        result["fab_trace"] = fab_trace
    return result


@app.get("/api/v1/chamber/status")
def chamber_status() -> dict[str, object]:
    return chamber_runtime.status()


@app.get("/api/v1/chamber/equipment")
def chamber_equipment() -> list[dict[str, object]]:
    return chamber_storage.equipment_summary()


@app.get("/api/v1/chamber/telemetry")
def chamber_telemetry(equipment_id: str | None = None, limit: int = 200) -> list[dict[str, object]]:
    return chamber_storage.telemetry_rows(equipment_id=equipment_id, limit=limit)


@app.get("/api/v1/chamber/predictions")
def chamber_predictions(equipment_id: str | None = None, limit: int = 200) -> list[dict[str, object]]:
    return chamber_storage.prediction_rows(equipment_id=equipment_id, limit=limit)


@app.get("/api/v1/chamber/detections")
def chamber_detections(prediction_id: str | None = None, limit: int = 200) -> list[dict[str, object]]:
    return chamber_storage.detection_rows(prediction_id=prediction_id, limit=limit)


@app.get("/api/v1/chamber/models")
def chamber_models() -> list[dict[str, object]]:
    return chamber_storage.list_models()


@app.post("/api/v1/chamber/retrain")
def chamber_retrain(request: ChamberRetrainRequest) -> dict[str, object]:
    result = chamber_runtime.retrain(force=request.force, trigger_type=request.trigger_type)
    if not result["accepted"]:
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/api/v1/chamber/models/{version}/promote")
def chamber_promote(version: str) -> dict[str, object]:
    try:
        return chamber_runtime.promote(version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/automation/status")
def monitor_status(line_id: str = "LINE-7") -> dict[str, object]:
    return automation_status(line_id=line_id)


@app.post("/api/v1/automation/tick")
def monitor_tick(request: AutomationTickRequest) -> dict[str, object]:
    return run_automation_tick(request)


@app.get("/api/v1/proxy-datasets")
def proxy_datasets() -> dict[str, object]:
    return proxy_dataset_manifest()


@app.get("/api/v1/metrology/thresholds")
def metrology_thresholds() -> dict[str, object]:
    return metrology_threshold_basis()


@app.get("/api/v1/handoff/latest")
def latest_handoff() -> dict[str, object]:
    report = get_latest_handoff_report()
    if report is None:
        raise HTTPException(status_code=404, detail="No handoff report yet")
    return report


@app.post("/api/v1/handoff/report")
def handoff_report(request: HandoffReportRequest) -> dict[str, object]:
    return generate_handoff_report(request)


@app.get("/api/v1/copilot/ops")
def ops_copilot(line_id: str = "ALL") -> dict[str, object]:
    return ops_copilot_summary(line_id=line_id)


@app.get("/api/v1/mlops/state")
def mlops_state() -> dict[str, object]:
    return pipeline_state()


@app.get("/api/v1/mlops/drift/history")
def mlops_drift_history(limit: int = 30) -> dict[str, object]:
    """Recent drift-score history + current Production model, for the drift chart."""
    from app.services.config import DRIFT_THRESHOLD  # noqa: PLC0415
    from app.services.storage import list_drift_events, production_model  # noqa: PLC0415

    events = list(reversed(list_drift_events(limit=limit)))  # oldest → newest for plotting
    return {
        "events": events,
        "threshold": DRIFT_THRESHOLD,
        "production_model": production_model(),
    }


@app.post("/api/v1/mlops/agent/run")
def mlops_agent_run(request: MlopsAgentRequest) -> dict[str, object]:
    """Run the fleet-level MLOps agent: it inspects model performance + drift via
    tools and decides whether to recommend retraining (approval-gated)."""
    try:
        from app.services.agent import run_mlops_agent  # noqa: PLC0415

        return run_mlops_agent(line_id=request.line_id, use_llm=request.use_llm, autonomy=request.autonomy)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"MLOps agent run failed: {exc}") from exc


@app.get("/api/v1/mlops/agent/traces")
def mlops_agent_traces(limit: int = 20) -> list[dict[str, object]]:
    """Recent MLOps-agent runs for the monitoring log — includes delegation-triggered
    runs (when the inspection agent escalated), which don't originate in the UI."""
    from app.services.storage import list_mlops_agent_traces  # noqa: PLC0415

    return list_mlops_agent_traces(limit=limit)


@app.post("/api/v1/mlops/agent/run/stream")
def mlops_agent_run_stream(request: MlopsAgentRequest) -> StreamingResponse:
    """Streaming MLOps agent run — emits tool_call / tool_result / token / done
    as Server-Sent Events so the UI shows the agent's reasoning live."""
    from app.services.agent import stream_mlops_agent  # noqa: PLC0415

    def event_source():
        try:
            for event in stream_mlops_agent(line_id=request.line_id, use_llm=request.use_llm, autonomy=request.autonomy):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "done", "final_action": f"오류: 분석 실행 실패 ({exc})", "agent_mode": "error", "tool_calls": []}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/mlops/agent/chat/stream")
def mlops_agent_chat_stream(request: MlopsChatRequest) -> StreamingResponse:
    """Streaming chat with the MLOps agent, grounded in the monitoring log so far —
    emits tool_call / tool_result / token / done as Server-Sent Events."""
    from app.services.mlops_chat import stream_mlops_chat  # noqa: PLC0415

    def event_source():
        try:
            for event in stream_mlops_chat(
                request.message, request.history,
                use_llm=request.use_llm, extra_context=request.extra_context,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "done", "reply": f"오류: 응답 생성 실패 ({exc})", "agent_mode": "error", "tool_calls": []}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/mlops/drift")
def drift(request: DriftRequest) -> dict[str, object]:
    return simulate_drift(request)


@app.post("/api/v1/mlops/retrain")
def retrain(request: RetrainRequest) -> dict[str, object]:
    return simulate_retraining(request)


@app.post("/api/v1/models/promote")
def promote(request: PromoteRequest) -> dict[str, object]:
    return promote_latest(request.version)


@app.post("/api/v1/models/rollback")
def model_rollback(request: RollbackRequest) -> dict[str, object]:
    return rollback(request.reason)


# ---------------------------------------------------------------------------
# Pending Approvals (Part 1-3)
# ---------------------------------------------------------------------------


@app.get("/api/v1/pending-approvals")
def pending_approvals(status: str = "pending") -> list[dict[str, object]]:
    """List High-risk Tool requests awaiting human approval."""
    return list_pending_approvals(status=status)


@app.post("/api/v1/approvals/{approval_id}/approve")
def approve_action(approval_id: str, request: ApprovalResolveRequest | None = None) -> dict[str, object]:
    """Approve a pending High-risk Tool request and execute the underlying action.

    An optional engineer comment is stored on the approval and later fed back to
    the agent as human-feedback context."""
    comment = request.comment if request else ""
    row = resolve_approval(approval_id, "approved", comment=comment)
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    tool_name = row.get("tool_name", "")
    payload = row.get("payload", {})

    # Execute the actual action post-approval
    if tool_name == "trigger_critical_alert":
        insert_alert(
            "critical",
            "sns/slack",
            payload.get("message", f"승인된 critical alert: {row.get('inspection_id')}"),
        )
    elif tool_name == "recommend_retrain":
        try:
            from app.services.mlops import simulate_retraining  # noqa: PLC0415
            from app.services.schemas import RetrainRequest as _RR  # noqa: PLC0415

            simulate_retraining(_RR(trigger_type="manual"))
        except Exception as exc:  # noqa: BLE001
            row["retraining_note"] = f"재학습 시뮬레이션 오류: {exc}"

    row["executed_action"] = tool_name
    return row


@app.post("/api/v1/approvals/{approval_id}/reject")
def reject_action(approval_id: str, request: ApprovalResolveRequest | None = None) -> dict[str, object]:
    """Reject a pending High-risk Tool request, storing an optional engineer comment."""
    comment = request.comment if request else ""
    row = resolve_approval(approval_id, "rejected", comment=comment)
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return row


def _evidence_from_record(record: dict, use_llm: bool = True) -> dict[str, object]:
    """Rebuild the agent Evidence dict from a persisted inspection record."""
    return {
        "inspection_id": record.get("id"),
        "defect_type": record.get("defect_type"),
        "equipment_id": record.get("equipment_id"),
        "risk_level": record.get("risk_level"),
        "risk_score": record.get("risk_score"),
        "confidence": record.get("confidence"),
        "metrology_rule_hits": record.get("action_card", {}).get("metrology_rule_hits", []),
        "rag_cases": record.get("cases", []),
        "process_context": record.get("process_context", {}),
        "metrology": record.get("metrology", {}),
        "image_urls": [url for url in [record.get("overlay_url"), record.get("roi_url")] if url],
        "use_llm": use_llm,
    }


@app.post("/api/v1/inspect/{inspection_id}/re-agent")
def re_agent(inspection_id: str) -> dict[str, object]:
    """Manually trigger Agent re-analysis on any inspection in the review queue."""
    record = get_inspection(inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found")

    try:
        from app.services.agent import run as agent_run  # noqa: PLC0415

        agent_result = agent_run(_evidence_from_record(record))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Agent re-run failed: {exc}") from exc

    return {
        "inspection_id": inspection_id,
        "agent_final_action": agent_result.get("final_action"),
        "agent_tool_calls": agent_result.get("tool_calls"),
        "agent_trace_id": agent_result.get("trace_id"),
        "agent_mode": agent_result.get("agent_mode"),
    }


@app.post("/api/v1/inspect/{inspection_id}/agent/stream")
def inspection_agent_stream(inspection_id: str, request: AgentStreamRequest) -> StreamingResponse:
    """Run the flagship inspection agent live, streaming its reasoning as SSE.

    Emits tool_call / tool_result / token / done events — the same contract the
    chat and MLOps streams use — so the situation-judgment → evidence → tool
    flow is visible instead of only landing as a polled trace.
    """
    record = get_inspection(inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    evidence = _evidence_from_record(record, use_llm=request.use_llm)

    def event_source():
        try:
            from app.services.agent import stream_inspection_agent  # noqa: PLC0415

            for event in stream_inspection_agent(evidence):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "done", "final_action": f"오류: 실행 실패 ({exc})", "agent_mode": "error", "tool_calls": [], "agent_kind": "inspection"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


import pathlib as _pathlib
_FRONTEND_DIST = _pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")

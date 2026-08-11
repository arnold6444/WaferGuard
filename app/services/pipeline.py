from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import io
import logging
import os
import random
import threading
from datetime import datetime, timezone

from app.services import object_store
from app.services.config import MODEL_VERSION
from app.services.action_card import (
    build_action_card,
    build_metrology_context,
    build_process_context,
    evaluate_metrology_rules,
    has_critical_metrology_hit,
    metrology_risk_delta,
)
from app.services.rag import retrieve_cases
from app.services.reporting import build_report
from app.services.risk import compute_risk_score, risk_level
from app.services.schemas import InspectRequest
from app.services.process_ops import inspection_operating_context
from app.services.storage import insert_alert, insert_inspection, production_model, utc_now
from app.services.synthetic_wafer import choose_defect, generate_images

logger = logging.getLogger(__name__)


def _run_agent_background(evidence: dict) -> None:
    try:
        from app.services.agent import run as agent_run  # noqa: PLC0415

        agent_run(evidence)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Background agent run failed for %s: %s", evidence.get("inspection_id"), exc
        )


# Serialize background agent runs through a single worker. The live stream can
# fire an inspection every couple of seconds; firing a concurrent LLM agent run
# for each one bursts the API rate limit (HTTP 429). One-at-a-time keeps the
# request rate gentle. A small bounded backlog drops excess rather than piling up.
_AGENT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")
_AGENT_MAX_PENDING = 4
_AGENT_PENDING = 0
_AGENT_PENDING_LOCK = threading.Lock()


def _submit_agent(evidence: dict) -> bool:
    global _AGENT_PENDING  # noqa: PLW0603
    with _AGENT_PENDING_LOCK:
        if _AGENT_PENDING >= _AGENT_MAX_PENDING:
            logger.warning(
                "Agent backlog full (%d pending); skipping background run for %s",
                _AGENT_PENDING, evidence.get("inspection_id"),
            )
            return False
        _AGENT_PENDING += 1

    def _task() -> None:
        global _AGENT_PENDING  # noqa: PLW0603
        try:
            _run_agent_background(evidence)
        finally:
            with _AGENT_PENDING_LOCK:
                _AGENT_PENDING -= 1

    _AGENT_EXECUTOR.submit(_task)
    return True


def _save_report_csv(inspection_id: str, record: dict) -> str:
    """Write a one-row metadata CSV and return its object key."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    cols = [
        "wafer_id", "lot_id", "equipment_id", "process_step", "recipe_id",
        "defect_type", "risk_level", "risk_score", "confidence",
        "cd_nm", "overlay_nm", "film_thickness_nm", "roughness_nm", "created_at",
    ]
    writer.writerow(cols)
    writer.writerow([record.get(c) for c in cols])
    data = buf.getvalue().encode("utf-8-sig")  # BOM → Excel reads Korean headers
    return object_store.put_bytes(f"reports/{inspection_id}.csv", data, "text/csv")


def _save_report_pdf(inspection_id: str, record: dict) -> str | None:
    """Render the report text to a simple PDF (best-effort). Returns key or None.

    fpdf2's core fonts are latin-1 only; Korean glyphs are replaced rather than
    crashing. For fully legible Korean, register a TTF via pdf.add_font(). The
    CSV (above) always holds the machine-readable data.
    """
    try:
        from fpdf import FPDF  # noqa: PLC0415 — optional dependency
    except Exception:  # noqa: BLE001
        return None
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("helvetica", size=11)
        safe = str(record.get("report", "")).encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 8, safe)
        data = bytes(pdf.output())
        return object_store.put_bytes(f"reports/{inspection_id}.pdf", data, "application/pdf")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF report generation failed for %s: %s", inspection_id, exc)
        return None


def run_inspection(request: InspectRequest) -> dict[str, object]:
    inspection_at = (
        request.inspection_timestamp.isoformat(timespec="seconds")
        if request.inspection_timestamp
        else utc_now()
    )
    defect_type = choose_defect(request.defect_hint)
    inspection_id = _inspection_id(request.wafer_id, defect_type)
    repeat_weight = _repeat_weight(request.line_id, defect_type)
    confidence = _confidence(defect_type)
    image_result = generate_images(
        inspection_id=inspection_id,
        defect_type=defect_type,
    )
    cases = retrieve_cases(
        defect_type,
        request.line_id,
        query_text=f"{defect_type} 결함, {request.process_step} 공정, {request.equipment_id} 설비 이상 대응",
    )
    process_context = build_process_context(request, inspection_timestamp=inspection_at)
    process_context.update(
        inspection_operating_context(
            lot_id=request.lot_id,
            wafer_id=request.wafer_id,
            equipment_id=request.equipment_id,
            inspection_at=inspection_at,
            recipe_id=request.recipe_id,
        )
    )
    if not process_context.get("process_timestamp") and process_context["related_process_events"]:
        process_context["process_timestamp"] = process_context["related_process_events"][0].get("observed_at")
    metrology = build_metrology_context(request, float(image_result["hotspot_ratio"]))
    metrology_rule_hits = evaluate_metrology_rules(defect_type, process_context, metrology)
    risk_score = compute_risk_score(
        defect_type=defect_type,
        confidence=confidence,
        hotspot_ratio=float(image_result["hotspot_ratio"]),
        repeat_weight=repeat_weight,
        metrology_risk_delta=metrology_risk_delta(metrology_rule_hits),
    )
    level = risk_level(risk_score)
    status = (
        "review_required"
        if confidence < 0.70 or level == "High" or has_critical_metrology_hit(metrology_rule_hits)
        else "auto_screened"
    )
    model = production_model()
    model_version = str(model.get("version", MODEL_VERSION))
    report = build_report(
        wafer_id=request.wafer_id,
        line_id=request.line_id,
        equipment_id=request.equipment_id,
        defect_type=defect_type,
        confidence=confidence,
        risk_level=level,
        hotspot_ratio=float(image_result["hotspot_ratio"]),
        cases=cases,
        process_context=process_context,
        metrology=metrology,
        metrology_rule_hits=metrology_rule_hits,
    )
    action_card = build_action_card(
        defect_type=defect_type,
        risk_level=level,
        confidence=confidence,
        process_context=process_context,
        metrology=metrology,
        cases=cases,
        metrology_rule_hits=metrology_rule_hits,
    )

    # Agent escalation: Low → rule-based only; Medium/High/review_required → Agent
    agent_result: dict | None = None
    needs_agent = level in ("Medium", "High") or status == "review_required"
    if needs_agent:
        evidence = {
            "inspection_id": inspection_id,
            "defect_type": defect_type,
            "equipment_id": request.equipment_id,
            "risk_level": level,
            "risk_score": risk_score,
            "confidence": confidence,
            "metrology_rule_hits": metrology_rule_hits,
            "rag_cases": cases,
            "process_context": process_context,
            "metrology": metrology,
            "image_urls": [
                image_result["overlay_key"],
                image_result["roi_key"],
            ],
            "use_llm": request.use_llm,
        }
        llm_active = request.use_llm and bool(os.environ.get("LUXIA_API_KEY", "").strip())
        if llm_active:
            # LLM analysis takes seconds — run it in the background so the
            # inspect response (and the live stream) returns immediately.
            # Serialized through a single worker so concurrent stream inspections
            # don't burst the LLM rate limit. The agent persists its trace, which
            # the Agent tab polls for.
            _submit_agent(evidence)
            agent_result = {
                "final_action": None,
                "tool_calls": [],
                "trace_id": None,
                "agent_mode": "pending",
            }
        else:
            try:
                from app.services.agent import run as agent_run  # noqa: PLC0415
                agent_result = agent_run(evidence)
            except Exception as exc:  # noqa: BLE001
                logger.error("Agent run failed for %s: %s", inspection_id, exc)
                agent_result = {
                    "final_action": f"Agent 실행 오류: {exc}",
                    "tool_calls": [],
                    "trace_id": None,
                    "agent_mode": "error",
                }

    record = {
        "id": inspection_id,
        "lot_id": request.lot_id,
        "wafer_id": request.wafer_id,
        "line_id": request.line_id,
        "equipment_id": request.equipment_id,
        "process_step": request.process_step,
        "recipe_id": request.recipe_id,
        "image_source": request.image_source,
        "proxy_dataset": request.proxy_dataset,
        "proxy_status": (
            f"WM-811K wafer map ({image_result['wafer_source']['wm811k_id']}, "
            f"lot={image_result['wafer_source']['lot_name']})"
        ),
        "defect_type": defect_type,
        "confidence": confidence,
        "risk_score": risk_score,
        "risk_level": level,
        "hotspot_ratio": image_result["hotspot_ratio"],
        # Store canonical object keys (never expiring presigned URLs). Read-time
        # presigning happens in storage._inspection_to_dict.
        "image_url": image_result["image_key"],
        "heatmap_url": image_result["heatmap_key"],
        "overlay_url": image_result["overlay_key"],
        "roi_url": image_result["roi_key"],
        "roi_bbox": image_result["roi_bbox"],
        "report": report,
        "cases": cases,
        "process_context": process_context,
        "metrology": metrology,
        # Metrology values also as individual columns for SQL search/aggregation.
        "cd_nm": metrology.get("cd_nm"),
        "overlay_nm": metrology.get("overlay_nm"),
        "film_thickness_nm": metrology.get("film_thickness_nm"),
        "roughness_nm": metrology.get("roughness_nm"),
        "action_card": action_card,
        "model_version": model_version,
        "status": status,
        "created_at": inspection_at,
        # Agent fields (None for Low / rule-only cases)
        "agent_final_action": agent_result.get("final_action") if agent_result else None,
        "agent_tool_calls": agent_result.get("tool_calls") if agent_result else None,
        "agent_trace_id": agent_result.get("trace_id") if agent_result else None,
        "agent_mode": agent_result.get("agent_mode") if agent_result else "rule_only",
    }
    # Generate downloadable report files (CSV always; PDF best-effort) and store
    # their object keys so they can be served via presigned URLs later.
    record["report_csv_url"] = _save_report_csv(inspection_id, record)
    record["report_pdf_url"] = _save_report_pdf(inspection_id, record)
    insert_inspection(record)
    if level == "High":
        insert_alert(
            "critical",
            "sns/slack",
            f"{request.line_id} {request.wafer_id}: {defect_type} High risk 감지",
        )
    # DB now holds canonical keys; the API response must return browser-usable
    # URLs (local path or S3 presigned), matching what reads return.
    for field in ("image_url", "heatmap_url", "overlay_url", "roi_url", "report_csv_url", "report_pdf_url"):
        if record.get(field):
            record[field] = object_store.presign(record[field])
    return record


def _inspection_id(wafer_id: str, defect_type: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(f"{wafer_id}-{defect_type}-{random.random()}".encode()).hexdigest()[:6].upper()
    return f"INS-{stamp}-{digest}"


def _confidence(defect_type: str) -> float:
    ranges = {
        "None": (0.92, 0.98),
        "Near-full": (0.90, 0.97),
        "Scratch": (0.84, 0.95),
        "Random": (0.72, 0.90),
    }
    low, high = ranges.get(defect_type, (0.80, 0.96))
    return round(random.uniform(low, high), 3)


def _repeat_weight(line_id: str, defect_type: str) -> float:
    digest = hashlib.sha1(f"{line_id}-{defect_type}".encode()).hexdigest()
    return round(int(digest[:2], 16) / 255, 3)

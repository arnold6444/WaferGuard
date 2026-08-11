"""
WaferGuard Agent — LangGraph StateGraph implementation.

Flow
----
START → decide → (tool_call | final_action) → tool_exec → decide → ... → final_action → END

Nodes
-----
decide:
    Calls GPT-4o-mini (via luxia_client.chat_with_tools) with the Evidence text
    + wafer image URLs.  The model either selects a Tool or produces a final answer.

tool_exec:
    Dispatches to a Tool function from the registry (see _TOOL_REGISTRY below).
    Falls back to no-op stubs if app/services/tools.py does not yet exist.

final_action:
    Collects the assistant's last message as the Agent decision text.  Persists
    the full trace to the agent_traces table.

Public API
----------
run(evidence: dict) -> dict
    evidence keys: inspection_id, defect_type, risk_level, risk_score,
                   confidence, metrology_rule_hits, rag_cases, process_context,
                   metrology, image_urls (list[str])

    Returns:
        {
          "final_action": str,
          "tool_calls": list[dict],   # [{name, args, result}, ...]
          "trace_id": str,
          "agent_mode": "llm" | "stub",
        }

Tool function signatures (defined in app/services/tools.py):
    search_similar_cases(query: str, k: int = 3) -> list[dict]
    inspect_image(image_url: str, focus: str) -> dict
    enqueue_for_review(inspection_id: str, reason: str) -> dict
    recommend_retrain(reason: str) -> dict
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Any, TypedDict

from app.services import luxia_client
from app.services.storage import insert_agent_trace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-call format)
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_similar_cases",
            "description": (
                "RAG: 유사 결함 사례를 검색합니다. "
                "defect 유형과 맥락을 자연어로 쿼리하면 SQLite RAG index에서 유사 사례 k건을 반환합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "결함 유형, 공정 단계, 위험 맥락을 포함한 자연어 쿼리",
                    },
                    "k": {
                        "type": "integer",
                        "description": "반환할 유사 사례 수 (기본 3)",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_image",
            "description": (
                "wafer 이미지 또는 Grad-CAM overlay를 GPT-4o-mini multimodal로 해석합니다. "
                "hotspot 위치, 패턴 특징, 주목할 영역을 판단합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "분석할 wafer 이미지 URL (overlay, ROI 등)",
                    },
                    "focus": {
                        "type": "string",
                        "description": "집중 분석 영역 또는 질문 (예: 'edge hotspot 위치 확인')",
                    },
                },
                "required": ["image_url", "focus"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enqueue_for_review",
            "description": (
                "해당 inspection을 엔지니어 검토 큐에 등록합니다. "
                "신뢰도가 낮거나 판단이 불확실할 때 사용합니다 (Low-risk Tool)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "inspection_id": {
                        "type": "string",
                        "description": "검토 큐에 등록할 inspection ID",
                    },
                    "reason": {
                        "type": "string",
                        "description": "검토 큐 등록 이유",
                    },
                },
                "required": ["inspection_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_retrain",
            "description": (
                "재학습 추천을 생성합니다 (High-risk Tool). "
                "실행 전 사람 승인이 필요하며, pending_approvals 테이블에 기록됩니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "재학습이 필요한 이유 (drift, 신규 패턴, 성능 저하 등)",
                    },
                },
                "required": ["reason"],
            },
        },
    },
]

# Extra tools layered onto the inspection agent on top of the action tools above.
# - Read-only lookups (Gap 1): let the Agent run its own playbook ("동일 설비 반복
#   여부 확인", "최근 24h 트렌드 비교") instead of telling a human to.
# - escalate_to_mlops (B-6): hand a confirmed fleet-level concern to the MLOps
#   agent and fold its decision back in.
# Reuses the schemas defined in tools.py to avoid drift.
_LOOKUP_TOOL_NAMES = {
    "get_equipment_history",
    "get_metrology_trend",
    "get_mlops_state",
    "compare_with_past_wafer",
    "escalate_to_mlops",
}
try:
    from app.services.tools import TOOL_SCHEMAS as _TOOLS_SCHEMAS  # noqa: PLC0415

    _TOOL_SCHEMAS.extend(
        s for s in _TOOLS_SCHEMAS if s.get("function", {}).get("name") in _LOOKUP_TOOL_NAMES
    )
except ImportError:
    logger.warning("tools.py lookup schemas unavailable; Agent runs without DB lookup tools.")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """당신은 WaferGuard Fab Ops Agent입니다.
반도체 공정 이상 상황을 판단하고 적절한 대응 action을 수행합니다.

규칙:
1. Evidence(risk score, metrology rule hit, RAG 유사 사례)를 해석하되, 새 risk score를 임의로 생성하지 않습니다.
2. 제공된 RAG 사례 외에 출처 없는 사례를 인용하지 않습니다 (환각 방지).
3. High-risk Tool(recommend_retrain)은 반드시 명확한 근거와 함께 호출합니다. High 리스크 검사는 시스템이 자동으로 critical alert를 발생시키므로, 에이전트가 별도로 알림을 만들 필요는 없습니다.
4. 판단이 불확실하면 enqueue_for_review로 엔지니어에게 넘깁니다.
5. 최종 판단은 한국어로 간결하게 작성합니다.
6. 본 시뮬레이션에서 결함 분류는 입력값이며, Agent는 분류 결과에 대한 운영 판단을 시뮬레이션합니다.
7. '과거 사람 피드백'이 제공되면 반드시 반영합니다. 같은 조치가 과거에 반려/오탐 처리됐다면 같은 권고를 반복하지 말고, 반려 사유를 근거로 다른 판단(예: 추가 조회 후 enqueue_for_review)을 고려합니다.

조회 도구 활용 (사람에게 시키지 말고 직접 확인하세요):
- 반복성 결함인지 판단하려면 get_equipment_history(equipment_id, defect_type)로 같은 설비의 최근 동일 결함 횟수를 직접 조회합니다.
- 계측값이 이번만 튄 건지, 계속 밀리는 건지 구분하려면 get_metrology_trend(equipment_id, metric)로 추세를 확인합니다.
- recommend_retrain을 호출하기 전에 get_mlops_state로 현재 모델 성능과 drift 증거를 먼저 확인합니다.
- 과거 사례 이미지와 직접 대조가 필요하면 compare_with_past_wafer를 사용합니다.
조회 결과를 최종 판단의 근거로 명시적으로 인용하세요 (예: "ETCH-02에서 24h 내 Scratch 5회 → 반복성, 설비 점검 우선").

다른 에이전트로 위임 (멀티에이전트):
- 문제가 개별 웨이퍼 차원을 넘어선다고 판단되면(같은 설비 반복성 결함을 get_equipment_history로 확인했거나, 계측 추세가 단발이 아니라 지속 drift로 보일 때) escalate_to_mlops(reason, equipment_id)로 MLOps 에이전트에 위임합니다.
- MLOps 에이전트는 모델 성능·drift·계측 추세를 직접 분석해 재학습 필요 여부를 판단해 돌려줍니다. 그 결론(mlops_decision)을 최종 판단에 인용해, 개별 설비 조치와 fleet-level 조치를 함께 제시하세요.
- 단발성으로 끝낼 문제까지 무분별하게 위임하지 마세요. 반복성·drift 근거가 모였을 때만 위임합니다."""

_SYSTEM_PROMPT += """

최종 답변 형식(아래 제목을 모두 포함):
## Observation
## Possible Causes
## Evidence
## Recommended Checks
## Recommended Action
## Confidence / Uncertainty

Possible Causes는 확정 원인이 아니라 검증할 후보로만 표현합니다. 공정 이벤트와 검사 결과의 시간적 연관성을 인과관계로 단정하지 마세요."""

# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    inspection_id: str
    messages: list[dict]
    tool_calls_log: list[dict]
    final_action: str
    image_urls: list[str]
    max_iterations: int
    iteration: int
    tool_schemas: list[dict]  # which tools this profile exposes (inspection vs mlops)
    tool_registry: dict | None  # optional per-run registry override (autonomy-bound tools)


# ---------------------------------------------------------------------------
# Tool registry (lazy import so missing tools.py doesn't break import)
# ---------------------------------------------------------------------------


def _get_tool_registry() -> dict[str, Any]:
    """Load tool functions from tools.py, falling back to no-op stubs."""
    try:
        from app.services import tools  # noqa: PLC0415

        return {
            "search_similar_cases": tools.search_similar_cases,
            "inspect_image": tools.inspect_image,
            "enqueue_for_review": tools.enqueue_for_review,
            "recommend_retrain": tools.recommend_retrain,
            "get_equipment_history": tools.get_equipment_history,
            "get_metrology_trend": tools.get_metrology_trend,
            "get_mlops_state": tools.get_mlops_state,
            "get_model_registry": tools.get_model_registry,
            "compare_with_past_wafer": tools.compare_with_past_wafer,
            "escalate_to_mlops": tools.escalate_to_mlops,
        }
    except (ImportError, AttributeError) as exc:
        logger.warning("tools.py not available (%s); using no-op stubs.", exc)
        return _stub_registry()


def _stub_registry() -> dict[str, Any]:
    def _stub(**kwargs: Any) -> dict:
        return {"status": "stub", "note": "tools.py 미설치 — 스텁 실행", **kwargs}

    return {
        "search_similar_cases": lambda **kwargs: [_stub(**kwargs)],
        "inspect_image": lambda **kwargs: _stub(**kwargs),
        "enqueue_for_review": lambda **kwargs: _stub(**kwargs),
        "recommend_retrain": lambda **kwargs: _stub(**kwargs),
        "get_equipment_history": lambda **kwargs: _stub(**kwargs),
        "get_metrology_trend": lambda **kwargs: _stub(**kwargs),
        "get_mlops_state": lambda **kwargs: _stub(**kwargs),
        "get_model_registry": lambda **kwargs: _stub(**kwargs),
        "compare_with_past_wafer": lambda **kwargs: _stub(**kwargs),
        "escalate_to_mlops": lambda **kwargs: _stub(**kwargs),
    }


# ---------------------------------------------------------------------------
# Evidence serializer
# ---------------------------------------------------------------------------


def _build_evidence_text(evidence: dict) -> str:
    """Convert rule-computed evidence dict into a structured prompt string."""
    lines: list[str] = [
        f"## 검사 ID: {evidence.get('inspection_id', 'N/A')}",
        f"## 결함 유형: {evidence.get('defect_type', 'Unknown')}",
        f"## 위험 수준: {evidence.get('risk_level', 'N/A')} (score: {evidence.get('risk_score', 0):.3f})",
        f"## 신뢰도: {evidence.get('confidence', 0):.1%}",
    ]

    # Metrology rule hits
    rule_hits = evidence.get("metrology_rule_hits", [])
    if rule_hits:
        lines.append("\n## 계측 Rule Hit")
        for hit in rule_hits:
            lines.append(
                f"  - [{hit.get('severity', '?')}] {hit.get('signal', '?')}: "
                f"{hit.get('evidence', '')} → {hit.get('action', '')}"
            )
    else:
        lines.append("\n## 계측 Rule Hit: 없음")

    # Process context
    pc = evidence.get("process_context", {})
    if pc:
        lines.append(
            f"\n## 공정 맥락: {pc.get('lot_id', '-')} / "
            f"{pc.get('process_step', '-')} / "
            f"{pc.get('tool_id', '-')} / "
            f"{pc.get('recipe_id', '-')}"
        )
        related_events = pc.get("related_process_events", [])
        if related_events:
            lines.append("## 시간적으로 연관된 공정 이상 후보 (인과관계 아님)")
            for event in related_events[:5]:
                metadata = event.get("metadata", {}) if isinstance(event, dict) else {}
                lines.append(
                    f"  - [{event.get('severity', '?')}] {event.get('observed_at', '-')} "
                    f"{event.get('equipment_id', '-')} / {event.get('event_type', '-')}: "
                    f"residual={metadata.get('residual', '-')}"
                )
        lot_context = pc.get("lot_context", {})
        neighbors = lot_context.get("neighboring_wafers", []) if isinstance(lot_context, dict) else []
        if neighbors:
            lines.append("## Lot 인접 Wafer 추세")
            for wafer in neighbors[:5]:
                lines.append(
                    f"  - {wafer.get('wafer_id', '-')}: {wafer.get('risk_level', '-')} / "
                    f"{wafer.get('defect_type', '-')} / {wafer.get('equipment_id', '-')}"
                )
        defect_pattern = lot_context.get("accumulated_defect_pattern", {}) if isinstance(lot_context, dict) else {}
        if defect_pattern:
            lines.append(f"## Lot 누적 결함 패턴: {json.dumps(defect_pattern, ensure_ascii=False)}")
        equipment_history = pc.get("equipment_history", {})
        if equipment_history:
            lines.append(
                "## 설비 검사 이력: "
                f"총 {equipment_history.get('inspection_count', 0)}건 / "
                f"High {equipment_history.get('high_risk_count', 0)}건 / "
                f"최근 결함 {equipment_history.get('recent_defects', [])}"
            )
        recipe_context = pc.get("recipe_context", {})
        if recipe_context:
            lines.append(
                "## Recipe 이력: "
                f"{recipe_context.get('recipe_id', '-')} / "
                f"총 {recipe_context.get('inspection_count', 0)}건 / "
                f"High {recipe_context.get('high_risk_count', 0)}건"
            )

    # Metrology values
    met = evidence.get("metrology", {})
    if met:
        lines.append(
            f"## 계측값: CD {met.get('cd_nm', '-')}nm, "
            f"overlay {met.get('overlay_nm', '-')}nm, "
            f"film thickness {met.get('film_thickness_nm', '-')}nm, "
            f"roughness {met.get('roughness_nm', '-')}nm, "
            f"defect count {met.get('defect_count', '-')}"
        )

    # RAG cases
    rag_cases = evidence.get("rag_cases", [])
    if rag_cases:
        lines.append("\n## 참고 사례 (RAG)")
        for case in rag_cases[:3]:
            learned = " [운영 학습]" if case.get("learned") else ""
            lines.append(
                f"  - [{case.get('defect_type', '?')}]{learned} {case.get('summary', case.get('content', ''))[:120]}"
            )
    else:
        lines.append("\n## 참고 사례 (RAG): 없음")

    # Prior human feedback (B-5) — episodic memory so the agent learns from how
    # engineers ruled on similar past cases instead of repeating rejected advice.
    pc = evidence.get("process_context", {}) or {}
    equipment_id = evidence.get("equipment_id") or pc.get("tool_id")
    feedback_block = _human_feedback_block(equipment_id, evidence.get("defect_type"))
    if feedback_block:
        lines.append(feedback_block)

    return "\n".join(lines)


_DECISION_LABEL = {
    "approved": "승인",
    "rejected": "반려",
    "false_alarm": "오탐 처리",
    "needs_review": "추가 리뷰",
}


def _human_feedback_block(equipment_id: str | None, defect_type: str | None) -> str:
    """Render recent human decisions on similar cases, newest first.

    Returns an empty string when there is no relevant feedback (so the section
    only appears when it carries signal).
    """
    try:
        from app.services.storage import recent_human_feedback  # noqa: PLC0415

        items = recent_human_feedback(equipment_id=equipment_id, defect_type=defect_type, limit=4)
    except Exception as exc:  # noqa: BLE001
        logger.debug("human feedback lookup failed: %s", exc)
        return ""
    if not items:
        return ""

    out = ["\n## 과거 사람 피드백 (이 설비/결함 관련 — 판단에 반영)"]
    for it in items:
        decision = _DECISION_LABEL.get(it.get("decision", ""), it.get("decision", "?"))
        tool = it.get("tool_name")
        target = f"{tool} → " if tool else ""
        reason = (it.get("reason") or "").strip()
        reason_txt = f" (사유: {reason[:80]})" if reason else ""
        out.append(f"  - {target}{decision}{reason_txt}")
        # the engineer's own comment at approve/reject time — the strongest signal,
        # so render it on its own line verbatim (truncated) when present.
        comment = (it.get("comment") or "").strip()
        if comment:
            out.append(f"    ↳ 담당자 코멘트: \"{comment[:160]}\"")
    return "\n".join(out)


def _retrain_approval_status_block() -> str:
    """Authoritative, current state of retrain approvals (overrides the log).

    The monitoring log labels any past ``recommend_retrain`` as "승인 대기", which
    goes stale the moment a human approves/rejects it. This block reads the live
    ``pending_approvals`` table so the agent knows what is *actually* still pending
    versus already ruled on — and surfaces the engineer's rejection reason so the
    next judgment incorporates it instead of re-recommending the same thing.
    """
    try:
        from app.services.storage import (  # noqa: PLC0415
            list_pending_approvals,
            recent_human_feedback,
        )

        pending = [
            a for a in list_pending_approvals("pending")
            if a.get("tool_name") == "recommend_retrain"
        ]
        resolved = [
            f for f in recent_human_feedback(None, None, limit=6)
            if f.get("kind") == "approval"
            and f.get("tool_name") == "recommend_retrain"
            and f.get("decision") in ("approved", "rejected")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("retrain approval status lookup failed: %s", exc)
        return ""

    out = ["\n## 재학습 승인 현황 (실시간 — 모니터링 로그의 '승인 대기' 표기보다 우선)"]
    if pending:
        out.append(f"  - 현재 대기 중인 재학습 승인 요청: {len(pending)}건")
        for a in pending[:3]:
            reason = (a.get("reason") or "").strip()
            out.append(f"    · [대기중] {reason[:110]}")
    else:
        out.append("  - 현재 대기 중인 재학습 승인 요청: 없음")
    if resolved:
        out.append("  - 최근 담당자 결정 (이 결정과 사유를 다음 판단에 반영):")
        for f in resolved[:4]:
            decision = "승인" if f.get("decision") == "approved" else "거절"
            reason = (f.get("reason") or "").strip()
            reason_txt = f" — 권고사유: {reason[:70]}" if reason else ""
            out.append(f"    · [{decision}]{reason_txt}")
            comment = (f.get("comment") or "").strip()
            if comment:
                out.append(f"      ↳ 담당자 사유: \"{comment[:160]}\"")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# LangGraph nodes
# ---------------------------------------------------------------------------


def _node_decide(state: AgentState) -> AgentState:
    """Call LLM with current messages and available tools."""
    resp = luxia_client.chat_with_tools(
        messages=state["messages"],
        tools=state.get("tool_schemas") or _TOOL_SCHEMAS,
        image_urls=state["image_urls"] if state["iteration"] == 0 else None,
    )
    msg = resp["choices"][0]["message"]
    state["messages"].append(msg)
    return state


def _node_tool_exec(state: AgentState) -> AgentState:
    """Execute all tool_calls from the last assistant message."""
    last_msg = state["messages"][-1]
    tool_calls = last_msg.get("tool_calls") or []
    registry = state.get("tool_registry") or _get_tool_registry()

    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}

        tool_fn = registry.get(name)
        if tool_fn is None:
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = tool_fn(**args)
            except Exception as exc:  # noqa: BLE001
                logger.error("Tool %s failed: %s", name, exc)
                result = {"error": str(exc)}

        # Log the tool call
        state["tool_calls_log"].append({"name": name, "args": args, "result": result})

        # Add tool result as a message
        state["messages"].append(
            {
                "role": "tool",
                "tool_call_id": tc.get("id", name),
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    state["iteration"] += 1
    return state


def _node_final_action(state: AgentState) -> AgentState:
    """Extract the final assistant text as the action decision."""
    # Walk messages in reverse to find the last assistant text content
    for msg in reversed(state["messages"]):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if content and isinstance(content, str):
                state["final_action"] = content
                return state
    state["final_action"] = "Agent 판단 결과를 추출할 수 없습니다."
    return state


# ---------------------------------------------------------------------------
# Routing condition
# ---------------------------------------------------------------------------


def _should_continue(state: AgentState) -> str:
    """Return 'tool_exec' if the last message has tool_calls, else 'final_action'."""
    if state["iteration"] >= state["max_iterations"]:
        return "final_action"
    last_msg = state["messages"][-1]
    tool_calls = last_msg.get("tool_calls")
    if tool_calls:
        return "tool_exec"
    return "final_action"


# ---------------------------------------------------------------------------
# Build LangGraph graph
# ---------------------------------------------------------------------------


def _build_graph():
    """Build the LangGraph StateGraph. Imported lazily to avoid hard dependency."""
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: PLC0415

        graph = StateGraph(AgentState)
        graph.add_node("decide", _node_decide)
        graph.add_node("tool_exec", _node_tool_exec)
        graph.add_node("final_action", _node_final_action)

        graph.add_edge(START, "decide")
        graph.add_conditional_edges(
            "decide",
            _should_continue,
            {"tool_exec": "tool_exec", "final_action": "final_action"},
        )
        graph.add_edge("tool_exec", "decide")
        graph.add_edge("final_action", END)

        return graph.compile()
    except ImportError:
        logger.warning("langgraph not installed; will use fallback sequential execution.")
        return None


_GRAPH = None  # lazily initialized


def _get_graph():
    global _GRAPH  # noqa: PLW0603
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


# ---------------------------------------------------------------------------
# Fallback sequential runner (used when langgraph is not installed)
# ---------------------------------------------------------------------------


def _run_sequential(state: AgentState) -> AgentState:
    """Simple sequential fallback: decide → tool_exec loop → final_action."""
    while state["iteration"] < state["max_iterations"]:
        state = _node_decide(state)
        route = _should_continue(state)
        if route == "final_action":
            break
        state = _node_tool_exec(state)
    return _node_final_action(state)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(evidence: dict) -> dict:
    """Run the Agent loop for a given inspection evidence dict.

    Parameters
    ----------
    evidence:
        Must include at least ``inspection_id``.  Optional keys:
        defect_type, risk_level, risk_score, confidence,
        metrology_rule_hits, rag_cases, process_context, metrology,
        image_urls (list[str]).

    Returns
    -------
    dict
        {
          "final_action": str,
          "tool_calls": list[dict],
          "trace_id": str,
          "agent_mode": "llm" | "stub",
        }
    """
    inspection_id = evidence.get("inspection_id", "unknown")
    image_urls: list[str] = evidence.get("image_urls", [])

    evidence_text = _build_evidence_text(evidence)

    initial_state: AgentState = {
        "inspection_id": inspection_id,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"다음 Evidence를 분석하고 적절한 대응 action을 결정하세요.\n\n{evidence_text}"
                ),
            },
        ],
        "tool_calls_log": [],
        "final_action": "",
        "image_urls": image_urls,
        "max_iterations": 5,
        "iteration": 0,
        "tool_schemas": _TOOL_SCHEMAS,
        "tool_registry": None,
    }

    use_llm = bool(evidence.get("use_llm", True))

    def _rule_fallback() -> str:
        rag_cases = evidence.get("rag_cases") or []
        first_action = (
            rag_cases[0].get("action") if rag_cases and isinstance(rag_cases[0], dict) else None
        ) or "엔지니어 검토 큐에서 수동 확인"
        process_events = (evidence.get("process_context") or {}).get("related_process_events") or []
        event_summary = (
            f"동일 Lot의 시간 연관 공정 이벤트 {len(process_events)}건"
            if process_events else "동일 Lot의 시간 연관 공정 이벤트 없음"
        )
        return (
            "## Observation\n"
            f"{evidence.get('defect_type', '결함')} 패턴, 위험도 {evidence.get('risk_level', '?')} "
            f"(score {evidence.get('risk_score', 0):.2f}).\n\n"
            "## Possible Causes\n"
            "공정·이송·검사 조건 중 하나와 연관됐을 가능성이 있으나 현재 증거만으로 원인을 확정할 수 없습니다.\n\n"
            "## Evidence\n"
            f"신뢰도 {evidence.get('confidence', 0):.1%}; {event_summary}. 시간적 연관성은 인과 증명이 아닙니다.\n\n"
            "## Recommended Checks\n"
            "동일 Lot 인접 wafer, 같은 장비·recipe 이력, 계측 rule hit를 함께 확인합니다.\n\n"
            "## Recommended Action\n"
            f"{first_action}.\n\n"
            "## Confidence / Uncertainty\n"
            "룰 기반 운영 보조 판단이며 실제 Fab 원인 확정에는 엔지니어 검토가 필요합니다."
        )

    return _drive_and_persist(initial_state, use_llm, _rule_fallback, agent_kind="inspection")


# ---------------------------------------------------------------------------
# Shared driver — runs the loop (graph or sequential) and persists the trace.
# ---------------------------------------------------------------------------


def _drive_and_persist(
    initial_state: AgentState,
    use_llm: bool,
    rule_fallback,
    agent_kind: str = "inspection",
    extra_trace: dict | None = None,
) -> dict:
    agent_mode = "llm" if use_llm and os.environ.get("LUXIA_API_KEY", "").strip() else "stub"

    if agent_mode == "llm":
        try:
            graph = _get_graph()
            final_state = graph.invoke(initial_state) if graph is not None else _run_sequential(initial_state)
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent graph execution failed: %s", exc)
            final_state = _node_final_action(initial_state)
    else:
        final_state = dict(initial_state)
        final_state["final_action"] = rule_fallback()

    inspection_id = initial_state["inspection_id"]
    trace = {
        "inspection_id": inspection_id,
        "agent_kind": agent_kind,
        "messages": final_state["messages"],
        "tool_calls": final_state["tool_calls_log"],
        "final_action": final_state["final_action"],
        "agent_mode": agent_mode,
        # how this run was triggered ("manual" | "delegation"), plus any source —
        # lets the MLOps monitoring log surface delegated runs distinctly.
        "trigger": "manual",
        **(extra_trace or {}),
    }
    try:
        trace_id = insert_agent_trace(inspection_id, trace)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist agent trace: %s", exc)
        trace_id = "trace-error"

    return {
        "final_action": final_state["final_action"],
        "tool_calls": final_state["tool_calls_log"],
        "trace_id": trace_id,
        "agent_mode": agent_mode,
        "agent_kind": agent_kind,
    }


# ===========================================================================
# MLOps Agent — fleet/model-lifecycle profile (distinct from the per-wafer
# inspection agent above). Shares the loop + lookup tools, differs in system
# prompt and allowed write-tool (recommend_retrain, approval-gated).
# ===========================================================================

_MLOPS_TOOL_NAMES = {
    "get_mlops_state",
    "get_model_registry",
    "get_metrology_trend",
    "get_equipment_history",
    "recommend_retrain",
}


def _mlops_tool_schemas() -> list[dict]:
    try:
        from app.services.tools import TOOL_SCHEMAS as _TS  # noqa: PLC0415

        return [s for s in _TS if s.get("function", {}).get("name") in _MLOPS_TOOL_NAMES]
    except ImportError:
        return [s for s in _TOOL_SCHEMAS if s.get("function", {}).get("name") in _MLOPS_TOOL_NAMES]


_MLOPS_SYSTEM_PROMPT = """당신은 WaferGuard MLOps 에이전트입니다.
개별 웨이퍼가 아니라 라인 전체의 모델 성능과 입력 분포 드리프트를 모니터링하고, 재학습 필요 여부를 판단합니다.

규칙:
1. 먼저 get_mlops_state로 현재 운영 모델 성능(F1), 최신 drift 이벤트, 최근 재학습 이력을 확인합니다.
   모델 레지스트리 전체(Production/Staging/Archived 단계별 목록)와 재학습 진행 현황,
   현재 학습 중인 후보가 있는지(retrain_in_progress)를 자세히 보려면 get_model_registry를 사용합니다.
2. 입력 분포가 실제로 밀리는지 보려면 get_metrology_trend(equipment_id, metric)로 주요 설비 계측 추세를 확인합니다.
3. 결함 구성이 바뀌었는지 보려면 get_equipment_history(equipment_id)로 결함 분포를 확인합니다.
4. drift score가 임계치를 넘고 + 성능 저하/입력 분포 변화 근거가 모이면 recommend_retrain(reason)을 호출합니다.
   반드시 수치 근거를 reason에 명시합니다. (실제 처리 방식은 아래 '자율 모드'에 따라 달라집니다.)
5. 근거가 부족하면 재학습을 권고하지 말고 "현 상태 유지 + 모니터링 지속"으로 판단합니다.
6. '재학습 승인 현황' 섹션이 승인 대기열의 실제 최신 상태입니다. 모니터링 로그에 과거 권고가 '승인 대기'로 보여도, 이 섹션에서 '대기 없음' 또는 '거절'로 확인되면 그것이 사실입니다. 이미 거절된 권고를 아직 대기 중인 것으로 오해하지 마세요.
7. 담당자가 거절한 재학습 권고는 같은 근거로 반복하지 않습니다. 거절 사유(담당자 사유)를 명시적으로 인용하고, 그 사유를 반영해 판단을 갱신합니다. (예: 거절 사유가 해소됐거나 새로운 악화 근거가 있을 때만 재권고)
8. 최종 판단은 한국어로 간결하게, drift score·F1·추세 수치를 인용해 작성합니다.
9. 수치를 지어내지 않습니다. 도구로 조회한 값만 사용합니다."""


_AUTONOMY_PROMPT = {
    "auto": (
        "[자율 모드: 자동 실행] recommend_retrain을 호출하면 사람 승인 없이 즉시 재학습이 실행됩니다. "
        "근거가 충분할 때만 신중히 호출하세요."
    ),
    "approval": (
        "[자율 모드: 승인 필요] recommend_retrain을 호출하면 사람 승인 대기열에 등록되며, "
        "엔지니어 승인 후에야 재학습이 실행됩니다."
    ),
    "notify": (
        "[자율 모드: 알림만] recommend_retrain을 호출하면 담당자에게 알림만 발송되고 실행되지 않습니다. "
        "권고 사실을 알리는 용도입니다."
    ),
}


def _mlops_registry(autonomy: str) -> dict[str, Any]:
    """Full tool registry with recommend_retrain bound to the autonomy mode."""
    registry = dict(_get_tool_registry())
    try:
        from app.services.tools import execute_retrain_decision  # noqa: PLC0415

        registry["recommend_retrain"] = lambda reason="", **_: execute_retrain_decision(reason=reason, mode=autonomy)
    except ImportError:
        pass
    return registry


def _build_mlops_evidence(line_id: str) -> str:
    lines = [f"## 대상 라인: {line_id}"]
    try:
        from app.services.mlops import pipeline_state  # noqa: PLC0415

        state = pipeline_state()
        models = state.get("models", [])
        prod = next((m for m in models if m.get("stage") == "Production"), None)
        if prod:
            lines.append(f"## 운영 모델: {prod.get('version')} (F1 {prod.get('f1_score')}, p95 {prod.get('latency_p95_ms')}ms)")
        de = state.get("latest_drift_event") or {}
        if de:
            lines.append(f"## 최신 drift: score {de.get('drift_score')} / {de.get('status')} / {de.get('action_taken')}")
        jobs = state.get("recent_retraining_jobs") or []
        if jobs:
            lines.append("## 최근 재학습:")
            for j in jobs[:3]:
                lines.append(f"  - {j.get('candidate_version')} F1 {j.get('f1_score')} ({j.get('trigger_type')})")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"## (운영 상태 조회 실패: {exc})")

    # surface a few high-volume equipment ids so the agent can query trends
    try:
        from app.services.storage import list_inspections  # noqa: PLC0415

        equips: list[str] = []
        for r in list_inspections(limit=60):
            eid = r.get("equipment_id")
            if eid and eid not in equips:
                equips.append(str(eid))
            if len(equips) >= 5:
                break
        if equips:
            lines.append("## 주요 설비(추세 조회용): " + ", ".join(equips))
    except Exception:  # noqa: BLE001
        pass

    # Authoritative current state of retrain approvals — kills the stale "승인 대기"
    # belief once an engineer has approved/rejected the recommendation.
    approval_block = _retrain_approval_status_block()
    if approval_block:
        lines.append(approval_block)

    # Fleet-level human feedback (B-5): how engineers ruled on past retrain
    # recommendations, so the agent doesn't re-recommend something just rejected.
    feedback_block = _human_feedback_block(None, None)
    if feedback_block:
        lines.append(feedback_block)

    return "\n".join(lines)


def run_mlops_agent(
    line_id: str = "ALL",
    use_llm: bool = True,
    autonomy: str = "approval",
    trigger: str = "manual",
    source: str | None = None,
) -> dict:
    """Run the MLOps agent over current fleet/model state.

    ``autonomy`` controls what happens when the agent decides to retrain:
    "auto" (execute now), "approval" (queue for human approval), "notify"
    (alert only). ``trigger`` marks how the run started ("manual" or "delegation"
    from the inspection agent) and ``source`` is the originating context — both are
    stored on the trace so the MLOps monitoring log can surface delegated runs.
    Returns the same shape as ``run``.
    """
    evidence_text = _build_mlops_evidence(line_id)
    trace_key = f"MLOPS-{line_id}"
    autonomy_note = _AUTONOMY_PROMPT.get(autonomy, _AUTONOMY_PROMPT["approval"])

    initial_state: AgentState = {
        "inspection_id": trace_key,
        "messages": [
            {"role": "system", "content": _MLOPS_SYSTEM_PROMPT + "\n\n" + autonomy_note},
            {
                "role": "user",
                "content": (
                    "다음 운영 상태를 분석하고 모델 재학습 필요 여부를 판단하세요. "
                    "필요하면 도구로 추세를 직접 조회한 뒤 결론을 내리세요.\n\n" + evidence_text
                ),
            },
        ],
        "tool_calls_log": [],
        "final_action": "",
        "image_urls": [],
        "max_iterations": 5,
        "iteration": 0,
        "tool_schemas": _mlops_tool_schemas(),
        "tool_registry": _mlops_registry(autonomy),
    }

    def _rule_fallback() -> str:
        return (
            "[룰 기반 판단 — LLM 미사용] 현재 운영 상태 요약만 제공합니다. "
            "drift 임계치 초과 시 재학습 권고가 필요하나, 자동 판단에는 LLM이 필요합니다.\n"
            + evidence_text
        )

    return _drive_and_persist(
        initial_state, use_llm, _rule_fallback, agent_kind="mlops",
        extra_trace={"trigger": trigger, "source": source, "autonomy": autonomy},
    )


# ---------------------------------------------------------------------------
# MLOps agent — streaming variant (tool_call / tool_result / token / done)
# ---------------------------------------------------------------------------

_MLOPS_STREAM_MAX_ITERATIONS = 5
_CHUNK_SIZE = 3


def _chunk_text(text: str, size: int = _CHUNK_SIZE) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _summarize_mlops_tool(name: str, result: object) -> str:
    if not isinstance(result, dict):
        return str(result)[:80] if result is not None else ""
    if result.get("error"):
        return f"오류: {result['error']}"
    if name == "get_mlops_state":
        pm = result.get("production_model") or {}
        de = result.get("latest_drift_event") or {}
        return f"운영모델 F1 {pm.get('f1_score', '?')} · drift {de.get('drift_score', '?')} ({de.get('status', '?')})"
    if name == "get_model_registry":
        pm = result.get("production_model") or {}
        stg = len(result.get("staging_candidates") or [])
        training = " · 학습 중" if result.get("retrain_in_progress") else ""
        return (
            f"모델 {result.get('total_models', '?')}개 (Prod {pm.get('version', '?')}, "
            f"Staging {stg}개){training}"
        )
    if name == "get_metrology_trend":
        return f"{result.get('metric')} {result.get('trend')} · Δ{result.get('delta')} ({result.get('pct_change')}%, n={result.get('n')})"
    if name == "get_equipment_history":
        rec = " · 반복성" if result.get("is_recurring") else ""
        return f"{result.get('equipment_id')} {result.get('window_hours')}h: 검사 {result.get('total_inspections')}건{rec}"
    if name == "recommend_retrain":
        mode = result.get("mode")
        if result.get("skipped"):
            return f"건너뜀 — 이미 진행 중 ({result.get('candidate_version', '?')})"
        if mode == "auto":
            return f"재학습 시작됨 → {result.get('candidate_version', '신규 모델')} (학습 중)"
        if mode == "notify":
            return "알림만 발송 (실행 안 함)"
        if result.get("deduped"):
            return f"기존 승인 요청에 합침 ({result.get('approval_id', 'pending')})"
        return f"승인 대기 등록 ({result.get('approval_id', 'pending')})"
    return "완료"


def stream_mlops_agent(line_id: str = "ALL", use_llm: bool = True, autonomy: str = "approval") -> Iterator[dict]:
    """Streaming MLOps agent: yields SSE-friendly events as it reasons.

    ``autonomy`` controls the retrain action: auto / approval / notify.
    Event types: tool_call / tool_result / token / done — same contract the
    inspection chat uses, so the frontend can reuse its stream handler.
    """
    evidence_text = _build_mlops_evidence(line_id)
    trace_key = f"MLOPS-{line_id}"
    autonomy_note = _AUTONOMY_PROMPT.get(autonomy, _AUTONOMY_PROMPT["approval"])
    has_key = bool(os.environ.get("LUXIA_API_KEY", "").strip())

    if not use_llm or not has_key:
        msg = (
            "[룰 기반 판단 — LLM 미사용] 현재 운영 상태 요약만 제공합니다. "
            "자동 재학습 판단에는 LLM이 필요합니다.\n" + evidence_text
        )
        for chunk in _chunk_text(msg):
            yield {"type": "token", "text": chunk}
        yield {"type": "done", "final_action": msg, "agent_mode": "stub", "tool_calls": [], "agent_kind": "mlops"}
        return

    messages: list[dict] = [
        {"role": "system", "content": _MLOPS_SYSTEM_PROMPT + "\n\n" + autonomy_note},
        {
            "role": "user",
            "content": (
                "다음 운영 상태를 분석하고 모델 재학습 필요 여부를 판단하세요. "
                "필요하면 도구로 추세를 직접 조회한 뒤 결론을 내리세요.\n\n" + evidence_text
            ),
        },
    ]
    schemas = _mlops_tool_schemas()
    registry = _mlops_registry(autonomy)
    tool_calls_log: list[dict] = []

    for iteration in range(_MLOPS_STREAM_MAX_ITERATIONS):
        last_turn = iteration == _MLOPS_STREAM_MAX_ITERATIONS - 1
        resp = luxia_client.chat_with_tools(messages, tools=None if last_turn else schemas)
        msg = resp.get("choices", [{}])[0].get("message", {}) or {}
        tool_calls = msg.get("tool_calls") if not last_turn else None

        if tool_calls:
            messages.append(msg)
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "name": name, "args": args}

                tool_fn = registry.get(name)
                if tool_fn is None:
                    result: object = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = tool_fn(**args)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("mlops agent tool %s failed: %s", name, exc)
                        result = {"error": str(exc)}

                tool_calls_log.append({"name": name, "args": args, "result": result})
                yield {"type": "tool_result", "name": name, "summary": _summarize_mlops_tool(name, result), "result": result}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", name),
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        content = msg.get("content") or "판단 결과를 생성하지 못했습니다."
        for chunk in _chunk_text(content):
            yield {"type": "token", "text": chunk}

        trace = {
            "inspection_id": trace_key,
            "agent_kind": "mlops",
            "messages": messages + [{"role": "assistant", "content": content}],
            "tool_calls": tool_calls_log,
            "final_action": content,
            "agent_mode": "llm",
        }
        try:
            trace_id = insert_agent_trace(trace_key, trace)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to persist mlops agent trace: %s", exc)
            trace_id = "trace-error"

        yield {
            "type": "done",
            "final_action": content,
            "agent_mode": "llm",
            "tool_calls": tool_calls_log,
            "trace_id": trace_id,
            "agent_kind": "mlops",
            "autonomy": autonomy,
        }
        return


# ---------------------------------------------------------------------------
# Inspection agent — streaming variant (B-7)
# ---------------------------------------------------------------------------
# The flagship per-wafer agent previously only ran in a background thread while
# the UI polled for the persisted trace. This streams the same loop so the
# situation-judgment → evidence → tool-exec flow is visible live, reusing the
# SSE event contract (tool_call / tool_result / token / done) the chat/MLOps
# streams already use.

_INSPECTION_STREAM_MAX_ITERATIONS = 5


def _summarize_inspection_tool(name: str, result: object) -> str:
    if isinstance(result, list):
        return f"유사 사례 {len(result)}건 반환"
    if not isinstance(result, dict):
        return str(result)[:80] if result is not None else ""
    if result.get("error"):
        return f"오류: {result['error']}"
    if name == "get_equipment_history":
        rec = " · 반복성" if result.get("is_recurring") else ""
        return (
            f"{result.get('equipment_id')} {result.get('window_hours')}h: "
            f"검사 {result.get('total_inspections')}건, 동일결함 {result.get('same_defect_count')}회{rec}"
        )
    if name == "get_metrology_trend":
        return f"{result.get('metric')} {result.get('trend')} · Δ{result.get('delta')} ({result.get('pct_change')}%, n={result.get('n')})"
    if name == "get_mlops_state":
        pm = result.get("production_model") or {}
        de = result.get("latest_drift_event") or {}
        return f"운영모델 F1 {pm.get('f1_score', '?')} · drift {de.get('status', '?')}"
    if name in ("inspect_image", "compare_with_past_wafer"):
        obs = result.get("observation", "")
        return (obs[:80] + "…") if len(obs) > 80 else (obs or "이미지 분석 완료")
    if name == "enqueue_for_review":
        return "검토 큐 등록"
    if name == "trigger_critical_alert":
        return f"긴급 알림 승인 대기 ({result.get('approval_id', 'pending')})"
    if name == "recommend_retrain":
        return f"재학습 권고 ({result.get('status', result.get('mode', '?'))})"
    if name == "escalate_to_mlops":
        used = result.get("mlops_tools_used") or []
        dec = (result.get("mlops_decision") or "").strip().replace("\n", " ")
        dec_short = (dec[:70] + "…") if len(dec) > 70 else dec
        return f"MLOps 에이전트 위임 → 도구 {len(used)}개 사용 · {dec_short}"
    return "완료"


def stream_inspection_agent(evidence: dict) -> Iterator[dict]:
    """Streaming inspection agent: yields SSE-friendly events as it reasons.

    Event types: tool_call / tool_result / token / done — identical contract to
    the chat and MLOps streams so the frontend can reuse its handler.
    """
    inspection_id = evidence.get("inspection_id", "unknown")
    image_urls: list[str] = evidence.get("image_urls", []) or []
    evidence_text = _build_evidence_text(evidence)
    use_llm = bool(evidence.get("use_llm", True))
    has_key = bool(os.environ.get("LUXIA_API_KEY", "").strip())

    if not use_llm or not has_key:
        rag_cases = evidence.get("rag_cases") or []
        first_action = (
            rag_cases[0].get("action") if rag_cases and isinstance(rag_cases[0], dict) else None
        ) or "엔지니어 검토 큐에서 수동 확인"
        msg = (
            f"[룰 기반 판단 — LLM 미사용] {evidence.get('defect_type', '결함')} 패턴, "
            f"위험도 {evidence.get('risk_level', '?')} "
            f"(score {evidence.get('risk_score', 0):.2f}). 권장 우선 조치: {first_action}."
        )
        for chunk in _chunk_text(msg):
            yield {"type": "token", "text": chunk}
        yield {"type": "done", "final_action": msg, "agent_mode": "stub", "tool_calls": [], "agent_kind": "inspection"}
        return

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"다음 Evidence를 분석하고 적절한 대응 action을 결정하세요.\n\n{evidence_text}"},
    ]
    registry = _get_tool_registry()
    tool_calls_log: list[dict] = []

    for iteration in range(_INSPECTION_STREAM_MAX_ITERATIONS):
        last_turn = iteration == _INSPECTION_STREAM_MAX_ITERATIONS - 1
        resp = luxia_client.chat_with_tools(
            messages,
            tools=None if last_turn else _TOOL_SCHEMAS,
            image_urls=image_urls if iteration == 0 else None,
        )
        msg = resp.get("choices", [{}])[0].get("message", {}) or {}
        tool_calls = msg.get("tool_calls") if not last_turn else None

        if tool_calls:
            messages.append(msg)
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "name": name, "args": args}

                tool_fn = registry.get(name)
                if tool_fn is None:
                    result: object = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = tool_fn(**args)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("inspection agent tool %s failed: %s", name, exc)
                        result = {"error": str(exc)}

                tool_calls_log.append({"name": name, "args": args, "result": result})
                yield {"type": "tool_result", "name": name, "summary": _summarize_inspection_tool(name, result), "result": result}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", name),
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        content = msg.get("content") or "판단 결과를 생성하지 못했습니다."
        for chunk in _chunk_text(content):
            yield {"type": "token", "text": chunk}

        trace = {
            "inspection_id": inspection_id,
            "agent_kind": "inspection",
            "messages": messages + [{"role": "assistant", "content": content}],
            "tool_calls": tool_calls_log,
            "final_action": content,
            "agent_mode": "llm",
        }
        try:
            trace_id = insert_agent_trace(inspection_id, trace)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to persist inspection agent trace: %s", exc)
            trace_id = "trace-error"

        yield {
            "type": "done",
            "final_action": content,
            "agent_mode": "llm",
            "tool_calls": tool_calls_log,
            "trace_id": trace_id,
            "agent_kind": "inspection",
        }
        return

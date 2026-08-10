from __future__ import annotations

from app.services.schemas import InspectRequest


DEFECT_PLAYBOOK: dict[str, dict[str, object]] = {
    "Center": {
        "possible_causes": ["CMP center pressure drift", "photo focus offset", "slurry delivery instability"],
        "metrology_checks": ["center CD trend", "film thickness uniformity", "focus/exposure log"],
        "process_checks": ["CMP pad conditioning", "slurry flow", "lithography focus recipe"],
        "next_actions": ["center-zone trend를 같은 lot/recipe 기준으로 비교", "metrology slice를 확인 후 엔지니어 review 기록"],
    },
    "Donut": {
        "possible_causes": ["deposition temperature zone drift", "showerhead non-uniformity", "plasma density ring pattern"],
        "metrology_checks": ["film thickness radial profile", "ellipsometry uniformity", "temperature zone log"],
        "process_checks": ["CVD showerhead PM history", "zone temperature alarm", "recipe change history"],
        "next_actions": ["ring pattern 반복 여부 확인", "증착 장비 PM 이력과 thickness map을 비교"],
    },
    "Edge-Loc": {
        "possible_causes": ["edge grip mark", "robot handoff contact", "edge handling instability"],
        "metrology_checks": ["edge inspection image", "edge exclusion trend", "overlay edge slice"],
        "process_checks": ["handler robot log", "load port contact", "cassette slot condition"],
        "next_actions": ["edge 위치 defect를 이송 로그와 대조", "동일 handler 반복 시 equipment memory에 남김"],
    },
    "Edge-Ring": {
        "possible_causes": ["EBR pressure drift", "CMP edge exclusion shift", "edge ring wear"],
        "metrology_checks": ["edge CD/overlay trend", "edge thickness profile", "edge inspection count"],
        "process_checks": ["EBR nozzle pressure", "CMP edge exclusion setting", "edge ring PM history"],
        "next_actions": ["동일 tool/recipe edge trend 확인", "High risk이면 자동 통과 금지 후 lot review"],
    },
    "Loc": {
        "possible_causes": ["local chamber particle", "temporary contamination", "localized process instability"],
        "metrology_checks": ["local defect density", "chamber particle trend", "surface inspection ROI"],
        "process_checks": ["recent chamber clean", "local gas flow alarm", "PM event history"],
        "next_actions": ["같은 chamber에서 Loc 반복 여부 확인", "국소 hotspot 위치를 lot history와 비교"],
    },
    "Random": {
        "possible_causes": ["cleanroom particle event", "cleaning/filter issue", "temporary contamination"],
        "metrology_checks": ["particle count trend", "random defect density", "surface contamination review"],
        "process_checks": ["cleaning recipe", "filter replacement log", "cleanroom particle monitor"],
        "next_actions": ["particle trend와 세정 이력을 함께 확인", "전체 accuracy보다 critical miss 여부를 우선 확인"],
    },
    "Scratch": {
        "possible_causes": ["robot arm contact", "cassette slot scratch", "load port handling damage"],
        "metrology_checks": ["linear hotspot ROI", "surface inspection zoom", "post-handling inspection"],
        "process_checks": ["robot path", "cassette slot", "load port contact mark"],
        "next_actions": ["길고 얇은 hotspot은 자동 승인 금지", "handling/contact checklist를 먼저 실행"],
    },
    "Near-full": {
        "possible_causes": ["recipe mismatch", "chamber event", "operator override or lot start condition error"],
        "metrology_checks": ["full-wafer defect density", "film uniformity", "recipe-to-wafer trace"],
        "process_checks": ["lot start recipe", "chamber event log", "operator override log"],
        "next_actions": ["즉시 lot hold 검토", "recipe 변경/override log를 확인 후 다음 lot 투입 전 공유"],
    },
    "None": {
        "possible_causes": ["normal baseline", "low-intensity noise", "temporary image artifact"],
        "metrology_checks": ["baseline CD", "baseline overlay", "baseline thickness"],
        "process_checks": ["normal lot condition", "inspection tool baseline", "recipe baseline"],
        "next_actions": ["정상 lot baseline으로 저장", "낮은 confidence거나 hotspot이 있으면 review queue로 올림"],
    },
}

METROLOGY_WINDOWS = {
    "cd_nm": {"target": 32.0, "warning_low": 29.0, "warning_high": 35.0, "critical_low": 27.5, "critical_high": 36.5},
    "overlay_nm": {"target": 4.0, "warning_high": 5.5, "critical_high": 7.0},
    "film_thickness_nm": {"target": 88.0, "warning_low": 80.0, "warning_high": 96.0, "critical_low": 74.0, "critical_high": 104.0},
    "roughness_nm": {"target": 1.2, "warning_high": 2.4, "critical_high": 3.5},
    "yield_proxy": {"target": 0.982, "warning_low": 0.965, "critical_low": 0.94},
}


def build_process_context(request: InspectRequest) -> dict[str, object]:
    context: dict[str, object] = {
        "lot_id": request.lot_id,
        "wafer_id": request.wafer_id,
        "line_id": request.line_id,
        "process_step": request.process_step,
        "tool_id": request.equipment_id,
        "recipe_id": request.recipe_id,
    }
    if request.vision_source:
        context["vision_evidence"] = {
            "source": request.vision_source,
            "statistical_score": request.vision_stat_score,
            "ai_score": request.vision_ai_score,
            "direction": request.vision_direction,
            "sequence_label": request.vision_sequence_label,
            "first_anomaly": request.vision_first_anomaly,
            "anomaly_area_ratio": request.vision_anomaly_area_ratio,
            "operator_note": request.operator_note,
        }
    return context


def build_metrology_context(request: InspectRequest, hotspot_ratio: float) -> dict[str, object]:
    defect_count = request.defect_count
    if defect_count is None and not request.vision_source:
        defect_count = int(max(1, round(hotspot_ratio * 1200))) if hotspot_ratio > 0 else 0
    return {
        "cd_nm": request.cd_nm,
        "overlay_nm": request.overlay_nm,
        "film_thickness_nm": request.film_thickness_nm,
        "roughness_nm": request.roughness_nm,
        "defect_count": defect_count,
        "yield_proxy": request.yield_proxy,
        "hotspot_ratio": round(hotspot_ratio, 4),
    }


def build_action_card(
    *,
    defect_type: str,
    risk_level: str,
    confidence: float,
    process_context: dict[str, object],
    metrology: dict[str, object],
    cases: list[dict[str, str]],
    metrology_rule_hits: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    playbook = DEFECT_PLAYBOOK.get(defect_type, DEFECT_PLAYBOOK["Random"])
    first_case = cases[0] if cases else {}
    metrology_rule_hits = metrology_rule_hits or []
    metrology_actions = [str(hit["action"]) for hit in metrology_rule_hits]
    review_rule = (
        "자동 승인 가능. 단, hotspot이 보이면 baseline과 비교"
        if risk_level == "Low" and confidence >= 0.85
        else "엔지니어 2차 확인 필요"
        if risk_level == "Medium"
        else "자동 통과 금지. lot 영향도와 설비 이력 즉시 확인"
    )
    vision_evidence = process_context.get("vision_evidence")
    has_vision_evidence = isinstance(vision_evidence, dict)
    return {
        "title": f"{defect_type} Defect Action Card",
        "defect_type": defect_type,
        "risk_level": risk_level,
        "confidence": confidence,
        "risk_statement": _risk_statement(defect_type, risk_level),
        "process_context": process_context,
        "metrology": metrology,
        "metrology_rule_hits": metrology_rule_hits,
        "metrology_risk_delta": metrology_risk_delta(metrology_rule_hits),
        "possible_causes": playbook["possible_causes"],
        "metrology_checks": playbook["metrology_checks"],
        "process_checks": playbook["process_checks"],
        "next_actions": [*metrology_actions, *playbook["next_actions"]],
        "rag_case": {
            "title": first_case.get("title", "표준 결함 대응 SOP"),
            "action": first_case.get("action", "Human-in-the-loop 검토 큐로 이동"),
        },
        "human_review_rule": review_rule,
        "source_boundary": (
            "wafer_particle 과제 샘플의 영상 점수와 WaferGuard 판단 보조 결과입니다. "
            "영상 신호만으로 파티클이나 실제 fab root cause를 확정하지 않습니다."
            if has_vision_evidence
            else "WM-811K wafer map + local fixture 기반 판단 보조 카드입니다. 실제 fab root cause를 확정하지 않습니다."
        ),
        "threshold_basis": (
            "영상 통계 2.0 / AI 50.0 샘플 임계값입니다. 실제 fab spec/control limit나 일반화 성능을 의미하지 않습니다."
            if has_vision_evidence
            else "demo SPC-style threshold입니다. 실제 fab spec/control limit가 아니며 구조 검증용 기준입니다."
        ),
    }


def evaluate_metrology_rules(
    defect_type: str,
    process_context: dict[str, object],
    metrology: dict[str, object],
) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    cd_nm = _float_value(metrology.get("cd_nm"))
    overlay_nm = _float_value(metrology.get("overlay_nm"))
    thickness_nm = _float_value(metrology.get("film_thickness_nm"))
    roughness_nm = _float_value(metrology.get("roughness_nm"))
    defect_count = _float_value(metrology.get("defect_count"))
    yield_proxy = _float_value(metrology.get("yield_proxy"))
    step = str(process_context.get("process_step", "Inspection"))
    tool = str(process_context.get("tool_id", "tool"))
    vision = process_context.get("vision_evidence")

    if isinstance(vision, dict):
        stat_score = _float_value(vision.get("statistical_score"))
        ai_score = _float_value(vision.get("ai_score"))
        area_ratio = _float_value(vision.get("anomaly_area_ratio"))
        direction = str(vision.get("direction") or "위치 미상")
        sequence = str(vision.get("sequence_label") or "시점 미상")
        if stat_score is not None and ai_score is not None and stat_score >= 2.0 and ai_score >= 50.0:
            area_text = f", 이상 면적 {area_ratio * 100:.2f}%" if area_ratio is not None else ""
            hits.append(_rule_hit(
                "Critical",
                "Statistical and AI vision anomaly agreement",
                f"영상 통계 {stat_score:.2f} / AI {ai_score:.2f}, {sequence}, {direction}{area_text}",
                "해당 시점 원본·히트맵을 검토하고 같은 챔버의 저항 추세와 PM/세정 이력을 대조",
                "Vision AI",
                0.12,
            ))
        elif (stat_score is not None and stat_score >= 2.0) or (ai_score is not None and ai_score >= 50.0):
            hits.append(_rule_hit(
                "Warning",
                "Single-detector vision anomaly",
                f"영상 통계 {stat_score if stat_score is not None else '-'} / AI {ai_score if ai_score is not None else '-'}, {sequence}, {direction}",
                "통계·AI 불일치 원인을 확인하고 촬영 조건 변화와 원본 영상을 우선 검토",
                "Vision AI",
                0.07,
            ))
        elif (stat_score is not None and stat_score >= 1.2) or (ai_score is not None and ai_score >= 35.0):
            hits.append(_rule_hit(
                "Warning",
                "Vision caution threshold",
                f"영상 통계 {stat_score if stat_score is not None else '-'} / AI {ai_score if ai_score is not None else '-'}, {sequence}",
                "다음 시점 영상을 추적하고 기준영상·조명 조건을 재확인",
                "Vision AI",
                0.04,
            ))

    if cd_nm is not None:
        window = METROLOGY_WINDOWS["cd_nm"]
        if cd_nm < window["critical_low"] or cd_nm > window["critical_high"]:
            hits.append(_rule_hit("Critical", "CD out of control", f"CD {cd_nm}nm is outside {window['critical_low']}-{window['critical_high']}nm", "CD trend와 lithography/etch recipe 변경 이력을 즉시 확인", "Metrology", 0.10))
        elif cd_nm < window["warning_low"] or cd_nm > window["warning_high"]:
            hits.append(_rule_hit("Warning", "CD drift", f"CD {cd_nm}nm is outside warning window {window['warning_low']}-{window['warning_high']}nm", "동일 lot의 CD slice와 최근 recipe drift 확인", "Metrology", 0.05))

    if overlay_nm is not None:
        window = METROLOGY_WINDOWS["overlay_nm"]
        if overlay_nm >= window["critical_high"]:
            hits.append(_rule_hit("Critical", "Overlay excursion", f"Overlay {overlay_nm}nm exceeds {window['critical_high']}nm", "overlay trend와 align/focus log를 확인하고 review queue로 승격", "Metrology", 0.10))
        elif overlay_nm >= window["warning_high"]:
            hits.append(_rule_hit("Warning", "Overlay drift", f"Overlay {overlay_nm}nm exceeds warning limit {window['warning_high']}nm", "edge/center overlay slice를 defect 위치와 비교", "Metrology", 0.05))

    if thickness_nm is not None:
        window = METROLOGY_WINDOWS["film_thickness_nm"]
        if thickness_nm < window["critical_low"] or thickness_nm > window["critical_high"]:
            hits.append(_rule_hit("Critical", "Film thickness excursion", f"Film thickness {thickness_nm}nm is outside {window['critical_low']}-{window['critical_high']}nm", "film uniformity map과 deposition/CMP 조건을 확인", "Process", 0.10))
        elif thickness_nm < window["warning_low"] or thickness_nm > window["warning_high"]:
            hits.append(_rule_hit("Warning", "Film thickness drift", f"Film thickness {thickness_nm}nm is outside warning window {window['warning_low']}-{window['warning_high']}nm", "thickness radial profile을 defect pattern과 비교", "Process", 0.05))

    if roughness_nm is not None:
        window = METROLOGY_WINDOWS["roughness_nm"]
        if roughness_nm >= window["critical_high"]:
            hits.append(_rule_hit("Critical", "Surface roughness excursion", f"Roughness {roughness_nm}nm exceeds {window['critical_high']}nm", "surface scan ROI와 CMP/cleaning 조건을 함께 확인", "Metrology", 0.09))
        elif roughness_nm >= window["warning_high"]:
            hits.append(_rule_hit("Warning", "Surface roughness drift", f"Roughness {roughness_nm}nm exceeds warning limit {window['warning_high']}nm", "roughness trend와 defect ROI를 같은 lot 기준으로 비교", "Metrology", 0.04))

    if defect_count is not None:
        if defect_count >= 600:
            hits.append(_rule_hit("Critical", "High defect density", f"Defect count {int(defect_count)} exceeds lot-hold review threshold", "lot hold 여부와 동일 tool 반복 defect trend 확인", "Yield", 0.12))
        elif defect_count >= 180:
            hits.append(_rule_hit("Warning", "Elevated defect count", f"Defect count {int(defect_count)} exceeds review threshold", f"동일 {tool} 최근 24시간 defect count trend 확인", "Yield", 0.06))

    if yield_proxy is not None:
        window = METROLOGY_WINDOWS["yield_proxy"]
        if yield_proxy < window["critical_low"]:
            hits.append(_rule_hit("Critical", "Yield proxy drop", f"Yield proxy {yield_proxy:.3f} is below {window['critical_low']}", "scrap/rework 영향도와 upstream lot 이력을 확인", "Yield", 0.10))
        elif yield_proxy < window["warning_low"]:
            hits.append(_rule_hit("Warning", "Yield proxy drift", f"Yield proxy {yield_proxy:.3f} is below {window['warning_low']}", "defect mix 변화와 yield proxy를 같은 shift report에 남김", "Yield", 0.05))

    hits.extend(_defect_specific_hits(defect_type, step, cd_nm, overlay_nm, thickness_nm, defect_count))
    return _dedupe_hits(hits)


def metrology_risk_delta(rule_hits: list[dict[str, object]]) -> float:
    return round(min(sum(float(hit.get("risk_delta", 0.0)) for hit in rule_hits), 0.22), 3)


def has_critical_metrology_hit(rule_hits: list[dict[str, object]]) -> bool:
    return any(hit.get("severity") == "Critical" for hit in rule_hits)


def _defect_specific_hits(
    defect_type: str,
    step: str,
    cd_nm: float | None,
    overlay_nm: float | None,
    thickness_nm: float | None,
    defect_count: float | None,
) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    if defect_type in {"Edge-Ring", "Edge-Loc"} and overlay_nm is not None and overlay_nm >= 5.0:
        hits.append(_rule_hit("Warning", "Edge defect with overlay drift", f"{defect_type} plus overlay {overlay_nm}nm", "edge overlay slice와 edge handling/EBR 조건을 함께 확인", "Metrology", 0.05))
    if defect_type in {"Donut", "Near-full"} and thickness_nm is not None and (thickness_nm < 82.0 or thickness_nm > 98.0):
        hits.append(_rule_hit("Warning", "Pattern matches film uniformity risk", f"{defect_type} with film thickness {thickness_nm}nm", "radial thickness uniformity와 chamber zone log 확인", "Process", 0.06))
    if defect_type == "Scratch" and defect_count is not None and defect_count >= 120:
        hits.append(_rule_hit("Critical", "Scratch count requires handling review", f"Scratch count proxy {int(defect_count)} after {step}", "robot arm/cassette/load-port contact checklist를 우선 실행", "Equipment", 0.08))
    if defect_type == "Random" and defect_count is not None and defect_count >= 160:
        hits.append(_rule_hit("Warning", "Random particle trend", f"Random defect count proxy {int(defect_count)}", "cleanroom particle trend와 cleaning/filter 이력 확인", "Yield", 0.05))
    if defect_type == "Near-full" and defect_count is not None and defect_count >= 500:
        hits.append(_rule_hit("Critical", "Near-full lot-level event", f"Near-full defect count proxy {int(defect_count)}", "즉시 lot hold 검토 후 recipe/override/chamber event 확인", "Process", 0.12))
    if defect_type in {"Center", "Loc"} and cd_nm is not None and (cd_nm < 29.0 or cd_nm > 35.0):
        hits.append(_rule_hit("Warning", "Local defect with CD drift", f"{defect_type} plus CD {cd_nm}nm", "local ROI의 CD slice와 focus/etch bias를 비교", "Metrology", 0.05))
    return hits


def _rule_hit(
    severity: str,
    signal: str,
    evidence: str,
    action: str,
    owner: str,
    risk_delta: float,
) -> dict[str, object]:
    return {
        "severity": severity,
        "signal": signal,
        "evidence": evidence,
        "action": action,
        "owner": owner,
        "risk_delta": risk_delta,
    }


def _float_value(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _dedupe_hits(hits: list[dict[str, object]]) -> list[dict[str, object]]:
    seen = set()
    deduped = []
    for hit in hits:
        key = (hit["severity"], hit["signal"], hit["owner"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


def _risk_statement(defect_type: str, risk_level: str) -> str:
    if defect_type == "None":
        return "정상 baseline 후보입니다. 낮은 confidence 또는 hotspot이 있으면 재검토합니다."
    if risk_level == "High":
        return "후속 공정 투입 전 품질 리스크를 먼저 확인해야 하는 항목입니다."
    if risk_level == "Medium":
        return "자동 판정만으로 종료하지 말고 metrology/process context를 함께 확인합니다."
    return "현재는 낮은 위험으로 보이지만 lot/tool 반복성은 계속 추적합니다."

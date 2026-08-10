import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.main import app


def main() -> None:
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200, health.text

    inspection = client.post(
        "/api/v1/inspect",
        json={
            "lot_id": "LOT-SMOKE-042",
            "wafer_id": "WF-SMOKE-001",
            "line_id": "LINE-7",
            "equipment_id": "ETCH-02",
            "process_step": "Etch",
            "recipe_id": "RCP-SMOKE-ETCH",
            "image_source": "public_proxy",
            "proxy_dataset": "mvtec-ad",
            "defect_hint": "Scratch",
            "cd_nm": 31.8,
            "overlay_nm": 7.2,
            "film_thickness_nm": 91.2,
            "roughness_nm": 3.8,
            "defect_count": 240,
        },
    )
    assert inspection.status_code == 200, inspection.text
    payload = inspection.json()
    assert payload["defect_type"] == "Scratch"
    assert payload["overlay_url"].endswith("_overlay.png")
    assert payload["roi_url"].endswith("_roi.png")
    assert payload["image_source"] == "public_proxy"
    assert payload["proxy_status"].startswith("WM-811K wafer map")
    assert payload["action_card"]["defect_type"] == "Scratch"
    assert payload["process_context"]["process_step"] == "Etch"
    assert payload["metrology"]["cd_nm"] == 31.8
    assert payload["metrology"]["roughness_nm"] == 3.8
    assert payload["metrology"]["defect_count"] == 240
    assert payload["status"] == "review_required"
    assert payload["action_card"]["metrology_rule_hits"]
    assert payload["action_card"]["metrology_risk_delta"] > 0

    vision_inspection = client.post(
        "/api/v1/inspect",
        json={
            "lot_id": "LOT-WAFER-VISION-SMOKE",
            "wafer_id": "WF-CH061-D24-SMOKE",
            "line_id": "LINE-7",
            "equipment_id": "Chamber_061",
            "process_step": "Inspection",
            "recipe_id": "RCP-WAFER-VISION-COMPARE",
            "image_source": "public_proxy",
            "proxy_dataset": "wafer-particle-sample",
            "defect_hint": "Edge-Loc",
            "defect_count": None,
            "yield_proxy": 0.982,
            "operator_note": "Vision integration smoke test; sample evidence only.",
            "vision_source": "wafer_particle comparison",
            "vision_stat_score": 32.7134,
            "vision_ai_score": 74.07,
            "vision_direction": "오른쪽 아래",
            "vision_sequence_label": "Day 24",
            "vision_first_anomaly": "Day 24",
            "vision_anomaly_area_ratio": 0.1355,
            "use_llm": False,
        },
    )
    assert vision_inspection.status_code == 200, vision_inspection.text
    vision_payload = vision_inspection.json()
    vision_hits = [
        hit
        for hit in vision_payload["action_card"]["metrology_rule_hits"]
        if hit.get("owner") == "Vision AI"
    ]
    assert vision_payload["process_context"]["vision_evidence"]["direction"] == "오른쪽 아래"
    assert vision_payload["metrology"]["defect_count"] is None
    assert vision_hits and vision_hits[0]["severity"] == "Critical"
    assert "wafer_particle" in vision_payload["action_card"]["source_boundary"]

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["total_inspections"] >= 1

    evaluation = client.get("/api/v1/evaluation/wm811k")
    assert evaluation.status_code == 200, evaluation.text
    eval_payload = evaluation.json()
    assert eval_payload["dataset"]["name"] == "WM-811K"
    assert len(eval_payload["confusion_matrix"]["labels"]) == 9
    assert eval_payload["summary"]["critical_missed_as_normal"] > 0
    assert "fixture" in eval_payload["dataset"]["mode"]

    rag_eval = client.get("/api/v1/rag/evaluation")
    assert rag_eval.status_code == 200, rag_eval.text
    assert rag_eval.json()["summary"]["question_count"] >= 10

    rag_index = client.get("/api/v1/rag/index")
    assert rag_index.status_code == 200, rag_index.text
    index_payload = rag_index.json()
    assert index_payload["indexed"] >= 1
    assert len(index_payload["defect_types"]) == 9

    rag_search = client.get(
        "/api/v1/rag/search",
        params={"defect_type": "Edge-Ring", "q": "EBR nozzle", "k": 6},
    )
    assert rag_search.status_code == 200, rag_search.text
    search_payload = rag_search.json()
    assert search_payload["defect_type"] == "Edge-Ring"
    assert search_payload["cases"], "expected at least one similar case"

    proxy_datasets = client.get("/api/v1/proxy-datasets")
    assert proxy_datasets.status_code == 200, proxy_datasets.text
    assert "반도체 fab 이미지로 표현하면 안 됩니다" in proxy_datasets.json()["source_boundary"]

    thresholds = client.get("/api/v1/metrology/thresholds")
    assert thresholds.status_code == 200, thresholds.text
    assert thresholds.json()["fields"]

    drift = client.post("/api/v1/mlops/drift", json={"intensity": "strong", "line_id": "LINE-7"})
    assert drift.status_code == 200, drift.text

    # approve/reject with an engineer comment → fed back as agent context
    from app.services import storage as _storage  # noqa: PLC0415

    apr_id = _storage.insert_pending_approval(
        "MLOPS-ALL", "recommend_retrain", {"trigger_type": "manual"}, "smoke: drift 0.42 권고"
    )
    rejected = client.post(
        f"/api/v1/approvals/{apr_id}/reject",
        json={"comment": "스모크: 신규 디바이스 편입이라 재학습 보류"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["comment"] == "스모크: 신규 디바이스 편입이라 재학습 보류"
    feedback = _storage.recent_human_feedback(None, None, limit=5)
    assert any(f.get("comment") for f in feedback), "approval comment should surface in human feedback"

    # MLOps agent chat endpoint streams SSE (stub path, no LLM required)
    mlops_chat = client.post(
        "/api/v1/mlops/agent/chat/stream",
        json={"message": "지금 재학습 필요해?", "use_llm": False},
    )
    assert mlops_chat.status_code == 200, mlops_chat.text
    assert '"type"' in mlops_chat.text and '"done"' in mlops_chat.text

    demo = client.post(
        "/api/v1/demo/seed",
        json={"line_id": "LINE-SMOKE", "reviewer": "smoke-test", "include_reviews": True},
    )
    assert demo.status_code == 200, demo.text
    assert demo.json()["created_count"] == 9
    assert demo.json()["reviewed_count"] == 9

    handoff = client.post(
        "/api/v1/handoff/report",
        json={
            "shift_from": "day",
            "shift_to": "night",
            "line_id": "LINE-7",
            "operator": "smoke-test",
            "note": "ETCH-02 scratch 추세 확인 필요",
        },
    )
    assert handoff.status_code == 200, handoff.text
    assert "다음 근무자 체크리스트" in handoff.json()["markdown"]

    scheduled_a = client.post(
        "/api/v1/handoff/report",
        json={
            "shift_from": "day",
            "shift_to": "night",
            "line_id": "LINE-SMOKE",
            "operator": "smoke-test",
            "note": "자동 초안 중복 방지 테스트",
            "scheduled_for": "23:59",
            "reuse_existing": True,
        },
    )
    assert scheduled_a.status_code == 200, scheduled_a.text
    scheduled_b = client.post(
        "/api/v1/handoff/report",
        json={
            "shift_from": "day",
            "shift_to": "night",
            "line_id": "LINE-SMOKE",
            "operator": "smoke-test",
            "note": "자동 초안 중복 방지 테스트",
            "scheduled_for": "23:59",
            "reuse_existing": True,
        },
    )
    assert scheduled_b.status_code == 200, scheduled_b.text
    assert scheduled_b.json()["id"] == scheduled_a.json()["id"]
    assert scheduled_b.json().get("reused_existing") is True

    state = client.get("/api/v1/mlops/state")
    assert state.status_code == 200, state.text

    # MLOps agent (rule-based fallback to avoid an LLM round-trip in the smoke test)
    mlops_agent = client.post("/api/v1/mlops/agent/run", json={"line_id": "LINE-7", "use_llm": False})
    assert mlops_agent.status_code == 200, mlops_agent.text
    assert mlops_agent.json().get("agent_kind") == "mlops"

    copilot = client.get("/api/v1/copilot/ops?line_id=LINE-7")
    assert copilot.status_code == 200, copilot.text
    assert copilot.json()["action_recommendations"]

    # Verify new endpoints
    approvals = client.get("/api/v1/pending-approvals")
    assert approvals.status_code == 200, approvals.text

    db_overview = client.get("/api/v1/db/overview")
    assert db_overview.status_code == 200, db_overview.text
    assert any(t["name"] == "inspections" for t in db_overview.json()["tables"])

    db_rows = client.get("/api/v1/db/tables/inspections?limit=5")
    assert db_rows.status_code == 200, db_rows.text
    assert db_rows.json()["total"] >= 1
    assert client.get("/api/v1/db/tables/not_a_table").status_code == 404

    print(
        {
            "health": health.json()["status"],
            "inspection_id": payload["id"],
            "vision_inspection_id": vision_payload["id"],
            "risk": payload["risk_level"],
            "agent_mode": payload.get("agent_mode", "n/a"),
            "metrology_rule_hits": len(payload["action_card"]["metrology_rule_hits"]),
            "roi": payload["roi_url"],
            "wm811k_macro_f1": eval_payload["summary"]["macro_f1"],
            "rag_eval_questions": rag_eval.json()["summary"]["question_count"],
            "drift_status": drift.json()["status"],
            "demo_created": demo.json()["created_count"],
            "handoff_id": handoff.json()["id"],
            "pending_approvals": len(approvals.json()),
            "copilot_actions": len(copilot.json()["action_recommendations"]),
            "models": len(state.json()["models"]),
            "mlops_agent": mlops_agent.json().get("agent_kind"),
            "db_tables": len(db_overview.json()["tables"]),
        }
    )


if __name__ == "__main__":
    main()

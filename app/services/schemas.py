from typing import Literal

from pydantic import BaseModel, Field


DefectType = Literal[
    "Center",
    "Donut",
    "Edge-Loc",
    "Edge-Ring",
    "Loc",
    "Random",
    "Scratch",
    "Near-full",
    "None",
]

ProcessStep = Literal[
    "Lithography",
    "Etch",
    "Deposition",
    "CMP",
    "Cleaning",
    "Inspection",
]

ImageSource = Literal[
    "synthetic_wafer",
    "public_proxy",
]


class InspectRequest(BaseModel):
    lot_id: str = Field(default="LOT-DEMO-042", min_length=2, max_length=64)
    wafer_id: str = Field(default="WF-DEMO-001", min_length=2, max_length=64)
    line_id: str = Field(default="LINE-7", min_length=2, max_length=64)
    equipment_id: str = Field(default="ETCH-02", min_length=2, max_length=64)
    process_step: ProcessStep = "Etch"
    recipe_id: str = Field(default="RCP-ETCH-EDGE-02", min_length=2, max_length=80)
    image_source: ImageSource = "synthetic_wafer"
    proxy_dataset: str | None = Field(default=None, max_length=80)
    defect_hint: DefectType | Literal["auto"] = "auto"
    cd_nm: float = Field(default=32.5, ge=0, le=5000)
    overlay_nm: float = Field(default=4.2, ge=0, le=5000)
    film_thickness_nm: float = Field(default=88.0, ge=0, le=100000)
    roughness_nm: float = Field(default=1.2, ge=0, le=10000)
    defect_count: int | None = Field(default=None, ge=0, le=1000000)
    yield_proxy: float = Field(default=0.982, ge=0, le=1)
    operator_note: str = Field(default="", max_length=500)
    vision_source: str | None = Field(default=None, max_length=80)
    vision_stat_score: float | None = Field(default=None, ge=0, le=100)
    vision_ai_score: float | None = Field(default=None, ge=0, le=100)
    vision_direction: str | None = Field(default=None, max_length=40)
    vision_sequence_label: str | None = Field(default=None, max_length=40)
    vision_first_anomaly: str | None = Field(default=None, max_length=40)
    vision_anomaly_area_ratio: float | None = Field(default=None, ge=0, le=1)
    use_llm: bool = True


class InspectionChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    use_llm: bool = True
    # on-screen AI recommendation (probable causes / next actions) the user is viewing
    extra_context: str = Field(default="", max_length=2000)


class AgentStreamRequest(BaseModel):
    use_llm: bool = True


class ReviewRequest(BaseModel):
    decision: Literal["approved", "needs_review", "false_alarm"]
    reviewer: str = Field(default="engineer", min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)


class DriftRequest(BaseModel):
    intensity: Literal["normal", "mild", "strong"] = "strong"
    line_id: str = Field(default="LINE-7", min_length=2, max_length=64)


class RetrainRequest(BaseModel):
    trigger_type: Literal["scheduled", "drift", "performance", "manual"] = "manual"


class MlopsAgentRequest(BaseModel):
    line_id: str = Field(default="ALL", min_length=2, max_length=64)
    use_llm: bool = True
    # autonomy mode for the retrain trigger:
    #  auto = execute now, approval = queue for human approval, notify = alert only
    autonomy: Literal["auto", "approval", "notify"] = "approval"


class ApprovalResolveRequest(BaseModel):
    # optional engineer comment captured at approve/reject time; fed back to the
    # agent as context so it learns from the human's reasoning.
    comment: str = Field(default="", max_length=500)


class MlopsChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    use_llm: bool = True
    # the on-screen monitoring log the engineer is looking at (optional)
    extra_context: str = Field(default="", max_length=4000)


class PromoteRequest(BaseModel):
    version: str | None = None


class RollbackRequest(BaseModel):
    reason: str = Field(default="F1 score degradation alarm", max_length=300)


class HandoffReportRequest(BaseModel):
    shift_from: Literal["day", "swing", "night"] = "day"
    shift_to: Literal["day", "swing", "night"] = "night"
    line_id: str = Field(default="ALL", min_length=2, max_length=64)
    operator: str = Field(default="shift-lead", min_length=1, max_length=64)
    note: str = Field(default="", max_length=700)
    scheduled_for: str | None = Field(default=None, max_length=16)
    reuse_existing: bool = False


class HandoffEditRequest(BaseModel):
    headline: str | None = Field(default=None, max_length=500)
    scrap_risk: Literal["Low", "Medium", "High"] | None = None
    operator_note: str | None = Field(default=None, max_length=700)
    markdown: str | None = Field(default=None, max_length=8000)


class HandoffSendRequest(BaseModel):
    sender: str = Field(default="shift-lead", min_length=1, max_length=64)
    message: str = Field(default="이대로 전달합니다.", max_length=500)


class AutomationTickRequest(BaseModel):
    line_id: str = Field(default="LINE-7", min_length=2, max_length=64)
    operator: str = Field(default="waferguard-agent", min_length=1, max_length=64)
    shift_from: Literal["day", "swing", "night"] = "day"
    shift_to: Literal["day", "swing", "night"] = "night"
    auto_handoff: bool = True
    drift_check: bool = True


class DemoSeedRequest(BaseModel):
    line_id: str = Field(default="LINE-7", min_length=2, max_length=64)
    reviewer: str = Field(default="demo-engineer", min_length=1, max_length=64)
    include_reviews: bool = True

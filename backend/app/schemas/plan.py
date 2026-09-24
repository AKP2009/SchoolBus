from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlanReEvaluateRequest(BaseModel):
    shift_id: str
    reason: str  # task_overrun | rain | low_health | fatigue_high | manager_edit (or free text)
    # optional: plan as of this time (default: replay time when the machine is replayed, else now)
    now: datetime | None = None


class PlanScheduleItem(BaseModel):
    task_id: str
    priority: int
    p50_min: float | None = None
    fits: bool
    start: datetime | None = None
    end: datetime | None = None


class PlanBreak(BaseModel):
    start: datetime
    end: datetime
    minutes: float


class PlanReEvaluateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    shift_id: str
    fits: list[str]
    moved_to_next_shift: list[str]
    new_order: list[str]
    explanation: str
    # extras from ml.inference.plan.re_evaluate_plan (optional for clients)
    triggers: list[str] = Field(default_factory=list)
    available_min: float | None = None
    schedule: list[PlanScheduleItem] = Field(default_factory=list)
    break_: PlanBreak | None = Field(default=None, alias="break")
    now: datetime | None = None  # the plan's clock
    fatigue: dict[str, Any] | None = None  # the fatigue_log row the fatigue trigger read


class PlanAcceptRequest(BaseModel):
    shift_id: str


class PlanAcceptResponse(BaseModel):
    shift_id: str
    applied: bool

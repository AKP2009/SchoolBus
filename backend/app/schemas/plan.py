from pydantic import BaseModel


class PlanReEvaluateRequest(BaseModel):
    shift_id: str
    reason: str


class PlanReEvaluateResponse(BaseModel):
    shift_id: str
    fits: list[str]
    moved_to_next_shift: list[str]
    new_order: list[str]
    explanation: str


class PlanAcceptRequest(BaseModel):
    shift_id: str


class PlanAcceptResponse(BaseModel):
    shift_id: str
    applied: bool

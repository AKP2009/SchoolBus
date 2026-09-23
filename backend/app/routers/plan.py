from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.plan import (
    PlanAcceptRequest,
    PlanAcceptResponse,
    PlanReEvaluateRequest,
    PlanReEvaluateResponse,
)

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post("/re-evaluate", response_model=PlanReEvaluateResponse)
def re_evaluate(body: PlanReEvaluateRequest) -> PlanReEvaluateResponse:
    raise not_implemented("Plan re-evaluation")


@router.post("/accept", response_model=PlanAcceptResponse)
def accept(body: PlanAcceptRequest) -> PlanAcceptResponse:
    raise not_implemented("Plan accept")

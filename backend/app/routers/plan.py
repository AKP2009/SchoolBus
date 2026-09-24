from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, Principal, forbidden
from app.core.errors import ApiError
from app.repo import Repo, get_repo
from app.runtime import get_engine
from app.schemas.plan import (
    PlanAcceptRequest,
    PlanAcceptResponse,
    PlanReEvaluateRequest,
    PlanReEvaluateResponse,
)
from app.services import tasks as svc

router = APIRouter(prefix="/plan", tags=["plan"])


def _check_shift(repo: Repo, user: Principal, shift_id: str) -> None:
    if user.is_manager:
        return
    shift = repo.shift(shift_id)
    if shift is None:
        raise ApiError(404, "NOT_FOUND", f"Unknown shift {shift_id}.")
    if not user.can_act_for(shift["operator_id"]):
        raise forbidden("Operators can only re-plan their own shift.")


@router.post("/re-evaluate", response_model=PlanReEvaluateResponse)
async def re_evaluate(
    body: PlanReEvaluateRequest,
    user: CurrentUser,
    repo: Annotated[Repo, Depends(get_repo)],
    engine: Annotated[Any, Depends(get_engine)],
) -> PlanReEvaluateResponse:
    """Re-plan the rest of the shift (models.md §12). Triggers detected now (overrun, rain,
    low health, fatigue high) are added to `reason`. Nothing is written until /plan/accept."""

    def run() -> dict[str, Any]:
        _check_shift(repo, user, body.shift_id)
        return svc.re_evaluate(repo, body.shift_id, body.reason, engine, body.now)

    return PlanReEvaluateResponse(**await run_in_threadpool(run))


@router.post("/accept", response_model=PlanAcceptResponse)
async def accept(
    body: PlanAcceptRequest, user: CurrentUser, repo: Annotated[Repo, Depends(get_repo)]
) -> PlanAcceptResponse:
    """Apply the last re-evaluation of the shift to `tasks` (operator or manager accepts)."""

    def run() -> dict[str, Any]:
        _check_shift(repo, user, body.shift_id)
        return svc.accept(repo, body.shift_id)

    return PlanAcceptResponse(**await run_in_threadpool(run))

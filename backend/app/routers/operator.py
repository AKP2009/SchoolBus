from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.auth import UserOrService, forbidden
from app.core.errors import ApiError
from app.repo import Repo, get_repo
from app.schemas.events import OperatorFatigueResponse
from app.services.events import latest_fatigue

router = APIRouter(prefix="/operator", tags=["operator"])


@router.get("/{operator_id}/fatigue", response_model=OperatorFatigueResponse)
async def operator_fatigue(
    operator_id: str, user: UserOrService, repo: Annotated[Repo, Depends(get_repo)]
) -> OperatorFatigueResponse:
    """Latest fatigue level and score (fatigue_log) for the vision service and the voice
    assistant. `stale` = older than 10 minutes of wall-clock time: treat as unknown."""
    if not user.can_act_for(operator_id):
        raise forbidden("Operators can only read their own fatigue level.")
    row = await run_in_threadpool(latest_fatigue, repo, operator_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"No fatigue data for {operator_id} yet.")
    return OperatorFatigueResponse(
        operator_id=operator_id,
        ts=row["ts"],
        shift_id=row.get("shift_id"),
        fatigue_level=row["fatigue_level"],
        fatigue_score=row.get("fatigue_score"),
        perclos_60s=row.get("perclos_60s"),
        stale=row["stale"],
        age_min=row["age_min"],
    )

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.ai_repo import AiRepo, get_ai_repo
from app.core.auth import CurrentUser, forbidden
from app.repo import Repo, get_repo
from app.runtime import get_telemetry
from app.schemas.training import RecommendRequest, RecommendResponse
from app.services.recommender import default_as_of, recommend
from app.services.telemetry import TelemetryHistory

router = APIRouter(prefix="/training", tags=["training"])


@router.post("/recommendations", response_model=RecommendResponse)
async def run_recommendations(
    body: RecommendRequest,
    user: CurrentUser,
    repo: Annotated[Repo, Depends(get_repo)],
    ai: Annotated[AiRepo, Depends(get_ai_repo)],
    telemetry: Annotated[TelemetryHistory, Depends(get_telemetry)],
) -> RecommendResponse:
    """Run the training recommender rules (models.md §10) now, as the daily job does. Writes
    training_recommendations (max 2 open per operator). An operator may run it for themselves;
    all operators (operator_id null) is managers only."""
    if body.operator_id is None and not user.is_manager:
        raise forbidden("Managers only.")
    if body.operator_id is not None and not user.can_act_for(body.operator_id):
        raise forbidden("Operators can only get their own recommendations.")

    def run() -> list[dict[str, Any]]:
        ids = [body.operator_id] if body.operator_id else ai.operator_ids()
        as_of = body.as_of or default_as_of(repo)
        return [recommend(repo, ai, telemetry, op, as_of) for op in ids]

    return RecommendResponse(results=await run_in_threadpool(run))  # type: ignore[arg-type]

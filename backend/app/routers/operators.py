"""GET /operators/{operator_id}/efficiency — summary + recommendations (docs/models.md §10)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.data import get_repo
from app.deps import User, auth
from app.errors import ApiError
from app.schemas import EfficiencyResponse, EfficiencySummaryRow, Recommendation
from ml.inference import task_time as task_time_service
from ml.inference.schemas import EfficiencyRow as MlEfficiencyRow
from ml.inference.schemas import Recommendation as MlRecommendation

router = APIRouter(prefix="/operators", tags=["operators"])


@router.get("/{operator_id}/efficiency", response_model=EfficiencyResponse)
def operator_efficiency(
    user: Annotated[User, Depends(auth)],
    operator_id: str,
    task_type: str | None = None,
    as_of: str | None = None,
) -> EfficiencyResponse:
    repo = get_repo()
    operators = repo.frames()["operators"]
    if operator_id not in set(operators["operator_id"]):
        raise ApiError(404, "OPERATOR_NOT_FOUND", f"Unknown operator_id: {operator_id}")
    try:
        resolved_as_of = as_of or task_time_service.default_as_of()
        summary: list[MlEfficiencyRow] = task_time_service.efficiency_summary(
            operator_id, task_type=task_type, as_of=resolved_as_of
        )
    except FileNotFoundError as exc:
        raise ApiError(
            503, "MODEL_NOT_LOADED", "Task time model is not loaded. Run ml training and restart."
        ) from exc
    open_recs = repo.open_recommendations(operator_id)
    recs: list[MlRecommendation] = task_time_service.recommend(
        operator_id, as_of=resolved_as_of, open_recs=open_recs
    )
    new = [r for r in recs if r.module_id not in {o.module_id for o in open_recs}]
    repo.save_recommendations(new)
    return EfficiencyResponse(
        operator_id=operator_id,
        as_of=resolved_as_of,
        summary=[EfficiencySummaryRow.model_validate(r.model_dump()) for r in summary],
        recommendations=[Recommendation.model_validate(r.model_dump()) for r in recs],
    )

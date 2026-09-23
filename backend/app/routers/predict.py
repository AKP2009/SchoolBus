"""POST /predict/task-time (docs/api_contract.md §predict, plus the efficiency extras)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.data import get_repo
from app.deps import User, auth
from app.errors import ApiError
from app.schemas import PredictRequest, PredictResponse, TaskTimePrediction
from ml.inference import task_time as task_time_service
from ml.inference.schemas import TaskTimePrediction as MlPrediction

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post("/task-time", response_model=PredictResponse)
def predict_task_time(
    body: PredictRequest, user: Annotated[User, Depends(auth)]
) -> PredictResponse:
    repo = get_repo()
    tasks = repo.frames()["tasks"]
    found = set(tasks["task_id"])
    missing = [task_id for task_id in body.task_ids if task_id not in found]
    if missing:
        raise ApiError(404, "TASK_NOT_FOUND", f"Unknown task_ids: {', '.join(missing[:5])}")
    rows = tasks[tasks["task_id"].isin(set(body.task_ids))]
    try:
        preds: list[MlPrediction] = task_time_service.predict(rows, repo.context())
    except FileNotFoundError as exc:
        raise ApiError(
            503, "MODEL_NOT_LOADED", "Task time model is not loaded. Run ml training and restart."
        ) from exc
    repo.save_predictions(preds)
    return PredictResponse(
        predictions=[TaskTimePrediction.model_validate(p.model_dump()) for p in preds]
    )

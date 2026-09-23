from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, forbidden
from app.repo import Repo, get_repo
from app.runtime import get_engine
from app.schemas.predict import TaskTimeRequest, TaskTimeResponse
from app.services.tasks import predict_and_store

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post("/task-time", response_model=TaskTimeResponse)
async def predict_task_time(
    body: TaskTimeRequest,
    user: CurrentUser,
    repo: Annotated[Repo, Depends(get_repo)],
    engine: Annotated[Any, Depends(get_engine)],
) -> TaskTimeResponse:
    """p10 / p50 / p90 minutes and SHAP factors per task, written to `tasks`. Machine health is
    the live overall score (running replay, else v_machine_health_latest). Operators may
    predict their own tasks only (like the tasks update policy)."""

    def run() -> list[dict[str, Any]]:
        if not user.is_manager:
            tasks = repo.tasks(task_ids=body.task_ids)
            if not all(user.can_act_for(o) for o in tasks.operator_id):
                raise forbidden("Operators can only predict their own tasks.")
        return predict_and_store(repo, body.task_ids, engine)

    return TaskTimeResponse(predictions=await run_in_threadpool(run))

from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.predict import TaskTimeRequest, TaskTimeResponse

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post("/task-time", response_model=TaskTimeResponse)
def predict_task_time(body: TaskTimeRequest) -> TaskTimeResponse:
    raise not_implemented("Task time prediction")

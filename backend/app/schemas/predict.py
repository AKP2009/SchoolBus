from pydantic import BaseModel, Field


class TaskTimeRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1)


class TaskTimeFactor(BaseModel):
    feature: str
    label: str
    impact_min: float


class TaskTimePrediction(BaseModel):
    task_id: str
    p10_min: float
    p50_min: float
    p90_min: float
    factors: list[TaskTimeFactor]
    operator_avg_min: float | None = None
    expected_efficiency: float | None = None


class TaskTimeResponse(BaseModel):
    predictions: list[TaskTimePrediction]

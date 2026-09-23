"""Pydantic payloads for the API (docs/api_contract.md + the task-time extras)."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    models: dict[str, str]


class PredictRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1)


class PredictionFactor(BaseModel):
    feature: str
    label: str
    impact_min: float


class TaskTimePrediction(BaseModel):
    task_id: str
    p10_min: float
    p50_min: float
    p90_min: float
    standard_min: float
    expected_efficiency: float
    operator_avg_min: float | None = None
    operator_avg_efficiency: float | None = None
    operator_prev_efficiency: float | None = None
    factors: list[PredictionFactor] = Field(default_factory=list)


class PredictResponse(BaseModel):
    predictions: list[TaskTimePrediction]


class EfficiencySummaryRow(BaseModel):
    task_type: str
    avg_efficiency: float | None = None
    prev_efficiency: float | None = None
    last_task_efficiency: float | None = None
    trend: str | None = None
    fleet_median: float | None = None
    n_tasks: int = 0


class Recommendation(BaseModel):
    operator_id: str
    task_type: str
    module_id: str
    reason: str
    trigger_metric: str = "efficiency"
    trigger_value: float
    sim_module_id: str | None = None
    status: str = "pending"


class EfficiencyResponse(BaseModel):
    operator_id: str
    as_of: str
    summary: list[EfficiencySummaryRow]
    recommendations: list[Recommendation]

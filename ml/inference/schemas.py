"""Pydantic payloads for Model 2 — task time estimation (docs/models.md §2)."""

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class TaskTimeContext(BaseModel):
    """Frames needed to featurize the tasks being predicted."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    shifts: pd.DataFrame
    operators: pd.DataFrame
    machines: pd.DataFrame
    weather: pd.DataFrame
    machine_health_daily: pd.DataFrame
    history: pd.DataFrame | None = None  # completed tasks for the 30d operator ratio


class Factor(BaseModel):
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
    factors: list[Factor] = Field(default_factory=list)


class EfficiencyRow(BaseModel):
    task_type: str
    avg_efficiency: float | None = None
    prev_efficiency: float | None = None
    last_task_efficiency: float | None = None
    trend: str | None = None  # 'up' | 'down' | 'flat'; None when avg or prev is missing
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

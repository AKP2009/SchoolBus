from datetime import datetime

from pydantic import BaseModel


class RecommendRequest(BaseModel):
    """operator_id null = every operator (managers only). as_of null = end of the latest shift."""

    operator_id: str | None = None
    as_of: datetime | None = None


class Recommendation(BaseModel):
    id: int
    module_id: str
    reason: str
    trigger_metric: str | None
    trigger_value: float | None


class TriggerFired(BaseModel):
    metric: str
    value: float
    modules: list[str]


class OperatorRecommendations(BaseModel):
    operator_id: str
    as_of: datetime | None
    triggers: list[TriggerFired]
    created: list[Recommendation]


class RecommendResponse(BaseModel):
    results: list[OperatorRecommendations]

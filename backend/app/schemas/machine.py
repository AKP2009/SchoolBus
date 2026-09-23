from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import MachineComponent


class MachineHealthResponse(BaseModel):
    machine_id: str
    ts: datetime
    overall: float = Field(ge=0, le=1)
    subsystems: dict[str, float]  # engine, cooling, hydraulics, electrical, undercarriage
    anomaly_score: float | None = None
    failure_probability: float | None = None
    likely_component: MachineComponent | None = None

from datetime import datetime
from typing import Any, Literal

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
    # extras from ml.inference.health.compute_health (optional for clients)
    band: Literal["green", "orange", "red"] | None = None
    subsystem_bands: dict[str, str] | None = None
    reasons: list[dict[str, Any]] | None = None


class MachineStateResponse(BaseModel):
    """Motion state from the replay stream (for the vision service)."""

    machine_id: str
    moving: bool  # ground_speed_kmh > 0.5
    ground_speed_kmh: float
    ts: datetime  # replay time of the row it comes from

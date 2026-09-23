from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.common import CameraSector, FatigueLevel, Severity


class AlertEvent(BaseModel):
    """Vision / voice event that becomes a safety_events row and an alert."""

    type: Literal["proximity_breach", "blindspot_intrusion", "fatigue_high", "phone_use", "sos"]
    machine_id: str
    operator_id: str | None = None
    ts: datetime
    severity: Severity
    distance_m: float | None = None
    sector: CameraSector | None = None
    approaching: bool | None = None
    details: dict[str, object] = Field(default_factory=dict)


class FatigueSample(BaseModel):
    """Per-minute fatigue_log row. Not an alert."""

    type: Literal["fatigue_sample"]
    operator_id: str
    shift_id: str
    ts: datetime
    ear_avg: float
    perclos_60s: float
    yawn_count: int
    head_down_events: int
    phone_detected: bool
    fatigue_score: float = Field(ge=0, le=1)
    fatigue_level: FatigueLevel


Event = Annotated[AlertEvent | FatigueSample, Field(discriminator="type")]


class EventResponse(BaseModel):
    stored: bool
    alert_id: int | None = None

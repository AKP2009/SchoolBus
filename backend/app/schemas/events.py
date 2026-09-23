from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import CameraSector, FatigueLevel, Severity

AlertEventType = Literal[
    "proximity_breach", "blindspot_intrusion", "fatigue_high", "phone_use", "sos"
]


class AlertEvent(BaseModel):
    """Vision / voice event that becomes a safety_events row and an alert.

    Per type (docs/api_contract.md): proximity_breach / blindspot_intrusion need machine_id,
    distance_m and an outside sector, severity warning or critical. fatigue_high and phone_use
    need machine_id and operator_id (sector defaults to cab). sos needs operator_id or
    machine_id; its severity is always emergency.
    """

    type: AlertEventType
    machine_id: str | None = None
    operator_id: str | None = None
    ts: datetime
    severity: Severity | None = None
    distance_m: float | None = Field(default=None, ge=0, le=100)
    sector: CameraSector | None = None
    approaching: bool | None = None
    details: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _per_type(self) -> "AlertEvent":
        if self.ts.tzinfo is None:
            raise ValueError("ts needs a timezone (UTC 'Z')")
        if self.type == "sos":
            if not (self.machine_id or self.operator_id):
                raise ValueError("sos needs operator_id or machine_id")
            self.severity = Severity.emergency
            return self
        if not self.machine_id:
            raise ValueError(f"{self.type} needs machine_id")
        if self.severity is None:
            raise ValueError(f"{self.type} needs severity")
        if self.severity == Severity.emergency:
            raise ValueError("only sos is an emergency")
        if self.type in ("proximity_breach", "blindspot_intrusion"):
            if self.distance_m is None:
                raise ValueError(f"{self.type} needs distance_m")
            if self.sector is None or self.sector == CameraSector.cab:
                raise ValueError(f"{self.type} needs an outside sector (front, rear, left, right)")
            if self.severity == Severity.info:
                raise ValueError(f"{self.type} severity is warning or critical")
        else:  # fatigue_high, phone_use: the cab camera
            if not self.operator_id:
                raise ValueError(f"{self.type} needs operator_id")
            if self.sector is None:
                self.sector = CameraSector.cab
        return self


class FatigueSample(BaseModel):
    """Per-minute fatigue_log row. Not an alert. `machine_id` is optional: without it the
    `fatigue` WebSocket message goes to the machine of `shift_id`."""

    type: Literal["fatigue_sample"]
    operator_id: str
    shift_id: str
    machine_id: str | None = None
    ts: datetime
    ear_avg: float | None = Field(default=None, ge=0, le=1)
    perclos_60s: float | None = Field(default=None, ge=0, le=1)
    yawn_count: int = Field(default=0, ge=0)
    head_down_events: int = Field(default=0, ge=0)
    phone_detected: bool = False
    fatigue_score: float = Field(ge=0, le=1)
    fatigue_level: FatigueLevel

    @model_validator(mode="after")
    def _tz(self) -> "FatigueSample":
        if self.ts.tzinfo is None:
            raise ValueError("ts needs a timezone (UTC 'Z')")
        return self


Event = Annotated[AlertEvent | FatigueSample, Field(discriminator="type")]


class EventResponse(BaseModel):
    stored: bool
    alert_id: int | None = None
    # extra (optional for clients): safety_events.id or fatigue_log.id
    event_id: int | None = None


class OperatorFatigueResponse(BaseModel):
    """Latest fatigue_log row of an operator (GET /operator/{operator_id}/fatigue)."""

    operator_id: str
    ts: datetime
    shift_id: str | None = None
    fatigue_level: FatigueLevel
    fatigue_score: float | None = None
    perclos_60s: float | None = None
    stale: bool  # older than STALE_MIN minutes of wall-clock time
    age_min: float

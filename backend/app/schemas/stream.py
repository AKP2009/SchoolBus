"""WebSocket /stream/{machine_id} messages, one JSON object per message."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.common import AlertStage, CameraSector, FatigueLevel, SafetyEventType, Severity


class StreamAlert(BaseModel):
    id: int
    alert_code: str
    severity: Severity
    stage: AlertStage
    title: str
    recommended_action: str | None = None


class StreamSafety(BaseModel):
    type: SafetyEventType
    distance_m: float | None = None
    sector: CameraSector | None = None
    approaching: bool | None = None


class StreamHealth(BaseModel):
    overall: float
    subsystems: dict[str, float]


class StreamFatigue(BaseModel):
    fatigue_level: FatigueLevel
    fatigue_score: float


class TelemetryMessage(BaseModel):
    kind: Literal["telemetry"] = "telemetry"
    data: dict[str, float | int | str | bool | None]  # one telemetry row, keyed by column name


class AlertMessage(BaseModel):
    kind: Literal["alert"] = "alert"
    data: StreamAlert


class SafetyMessage(BaseModel):
    kind: Literal["safety"] = "safety"
    data: StreamSafety


class HealthMessage(BaseModel):
    kind: Literal["health"] = "health"
    data: StreamHealth


class FatigueMessage(BaseModel):
    kind: Literal["fatigue"] = "fatigue"
    data: StreamFatigue


ServerMessage = Annotated[
    TelemetryMessage | AlertMessage | SafetyMessage | HealthMessage | FatigueMessage,
    Field(discriminator="kind"),
]


class PingMessage(BaseModel):
    """Client -> server keepalive, every 20 s."""

    kind: Literal["ping"] = "ping"

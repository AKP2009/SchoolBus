"""Shared types. Enums mirror the Postgres enums in supabase/migrations/001_init.sql."""

from enum import StrEnum

from pydantic import BaseModel


class Severity(StrEnum):
    info = "info"
    warning = "warning"
    critical = "critical"
    emergency = "emergency"


class AlertStage(StrEnum):
    warn = "warn"
    derate = "derate"
    recommend_shutdown = "recommend_shutdown"
    escalated = "escalated"
    resolved = "resolved"


class SafetyEventType(StrEnum):
    seatbelt_unfastened = "seatbelt_unfastened"
    proximity_breach = "proximity_breach"
    blindspot_intrusion = "blindspot_intrusion"
    fatigue_high = "fatigue_high"
    phone_use = "phone_use"
    tip_risk = "tip_risk"
    geofence_breach = "geofence_breach"
    harsh_maneuver = "harsh_maneuver"
    overspeed = "overspeed"
    sos = "sos"


class CameraSector(StrEnum):
    front = "front"
    rear = "rear"
    left = "left"
    right = "right"
    cab = "cab"


class FatigueLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class MachineComponent(StrEnum):
    engine = "engine"
    hydraulics = "hydraulics"
    cooling = "cooling"
    electrical = "electrical"
    brakes = "brakes"
    undercarriage = "undercarriage"
    other = "other"


class IncidentType(StrEnum):
    near_miss = "near_miss"
    collision = "collision"
    injury = "injury"
    equipment_damage = "equipment_damage"
    spill_leak = "spill_leak"
    other = "other"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody

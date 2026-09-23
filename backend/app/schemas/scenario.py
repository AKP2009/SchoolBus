from enum import StrEnum

from pydantic import BaseModel


class ScenarioName(StrEnum):
    overheating = "overheating"
    hydraulic_leak = "hydraulic_leak"
    fatigue = "fatigue"
    proximity = "proximity"
    tip_risk = "tip_risk"
    seatbelt = "seatbelt"
    sos = "sos"


class ScenarioRequest(BaseModel):
    machine_id: str


class ScenarioResponse(BaseModel):
    scenario: ScenarioName
    machine_id: str
    started: bool

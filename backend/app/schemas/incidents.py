from pydantic import BaseModel

from app.schemas.common import IncidentType, Severity


class IncidentTranscribeRequest(BaseModel):
    transcript: str
    operator_id: str
    machine_id: str | None = None


class IncidentDraft(BaseModel):
    """Structured draft the operator confirms before it is saved to incidents."""

    incident_type: IncidentType
    severity: Severity
    description: str
    injury: bool

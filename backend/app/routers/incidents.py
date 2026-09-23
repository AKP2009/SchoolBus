from fastapi import APIRouter

from app.core.auth import CurrentUser
from app.core.errors import not_implemented
from app.schemas.incidents import IncidentDraft, IncidentTranscribeRequest

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("/transcribe", response_model=IncidentDraft)
def transcribe(body: IncidentTranscribeRequest, _: CurrentUser) -> IncidentDraft:
    raise not_implemented("Incident transcript to draft")

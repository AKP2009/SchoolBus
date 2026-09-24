from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, forbidden
from app.llm import LLM, get_llm
from app.schemas.incidents import IncidentDraft, IncidentTranscribeRequest
from app.services.incidents import draft

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("/transcribe", response_model=IncidentDraft)
async def transcribe(
    body: IncidentTranscribeRequest, user: CurrentUser, llm: Annotated[LLM, Depends(get_llm)]
) -> IncidentDraft:
    """Voice transcript -> incident draft for the operator to confirm. Nothing is saved: the web
    app upserts the confirmed row into incidents (reported_via='voice', voice_transcript)."""
    if not user.can_act_for(body.operator_id):
        raise forbidden("Operators can only report incidents as themselves.")
    return await run_in_threadpool(draft, llm, body.transcript, body.machine_id)

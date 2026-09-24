from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.core.auth import CurrentUser
from app.core.errors import not_implemented
from app.schemas.voice import VoiceCommandResponse

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/command", response_model=VoiceCommandResponse)
def voice_command(
    audio: Annotated[UploadFile, File(description="audio/webm")],
    operator_id: Annotated[str, Form()],
    machine_id: Annotated[str, Form()],
    _: CurrentUser,
) -> VoiceCommandResponse:
    raise not_implemented("Voice command")

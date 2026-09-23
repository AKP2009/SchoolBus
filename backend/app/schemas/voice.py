from pydantic import BaseModel, Field


class VoiceCommandResponse(BaseModel):
    """Request is multipart: audio (audio/webm) + operator_id + machine_id form fields."""

    transcript: str
    intent: str
    reply_text: str
    reply_audio_url: str | None = None
    action: dict[str, object] = Field(default_factory=dict)

from uuid import UUID

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: UUID
    operator_id: str
    message: str
    language: str = "en"


class ChatSource(BaseModel):
    document_id: int
    title: str
    chunk_index: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]

from fastapi import APIRouter

from app.core.auth import CurrentUser
from app.core.errors import not_implemented
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, _: CurrentUser) -> ChatResponse:
    raise not_implemented("Chat")

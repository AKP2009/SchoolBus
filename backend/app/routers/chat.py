from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.ai_repo import AiRepo, get_ai_repo
from app.core.auth import CurrentUser, forbidden
from app.llm import LLM, get_llm
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services import chat as svc

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: CurrentUser,
    repo: Annotated[AiRepo, Depends(get_ai_repo)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> ChatResponse:
    """RAG answer from the knowledge base (models.md §7). Both turns go to chat_messages.
    503 LLM_UNAVAILABLE when LLM_API_KEY is missing."""
    if not user.can_act_for(body.operator_id):
        raise forbidden("Operators can only chat as themselves.")
    asked_at = datetime.now(UTC)
    result = await run_in_threadpool(svc.answer, repo, llm, body.message, body.language)
    await run_in_threadpool(
        svc.save_turns, repo, str(body.session_id), body.operator_id, body.message, result, asked_at
    )
    return ChatResponse(answer=result.answer, sources=[ChatSource(**s) for s in result.sources])

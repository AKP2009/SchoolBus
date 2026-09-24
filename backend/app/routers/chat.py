from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.ai_repo import AiRepo, get_ai_repo
from app.core.auth import CurrentUser, forbidden
from app.llm import LLM, get_llm_factory
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services import chat as svc
from app.services.chat_cache import ChatCache, get_chat_cache

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: CurrentUser,
    repo: Annotated[AiRepo, Depends(get_ai_repo)],
    cache: Annotated[ChatCache, Depends(get_chat_cache)],
    llm: Annotated[Callable[[], LLM], Depends(get_llm_factory)],
) -> ChatResponse:
    """RAG answer from the knowledge base (models.md §7), from the cache when the question was
    answered before. Both turns go to chat_messages. 503 CHAT_BUSY when the LLM is rate limited
    or overloaded and nothing is cached; 503 LLM_UNAVAILABLE without LLM_API_KEY (cache misses)."""
    if not user.can_act_for(body.operator_id):
        raise forbidden("Operators can only chat as themselves.")
    asked_at = datetime.now(UTC)
    result, cached = await run_in_threadpool(
        svc.answer_or_cache, repo, cache, llm, body.message, body.language
    )
    await run_in_threadpool(
        svc.save_turns, repo, str(body.session_id), body.operator_id, body.message, result, asked_at
    )
    return ChatResponse(
        answer=result.answer, sources=[ChatSource(**s) for s in result.sources], cached=cached
    )

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.ai_repo import AiRepo, get_ai_repo
from app.core.auth import CurrentUser, forbidden
from app.core.errors import ApiError
from app.llm import LLM, get_llm_factory
from app.schemas.handover import HandoverResponse
from app.services import handover as svc

router = APIRouter(prefix="/handover", tags=["handover"])


@router.post("/{shift_id}", response_model=HandoverResponse)
async def handover(
    shift_id: str,
    user: CurrentUser,
    repo: Annotated[AiRepo, Depends(get_ai_repo)],
    llm: Annotated[Callable[[], LLM], Depends(get_llm_factory)],
) -> HandoverResponse:
    """5-line summary of the shift for the next operator (models.md §9), saved to
    shifts.handover_summary. Managers, or operators of the shift's site. When the LLM is rate
    limited, overloaded or not configured, a pre-generated summary is used if there is one."""
    shift = await run_in_threadpool(repo.shift_full, shift_id)
    if shift is None:
        raise ApiError(404, "NOT_FOUND", f"Shift {shift_id} not found.")
    if not (user.can_act_for(shift["operator_id"]) or user.site_id == shift["site_id"]):
        raise forbidden("You can only create handovers for shifts at your site.")
    summary, pre = await run_in_threadpool(svc.generate_or_pregenerated, repo, llm, shift_id)
    return HandoverResponse(summary=summary, pre_generated=pre)

from fastapi import APIRouter

from app.core.auth import CurrentUser
from app.core.errors import not_implemented
from app.schemas.handover import HandoverResponse

router = APIRouter(prefix="/handover", tags=["handover"])


@router.post("/{shift_id}", response_model=HandoverResponse)
def handover(shift_id: str, _: CurrentUser) -> HandoverResponse:
    raise not_implemented("Handover summary")

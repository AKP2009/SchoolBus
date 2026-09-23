from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.replay import ReplayStartRequest, ReplayStatus

router = APIRouter(prefix="/replay", tags=["replay"])


@router.post("/start", response_model=ReplayStatus)
def start(body: ReplayStartRequest) -> ReplayStatus:
    raise not_implemented("Replay start")


@router.post("/stop", response_model=ReplayStatus)
def stop() -> ReplayStatus:
    raise not_implemented("Replay stop")


@router.get("/status", response_model=ReplayStatus)
def status() -> ReplayStatus:
    return ReplayStatus(running=False)

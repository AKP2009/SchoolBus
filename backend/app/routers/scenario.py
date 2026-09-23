from fastapi import APIRouter

from app.core.errors import ApiError
from app.replay.engine import ReplayError
from app.replay.runtime import get_engine
from app.schemas.scenario import ScenarioName, ScenarioRequest, ScenarioResponse

router = APIRouter(prefix="/scenario", tags=["scenario"])


@router.post("/{name}", response_model=ScenarioResponse)
def trigger(name: ScenarioName, body: ScenarioRequest) -> ScenarioResponse:
    """Splice a scripted sequence into the live replay of one machine (demo panel only).

    Built: overheating, hydraulic_leak, tip_risk, seatbelt. fatigue, proximity and sos come
    from the vision service / voice through POST /events and return 501 here.
    """
    try:
        sc = get_engine().trigger(name.value, body.machine_id)
    except ReplayError as e:
        raise ApiError(e.status, e.code, e.message) from e
    return ScenarioResponse(
        scenario=name,
        machine_id=body.machine_id,
        started=True,
        start_ts=sc.start_ts,
        duration_min=sc.duration_s / 60,
    )

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, UserOrService
from app.core.errors import ApiError
from app.replay.runtime import get_engine
from app.repo import Repo, get_repo
from app.schemas.machine import MachineHealthResponse, MachineStateResponse

router = APIRouter(prefix="/machine", tags=["machine"])

SUBSYSTEMS = ("engine", "cooling", "hydraulics", "electrical", "undercarriage")


@router.get("/{machine_id}/health", response_model=MachineHealthResponse)
async def machine_health(
    machine_id: str,
    _: CurrentUser,
    repo: Annotated[Repo, Depends(get_repo)],
    engine: Annotated[Any, Depends(get_engine)],
) -> MachineHealthResponse:
    """Latest health from the live replay, else the latest stored snapshot."""
    live = engine.machine_health(machine_id)
    if live is not None:
        return MachineHealthResponse(**live)
    snap = await run_in_threadpool(repo.latest_health, machine_id)
    if snap is None:
        raise ApiError(404, "NOT_FOUND", f"No health data for {machine_id} yet. Start the replay.")
    details = snap.get("details") or {}
    return MachineHealthResponse(
        machine_id=machine_id,
        ts=snap["ts"],
        overall=float(snap["overall_score"]),
        subsystems={
            s: float(snap[f"{s}_score"]) for s in SUBSYSTEMS if snap.get(f"{s}_score") is not None
        },
        anomaly_score=snap.get("anomaly_score"),
        failure_probability=details.get("failure_probability"),
        likely_component=details.get("likely_component"),
        band=details.get("band"),
        reasons=details.get("reasons"),
    )


@router.get("/{machine_id}/state", response_model=MachineStateResponse)
def machine_state(
    machine_id: str, _: UserOrService, engine: Annotated[Any, Depends(get_engine)]
) -> MachineStateResponse:
    """Is the machine moving right now (replay stream)? Used by the vision service."""
    state = engine.machine_state(machine_id)
    if state is None:
        raise ApiError(404, "NOT_FOUND", f"{machine_id} is not in the running replay.")
    return MachineStateResponse(**state)

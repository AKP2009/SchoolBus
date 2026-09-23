from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db import get_supabase
from app.replay.engine import ReplayError
from app.replay.runtime import get_engine
from app.replay.source import choose_source, load_geofences, load_machines
from app.schemas.replay import ReplayStartRequest, ReplayStatus

router = APIRouter(prefix="/replay", tags=["replay"])


def replay_error(e: ReplayError) -> ApiError:
    return ApiError(e.status, e.code, e.message)


@router.post("/start", response_model=ReplayStatus)
async def start(body: ReplayStartRequest) -> ReplayStatus:
    engine = get_engine()
    client = get_supabase()
    ids = list(dict.fromkeys(body.machine_ids))
    try:
        source = await run_in_threadpool(
            choose_source, client, get_settings().telemetry_parquet, ids, body.from_ts
        )
    except FileNotFoundError as e:
        raise ApiError(
            503, "NO_TELEMETRY_SOURCE", f"Supabase has no telemetry for that window and {e}"
        ) from e
    machines = await run_in_threadpool(load_machines, client, ids)
    sites = sorted({m["site_id"] for m in machines.values() if m.get("site_id")})
    geofences = await run_in_threadpool(load_geofences, client, sites)
    try:
        await engine.start(ids, body.from_ts, body.speed, source, machines, geofences)
    except ReplayError as e:
        raise replay_error(e) from e
    return ReplayStatus(**engine.status())


@router.post("/stop", response_model=ReplayStatus)
async def stop() -> ReplayStatus:
    engine = get_engine()
    await engine.stop()
    return ReplayStatus(**engine.status())


@router.get("/status", response_model=ReplayStatus)
def status() -> ReplayStatus:
    return ReplayStatus(**get_engine().status())

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.core.auth import Manager
from app.repo import Repo, get_repo
from app.runtime import get_telemetry
from app.schemas.analytics import ClusterRunResponse
from app.services.analytics import cluster_week
from app.services.telemetry import TelemetryHistory

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/cluster", response_model=ClusterRunResponse)
async def cluster(
    week_start: date,
    _: Manager,
    repo: Annotated[Repo, Depends(get_repo)],
    telemetry: Annotated[TelemetryHistory, Depends(get_telemetry)],
) -> ClusterRunResponse:
    """Weekly metrics, clusters, efficiency ranks and outliers for the week starting on
    `week_start` (a Monday) -> fleet_metrics_weekly. Managers only. Takes 10-30 s."""
    out = await run_in_threadpool(cluster_week, repo, telemetry, week_start)
    return ClusterRunResponse(**out)

"""APScheduler jobs (docs/supabase.md §10), started with the app when SCHEDULER_ENABLED.

| Job | Every | Does |
|---|---|---|
| maintenance_scoring | 5 s wall clock; acts per 10 min of replay time | maintenance_predictions |
| fleet_clustering | daily 01:00 Asia/Kolkata | fleet_metrics_weekly for the latest week |
| vision_alert_expiry | 10 s | resolves vision alerts with no event for 30 s |

Training recommendations and handover summaries come with the next task.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from starlette.concurrency import run_in_threadpool

from app.runtime import get_event_service, get_maintenance_scorer, get_repo, get_telemetry
from app.services.analytics import cluster_week, latest_week

log = logging.getLogger(__name__)

MAINTENANCE_TICK_S = 5
EXPIRY_TICK_S = 10


async def maintenance_scoring() -> None:
    await get_maintenance_scorer().tick()


async def vision_alert_expiry() -> None:
    await get_event_service().expire()


def _cluster_latest() -> None:
    repo = get_repo()
    week = latest_week(repo)
    if week is None:
        log.info("fleet clustering: no shifts yet")
        return
    out = cluster_week(repo, get_telemetry(), week)
    log.info("fleet clustering %s: %s rows. %s", week, out["rows_written"], out["summary"])


async def fleet_clustering() -> None:
    try:
        await run_in_threadpool(_cluster_latest)
    except Exception:  # noqa: BLE001 - try again tomorrow
        log.exception("fleet clustering job failed")


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 30}
    sched.add_job(
        maintenance_scoring,
        "interval",
        seconds=MAINTENANCE_TICK_S,
        id="maintenance_scoring",
        **common,
    )
    sched.add_job(
        vision_alert_expiry, "interval", seconds=EXPIRY_TICK_S, id="vision_alert_expiry", **common
    )
    sched.add_job(
        fleet_clustering,
        "cron",
        hour=1,
        minute=0,
        timezone="Asia/Kolkata",
        id="fleet_clustering",
        **{**common, "misfire_grace_time": 3600},
    )
    return sched

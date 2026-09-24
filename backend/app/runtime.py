"""Process-wide services, wired to Supabase (service role) and the WebSocket manager.

Routers take them as FastAPI dependencies, so tests override them with in-memory fakes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.ai_repo import get_ai_repo
from app.core.config import get_settings
from app.jobs.ai import HandoverJob
from app.jobs.maintenance import MaintenanceScorer
from app.replay.runtime import get_engine
from app.repo import Repo, get_repo
from app.services.events import EventService
from app.services.telemetry import TelemetryHistory
from app.ws import manager

__all__ = [
    "get_ai_repo",
    "get_engine",
    "get_event_service",
    "get_handover_job",
    "get_maintenance_scorer",
    "get_repo",
    "get_telemetry",
]


@lru_cache
def get_event_service() -> EventService:
    return EventService(get_repo(), manager)


@lru_cache
def get_telemetry() -> TelemetryHistory:
    from app.db import get_supabase

    return TelemetryHistory(get_settings().telemetry_parquet, get_supabase())


@lru_cache
def get_maintenance_scorer() -> MaintenanceScorer:
    return MaintenanceScorer(get_repo(), get_engine(), get_telemetry())


@lru_cache
def get_handover_job() -> HandoverJob:
    return HandoverJob(get_ai_repo(), get_engine())


def engine_or_none() -> Any:
    """The replay engine (for live health / replay clock)."""
    return get_engine()


def repo_dep() -> Repo:
    return get_repo()

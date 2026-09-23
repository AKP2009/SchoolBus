"""Process-wide replay engine, wired to Supabase (service role) and the WebSocket manager."""

from __future__ import annotations

from functools import lru_cache

from app.db import get_supabase
from app.replay.engine import ReplayEngine
from app.replay.writer import SupabaseStore
from app.ws import manager


@lru_cache
def get_engine() -> ReplayEngine:
    return ReplayEngine(pusher=manager, store=SupabaseStore(get_supabase()))

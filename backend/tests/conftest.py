from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

# Test settings win over backend/.env (pydantic-settings reads the environment first). No test
# talks to Supabase: the repo, pusher, engine and profiles are replaced below.
os.environ.update(
    {
        "SUPABASE_URL": "http://supabase.test",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
        "SUPABASE_JWT_SECRET": "test-jwt-secret-at-least-32-bytes-long!!",
        "VISION_API_TOKEN": "test-vision-token",
        "SCHEDULER_ENABLED": "false",
    }
)

VISION_TOKEN = os.environ["VISION_API_TOKEN"]
JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]

T0 = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)

NORMAL: dict[str, Any] = {
    "machine_id": "M04",
    "operator_id": "OP03",
    "shift_id": "SH-2026-08-20-M04-D",
    "engine_rpm": 1800.0,
    "engine_load_pct": 70.0,
    "coolant_temp_c": 88.0,
    "engine_oil_temp_c": 96.0,
    "oil_pressure_kpa": 350.0,
    "hydraulic_pressure_bar": 240.0,
    "hydraulic_oil_temp_c": 68.0,
    "fuel_rate_lph": 16.0,
    "fuel_level_pct": 70.0,
    "battery_voltage": 27.8,
    "vibration_rms_g": 0.5,
    "ground_speed_kmh": 1.0,
    "pitch_deg": 1.0,
    "roll_deg": 1.0,
    "gps_lat": 12.90,
    "gps_lon": 77.60,
    "seatbelt_fastened": True,
    "is_idle": False,
    "fault_code": None,
}


def row(minute: float, **over: Any) -> dict[str, Any]:
    """A normal working telemetry row at T0 + minute, with overrides."""
    return {**NORMAL, "ts": T0 + timedelta(minutes=minute), "_period_s": 60.0, **over}


@pytest.fixture
def mkrow():
    return row


# ---------------------------------------------------------------------------------------------
# API fixtures
# ---------------------------------------------------------------------------------------------
PROFILES: dict[str, dict[str, Any]] = {
    "u-ravi": {"id": "u-ravi", "role": "operator", "operator_id": "OP03", "site_id": "S1"},
    "u-op05": {"id": "u-op05", "role": "operator", "operator_id": "OP05", "site_id": "S1"},
    "u-priya": {"id": "u-priya", "role": "manager", "operator_id": None, "site_id": "S1"},
}


def make_token(user_id: str, exp_in_s: int = 3600, secret: str = JWT_SECRET) -> str:
    import jwt

    now = int(time.time())
    claims = {
        "sub": user_id,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + exp_in_s,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def headers() -> Callable[[str], dict[str, str]]:
    """headers("operator") (Ravi, OP03), headers("op05"), headers("manager"), headers("vision")."""
    users = {"operator": "u-ravi", "op05": "u-op05", "manager": "u-priya"}

    def h(who: str) -> dict[str, str]:
        if who == "vision":
            return bearer(VISION_TOKEN)
        return bearer(make_token(users[who]))

    return h


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fakes import MemoryRepo

    from app.core import auth

    monkeypatch.setattr(auth._profiles, "get", lambda uid: PROFILES.get(uid))
    return MemoryRepo()


@pytest.fixture
def pusher() -> Any:
    from fakes import FakePusher

    return FakePusher()


class StubEngine:
    """Replay engine stand-in: not running, optional live health per machine."""

    def __init__(self) -> None:
        self.running = False
        self.replay_ts: datetime | None = None
        self.from_ts: datetime | None = None
        self.streams: dict[str, Any] = {}
        self.health: dict[str, dict[str, Any]] = {}

    def machine_health(self, machine_id: str) -> dict[str, Any] | None:
        return self.health.get(machine_id)

    def machine_state(self, machine_id: str) -> dict[str, Any] | None:
        return None

    def status(self) -> dict[str, Any]:
        return {"running": self.running}

    async def stop(self) -> None:
        self.running = False


@pytest.fixture
def engine() -> StubEngine:
    return StubEngine()


@pytest.fixture
def client(repo: Any, pusher: Any, engine: StubEngine) -> Iterator[Any]:
    from fakes import FakeTelemetry
    from fastapi.testclient import TestClient

    from app.main import app
    from app.repo import get_repo
    from app.runtime import get_engine, get_event_service, get_telemetry
    from app.services.events import EventService
    from app.services.tasks import pending_plans

    service = EventService(repo, pusher)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_event_service] = lambda: service
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_telemetry] = FakeTelemetry
    pending_plans._plans.clear()
    c = TestClient(app, raise_server_exceptions=False)  # no lifespan: no scheduler, no engine
    c.service = service  # type: ignore[attr-defined]
    yield c
    app.dependency_overrides.clear()

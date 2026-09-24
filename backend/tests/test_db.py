"""Supabase calls retry transient connection errors, then fail as 503 DB_UNAVAILABLE."""

from __future__ import annotations

import httpx
import pytest

from app import db
from app.db import DatabaseUnavailable, RetryTransport


class Flaky(httpx.BaseTransport):
    """Raises `error` for the first `failures` requests, then answers `[{"id": 1}]`."""

    def __init__(self, failures: int, error: type[Exception] = httpx.ConnectError) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error("connection reset", request=request)
        return httpx.Response(200, json=[{"id": 1}], request=request)


def transport(inner: httpx.BaseTransport, sleeps: list[float]) -> httpx.Client:
    return httpx.Client(transport=RetryTransport(inner, sleep=sleeps.append))


def test_retries_with_backoff_then_succeeds():
    inner, sleeps = Flaky(failures=2), []
    r = transport(inner, sleeps).get("http://supabase.test/rest/v1/telemetry")
    assert r.json() == [{"id": 1}]
    assert inner.calls == 3
    assert sleeps == [0.25, 0.5]


def test_gives_up_after_the_attempts():
    inner, sleeps = Flaky(failures=10), []
    with pytest.raises(DatabaseUnavailable, match="after 3 attempts"):
        transport(inner, sleeps).get("http://supabase.test/rest/v1/telemetry")
    assert inner.calls == 3
    assert len(sleeps) == 2


def test_insert_is_retried_only_when_it_never_reached_the_server():
    url = "http://supabase.test/rest/v1/alerts"
    sent = Flaky(failures=1, error=httpx.ConnectError)
    assert transport(sent, []).post(url, json={}).status_code == 200
    assert sent.calls == 2
    # the server may have inserted the row before the connection broke: no duplicate
    broken = Flaky(failures=1, error=httpx.RemoteProtocolError)
    with pytest.raises(DatabaseUnavailable):
        transport(broken, []).post(url, json={})
    assert broken.calls == 1
    # a read is safe to repeat
    read = Flaky(failures=1, error=httpx.RemoteProtocolError)
    assert transport(read, []).get(url).status_code == 200


def test_supabase_client_uses_the_retrying_http_client(monkeypatch):
    from supabase import ClientOptions, create_client

    monkeypatch.setattr(db.time, "sleep", lambda _: None)
    inner = Flaky(failures=2)
    client = create_client(
        "http://supabase.test",
        "test-service-role",
        options=ClientOptions(httpx_client=db.http_client(inner)),
    )
    assert client.table("machines").select("*").execute().data == [{"id": 1}]
    assert inner.calls == 3


def test_route_returns_503_db_unavailable(client, headers):
    from app.main import app
    from app.runtime import get_engine

    def unreachable():
        raise DatabaseUnavailable("Supabase unreachable after 3 attempts")

    app.dependency_overrides[get_engine] = unreachable
    r = client.get("/replay/status", headers=headers("manager"))
    assert r.status_code == 503
    assert r.json() == {
        "error": {
            "code": "DB_UNAVAILABLE",
            "message": "The database is unreachable right now. Try again in a moment.",
        }
    }

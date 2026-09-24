"""CORS: the web app's Vite dev (5173) and preview (4173) servers may call the API."""

from __future__ import annotations

import pytest

WEB_ORIGINS = ["http://localhost:5173", "http://localhost:4173"]


def test_default_origins_are_dev_and_preview():
    from app.core.config import Settings

    field = Settings.model_fields["cors_origins"]
    assert [o.strip() for o in field.default.split(",")] == WEB_ORIGINS


def test_origins_come_from_env(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test ,")
    assert Settings().cors_origin_list == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize("origin", WEB_ORIGINS)
def test_preflight_allows_web_app(client, origin):
    r = client.options(
        "/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert r.headers["access-control-allow-credentials"] == "true"


def test_other_origins_get_no_cors_headers(client):
    r = client.get("/health", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in r.headers

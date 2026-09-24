"""CORS: the web app's Vite dev (5173) and preview (4173) servers may call the API."""

from __future__ import annotations

import pytest

WEB_ORIGINS = ["http://localhost:5173", "http://localhost:4173"]


@pytest.fixture
def default_cors(client, monkeypatch):
    """The app's CORS middleware with the default origins, not the ones in a local backend/.env.
    app.main reads the origins once at import, so patch the middleware and rebuild the stack."""
    from starlette.middleware.cors import CORSMiddleware

    from app.core.config import Settings

    cors = next(m for m in client.app.user_middleware if m.cls is CORSMiddleware)
    origins = Settings.model_construct().cors_origin_list  # field defaults: no .env, no env vars
    monkeypatch.setitem(cors.kwargs, "allow_origins", origins)
    monkeypatch.setattr(client.app, "middleware_stack", None)  # undo restores the old stack
    return client


def test_default_origins_are_dev_and_preview():
    from app.core.config import Settings

    field = Settings.model_fields["cors_origins"]
    assert [o.strip() for o in field.default.split(",")] == WEB_ORIGINS


def test_origins_come_from_env(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test ,")
    assert Settings().cors_origin_list == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize("origin", WEB_ORIGINS)
def test_preflight_allows_web_app(default_cors, origin):
    r = default_cors.options(
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

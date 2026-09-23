"""Bearer auth on every endpoint except /health, and the contract error shape."""

from __future__ import annotations

from conftest import bearer, make_token


def _error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


def test_missing_token_is_401(client):
    for method, path in [
        ("post", "/predict/task-time"),
        ("post", "/plan/re-evaluate"),
        ("post", "/plan/accept"),
        ("post", "/events"),
        ("post", "/analytics/cluster?week_start=2026-06-01"),
        ("get", "/operator/OP03/fatigue"),
        ("get", "/machine/M04/health"),
        ("get", "/machine/M04/state"),
        ("get", "/replay/status"),
        ("post", "/replay/stop"),
        ("post", "/chat"),
        ("post", "/handover/SH-1"),
    ]:
        _error(client.request(method, path, json={}), 401, "UNAUTHORIZED")


def test_bad_tokens_are_401(client):
    for token in (
        "not-a-jwt",
        make_token("u-ravi", exp_in_s=-10),  # expired
        make_token("u-ravi", secret="some-other-secret-that-is-long-enough!!"),  # forged
    ):
        _error(client.get("/replay/status", headers=bearer(token)), 401, "UNAUTHORIZED")
    resp = client.get("/replay/status", headers={"Authorization": "Basic abc"})
    _error(resp, 401, "UNAUTHORIZED")


def test_valid_token_passes(client, headers):
    assert client.get("/replay/status", headers=headers("operator")).status_code == 200


def test_user_without_profile_is_403(client):
    _error(client.get("/replay/status", headers=bearer(make_token("u-nobody"))), 403, "FORBIDDEN")


def test_vision_token_only_on_vision_endpoints(client, headers):
    h = headers("vision")
    _error(client.get("/replay/status", headers=h), 403, "FORBIDDEN")
    _error(client.post("/predict/task-time", json={"task_ids": ["x"]}, headers=h), 403, "FORBIDDEN")
    # allowed: its own endpoints (404 = passed auth, no data)
    assert client.get("/machine/M04/state", headers=h).status_code == 404
    assert client.get("/operator/OP05/fatigue", headers=h).status_code == 200


def test_managers_only(client, headers):
    op = headers("operator")
    _error(client.post("/analytics/cluster?week_start=2026-06-01", headers=op), 403, "FORBIDDEN")
    body = {"machine_ids": ["M04"], "from": "2026-08-20T01:30:00Z", "speed": 10}
    _error(client.post("/replay/start", json=body, headers=op), 403, "FORBIDDEN")
    _error(client.post("/replay/stop", headers=op), 403, "FORBIDDEN")
    resp = client.post("/scenario/overheating", json={"machine_id": "M04"}, headers=op)
    _error(resp, 403, "FORBIDDEN")


def test_error_shape_for_framework_errors(client, headers):
    _error(client.get("/no/such/route"), 404, "NOT_FOUND")
    _error(client.get("/events", headers=headers("vision")), 405, "METHOD_NOT_ALLOWED")
    resp = client.post("/predict/task-time", json={"task_ids": []}, headers=headers("manager"))
    _error(resp, 400, "VALIDATION_ERROR")
    assert "task_ids" in resp.json()["error"]["message"]


def test_websocket_needs_token(client, headers):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/stream/M04") as ws:
            ws.receive_text()
    assert e.value.code == 4401
    token = headers("operator")["Authorization"].split()[1]
    with client.websocket_connect(f"/stream/M04?token={token}") as ws:
        ws.send_text('{"kind": "ping"}')

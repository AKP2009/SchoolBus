"""Tests for vision/backend_client.py and the auth header on POST /events, against a mock backend
(a real HTTP server on localhost in a thread), no camera and no real backend."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import backend_client as B
from backend_client import MachineState, OperatorFatigue
from events import EventSink

TOKEN = "test-vision-token"
AUTH = f"Bearer {TOKEN}"


class MockBackend:
    """routes: path -> (status, json body). Records (method, path, Authorization, body)."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict[str, Any]]] = {}
        self.requests: list[tuple[str, str, str | None, Any]] = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, body: Any = None) -> None:
                mock.requests.append(
                    (self.command, self.path, self.headers.get("Authorization"), body)
                )
                if self.headers.get("Authorization") != AUTH:
                    status, payload = 401, {"error": {"code": "UNAUTHORIZED"}}
                else:
                    status, payload = mock.routes.get(self.path, (404, {"error": {}}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._answer()

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("content-length", 0))
                self._answer(json.loads(self.rfile.read(n)))

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend() -> Iterator[MockBackend]:
    mock = MockBackend()
    yield mock
    mock.stop()


def state(moving: bool, kmh: float = 3.2) -> tuple[int, dict[str, Any]]:
    body = {
        "machine_id": "M05",
        "moving": moving,
        "ground_speed_kmh": kmh,
        "ts": "2026-08-20T02:02:00Z",
    }
    return 200, body


def fatigue(level: str, stale: bool = False) -> tuple[int, dict[str, Any]]:
    body = {
        "operator_id": "OP02",
        "ts": "2026-09-24T04:10:00Z",
        "shift_id": "SH-2026-09-24-M05-D",
        "fatigue_level": level,
        "fatigue_score": 0.7,
        "perclos_60s": 0.2,
        "stale": stale,
        "age_min": 0.8,
    }
    return 200, body


def wait_for(cond, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


# --- machine state ------------------------------------------------------------------------------
def test_machine_state_reads_moving_flag(backend):
    m = MachineState(backend.url, "M05", TOKEN)
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    assert m.poll_once() is False and m.source == "backend"
    backend.routes["/machine/M05/state"] = state(True)
    assert m.poll_once() is True


def test_machine_state_polls_on_a_thread_and_caches(backend):
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    m = MachineState(backend.url, "M05", TOKEN, poll_s=0.05).start()
    try:
        assert wait_for(lambda: m.source == "backend" and m.moving() is False)
        backend.routes["/machine/M05/state"] = state(True)
        assert wait_for(lambda: m.moving() is True)
        n = len(backend.requests)
        assert wait_for(lambda: len(backend.requests) >= n + 2)  # keeps polling
    finally:
        m.stop()
    assert all(r[:2] == ("GET", "/machine/M05/state") for r in backend.requests)


def test_default_poll_interval_is_two_seconds():
    assert B.POLL_S == 2.0
    assert MachineState("http://x", "M05").poll_s == 2.0
    assert OperatorFatigue("http://x", "OP02").poll_s == 2.0


def test_machine_not_in_replay_uses_safe_default(backend):
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    m = MachineState(backend.url, "M05", TOKEN)
    m.poll_once()
    del backend.routes["/machine/M05/state"]  # 404: replay stopped
    assert m.poll_once() is B.MOVING_DEFAULT is True
    assert m.source == "default"


def test_backend_down_keeps_last_known_value(backend):
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    m = MachineState(backend.url, "M05", TOKEN, timeout_s=0.5)
    assert m.poll_once() is False
    backend.stop()
    assert m.poll_once() is False  # not the default True
    assert m.source == "last"


def test_backend_never_up_gives_safe_defaults():
    dead = "http://127.0.0.1:9"  # discard port: connection refused
    m = MachineState(dead, "M05", TOKEN, timeout_s=0.3)
    f = OperatorFatigue(dead, "OP02", TOKEN, timeout_s=0.3)
    assert m.poll_once() is True and m.source == "default"
    assert f.poll_once() is False and f.source == "default"


@pytest.mark.parametrize("status", [500, 503, 401, 403])
def test_errors_keep_last_known_value(backend, status):
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    m = MachineState(backend.url, "M05", TOKEN)
    m.poll_once()
    backend.routes["/machine/M05/state"] = (status, {"error": {}})
    assert m.poll_once() is False


def test_wrong_token_is_rejected_and_value_stays_default(backend):
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    m = MachineState(backend.url, "M05", "wrong")
    assert m.poll_once() is True and m.source == "default"


def test_poller_thread_survives_backend_outage(backend):
    backend.routes["/machine/M05/state"] = state(False, 0.0)
    m = MachineState(backend.url, "M05", TOKEN, poll_s=0.05, timeout_s=0.3).start()
    try:
        assert wait_for(lambda: m.source == "backend")
        backend.stop()
        assert wait_for(lambda: m.source == "last")
        n = m.polls
        assert wait_for(lambda: m.polls >= n + 2)  # still polling, did not raise
        assert m.moving() is False
    finally:
        m.stop()


def test_override_does_not_poll(backend):
    m = MachineState(backend.url, "M05", TOKEN, override=False, poll_s=0.01).start()
    f = OperatorFatigue(backend.url, "OP02", TOKEN, override=True, poll_s=0.01).start()
    time.sleep(0.1)
    assert m.moving() is False and f.is_high() is True
    assert m.poll_once() is False and m.source == "override"
    assert backend.requests == []
    m.stop()
    f.stop()


# --- operator fatigue ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "route, high, source",
    [
        (fatigue("high"), True, "backend"),
        (fatigue("medium"), False, "backend"),
        (fatigue("low"), False, "backend"),
        (fatigue("high", stale=True), False, "default"),  # contract: stale = unknown
        ((404, {"error": {"code": "NOT_FOUND"}}), False, "default"),
    ],
)
def test_fatigue_high_check(backend, route, high, source):
    backend.routes["/operator/OP02/fatigue"] = route
    f = OperatorFatigue(backend.url, "OP02", TOKEN)
    assert f.poll_once() is high and f.is_high() is high
    assert f.source == source


def test_fatigue_backend_down_keeps_last_high(backend):
    backend.routes["/operator/OP02/fatigue"] = fatigue("high")
    f = OperatorFatigue(backend.url, "OP02", TOKEN, timeout_s=0.5)
    assert f.poll_once() is True
    backend.stop()
    assert f.poll_once() is True and f.source == "last"


def test_bad_body_keeps_value(backend):
    backend.routes["/operator/OP02/fatigue"] = (200, {"unexpected": 1})
    f = OperatorFatigue(backend.url, "OP02", TOKEN)
    assert f.poll_once() is False  # no fatigue_level: default, no exception
    backend.routes["/machine/M05/state"] = (200, {"machine_id": "M05"})
    assert MachineState(backend.url, "M05", TOKEN).poll_once() is True


# --- auth header --------------------------------------------------------------------------------
def test_auth_header_format():
    assert B.auth_headers(TOKEN) == {"Authorization": AUTH}
    assert B.auth_headers(None) == {} and B.auth_headers("") == {}


def test_every_call_carries_the_bearer_token(backend):
    backend.routes["/machine/M05/state"] = state(True)
    backend.routes["/operator/OP02/fatigue"] = fatigue("low")
    backend.routes["/events"] = (200, {"stored": True, "alert_id": 1, "event_id": 2})
    m = MachineState(backend.url, "M05", TOKEN, poll_s=0.05).start()
    f = OperatorFatigue(backend.url, "OP02", TOKEN)
    f.poll_once()
    sink = EventSink(backend.url, token=TOKEN)
    event = {
        "type": "proximity_breach",
        "machine_id": "M05",
        "operator_id": "OP02",
        "ts": "2026-09-23T10:15:03Z",
        "severity": "critical",
        "distance_m": 2.4,
        "sector": "rear",
        "approaching": True,
        "details": {},
    }
    sink.send(event)
    try:
        assert wait_for(lambda: sink.sent == 1)
        assert wait_for(lambda: m.polls >= 2)
    finally:
        sink.close()
        m.stop()
    paths = {(r[0], r[1]) for r in backend.requests}
    assert {
        ("GET", "/machine/M05/state"),
        ("GET", "/operator/OP02/fatigue"),
        ("POST", "/events"),
    } <= paths
    assert all(r[2] == AUTH for r in backend.requests)
    assert [r[3] for r in backend.requests if r[0] == "POST"] == [event]


def test_event_sink_without_token_is_rejected_not_retried(backend):
    backend.routes["/events"] = (200, {"stored": True})
    sink = EventSink(backend.url, token=None)
    sink.send({"type": "sos", "operator_id": "OP02", "ts": "2026-09-23T10:15:03Z"})
    assert wait_for(lambda: sink.dropped == 1)
    sink.close()
    assert sink.sent == 0 and backend.requests[0][2] is None


# --- env ----------------------------------------------------------------------------------------
def test_load_env_reads_file_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nVISION_API_TOKEN='abc123'\nMACHINE_ID=M09\nEMPTY=\nnot a line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VISION_API_TOKEN", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)
    monkeypatch.setenv("MACHINE_ID", "M05")
    B.load_env(env)
    import os

    assert os.environ["VISION_API_TOKEN"] == "abc123"
    assert os.environ["MACHINE_ID"] == "M05"  # already set: kept
    assert "EMPTY" not in os.environ
    B.load_env(tmp_path / "missing.env")  # no error


def test_run_defaults_are_the_demo_persona(monkeypatch):
    run = pytest.importorskip("run")  # needs cv2
    for key in ("MACHINE_ID", "OPERATOR_ID", "BACKEND_URL", "VISION_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VISION_API_TOKEN", TOKEN)
    args = run.parse_args([])
    assert (args.machine_id, args.operator_id) == ("M05", "OP02")
    assert args.backend == "http://localhost:8000" and args.token == TOKEN
    assert args.machine_moving is None and args.fatigue_high is None  # poll the backend
    args = run.parse_args(["--machine-moving", "--no-fatigue-high"])
    assert args.machine_moving is True and args.fatigue_high is False
    assert run.parse_args(["--no-machine-moving"]).machine_moving is False

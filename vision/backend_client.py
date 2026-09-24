"""Read-side of the backend for the vision service (docs/api_contract.md).

- `load_env` reads vision/.env (VISION_API_TOKEN, ...) into os.environ without overriding it.
- `auth_headers` is the `Authorization: Bearer <VISION_API_TOKEN>` header every call carries.
- `MachineState` polls `GET /machine/{id}/state` for the machine-moving flag.
- `OperatorFatigue` polls `GET /operator/{id}/fatigue` for the high-fatigue check.

A poller runs on a daemon thread, GETs every `poll_s` seconds and caches the answer; reading it
never blocks the frame loop and never raises. What the cached value is:
- the backend answered: its value;
- the backend answered but has nothing usable (404: machine not in the running replay, no
  fatigue row; or a `stale` fatigue row): the safe default;
- the backend is down, times out, 5xx, or rejects the token (401/403): the last known value,
  or the safe default if there never was one.
Safe defaults: moving = True (eyes closed > 2 s is then critical; the machine may be moving),
fatigue high = False (the standard zones apply; widening them on every outage would flood alerts).
A manual override (the --machine-moving / --fatigue-high CLI flags) replaces polling entirely.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("vision.backend")

ENV_PATH = Path(__file__).resolve().parent / ".env"
POLL_S = 2.0
TIMEOUT_S = 1.5
MOVING_DEFAULT = True
FATIGUE_HIGH_DEFAULT = False


def load_env(path: Path = ENV_PATH) -> None:
    """KEY=VALUE lines into os.environ; variables already set win. A missing file is fine."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


def auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class Poller[T]:
    """Cache of one backend GET, refreshed every `poll_s` on a background thread.

    `parse` turns a 200 JSON body into a value, or None when the body is not usable (then the
    default applies). Call `start()` to begin polling; `poll_once()` does one round synchronously.
    """

    def __init__(
        self,
        backend: str,
        path: str,
        parse: Callable[[dict[str, Any]], T | None],
        default: T,
        token: str | None = None,
        override: T | None = None,
        poll_s: float = POLL_S,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self.url = backend.rstrip("/") + path
        self.parse = parse
        self.default = default
        self.override = override
        self.poll_s = poll_s
        self.headers = auth_headers(token)
        self.timeout_s = timeout_s
        self.source = "override" if override is not None else "default"
        self.polls = 0
        self._value: T = override if override is not None else default
        self._up: bool | None = None  # for logging transitions only
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def value(self) -> T:
        return self._value

    def start(self) -> Poller[T]:
        if self.override is None and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name=f"poll {self.url}", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout_s + 0.5)

    def _loop(self) -> None:
        with httpx.Client(timeout=self.timeout_s, headers=self.headers) as client:
            while not self._stop.is_set():
                self.poll_once(client)
                self._stop.wait(self.poll_s)

    def poll_once(self, client: httpx.Client | None = None) -> T:
        if self.override is not None:
            return self._value
        self.polls += 1
        try:
            if client is None:
                with httpx.Client(timeout=self.timeout_s, headers=self.headers) as c:
                    resp = c.get(self.url)
            else:
                resp = client.get(self.url)
        except httpx.HTTPError as exc:
            self._keep(f"unreachable ({exc.__class__.__name__})")
            return self._value
        if resp.status_code == 200:
            try:
                parsed = self.parse(resp.json())
            except (ValueError, TypeError, KeyError) as exc:
                self._keep(f"bad body ({exc})")
                return self._value
            self._set(parsed)
        elif resp.status_code == 404:
            self._set(None)
        elif resp.status_code in (401, 403):
            self._keep(f"{resp.status_code} (check VISION_API_TOKEN)", level=logging.ERROR)
        else:
            self._keep(f"HTTP {resp.status_code}")
        return self._value

    def _set(self, parsed: T | None) -> None:
        if self._up is not True:
            log.info("GET %s ok", self.url)
        self._up = True
        value, source = (self.default, "default") if parsed is None else (parsed, "backend")
        if value != self._value or source != self.source:
            log.info("GET %s -> %s (%s)", self.url, value, source)
        self._value, self.source = value, source

    def _keep(self, why: str, level: int = logging.WARNING) -> None:
        if self._up is not False:
            kept = "last known" if self.source == "backend" else "default"
            log.log(level, "GET %s %s; keeping the %s value %s", self.url, why, kept, self._value)
        self._up = False
        if self.source == "backend":
            self.source = "last"


def _moving(body: dict[str, Any]) -> bool | None:
    return bool(body["moving"])


def _fatigue_high(body: dict[str, Any]) -> bool | None:
    if body.get("stale", False) or body.get("fatigue_level") is None:
        return None  # contract: a stale row means the level is unknown
    return body["fatigue_level"] == "high"


class MachineState(Poller[bool]):
    def __init__(self, backend: str, machine_id: str, token: str | None = None, **kw: Any) -> None:
        super().__init__(
            backend, f"/machine/{machine_id}/state", _moving, MOVING_DEFAULT, token, **kw
        )

    def moving(self) -> bool:
        return self.value


class OperatorFatigue(Poller[bool]):
    def __init__(self, backend: str, operator_id: str, token: str | None = None, **kw: Any) -> None:
        super().__init__(
            backend,
            f"/operator/{operator_id}/fatigue",
            _fatigue_high,
            FATIGUE_HIGH_DEFAULT,
            token,
            **kw,
        )

    def is_high(self) -> bool:
        return self.value

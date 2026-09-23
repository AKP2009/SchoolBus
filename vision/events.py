"""Deliver vision events to the backend's `POST /events` (docs/api_contract.md).

`EventSink.send(event)` never blocks the frame loop and never raises. A background thread posts
with httpx and retries with exponential backoff while the backend is down (connection errors,
timeouts, 5xx). 4xx and 501 (the endpoint is still a stub) are not retryable: the event is logged
and dropped. The queue is bounded; when it is full the oldest event is dropped, since a stale
proximity warning is worth less than a fresh one.

`dry_run=True` prints each event as one JSON line instead of posting.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

import httpx

log = logging.getLogger("vision.events")

MAX_QUEUE = 200
BACKOFF_START_S = 0.5
BACKOFF_MAX_S = 10.0
TIMEOUT_S = 3.0


class EventSink:
    def __init__(self, backend: str, dry_run: bool = False, token: str | None = None) -> None:
        self.url = backend.rstrip("/") + "/events"
        self.dry_run = dry_run
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.sent = 0
        self.dropped = 0
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=MAX_QUEUE)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if not dry_run:
            self._thread = threading.Thread(target=self._worker, name="event-sink", daemon=True)
            self._thread.start()

    def send(self, event: dict[str, Any]) -> None:
        if self.dry_run:
            print(json.dumps(event), flush=True)
            self.sent += 1
            return
        while True:
            try:
                self._queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass

    def close(self, timeout_s: float = 2.0) -> None:
        """Give queued events a short chance to go out, then stop the worker."""
        deadline = time.monotonic() + timeout_s
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _worker(self) -> None:
        with httpx.Client(timeout=TIMEOUT_S, headers=self.headers) as client:
            while not self._stop.is_set():
                try:
                    event = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._deliver(client, event)

    def _deliver(self, client: httpx.Client, event: dict[str, Any]) -> None:
        backoff = BACKOFF_START_S
        while not self._stop.is_set():
            try:
                resp = client.post(self.url, json=event)
            except httpx.HTTPError as exc:  # backend down, DNS, timeout
                log.warning("POST /events failed (%s); retry in %.1f s", exc, backoff)
            else:
                if resp.status_code < 300:
                    self.sent += 1
                    return
                if resp.status_code < 500 or resp.status_code == 501:
                    self.dropped += 1
                    log.error("POST /events rejected %s: %s", resp.status_code, resp.text[:200])
                    return
                log.warning("POST /events got %s; retry in %.1f s", resp.status_code, backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

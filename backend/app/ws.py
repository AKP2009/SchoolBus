"""WebSocket fan-out for `GET /stream/{machine_id}` (docs/api_contract.md).

Server -> client: one JSON object per message, `kind` in telemetry / alert / safety / health /
fatigue. Client -> server: `{"kind": "ping"}` every 20 s. A client silent for longer than
`STALE_S` (3 missed pings) is dropped; a failed send drops the client too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

log = logging.getLogger(__name__)

KINDS = ("telemetry", "alert", "safety", "health", "fatigue")
STALE_S = 60.0


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat().replace("+00:00", "Z")
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def encode(kind: str, data: Any) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown message kind {kind!r}")
    return json.dumps({"kind": kind, "data": data}, default=_default, allow_nan=False)


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: dict[str, set[WebSocket]] = defaultdict(set)
        self.last_seen: dict[WebSocket, float] = {}
        self._lock = asyncio.Lock()

    async def connect(self, machine_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients[machine_id].add(ws)
            self.last_seen[ws] = time.monotonic()

    async def disconnect(self, machine_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.get(machine_id, set()).discard(ws)
            if machine_id in self.clients and not self.clients[machine_id]:
                del self.clients[machine_id]
            self.last_seen.pop(ws, None)
        if ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.close()
            except RuntimeError:
                pass

    def touch(self, ws: WebSocket) -> None:
        self.last_seen[ws] = time.monotonic()

    def count(self, machine_id: str | None = None) -> int:
        if machine_id is None:
            return sum(len(s) for s in self.clients.values())
        return len(self.clients.get(machine_id, ()))

    async def send(self, machine_id: str, kind: str, data: Any) -> None:
        """Push one message to every client of the machine. Never raises."""
        targets = list(self.clients.get(machine_id, ()))
        if not targets:
            return
        try:
            text = encode(kind, data)
        except (TypeError, ValueError) as e:
            log.error("dropping %s message for %s: %s", kind, machine_id, e)
            return
        now = time.monotonic()
        for ws in targets:
            if now - self.last_seen.get(ws, now) > STALE_S:
                await self.disconnect(machine_id, ws)
                continue
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001 - client went away mid-send
                await self.disconnect(machine_id, ws)


manager = ConnectionManager()

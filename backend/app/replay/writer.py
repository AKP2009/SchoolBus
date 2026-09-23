"""Ordered writer: alerts and health snapshots -> Supabase (service role), alerts -> WebSocket.

One consumer task handles the queue in order, so an alert's insert (which returns its id) always
completes before any update of the same alert, and the WebSocket `alert` message carries the id.
Consecutive health snapshots are inserted in one batch. Telemetry rows are never written.

A failed write is logged and skipped; the replay keeps going. An alert whose insert failed
gets a negative local id on the WebSocket, and its later updates are retried as inserts.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from app.alerts.rules import AlertEvent

log = logging.getLogger(__name__)


class Pusher(Protocol):
    async def send(self, machine_id: str, kind: str, data: Any) -> None: ...


@dataclass
class _HealthItem:
    row: dict[str, Any]


class Store(Protocol):
    """What the writer needs from the database (sync; called in the threadpool)."""

    def insert_alert(self, row: dict[str, Any]) -> int: ...
    def update_alert(self, alert_id: int, fields: dict[str, Any]) -> None: ...
    def insert_health(self, rows: list[dict[str, Any]]) -> None: ...
    def acknowledged(self, alert_ids: list[int]) -> set[int]: ...
    def latest_maintenance(self, machine_id: str, at: str) -> dict[str, Any] | None: ...


class SupabaseStore:
    def __init__(self, client: Any) -> None:
        self.sb = client

    def insert_alert(self, row: dict[str, Any]) -> int:
        data = self.sb.table("alerts").insert(row).execute().data
        return int(data[0]["id"])

    def update_alert(self, alert_id: int, fields: dict[str, Any]) -> None:
        self.sb.table("alerts").update(fields).eq("id", alert_id).execute()

    def insert_health(self, rows: list[dict[str, Any]]) -> None:
        self.sb.table("machine_health_snapshots").insert(rows).execute()

    def acknowledged(self, alert_ids: list[int]) -> set[int]:
        if not alert_ids:
            return set()
        data = (
            self.sb.table("alerts")
            .select("id,acknowledged_at")
            .in_("id", alert_ids)
            .not_.is_("acknowledged_at", "null")
            .execute()
            .data
        )
        return {int(r["id"]) for r in data or []}

    def latest_maintenance(self, machine_id: str, at: str) -> dict[str, Any] | None:
        data = (
            self.sb.table("maintenance_predictions")
            .select(
                "machine_id,predicted_at,horizon_hours,failure_probability,likely_component,top_factors"
            )
            .eq("machine_id", machine_id)
            .lte("predicted_at", at)
            .order("predicted_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return data[0] if data else None


class MemoryStore:
    """In-memory Store for tests and for running without a database."""

    def __init__(self) -> None:
        self.alerts: dict[int, dict[str, Any]] = {}
        self.health: list[dict[str, Any]] = []
        self.acked: set[int] = set()
        self.maintenance: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)

    def insert_alert(self, row: dict[str, Any]) -> int:
        i = next(self._ids)
        self.alerts[i] = {"id": i, **row}
        return i

    def update_alert(self, alert_id: int, fields: dict[str, Any]) -> None:
        self.alerts[alert_id].update(fields)

    def insert_health(self, rows: list[dict[str, Any]]) -> None:
        self.health.extend(rows)

    def acknowledged(self, alert_ids: list[int]) -> set[int]:
        return self.acked & set(alert_ids)

    def latest_maintenance(self, machine_id: str, at: str) -> dict[str, Any] | None:
        return self.maintenance.get(machine_id)


UPDATE_FIELDS = (
    "title",
    "message",
    "recommended_action",
    "severity",
    "stage",
    "anomaly_score",
    "evidence",
    "resolved_at",
)


class Writer:
    def __init__(self, store: Store, pusher: Pusher) -> None:
        self.store = store
        self.pusher = pusher
        self.queue: asyncio.Queue[AlertEvent | _HealthItem | None] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        self._local_ids = itertools.count(-1, -1)
        self.errors = 0

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name="replay-writer")

    async def stop(self, drain: bool = True) -> None:
        if self.task is None:
            return
        if drain:
            await self.queue.put(None)
            await self.task
        else:
            self.task.cancel()
        self.task = None

    async def flush(self) -> None:
        """Wait until everything queued so far is written (tests, shutdown)."""
        await self.queue.join()

    def alert(self, event: AlertEvent) -> None:
        self.queue.put_nowait(event)

    def health(self, row: dict[str, Any]) -> None:
        self.queue.put_nowait(_HealthItem(row))

    async def _run(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            batch: list[_HealthItem] = []
            try:
                if isinstance(item, _HealthItem):
                    batch.append(item)
                    # take any further queued health rows with it (stop at the first alert)
                    while not self.queue.empty():
                        nxt = self.queue._queue[0]  # type: ignore[attr-defined]
                        if not isinstance(nxt, _HealthItem):
                            break
                        batch.append(self.queue.get_nowait())  # type: ignore[arg-type]
                    await self._write_health([b.row for b in batch])
                else:
                    await self._write_alert(item)
            except Exception:  # noqa: BLE001 - keep the writer alive
                log.exception("writer failed")
            finally:
                for _ in range(len(batch) or 1):
                    self.queue.task_done()

    async def _write_health(self, rows: list[dict[str, Any]]) -> None:
        try:
            await run_in_threadpool(self.store.insert_health, rows)
        except Exception as e:  # noqa: BLE001
            self.errors += 1
            log.error("health snapshot insert failed (%d rows): %s", len(rows), e)

    async def _write_alert(self, ev: AlertEvent) -> None:
        a = ev.alert
        try:
            if ev.op == "insert" or a.db_id is None or a.db_id < 0:
                a.db_id = await run_in_threadpool(self.store.insert_alert, ev.row)
            else:
                fields = {k: ev.row[k] for k in UPDATE_FIELDS}
                await run_in_threadpool(self.store.update_alert, a.db_id, fields)
        except Exception as e:  # noqa: BLE001
            self.errors += 1
            log.error("alert %s %s failed: %s", ev.op, a.alert_code, e)
            if a.db_id is None:
                a.db_id = next(self._local_ids)
        data = {**ev.stream, "id": a.db_id}
        await self.pusher.send(a.machine_id, "alert", data)

"""Telemetry replay engine (docs/architecture.md "Live telemetry and alerts").

`POST /replay/start` replays the recorded telemetry of the selected machines from a start
timestamp as if it were live, at `speed` minutes of data per real minute (1, 10 or 60):

1. Rows come from Supabase `telemetry` in pages, or from the generator's parquet as a fallback
   (`source.choose_source`). The hour before the start is read as warm-up: it fills the rule
   history and the anomaly window, but raises nothing.
2. Each row goes through the rule engine (`app.alerts.rules`) and out on
   `ws /stream/{machine_id}` as a `telemetry` message. Telemetry is never written to the DB.
3. At the first row of every data minute, per machine, in the threadpool: model 1
   (`ml.inference.anomaly.score_anomaly`) on the trailing 60-minute window (one row per
   minute, the same features, glitch handling, idle/working split and persistence as the
   model), then `ml.inference.health.compute_health` with the rule engine's open alerts and the
   latest `maintenance_predictions` row at or before the replay time, if there is one.
   The snapshot goes to `machine_health_snapshots` and out as a `health` message.
4. Alert changes (new, stage change, severity rise, resolved) go to `alerts` through the ordered
   writer, which then pushes the `alert` message with the row id.

`POST /scenario/{name}` splices a scripted sequence (`scenarios.py`) into one machine's stream.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from starlette.concurrency import run_in_threadpool

from app.alerts.rules import MachineRuleEngine, load_thresholds
from app.core.config import REPO_ROOT
from app.replay import scenarios as scenario_lib
from app.replay.scenarios import Scenario
from app.replay.source import TelemetrySource
from app.replay.writer import Pusher, Store, Writer

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.inference import anomaly as A  # noqa: E402
from ml.inference import health as H  # noqa: E402

log = logging.getLogger(__name__)

WARMUP = timedelta(hours=1)
ANOMALY_WINDOW = timedelta(minutes=60)  # >= 30 min needed for idle_pct30 / slope15
REFILL_BELOW = 200
MAINTENANCE_REFRESH = timedelta(hours=1)
TRAVEL_KMH = H.TRAVEL_KMH


class ReplayError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def telemetry_payload(row: dict[str, Any]) -> dict[str, Any]:
    """`telemetry` message data: the row's columns, ts as ISO text, no internal keys."""
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out["ts"] = _iso(row["ts"])
    return out


@dataclass
class MachineStream:
    machine_id: str
    machine_type: str
    site_id: str | None
    rules: MachineRuleEngine
    buffer: deque[dict[str, Any]] = field(default_factory=deque)
    cursor: datetime | None = None  # ts of the last row fetched
    exhausted: bool = False
    minute_rows: deque[dict[str, Any]] = field(default_factory=deque)
    last_minute: datetime | None = None
    last_row: dict[str, Any] | None = None  # latest emitted row
    template: dict[str, Any] | None = None  # latest recorded row (scenario template)
    scenario: Scenario | None = None
    health: dict[str, Any] | None = None
    anomaly: dict[str, Any] | None = None
    maintenance: dict[str, Any] | None = None
    maintenance_checked: datetime | None = None

    def add_minute_row(self, row: dict[str, Any]) -> None:
        self.minute_rows.append(row)
        cutoff = row["ts"] - ANOMALY_WINDOW
        while self.minute_rows and self.minute_rows[0]["ts"] <= cutoff:
            self.minute_rows.popleft()


class ReplayEngine:
    def __init__(
        self,
        pusher: Pusher,
        store: Store | None = None,
        tick_s: float = 0.5,
        score: bool = True,
    ) -> None:
        self.pusher = pusher
        self.store = store
        self.tick_s = tick_s
        self.score_enabled = score
        self.thresholds = load_thresholds()
        self.writer: Writer | None = None
        self.source: TelemetrySource | None = None
        self.streams: dict[str, MachineStream] = {}
        self.speed: float = 1
        self.replay_ts: datetime | None = None
        self.running = False
        self.task: asyncio.Task[None] | None = None
        self.anomaly_error: str | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------------------------
    async def start(
        self,
        machine_ids: list[str],
        from_ts: datetime,
        speed: float,
        source: TelemetrySource,
        machines: dict[str, dict[str, Any]],
        geofences: list[dict[str, Any]] | None = None,
        run_loop: bool = True,
    ) -> None:
        async with self._lock:
            await self._stop()
            from_ts = from_ts.astimezone(UTC)
            unknown = [m for m in machine_ids if m not in machines]
            if unknown:
                raise ReplayError(
                    "UNKNOWN_MACHINE", f"Unknown machine(s): {', '.join(unknown)}", 404
                )
            self.source = source
            self.speed = speed
            self.replay_ts = from_ts
            self.streams = {}
            for mid in dict.fromkeys(machine_ids):
                info = machines[mid]
                site = info.get("site_id")
                fences = [g for g in geofences or [] if g.get("site_id") == site]
                rules = MachineRuleEngine(mid, info["machine_type"], site, self.thresholds, fences)
                self.streams[mid] = MachineStream(mid, info["machine_type"], site, rules)
            await run_in_threadpool(self._warmup, from_ts)
            if self.score_enabled:
                await run_in_threadpool(self._load_models)
            if self.store is not None:
                self.writer = Writer(self.store, self.pusher)
                self.writer.start()
            self.running = True
            if run_loop:
                self.task = asyncio.create_task(self._loop(), name="replay-loop")

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        self.running = False
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.task = None
        if self.writer is not None:
            await self.writer.stop(drain=True)
            self.writer = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "machine_ids": list(self.streams),
            "speed": self.speed if self.streams else None,
            "replay_ts": self.replay_ts,
            "source": self.source.name if self.source else None,
            "scenarios": {
                m: s.scenario.name for m, s in self.streams.items() if s.scenario is not None
            },
        }

    def _load_models(self) -> None:
        try:
            A.load_artifacts()
            self.anomaly_error = None
        except Exception as e:  # noqa: BLE001 - replay still runs rules + health without it
            self.anomaly_error = str(e)
            log.error("anomaly model not loaded, health runs without it: %s", e)

    def _warmup(self, from_ts: datetime) -> None:
        assert self.source is not None
        end = from_ts - timedelta(microseconds=1)
        for s in self.streams.values():
            rows = self.source.fetch(
                s.machine_id, from_ts - WARMUP, end, limit=int(WARMUP.total_seconds() // 60) + 5
            )
            for r in rows:
                s.rules.prime(r)
                s.add_minute_row(r)
                s.last_minute = r["ts"].replace(second=0, microsecond=0)
                s.template = r
            s.cursor = end

    # -- clock ---------------------------------------------------------------------------------
    async def _loop(self) -> None:
        last = time.monotonic()
        try:
            while self.running:
                await asyncio.sleep(self.tick_s)
                now = time.monotonic()
                assert self.replay_ts is not None
                target = self.replay_ts + timedelta(seconds=(now - last) * self.speed)
                last = now
                await self.advance_to(target)
                if all(
                    s.exhausted and not s.buffer and s.scenario is None
                    for s in self.streams.values()
                ):
                    log.info("replay reached the end of the data at %s", self.replay_ts)
                    self.running = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("replay loop crashed")
            self.running = False

    async def advance_to(self, target: datetime) -> None:
        """Emit and process every row with ts <= target (all machines), then move the clock."""
        for s in self.streams.values():
            for row in await self._due(s, target):
                await self._process(s, row)
        self.replay_ts = target

    async def _refill(self, s: MachineStream) -> None:
        if s.exhausted or len(s.buffer) >= REFILL_BELOW or self.source is None:
            return
        assert s.cursor is not None
        rows = await run_in_threadpool(self.source.fetch, s.machine_id, s.cursor)
        if not rows:
            s.exhausted = True
            return
        s.buffer.extend(rows)
        s.cursor = rows[-1]["ts"]

    async def _due(self, s: MachineStream, target: datetime) -> list[dict[str, Any]]:
        base: list[dict[str, Any]] = []
        while True:
            await self._refill(s)
            if not s.buffer or s.buffer[0]["ts"] > target:
                break
            base.append(s.buffer.popleft())
        if base:
            s.template = base[-1]
        sc = s.scenario
        if sc is None:
            return base
        # a scenario owns the stream: recorded rows inside its window only refresh the template
        later = [r for r in base if r["ts"] >= sc.end_ts]
        inside = [r for r in base if r["ts"] < sc.end_ts]
        if inside:
            sc.template = {
                **inside[-1],
                "machine_id": s.machine_id,
                "_machine_type": s.machine_type,
            }
        out = sc.due_rows(target)
        if sc.finished:
            log.info("scenario %s on %s finished", sc.name, s.machine_id)
            s.scenario = None
            out += later
        return out

    # -- per row -------------------------------------------------------------------------------
    async def _process(self, s: MachineStream, row: dict[str, Any]) -> None:
        s.last_row = row
        for ev in s.rules.process(row):
            if self.writer is not None:
                self.writer.alert(ev)
        await self.pusher.send(s.machine_id, "telemetry", telemetry_payload(row))
        minute = row["ts"].replace(second=0, microsecond=0)
        if s.last_minute is None or minute > s.last_minute:
            s.last_minute = minute
            s.add_minute_row(row)
            await self._score_minute(s, row["ts"])

    async def _score_minute(self, s: MachineStream, ts: datetime) -> None:
        ack_ids = [
            a.db_id
            for a in s.rules.open_alerts()
            if a.stage == "recommend_shutdown" and a.db_id and a.db_id > 0 and not a.acknowledged
        ]
        refresh_maint = (
            s.maintenance_checked is None or ts - s.maintenance_checked >= MAINTENANCE_REFRESH
        )
        if refresh_maint:
            s.maintenance_checked = ts
        window = pd.DataFrame(list(s.minute_rows))
        result = await run_in_threadpool(self._infer_io, s, window, ts, ack_ids, refresh_maint)
        anomaly, acked, maint = result
        for alert_id in acked:
            s.rules.acknowledge(alert_id)
        if refresh_maint:
            s.maintenance = maint
        s.anomaly = anomaly
        for ev in s.rules.observe_anomaly(anomaly, ts):
            if self.writer is not None:
                self.writer.alert(ev)
        speed = (s.last_row or {}).get("ground_speed_kmh") or 0.0
        health = H.compute_health(
            s.machine_id,
            ts,
            rule_states=[a.health_state() for a in s.rules.open_alerts()],
            anomaly=anomaly,
            maintenance=s.maintenance,
            travelling=speed > TRAVEL_KMH,
        )
        s.health = health
        if self.writer is not None:
            self.writer.health(H.to_snapshot_row(health))
        await self.pusher.send(
            s.machine_id,
            "health",
            {"overall": health["overall"], "subsystems": health["subsystems"]},
        )

    def _infer_io(
        self,
        s: MachineStream,
        window: pd.DataFrame,
        ts: datetime,
        ack_ids: list[int],
        refresh_maint: bool,
    ) -> tuple[dict[str, Any] | None, set[int], dict[str, Any] | None]:
        """Threadpool: anomaly score, acknowledgement poll, maintenance lookup."""
        anomaly = None
        if self.score_enabled and self.anomaly_error is None and not window.empty:
            try:
                anomaly = A.score_anomaly(
                    window.loc[:, A.INPUT_COLUMNS], machine_type=s.machine_type
                )
            except Exception as e:  # noqa: BLE001
                log.warning("anomaly scoring failed for %s at %s: %s", s.machine_id, ts, e)
        acked: set[int] = set()
        maint = None
        if self.store is not None:
            try:
                if ack_ids:
                    acked = self.store.acknowledged(ack_ids)
                if refresh_maint:
                    maint = self.store.latest_maintenance(s.machine_id, _iso(ts))
            except Exception as e:  # noqa: BLE001
                log.warning("store read failed for %s: %s", s.machine_id, e)
        return anomaly, acked, maint

    # -- scenarios / reads ---------------------------------------------------------------------
    def trigger(self, name: str, machine_id: str) -> Scenario:
        if not self.running or self.replay_ts is None:
            raise ReplayError("REPLAY_NOT_RUNNING", "Start the replay first: POST /replay/start.")
        s = self.streams.get(machine_id)
        if s is None:
            raise ReplayError(
                "MACHINE_NOT_IN_REPLAY",
                f"{machine_id} is not in the replay. Restart it with {machine_id} included.",
            )
        if name not in scenario_lib.SCENARIOS:
            raise ReplayError(
                "NOT_IMPLEMENTED", f"Scenario '{name}' is not built into the replay.", 501
            )
        if s.scenario is not None:
            raise ReplayError(
                "SCENARIO_RUNNING",
                f"Scenario '{s.scenario.name}' is still running on {machine_id}.",
            )
        template = s.template or s.last_row
        if template is None:
            raise ReplayError(
                "NO_TELEMETRY", f"No telemetry for {machine_id} yet to base the scenario on."
            )
        sc = scenario_lib.make(name)
        start = self.replay_ts
        if s.last_row is not None and start <= s.last_row["ts"]:
            start = s.last_row["ts"] + timedelta(seconds=1)
        sc.start_ts = start
        sc.template = {**template, "machine_id": machine_id, "_machine_type": s.machine_type}
        s.scenario = sc
        log.info("scenario %s on %s from %s", name, machine_id, start)
        return sc

    def machine_health(self, machine_id: str) -> dict[str, Any] | None:
        s = self.streams.get(machine_id)
        return s.health if s else None

    def machine_state(self, machine_id: str) -> dict[str, Any] | None:
        s = self.streams.get(machine_id)
        if s is None or s.last_row is None:
            return None
        speed = s.last_row.get("ground_speed_kmh")
        speed = float(speed) if speed is not None else 0.0
        return {
            "machine_id": machine_id,
            "moving": speed > self.thresholds["moving_kmh"],
            "ground_speed_kmh": round(speed, 2),
            "ts": s.last_row["ts"],
        }

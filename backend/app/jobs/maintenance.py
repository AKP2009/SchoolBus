"""Predictive maintenance scoring job (docs/supabase.md §10, models.md §3).

Every `EVERY` of replay (data) time, per machine in the running replay:
1. telemetry history before the replay start (`TelemetryHistory`: parquet, else Supabase) plus
   the rows the replay has emitted since (scenario rows included), one row per minute,
2. model 1 on every minute (`maintenance.score_minutes`; the history part is scored once per
   replay and cached, new minutes get a 60-minute context),
3. `maintenance.build_hourly_features` -> the machine's latest engine-hour row ->
   `maintenance.predict_failure`,
4. a `maintenance_predictions` row with `predicted_at` = replay time, handed to the replay
   engine at once so the next health minute shows the new failure probability.

APScheduler calls `tick()` every few seconds of wall-clock time; it only scores a machine once
its replay clock has moved `EVERY` since the last score.
"""

from __future__ import annotations

import logging
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from starlette.concurrency import run_in_threadpool

from app.core.config import REPO_ROOT
from app.repo import Repo, iso
from app.services.telemetry import TelemetryHistory, rows_frame

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.inference import maintenance as M  # noqa: E402

log = logging.getLogger(__name__)

EVERY = timedelta(minutes=10)  # replay time between two scores of one machine
HISTORY = timedelta(days=35)  # > 336 engine hours of baseline + 72 h windows at ~15 h a day
CONTEXT = timedelta(minutes=60)  # model 1 context for scoring new minutes


@dataclass
class _Cache:
    replay_start: datetime
    history: pd.DataFrame  # telemetry before the replay start
    scores: pd.DataFrame  # model 1 per history minute


class MaintenanceScorer:
    def __init__(self, repo: Repo, engine: Any, telemetry: TelemetryHistory) -> None:
        self.repo = repo
        self.engine = engine
        self.telemetry = telemetry
        self.last_scored: dict[str, tuple[datetime, datetime]] = {}  # mid -> (replay start, at)
        self._cache: dict[str, _Cache] = {}
        self._static: tuple[datetime, dict[str, pd.DataFrame]] | None = None
        self.model_version: str | None = None
        self.error: str | None = None

    def _static_frames(self, replay_start: datetime) -> dict[str, pd.DataFrame]:
        """shifts, machines and maintenance_log (small, loaded once per replay)."""
        if self._static is None or self._static[0] != replay_start:
            self._static = (
                replay_start,
                {
                    "shifts": self.repo.shifts(),
                    "machines": self.repo.machines(),
                    "maintenance_log": self.repo.maintenance_log(),
                },
            )
        return self._static[1]

    def _history(self, mid: str, machine_type: str, replay_start: datetime) -> _Cache:
        c = self._cache.get(mid)
        if c is None or c.replay_start != replay_start:
            hist = self.telemetry.frame([mid], replay_start - HISTORY, replay_start)
            scores = (
                M.score_minutes(hist, {mid: machine_type})
                if not hist.empty
                else pd.DataFrame(columns=["machine_id", "ts", "anomaly_score", "kind"])
            )
            c = _Cache(replay_start, hist, scores)
            self._cache[mid] = c
        return c

    def score(
        self,
        mid: str,
        machine_type: str,
        replay_start: datetime,
        replayed: list[dict[str, Any]],
        at: datetime,
    ) -> dict[str, Any] | None:
        """One maintenance_predictions row for `mid` at replay time `at` (sync, threadpool)."""
        art = M.load_artifacts()
        self.model_version = str(art["config"].get("version", "v1"))
        c = self._history(mid, machine_type, replay_start)
        new = rows_frame(replayed) if replayed else pd.DataFrame(columns=c.history.columns)
        if not new.empty:
            ctx = c.history[c.history.ts >= replay_start - CONTEXT]
            both = pd.concat([ctx, new], ignore_index=True)
            s = M.score_minutes(both, {mid: machine_type})
            s = s[pd.to_datetime(s.ts, utc=True) >= pd.Timestamp(replay_start)]
            with warnings.catch_warnings():  # all-NA fault_code in the new rows is expected
                warnings.simplefilter("ignore", FutureWarning)
                tel = pd.concat([c.history, new], ignore_index=True)
                scores = pd.concat([c.scores, s], ignore_index=True)
        else:
            tel, scores = c.history, c.scores
        if tel.empty:
            return None
        st = self._static_frames(replay_start)
        feats = M.build_hourly_features(
            tel, scores, st["shifts"], st["machines"], st["maintenance_log"]
        )
        feats = feats[feats.machine_id == mid]
        if feats.empty:
            return None
        p = M.predict_failure(feats.iloc[[-1]])[0]
        return {
            "machine_id": mid,
            "predicted_at": iso(at),
            "horizon_hours": p["horizon_hours"],
            "failure_probability": p["failure_probability"],
            "likely_component": p["likely_component"],
            "top_factors": p["top_factors"],
            "model_version": self.model_version,
        }

    def _score_and_store(self, *args: Any) -> dict[str, Any] | None:
        row = self.score(*args)
        if row is not None:
            row = {**row, "id": self.repo.insert_maintenance_prediction(row)}
        return row

    async def tick(self) -> int:
        """Score every replayed machine whose replay clock moved EVERY since its last score.
        Returns the number of rows written."""
        eng = self.engine
        if not eng.running or eng.replay_ts is None or eng.from_ts is None:
            return 0
        written = 0
        for mid, s in list(eng.streams.items()):
            at = eng.replay_ts
            last = self.last_scored.get(mid)
            if last is not None and last[0] == eng.from_ts and at - last[1] < EVERY:
                continue
            self.last_scored[mid] = (eng.from_ts, at)
            try:
                row = await run_in_threadpool(
                    self._score_and_store, mid, s.machine_type, eng.from_ts, list(s.replayed), at
                )
            except Exception as e:  # noqa: BLE001 - keep the other machines going
                self.error = str(e)
                log.exception("maintenance scoring failed for %s", mid)
                continue
            self.error = None
            if row is None:
                continue
            written += 1
            eng.set_maintenance(mid, row)
            log.info(
                "maintenance %s at %s: p=%.3f %s",
                mid,
                row["predicted_at"],
                row["failure_probability"],
                row["likely_component"],
            )
        return written

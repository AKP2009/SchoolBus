"""In-memory stand-ins for the database, WebSocket and telemetry history.

`MemoryRepo` implements `app.repo.Repo` on the generator's committed 1-day sample
(`data/output/sample/`, 2026-06-01, machines M01 and M06), so the task-time, plan, maintenance
and clustering code runs on real rows and the real models.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.replay.source import TELEMETRY_COLUMNS
from app.repo import (
    FATIGUE_COLUMNS,
    MACHINE_COLUMNS,
    MAINTENANCE_LOG_COLUMNS,
    OPERATOR_COLUMNS,
    SAFETY_COLUMNS,
    SHIFT_COLUMNS,
    TASK_COLUMNS,
    WEATHER_COLUMNS,
    frame,
    iso,
)

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "output" / "sample"


def _csv(name: str, columns: list[str]) -> pd.DataFrame:
    df = pd.read_csv(SAMPLE / f"{name}.csv", keep_default_na=True)
    rows = df.astype(object).where(df.notna(), None).to_dict("records")
    return frame(rows, columns)


def _ts(v: Any) -> pd.Timestamp:
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for r in df.astype(object).where(df.notna(), None).to_dict("records"):
        out.append({k: (iso(v) if isinstance(v, pd.Timestamp) else v) for k, v in r.items()})
    return out


class MemoryRepo:
    def __init__(self) -> None:
        self.t_shifts = _csv("shifts", SHIFT_COLUMNS)
        self.t_tasks = _csv("tasks", TASK_COLUMNS)
        self.t_tasks["prediction_factors"] = None
        self.t_tasks["updated_at"] = None
        self.t_weather = _csv("weather", WEATHER_COLUMNS)
        self.t_operators = _csv("operators", OPERATOR_COLUMNS)
        self.t_machines = _csv("machines", MACHINE_COLUMNS)
        self.t_fatigue = _csv("fatigue_log", FATIGUE_COLUMNS)
        self.t_mlog = _csv("maintenance_log", MAINTENANCE_LOG_COLUMNS)
        self.t_safety = _csv("safety_events", SAFETY_COLUMNS)
        self.alerts: dict[int, dict[str, Any]] = {}
        self.safety_rows: list[dict[str, Any]] = []
        self.fatigue_rows: list[dict[str, Any]] = []
        self.predictions: list[dict[str, Any]] = []
        self.fleet: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.health: dict[str, dict[str, Any]] = {}
        self.task_updates: list[tuple[str, dict[str, Any]]] = []
        self._ids = itertools.count(1)

    # -- lookups -------------------------------------------------------------------------------
    def _row(self, df: pd.DataFrame, key: str, value: str) -> dict[str, Any] | None:
        hit = df[df[key] == value]
        return _records(hit.head(1))[0] if len(hit) else None

    def machine(self, machine_id: str) -> dict[str, Any] | None:
        return self._row(self.t_machines, "machine_id", machine_id)

    def operator(self, operator_id: str) -> dict[str, Any] | None:
        return self._row(self.t_operators, "operator_id", operator_id)

    def shift(self, shift_id: str) -> dict[str, Any] | None:
        return self._row(self.t_shifts, "shift_id", shift_id)

    def latest_health(self, machine_id: str) -> dict[str, Any] | None:
        return self.health.get(machine_id)

    def latest_fatigue(
        self, operator_id: str, until: datetime | None = None, since: datetime | None = None
    ) -> dict[str, Any] | None:
        f = self.t_fatigue[self.t_fatigue.operator_id == operator_id]
        if until is not None:
            f = f[f.ts <= _ts(until)]
        if since is not None:
            f = f[f.ts >= _ts(since)]
        if f.empty:
            return None
        return _records(f.sort_values("ts").tail(1))[0]

    # -- writes --------------------------------------------------------------------------------
    def insert_alert(self, row: dict[str, Any]) -> int:
        i = next(self._ids)
        self.alerts[i] = {"id": i, **row}
        return i

    def update_alert(self, alert_id: int, fields: dict[str, Any]) -> None:
        self.alerts[alert_id].update(fields)

    def insert_safety_event(self, row: dict[str, Any]) -> int:
        i = next(self._ids)
        self.safety_rows.append({"id": i, **row})
        return i

    def insert_fatigue(self, row: dict[str, Any]) -> int:
        i = next(self._ids)
        self.fatigue_rows.append({"id": i, **row})
        new = frame([{"id": i, **row}], FATIGUE_COLUMNS)
        self.t_fatigue = pd.concat([self.t_fatigue, new], ignore_index=True)
        return i

    def update_task(self, task_id: str, fields: dict[str, Any]) -> None:
        self.task_updates.append((task_id, fields))
        idx = self.t_tasks.index[self.t_tasks.task_id == task_id]
        for k, v in fields.items():
            if k == "scheduled_start":
                v = _ts(v) if v is not None else pd.NaT
            elif k in ("prediction_factors", "updated_at", "status", "delay_reason"):
                self.t_tasks[k] = self.t_tasks[k].astype(object)
            for i in idx:
                self.t_tasks.at[i, k] = v

    def insert_maintenance_prediction(self, row: dict[str, Any]) -> int:
        i = next(self._ids)
        self.predictions.append({"id": i, **row})
        return i

    def upsert_fleet_metrics(self, rows: list[dict[str, Any]]) -> int:
        for r in rows:
            self.fleet[(r["entity_type"], r["entity_id"], r["week_start"])] = r
        return len(rows)

    # -- frames --------------------------------------------------------------------------------
    def tasks(
        self, task_ids: Iterable[str] | None = None, shift_ids: Iterable[str] | None = None
    ) -> pd.DataFrame:
        t = self.t_tasks
        if task_ids is not None:
            t = t[t.task_id.isin(list(task_ids))]
        if shift_ids is not None:
            t = t[t.shift_id.isin(list(shift_ids))]
        return t[TASK_COLUMNS].reset_index(drop=True).copy()

    def task_history(
        self, operator_ids: Iterable[str], start: datetime, end: datetime
    ) -> pd.DataFrame:
        t = self.t_tasks
        t = t[t.operator_id.isin(list(operator_ids)) & (t.status == "completed")]
        t = t[(t.actual_end >= _ts(start)) & (t.actual_end < _ts(end))]
        return t[TASK_COLUMNS].reset_index(drop=True).copy()

    def weather(self, site_ids: Iterable[str], start: datetime, end: datetime) -> pd.DataFrame:
        w = self.t_weather
        w = w[w.site_id.isin(list(site_ids)) & (w.ts >= _ts(start)) & (w.ts <= _ts(end))]
        return w.reset_index(drop=True).copy()

    def operators(self, operator_ids: Iterable[str]) -> pd.DataFrame:
        return self.t_operators[self.t_operators.operator_id.isin(list(operator_ids))].copy()

    def machines(self, machine_ids: Iterable[str] | None = None) -> pd.DataFrame:
        m = self.t_machines
        if machine_ids is not None:
            m = m[m.machine_id.isin(list(machine_ids))]
        return m.reset_index(drop=True).copy()

    def shifts(
        self,
        machine_ids: Iterable[str] | None = None,
        operator_ids: Iterable[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> pd.DataFrame:
        s = self.t_shifts
        if machine_ids is not None or operator_ids is not None:
            keep = pd.Series(False, index=s.index)
            if machine_ids is not None:
                keep |= s.machine_id.isin(list(machine_ids))
            if operator_ids is not None:
                keep |= s.operator_id.isin(list(operator_ids))
            s = s[keep]
        if date_from is not None:
            s = s[s.shift_date >= date_from.isoformat()]
        if date_to is not None:
            s = s[s.shift_date <= date_to.isoformat()]
        return s.reset_index(drop=True).copy()

    def fatigue_log(
        self, operator_ids: Iterable[str], start: datetime, end: datetime
    ) -> pd.DataFrame:
        f = self.t_fatigue
        f = f[f.operator_id.isin(list(operator_ids)) & (f.ts >= _ts(start)) & (f.ts < _ts(end))]
        return f.reset_index(drop=True).copy()

    def maintenance_log(self, machine_ids: Iterable[str] | None = None) -> pd.DataFrame:
        m = self.t_mlog
        if machine_ids is not None:
            m = m[m.machine_id.isin(list(machine_ids))]
        return m.reset_index(drop=True).copy()

    def safety_events(self, start: datetime, end: datetime) -> pd.DataFrame:
        s = self.t_safety
        return s[(s.ts >= _ts(start)) & (s.ts < _ts(end))].reset_index(drop=True).copy()

    def latest_shift_date(self) -> date | None:
        return date.fromisoformat(self.t_shifts.shift_date.max()) if len(self.t_shifts) else None


class FakePusher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []

    async def send(self, machine_id: str, kind: str, data: Any) -> None:
        self.sent.append((machine_id, kind, data))

    def kinds(self, machine_id: str | None = None) -> list[str]:
        return [k for m, k, _ in self.sent if machine_id is None or m == machine_id]


def sample_telemetry() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE / "telemetry.csv", usecols=TELEMETRY_COLUMNS)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)


class FakeTelemetry:
    """TelemetryHistory on the sample telemetry."""

    source = "parquet"

    def __init__(self) -> None:
        self.df = sample_telemetry()

    def frame(self, machine_ids: list[str], start: datetime, end: datetime) -> pd.DataFrame:
        d = self.df
        d = d[d.machine_id.isin(machine_ids) & (d.ts >= _ts(start)) & (d.ts < _ts(end))]
        return d.reset_index(drop=True).copy()

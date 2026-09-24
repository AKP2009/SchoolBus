"""Database access for the event, prediction, plan and analytics endpoints and the jobs.

`Repo` is what the services need; `SupabaseRepo` implements it with the service-role client
(bypasses RLS: the routers check permissions first). Tests use an in-memory implementation
(`tests/fakes.py`). All methods are sync: call them in the threadpool.

Frames come back with tz-aware UTC timestamps. Columns are selected explicitly; `anomaly_label`
and `anomaly_type` are never read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Protocol

import pandas as pd

log = logging.getLogger(__name__)

PAGE = 1000  # PostgREST max rows per request

SHIFT_COLUMNS = [
    "shift_id",
    "site_id",
    "operator_id",
    "machine_id",
    "shift_type",
    "shift_date",
    "start_time",
    "end_time",
]
TASK_COLUMNS = [
    "task_id",
    "site_id",
    "shift_id",
    "machine_id",
    "operator_id",
    "sequence_no",
    "task_date",
    "task_type",
    "material_type",
    "quantity",
    "unit",
    "terrain_slope_deg",
    "haul_distance_m",
    "priority",
    "scheduled_start",
    "predicted_p10_min",
    "predicted_p50_min",
    "predicted_p90_min",
    "actual_start",
    "actual_end",
    "actual_duration_min",
    "status",
    "delay_reason",
]
WEATHER_COLUMNS = ["site_id", "ts", "temp_c", "rain_mm", "wind_kmh", "visibility_m", "dust_index"]
OPERATOR_COLUMNS = [
    "operator_id",
    "site_id",
    "skill_score",
    "experience_years",
    "certification_level",
]
MACHINE_COLUMNS = [
    "machine_id",
    "site_id",
    "machine_type",
    "year",
    "total_engine_hours",
    "service_interval_hours",
]
FATIGUE_COLUMNS = [
    "id",
    "ts",
    "operator_id",
    "shift_id",
    "ear_avg",
    "perclos_60s",
    "yawn_count",
    "head_down_events",
    "phone_detected",
    "fatigue_score",
    "fatigue_level",
]
MAINTENANCE_LOG_COLUMNS = [
    "machine_id",
    "event_date",
    "engine_hours_at_event",
    "component",
    "event_type",
]
SAFETY_COLUMNS = ["ts", "site_id", "machine_id", "operator_id", "event_type", "severity"]
TS_COLUMNS = {
    "start_time",
    "end_time",
    "scheduled_start",
    "actual_start",
    "actual_end",
    "ts",
    "event_date",
    "predicted_at",
}
NUMERIC_COLUMNS = {
    "quantity",
    "terrain_slope_deg",
    "haul_distance_m",
    "predicted_p10_min",
    "predicted_p50_min",
    "predicted_p90_min",
    "actual_duration_min",
    "temp_c",
    "rain_mm",
    "wind_kmh",
    "visibility_m",
    "dust_index",
    "skill_score",
    "experience_years",
    "total_engine_hours",
    "service_interval_hours",
    "engine_hours_at_event",
    "fatigue_score",
    "ear_avg",
    "perclos_60s",
}


def frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    """Rows -> DataFrame with the given columns, UTC timestamps and float numerics."""
    df = pd.DataFrame(rows, columns=columns)
    for c in columns:
        if c in TS_COLUMNS:
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
        elif c in NUMERIC_COLUMNS:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def iso(ts: datetime | pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.isoformat().replace("+00:00", "Z")


class Repo(Protocol):
    # lookups
    def machine(self, machine_id: str) -> dict[str, Any] | None: ...
    def operator(self, operator_id: str) -> dict[str, Any] | None: ...
    def shift(self, shift_id: str) -> dict[str, Any] | None: ...
    def latest_health(self, machine_id: str) -> dict[str, Any] | None: ...
    def latest_fatigue(
        self, operator_id: str, until: datetime | None = None, since: datetime | None = None
    ) -> dict[str, Any] | None: ...

    # writes
    def insert_alert(self, row: dict[str, Any]) -> int: ...
    def open_vision_alerts(self) -> list[dict[str, Any]]: ...
    def update_alert(self, alert_id: int, fields: dict[str, Any]) -> None: ...
    def insert_safety_event(self, row: dict[str, Any]) -> int: ...
    def insert_fatigue(self, row: dict[str, Any]) -> int: ...
    def update_task(self, task_id: str, fields: dict[str, Any]) -> None: ...
    def insert_maintenance_prediction(self, row: dict[str, Any]) -> int: ...
    def upsert_fleet_metrics(self, rows: list[dict[str, Any]]) -> int: ...

    # frames
    def tasks(
        self, task_ids: Iterable[str] | None = None, shift_ids: Iterable[str] | None = None
    ) -> pd.DataFrame: ...
    def task_history(
        self, operator_ids: Iterable[str], start: datetime, end: datetime
    ) -> pd.DataFrame: ...
    def weather(self, site_ids: Iterable[str], start: datetime, end: datetime) -> pd.DataFrame: ...
    def operators(self, operator_ids: Iterable[str]) -> pd.DataFrame: ...
    def machines(self, machine_ids: Iterable[str] | None = None) -> pd.DataFrame: ...
    def shifts(
        self,
        machine_ids: Iterable[str] | None = None,
        operator_ids: Iterable[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> pd.DataFrame: ...
    def fatigue_log(
        self, operator_ids: Iterable[str], start: datetime, end: datetime
    ) -> pd.DataFrame: ...
    def maintenance_log(self, machine_ids: Iterable[str] | None = None) -> pd.DataFrame: ...
    def safety_events(self, start: datetime, end: datetime) -> pd.DataFrame: ...
    def latest_shift_date(self) -> date | None: ...


class SupabaseRepo:
    def __init__(self, client: Any) -> None:
        self.sb = client

    # -- helpers -------------------------------------------------------------------------------
    def _all(self, build: Callable[[], Any]) -> list[dict[str, Any]]:
        """Every row of a query, one PostgREST page at a time. `build` returns a fresh ordered
        query (a query builder can't be reused after execute)."""
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            data = build().range(start, start + PAGE - 1).execute().data or []
            out.extend(data)
            if len(data) < PAGE:
                return out
            start += PAGE

    def _one(self, table: str, columns: str, key: str, value: str) -> dict[str, Any] | None:
        data = self.sb.table(table).select(columns).eq(key, value).limit(1).execute().data
        return data[0] if data else None

    # -- lookups -------------------------------------------------------------------------------
    def machine(self, machine_id: str) -> dict[str, Any] | None:
        return self._one("machines", ",".join(MACHINE_COLUMNS), "machine_id", machine_id)

    def operator(self, operator_id: str) -> dict[str, Any] | None:
        return self._one("operators", "operator_id,site_id", "operator_id", operator_id)

    def shift(self, shift_id: str) -> dict[str, Any] | None:
        return self._one("shifts", ",".join(SHIFT_COLUMNS), "shift_id", shift_id)

    def latest_health(self, machine_id: str) -> dict[str, Any] | None:
        return self._one("v_machine_health_latest", "*", "machine_id", machine_id)

    def latest_fatigue(
        self, operator_id: str, until: datetime | None = None, since: datetime | None = None
    ) -> dict[str, Any] | None:
        q = self.sb.table("fatigue_log").select(",".join(FATIGUE_COLUMNS))
        q = q.eq("operator_id", operator_id)
        if until is not None:
            q = q.lte("ts", iso(until))
        if since is not None:
            q = q.gte("ts", iso(since))
        data = q.order("ts", desc=True).limit(1).execute().data
        return data[0] if data else None

    # -- writes --------------------------------------------------------------------------------
    def insert_alert(self, row: dict[str, Any]) -> int:
        return int(self.sb.table("alerts").insert(row).execute().data[0]["id"])

    def update_alert(self, alert_id: int, fields: dict[str, Any]) -> None:
        self.sb.table("alerts").update(fields).eq("id", alert_id).execute()

    def open_vision_alerts(self) -> list[dict[str, Any]]:
        return list(
            self.sb.table("alerts")
            .select("id,ts,machine_id,alert_code,severity,title,recommended_action,evidence")
            .eq("source", "vision")
            .is_("resolved_at", "null")
            .order("id")
            .limit(200)
            .execute()
            .data
            or []
        )

    def insert_safety_event(self, row: dict[str, Any]) -> int:
        return int(self.sb.table("safety_events").insert(row).execute().data[0]["id"])

    def insert_fatigue(self, row: dict[str, Any]) -> int:
        return int(self.sb.table("fatigue_log").insert(row).execute().data[0]["id"])

    def update_task(self, task_id: str, fields: dict[str, Any]) -> None:
        self.sb.table("tasks").update(fields).eq("task_id", task_id).execute()

    def insert_maintenance_prediction(self, row: dict[str, Any]) -> int:
        return int(self.sb.table("maintenance_predictions").insert(row).execute().data[0]["id"])

    def upsert_fleet_metrics(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        written = 0
        for i in range(0, len(rows), 500):
            part = rows[i : i + 500]
            self.sb.table("fleet_metrics_weekly").upsert(
                part, on_conflict="entity_type,entity_id,week_start"
            ).execute()
            written += len(part)
        return written

    # -- frames --------------------------------------------------------------------------------
    def tasks(
        self, task_ids: Iterable[str] | None = None, shift_ids: Iterable[str] | None = None
    ) -> pd.DataFrame:
        def build() -> Any:
            q = self.sb.table("tasks").select(",".join(TASK_COLUMNS))
            if task_ids is not None:
                q = q.in_("task_id", list(task_ids))
            if shift_ids is not None:
                q = q.in_("shift_id", list(shift_ids))
            return q.order("task_id")

        return frame(self._all(build), TASK_COLUMNS)

    def task_history(
        self, operator_ids: Iterable[str], start: datetime, end: datetime
    ) -> pd.DataFrame:
        ops = list(operator_ids)
        return frame(
            self._all(
                lambda: self.sb.table("tasks")
                .select(",".join(TASK_COLUMNS))
                .in_("operator_id", ops)
                .eq("status", "completed")
                .gte("actual_end", iso(start))
                .lt("actual_end", iso(end))
                .order("task_id")
            ),
            TASK_COLUMNS,
        )

    def weather(self, site_ids: Iterable[str], start: datetime, end: datetime) -> pd.DataFrame:
        sites = list(site_ids)
        return frame(
            self._all(
                lambda: self.sb.table("weather")
                .select(",".join(WEATHER_COLUMNS))
                .in_("site_id", sites)
                .gte("ts", iso(start))
                .lte("ts", iso(end))
                .order("ts")
            ),
            WEATHER_COLUMNS,
        )

    def operators(self, operator_ids: Iterable[str]) -> pd.DataFrame:
        ops = list(operator_ids)
        data = (
            self.sb.table("operators")
            .select(",".join(OPERATOR_COLUMNS))
            .in_("operator_id", ops)
            .execute()
            .data
        )
        return frame(data or [], OPERATOR_COLUMNS)

    def machines(self, machine_ids: Iterable[str] | None = None) -> pd.DataFrame:
        q = self.sb.table("machines").select(",".join(MACHINE_COLUMNS))
        if machine_ids is not None:
            q = q.in_("machine_id", list(machine_ids))
        return frame(q.order("machine_id").execute().data or [], MACHINE_COLUMNS)

    def shifts(
        self,
        machine_ids: Iterable[str] | None = None,
        operator_ids: Iterable[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> pd.DataFrame:
        mids = list(machine_ids) if machine_ids is not None else None
        ops = list(operator_ids) if operator_ids is not None else None

        def build() -> Any:
            q = self.sb.table("shifts").select(",".join(SHIFT_COLUMNS))
            if mids is not None and ops is not None:
                q = q.or_(f"machine_id.in.({','.join(mids)}),operator_id.in.({','.join(ops)})")
            elif mids is not None:
                q = q.in_("machine_id", mids)
            elif ops is not None:
                q = q.in_("operator_id", ops)
            if date_from is not None:
                q = q.gte("shift_date", date_from.isoformat())
            if date_to is not None:
                q = q.lte("shift_date", date_to.isoformat())
            return q.order("shift_id")

        return frame(self._all(build), SHIFT_COLUMNS)

    def fatigue_log(
        self, operator_ids: Iterable[str], start: datetime, end: datetime
    ) -> pd.DataFrame:
        ops = list(operator_ids)
        return frame(
            self._all(
                lambda: self.sb.table("fatigue_log")
                .select(",".join(FATIGUE_COLUMNS))
                .in_("operator_id", ops)
                .gte("ts", iso(start))
                .lt("ts", iso(end))
                .order("id")
            ),
            FATIGUE_COLUMNS,
        )

    def maintenance_log(self, machine_ids: Iterable[str] | None = None) -> pd.DataFrame:
        mids = list(machine_ids) if machine_ids is not None else None

        def build() -> Any:
            q = self.sb.table("maintenance_log").select(",".join(MAINTENANCE_LOG_COLUMNS))
            if mids is not None:
                q = q.in_("machine_id", mids)
            return q.order("id")

        return frame(self._all(build), MAINTENANCE_LOG_COLUMNS)

    def safety_events(self, start: datetime, end: datetime) -> pd.DataFrame:
        return frame(
            self._all(
                lambda: self.sb.table("safety_events")
                .select(",".join(SAFETY_COLUMNS))
                .gte("ts", iso(start))
                .lt("ts", iso(end))
                .order("id")
            ),
            SAFETY_COLUMNS,
        )

    def latest_shift_date(self) -> date | None:
        data = (
            self.sb.table("shifts")
            .select("shift_date")
            .order("shift_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return date.fromisoformat(data[0]["shift_date"]) if data else None


@lru_cache
def get_repo() -> Repo:
    from app.db import get_supabase

    return SupabaseRepo(get_supabase())

"""Telemetry sources for the replay engine (sync; the engine calls them in the threadpool).

`SupabaseSource` pages through the `telemetry` table (the loaded 14 days, 2026-08-16 → 08-29).
`ParquetSource` reads `data/output/telemetry.parquet` from the generator: the fallback when
Supabase is unreachable or has no rows for the requested window.

Columns are selected explicitly: `anomaly_label` / `anomaly_type` (ground truth) are never read.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

log = logging.getLogger(__name__)

TELEMETRY_COLUMNS = [
    "ts",
    "machine_id",
    "operator_id",
    "shift_id",
    "engine_rpm",
    "engine_load_pct",
    "coolant_temp_c",
    "engine_oil_temp_c",
    "oil_pressure_kpa",
    "hydraulic_pressure_bar",
    "hydraulic_oil_temp_c",
    "fuel_rate_lph",
    "fuel_level_pct",
    "battery_voltage",
    "vibration_rms_g",
    "ground_speed_kmh",
    "pitch_deg",
    "roll_deg",
    "gps_lat",
    "gps_lon",
    "seatbelt_fastened",
    "is_idle",
    "fault_code",
]
TEXT_COLUMNS = {"machine_id", "operator_id", "shift_id", "fault_code"}
BOOL_COLUMNS = {"seatbelt_fastened", "is_idle"}
PAGE = 1000  # PostgREST default max rows per request


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """One telemetry row with a tz-aware UTC `ts`, floats, bools and None for missing."""
    row: dict[str, Any] = {}
    for c in TELEMETRY_COLUMNS:
        v = raw.get(c)
        if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
            row[c] = None
        elif c == "ts":
            t = pd.Timestamp(v)
            row[c] = (
                t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            ).to_pydatetime()
        elif c in TEXT_COLUMNS:
            row[c] = str(v)
        elif c in BOOL_COLUMNS:
            row[c] = bool(v)
        else:
            row[c] = float(v)
    row["_period_s"] = 60.0
    return row


class TelemetrySource(Protocol):
    name: str

    def fetch(
        self, machine_id: str, after: datetime, until: datetime | None = None, limit: int = PAGE
    ) -> list[dict[str, Any]]:
        """Rows with after < ts (<= until), ascending, at most `limit`."""
        ...


def _iso(ts: datetime) -> str:
    return ts.isoformat()


class SupabaseSource:
    name = "supabase"

    def __init__(self, client: Any) -> None:
        self.sb = client

    def fetch(
        self, machine_id: str, after: datetime, until: datetime | None = None, limit: int = PAGE
    ) -> list[dict[str, Any]]:
        q = (
            self.sb.table("telemetry")
            .select(",".join(TELEMETRY_COLUMNS))
            .eq("machine_id", machine_id)
            .gt("ts", _iso(after))
        )
        if until is not None:
            q = q.lte("ts", _iso(until))
        data = q.order("ts").limit(limit).execute().data or []
        return [normalize(r) for r in data]


class ParquetSource:
    name = "parquet"

    def __init__(self, path: str | Path, machine_ids: list[str]) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Telemetry parquet not found: {self.path}")
        df = pd.read_parquet(
            self.path, columns=TELEMETRY_COLUMNS, filters=[("machine_id", "in", list(machine_ids))]
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        self.frames = {
            m: g.sort_values("ts", kind="stable").reset_index(drop=True)
            for m, g in df.groupby("machine_id", sort=False)
        }

    def fetch(
        self, machine_id: str, after: datetime, until: datetime | None = None, limit: int = PAGE
    ) -> list[dict[str, Any]]:
        g = self.frames.get(machine_id)
        if g is None:
            return []
        i = int(g["ts"].searchsorted(pd.Timestamp(after), side="right"))
        part = g.iloc[i : i + limit]
        if until is not None:
            part = part[part["ts"] <= pd.Timestamp(until)]
        return [normalize(r) for r in part.to_dict("records")]


def load_machines(client: Any | None, machine_ids: list[str]) -> dict[str, dict[str, Any]]:
    """machine_id -> {site_id, machine_type}: Supabase `machines`, else the generator's CSV."""
    if client is not None:
        try:
            data = (
                client.table("machines")
                .select("machine_id,site_id,machine_type")
                .in_("machine_id", machine_ids)
                .execute()
                .data
            )
            if data:
                return {r["machine_id"]: r for r in data}
        except Exception as e:  # noqa: BLE001 - fall through to the CSV
            log.warning("machines from Supabase failed: %s", e)
    from app.core.config import get_settings

    csv = Path(get_settings().telemetry_parquet).with_name("machines.csv")
    if csv.exists():
        df = pd.read_csv(csv, usecols=["machine_id", "site_id", "machine_type"])
        return {r["machine_id"]: r for r in df.to_dict("records") if r["machine_id"] in machine_ids}
    return {}


def load_geofences(client: Any | None, site_ids: list[str]) -> list[dict[str, Any]]:
    """Active speed_limited geofences of the sites (empty without Supabase)."""
    if client is None or not site_ids:
        return []
    try:
        return (
            client.table("geofences")
            .select("id,site_id,name,zone_type,polygon,speed_limit_kmh")
            .in_("site_id", site_ids)
            .eq("active", True)
            .eq("zone_type", "speed_limited")
            .execute()
            .data
            or []
        )
    except Exception as e:  # noqa: BLE001
        log.warning("geofences from Supabase failed: %s", e)
        return []


def choose_source(
    client: Any | None, parquet_path: str, machine_ids: list[str], start: datetime
) -> TelemetrySource:
    """Supabase if it has rows for any machine in [start - 1 h, start + 1 day], else parquet."""
    if client is not None:
        src = SupabaseSource(client)
        try:
            for m in machine_ids:
                if src.fetch(m, start - timedelta(hours=1), start + timedelta(days=1), limit=1):
                    return src
            log.warning("Supabase telemetry has no rows near %s; using parquet", start)
        except Exception as e:  # noqa: BLE001
            log.warning("Supabase telemetry unreachable (%s); using parquet", e)
    return ParquetSource(parquet_path, machine_ids)

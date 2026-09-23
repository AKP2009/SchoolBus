"""Bulk telemetry history for the maintenance scoring job and fleet clustering.

Predictive maintenance needs weeks of history per machine (72 engine-hour windows and a
baseline 72-336 engine hours back), more than the 14 days loaded into Supabase. So the
generator's `data/output/telemetry.parquet` (all 90 days) is read first when it exists; without
it, Supabase `telemetry` is paged through (whatever it holds). Columns are selected
explicitly: `anomaly_label` / `anomaly_type` are never read.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.replay.source import PAGE, TELEMETRY_COLUMNS, SupabaseSource, normalize

log = logging.getLogger(__name__)


class TelemetryHistory:
    def __init__(self, parquet_path: str | Path | None, client: Any | None) -> None:
        self.path = Path(parquet_path) if parquet_path else None
        self.client = client
        self._frames: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    @property
    def source(self) -> str:
        return "parquet" if self.path is not None and self.path.exists() else "supabase"

    def _parquet_machine(self, machine_id: str) -> pd.DataFrame:
        with self._lock:
            df = self._frames.get(machine_id)
            if df is None:
                assert self.path is not None
                df = pd.read_parquet(
                    self.path, columns=TELEMETRY_COLUMNS, filters=[("machine_id", "==", machine_id)]
                )
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df = df.sort_values("ts", kind="stable").reset_index(drop=True)
                self._frames[machine_id] = df
            return df

    def _supabase_machine(self, machine_id: str, start: datetime, end: datetime) -> pd.DataFrame:
        src = SupabaseSource(self.client)
        rows: list[dict[str, Any]] = []
        after = start - pd.Timedelta(microseconds=1)
        while True:
            page = src.fetch(machine_id, after, end, limit=PAGE)
            rows.extend(page)
            if len(page) < PAGE:
                break
            after = page[-1]["ts"]
        df = pd.DataFrame(rows, columns=[*TELEMETRY_COLUMNS, "_period_s"])
        df = df.drop(columns="_period_s")
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df[df.ts < end]

    def frame(self, machine_ids: list[str], start: datetime, end: datetime) -> pd.DataFrame:
        """Telemetry rows of the machines with start <= ts < end, sorted by (machine_id, ts)."""
        parts = []
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        for mid in machine_ids:
            if self.source == "parquet":
                df = self._parquet_machine(mid)
                i, j = df.ts.searchsorted(lo, side="left"), df.ts.searchsorted(hi, side="left")
                parts.append(df.iloc[i:j])
            elif self.client is not None:
                parts.append(self._supabase_machine(mid, start, end))
        if not parts:
            return pd.DataFrame(columns=TELEMETRY_COLUMNS)
        return pd.concat(parts, ignore_index=True)


def rows_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Replay rows (dicts from `source.normalize`) -> a telemetry frame."""
    df = pd.DataFrame([normalize(r) for r in rows], columns=[*TELEMETRY_COLUMNS, "_period_s"])
    df = df.drop(columns="_period_s")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df

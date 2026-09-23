"""Load the last N days of synthetic data from data/output/ into Supabase.

    python data/generator/load_to_supabase.py --days 14 --reset
    python data/generator/load_to_supabase.py --estimate-only      # size check, loads nothing

Connects with DATABASE_URL from data/.env: the session pooler on port 5432 (see
docs/supabase.md §3). COPY and one long transaction need a session connection, so the
transaction pooler on 6543 is refused.

Window: the last N shift dates. Tables are loaded in the FK order of docs/supabase.md §9, in a
single transaction, so a failed run leaves the database unchanged.
- Master data (sites, operators, machines, training_modules) is loaded complete and upserted
  on its primary key, so rows the app created that reference it (profiles, alerts) survive.
- shifts is loaded complete (small) so every reference resolves; maintenance_log and
  training_records are loaded complete because they are history that predates the window.
- tasks, telemetry and fatigue_log are filtered by shift; weather, safety_events and
  incidents by timestamp.
Nothing is read from data/output/truth/: ground truth stays out of the database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import psycopg
from dotenv import load_dotenv
from psycopg import sql

from common import REPO_ROOT, ist_to_utc

OUTPUT_DIR = REPO_ROOT / "data" / "output"
ENV_FILE = REPO_ROOT / "data" / ".env"
# Scenario simulator content (12 scenarios), written as TM-SIM-01.scenario on every load.
SCENARIOS_FILE = REPO_ROOT / "backend" / "kb" / "scenarios.json"

# FK order, as in docs/supabase.md §9 and generate.py.
TABLE_ORDER = [
    "sites",
    "operators",
    "machines",
    "shifts",
    "weather",
    "tasks",
    "maintenance_log",
    "telemetry",
    "safety_events",
    "fatigue_log",
    "incidents",
    "training_modules",
    "training_records",
]
# Upserted on their primary key, never truncated.
MASTER_KEYS = {
    "sites": "site_id",
    "operators": "operator_id",
    "machines": "machine_id",
    "training_modules": "module_id",
}
# Truncated by --reset, in reverse FK order.
FACT_TABLES = [t for t in TABLE_ORDER if t not in MASTER_KEYS]
COPY_TABLES = {"telemetry", "fatigue_log"}
# Event tables loaded with explicit ids from the generator.
IDENTITY_TABLES = {
    "weather",
    "maintenance_log",
    "telemetry",
    "safety_events",
    "fatigue_log",
    "incidents",
    "training_records",
}

BATCH_ROWS = 1000
COPY_CHUNK_ROWS = 20_000
WARN_MB = 400  # free tier is 500 MB

ALL_TYPES = "{excavator,wheel_loader,dozer,articulated_truck}"
LANGS = "{en,hi,ta}"

# The generator ships 10 modules; these two close the gaps in the recommender triggers of
# docs/models.md §10: time_ratio for the non-excavator task types, and the
# 'needs safety coaching' cluster.
EXTRA_MODULES: list[dict[str, Any]] = [
    {
        "module_id": "TM-CYC-01",
        "title": "Loading, hauling and grading cycles",
        "topic": "technique",
        "format": "video",
        "duration_min": "14",
        "difficulty": "2",
        "content_path": "TM-CYC-01/content.mp4",
        "target_metric": "time_ratio",
        "machine_types": "{wheel_loader,dozer,articulated_truck}",
        "languages": LANGS,
        "scenario": None,
    },
    {
        "module_id": "TM-SIM-01",
        "title": "Safety scenario simulator pack",
        "topic": "safety",
        "format": "scenario",
        "duration_min": "20",
        "difficulty": "2",
        "content_path": "TM-SIM-01/content.json",
        "target_metric": "cluster_label",
        "machine_types": ALL_TYPES,
        "languages": LANGS,
        "scenario": None,  # filled from SCENARIOS_FILE in build_tables
    },
]
# Every §10 trigger must map to at least one module's target_metric.
REQUIRED_TRIGGERS = {
    "idle_pct",
    "harsh_maneuver",
    "time_ratio",
    "proximity_breach",
    "seatbelt_unfastened",
    "tip_risk",
    "cluster_label",
}


class LoadError(Exception):
    """A problem the user must fix; printed without a traceback."""


def read_scenarios() -> str:
    """backend/kb/scenarios.json as the jsonb text for TM-SIM-01.scenario."""
    try:
        pack = json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LoadError(f"Cannot read {SCENARIOS_FILE}: {e}") from e
    steps = pack.get("steps") if isinstance(pack, dict) else None
    if not steps or not all(0 <= s.get("answer", -1) < len(s.get("choices", [])) for s in steps):
        raise LoadError(f"{SCENARIOS_FILE}: needs steps with choices and a valid answer index")
    return json.dumps(pack, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Reading and windowing
# ---------------------------------------------------------------------------
def read_csv(name: str) -> pd.DataFrame:
    # Keep every value as the text the generator wrote; Postgres parses it into the column type.
    return pd.read_csv(OUTPUT_DIR / f"{name}.csv", dtype=str, keep_default_na=False, na_values=[""])


def read_telemetry(shift_ids: list[str]) -> pd.DataFrame:
    path = OUTPUT_DIR / "telemetry.parquet"
    if not path.exists():
        raise LoadError(f"{path} not found: run generate.py without --sample first")
    return pd.read_parquet(path, filters=[("shift_id", "in", shift_ids)])


def build_tables(days: int) -> tuple[dict[str, pd.DataFrame], date]:
    """All tables for the last `days` shift dates, keyed by table name."""
    if not (OUTPUT_DIR / "shifts.csv").exists():
        raise LoadError(f"No data in {OUTPUT_DIR}: run generate.py first")

    t: dict[str, pd.DataFrame] = {name: read_csv(name) for name in MASTER_KEYS}
    extra = pd.DataFrame(EXTRA_MODULES)
    extra.loc[extra["module_id"] == "TM-SIM-01", "scenario"] = read_scenarios()
    t["training_modules"] = pd.concat(
        [t["training_modules"], extra], ignore_index=True
    ).drop_duplicates("module_id", keep="first")
    missing = REQUIRED_TRIGGERS - set(t["training_modules"]["target_metric"])
    if missing:
        raise LoadError(f"training_modules has no module for trigger(s): {sorted(missing)}")

    shifts = read_csv("shifts")
    last_day = date.fromisoformat(shifts["shift_date"].max())
    first_day = last_day - timedelta(days=days - 1)
    cutoff = ist_to_utc(first_day, "00:00")  # window starts at local midnight
    in_window = shifts["shift_date"] >= first_day.isoformat()
    window_shifts = shifts.loc[in_window, "shift_id"].tolist()

    def since(df: pd.DataFrame, col: str) -> pd.DataFrame:
        return df[pd.to_datetime(df[col], utc=True) >= cutoff]

    t["shifts"] = shifts
    t["weather"] = since(read_csv("weather"), "ts")
    tasks = read_csv("tasks")
    t["tasks"] = tasks[tasks["shift_id"].isin(window_shifts)]
    t["maintenance_log"] = read_csv("maintenance_log")
    t["telemetry"] = read_telemetry(window_shifts)
    t["safety_events"] = since(read_csv("safety_events"), "ts")
    fatigue = read_csv("fatigue_log")
    t["fatigue_log"] = fatigue[fatigue["shift_id"].isin(window_shifts)]
    incidents = since(read_csv("incidents"), "ts").copy()
    # An incident may point at a safety event from before the window.
    loaded_events = set(t["safety_events"]["id"])
    orphan = incidents["linked_event_id"].notna() & ~incidents["linked_event_id"].isin(
        loaded_events
    )
    incidents.loc[orphan, "linked_event_id"] = None
    t["incidents"] = incidents
    t["training_records"] = read_csv("training_records")
    return {name: t[name] for name in TABLE_ORDER}, first_day


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def connect() -> psycopg.Connection:
    load_dotenv(ENV_FILE)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise LoadError(f"DATABASE_URL is not set: copy data/.env.example to {ENV_FILE}")
    parsed = urlparse(url)
    if parsed.port == 6543:
        raise LoadError(
            "DATABASE_URL uses the transaction pooler (port 6543). COPY and the long load "
            "transaction need the session pooler: use port 5432 (docs/supabase.md §3)."
        )
    try:
        return psycopg.connect(url, connect_timeout=15)
    except psycopg.OperationalError as e:
        hint = ""
        if parsed.hostname and parsed.hostname.startswith("db."):
            hint = "\nThe direct db.<ref> host is IPv6-only; use the session pooler URL."
        raise LoadError(f"Cannot connect to the database: {e}{hint}") from e


def check_migration(conn: psycopg.Connection) -> None:
    missing = [
        name
        for name in TABLE_ORDER
        if conn.execute("select to_regclass(%s)", [f"public.{name}"]).fetchone()[0] is None
    ]
    if missing:
        raise LoadError(
            f"Tables missing: {', '.join(missing)}.\n"
            "Migration 001_init.sql has not been applied: run `supabase db push` first."
        )


def row_counts(conn: psycopg.Connection, tables: Iterable[str]) -> dict[str, int]:
    return {
        name: conn.execute(
            sql.SQL("select count(*) from {}").format(sql.Identifier(name))
        ).fetchone()[0]
        for name in tables
    }


def to_rows(df: pd.DataFrame) -> list[tuple[Any, ...]]:
    return list(df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))


def to_copy_csv(df: pd.DataFrame) -> str:
    # Unquoted empty fields are NULL in COPY csv format.
    return df.to_csv(index=False, header=False, na_rep="")


# ---------------------------------------------------------------------------
# Size estimate
# ---------------------------------------------------------------------------
def estimate_mb(conn: psycopg.Connection, tables: dict[str, pd.DataFrame]) -> float:
    """Rough on-disk size of the load: heap tuples plus one btree entry per index per row."""
    total = 0.0
    for name, df in tables.items():
        if df.empty:
            continue
        sample = df.head(2000)
        csv_bytes = len(to_copy_csv(sample).encode()) / len(sample)
        n_indexes = conn.execute(
            "select count(*) from pg_indexes where schemaname = 'public' and tablename = %s",
            [name],
        ).fetchone()[0]
        per_row = 28 + 1.3 * csv_bytes + 36 * n_indexes  # tuple header + data + index entries
        total += len(df) * per_row * 1.15  # page fill factor and free space
    return total / 1024**2


def projected_mb(conn: psycopg.Connection, load_mb: float) -> tuple[float, float]:
    """Current database size, and its size after replacing the loaded tables."""
    db = conn.execute("select pg_database_size(current_database())").fetchone()[0] / 1024**2
    replaced = (
        sum(
            conn.execute("select pg_total_relation_size(%s)", [f"public.{name}"]).fetchone()[0]
            for name in TABLE_ORDER
        )
        / 1024**2
    )
    return db, db - replaced + load_mb


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def upsert(cur: psycopg.Cursor, name: str, df: pd.DataFrame) -> None:
    cols = list(df.columns)
    key = MASTER_KEYS[name]
    query = sql.SQL(
        "insert into {t} ({cols}) values ({vals}) on conflict ({key}) do update set {updates}"
    ).format(
        t=sql.Identifier(name),
        cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
        vals=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        key=sql.Identifier(key),
        updates=sql.SQL(", ").join(
            sql.SQL("{c} = excluded.{c}").format(c=sql.Identifier(c)) for c in cols if c != key
        ),
    )
    cur.executemany(query, to_rows(df))


def insert(cur: psycopg.Cursor, name: str, df: pd.DataFrame) -> None:
    cols = list(df.columns)
    overriding = sql.SQL("overriding system value") if name in IDENTITY_TABLES else sql.SQL("")
    query = sql.SQL("insert into {t} ({cols}) {overriding} values ({vals})").format(
        t=sql.Identifier(name),
        cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
        overriding=overriding,
        vals=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    rows = to_rows(df)
    for i in range(0, len(rows), BATCH_ROWS):
        cur.executemany(query, rows[i : i + BATCH_ROWS])


def copy(cur: psycopg.Cursor, name: str, df: pd.DataFrame) -> None:
    # COPY FROM writes identity values as given, like OVERRIDING SYSTEM VALUE.
    query = sql.SQL("copy {t} ({cols}) from stdin with (format csv)").format(
        t=sql.Identifier(name), cols=sql.SQL(", ").join(map(sql.Identifier, df.columns))
    )
    with cur.copy(query) as cp:
        for i in range(0, len(df), COPY_CHUNK_ROWS):
            cp.write(to_copy_csv(df.iloc[i : i + COPY_CHUNK_ROWS]))


def reset_sequences(cur: psycopg.Cursor) -> None:
    """Point every identity sequence past the max id, since event rows carry explicit ids."""
    cols = cur.execute(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'public' and is_identity = 'YES'"
    ).fetchall()
    for table, col in cols:
        cur.execute(
            sql.SQL(
                "select setval(pg_get_serial_sequence({qual}, {col}), "
                "coalesce((select max({c}) from {t}), 0) + 1, false)"
            ).format(
                qual=sql.Literal(f"public.{table}"),
                col=sql.Literal(col),
                c=sql.Identifier(col),
                t=sql.Identifier(table),
            )
        )


def load(conn: psycopg.Connection, tables: dict[str, pd.DataFrame], reset: bool) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set local statement_timeout = 0")
        if reset:
            cur.execute(
                sql.SQL("truncate {} restart identity").format(
                    sql.SQL(", ").join(map(sql.Identifier, reversed(FACT_TABLES)))
                )
            )
            print(f"  truncated {len(FACT_TABLES)} tables")
        else:
            filled = {n: c for n, c in row_counts(conn, FACT_TABLES).items() if c}
            if filled:
                raise LoadError(
                    f"Already loaded ({', '.join(filled)} not empty): rerun with --reset."
                )
        for name, df in tables.items():
            t0 = time.perf_counter()
            if name in MASTER_KEYS:
                upsert(cur, name, df)
            elif name in COPY_TABLES:
                copy(cur, name, df)
            else:
                insert(cur, name, df)
            print(f"  {name:<18} {len(df):>9,} rows  {time.perf_counter() - t0:6.1f} s")
        reset_sequences(cur)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", type=int, default=14, help="shift dates to load (default 14)")
    ap.add_argument("--reset", action="store_true", help="truncate loaded tables first")
    ap.add_argument("--estimate-only", action="store_true", help="print the size estimate and exit")
    ap.add_argument("--force", action="store_true", help=f"load even if above {WARN_MB} MB")
    args = ap.parse_args()
    if args.days < 1:
        ap.error("--days must be at least 1")

    t0 = time.perf_counter()
    try:
        tables, first_day = build_tables(args.days)
        print(
            f"Window: {args.days} days from {first_day} (IST), "
            f"read in {time.perf_counter() - t0:.1f} s"
        )
        with connect() as conn:
            check_migration(conn)
            load_mb = estimate_mb(conn, tables)
            db_mb, after_mb = projected_mb(conn, load_mb)
            print(
                f"Estimated load {load_mb:.0f} MB; database now {db_mb:.0f} MB, "
                f"about {after_mb:.0f} MB after the load"
            )
            if after_mb > WARN_MB:
                print(f"WARNING: above {WARN_MB} MB (free tier limit is 500 MB). Lower --days.")
                if not args.force and not args.estimate_only:
                    print("Not loading. Rerun with --force to load anyway.")
                    return 1
            if args.estimate_only:
                return 0

            print("Loading")
            load(conn, tables, args.reset)
            counts = row_counts(conn, TABLE_ORDER)
            size = conn.execute("select pg_database_size(current_database())").fetchone()[0]
    except LoadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print("Row counts in the database")
    for name, n in counts.items():
        print(f"  {name:<18} {n:>9,}")
    print(f"Database size {size / 1024**2:.0f} MB. Done in {time.perf_counter() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

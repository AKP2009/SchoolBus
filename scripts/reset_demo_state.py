"""Return the database to a clean demo starting state without reloading the 14 days.

    python scripts/reset_demo_state.py                  # dry run (default): what would change, per table
    python scripts/reset_demo_state.py --apply          # do it, in one transaction
    python scripts/reset_demo_state.py --apply --backend http://localhost:8000

Reads DATABASE_URL from data/.env (session pooler, 5432, as for the loader).

Removed, because testing and live runs create them and the loader never does:
- every row of alerts, machine_health_snapshots, maintenance_predictions, fleet_metrics_weekly,
  chat_messages, training_recommendations
- safety_events, fatigue_log and incidents rows added after the load
- shifts.handover_summary / handover_generated_at, and tasks.predicted_p10/p50/p90_min and
  prediction_factors (the generator leaves all of these empty, so any value was written by the backend)
Never touched: telemetry, tasks (apart from the prediction columns), shifts (apart from the handover
columns), operators, machines, training_modules, auth users and profiles.

How "added after the load" is found. Timestamps are not reliable: the backend writes vision and
replay events with the replay clock, so a live row can have an August `ts` like the loaded ones.
Ids are reliable: the loader keeps the generator's ids and moves each sequence past the max, so
anything inserted later has a higher id. The load is one transaction, so every loaded row carries
the same `xmin` (the loader's transaction id), and each REST insert has its own. The loader's
transaction is the one that wrote the untouched tasks rows (tasks.updated_at = the load time).
Per table, the loaded maximum is the highest id still carrying that xmin (for incidents, also
created_at = the load time, because a manager edit gives a row a new xmin). Rows above it go.

Before --apply, if the backend answers on /health, GET /replay/status must say no replay and no
scenario is running; if the backend is up but the status can't be read, the script refuses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psycopg
from dotenv import dotenv_values, load_dotenv
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ENV = REPO_ROOT / "data" / ".env"
BACKEND_ENV = REPO_ROOT / "backend" / ".env"
DEMO_MANAGER = ("priya@demo.site", "demo1234")  # backend/scripts/create_demo_users.py

# Only ever written by the backend or the app: emptied completely.
LIVE_TABLES = [
    "alerts",
    "machine_health_snapshots",
    "maintenance_predictions",
    "fleet_metrics_weekly",
    "chat_messages",
    "training_recommendations",
]
# Loaded by the loader and also appended to live: rows above the loaded maximum go.
MIXED_TABLES = ["incidents", "safety_events", "fatigue_log"]

TASK_PREDICTION_COLS = [
    "predicted_p10_min",
    "predicted_p50_min",
    "predicted_p90_min",
    "prediction_factors",
]
SHIFT_HANDOVER_COLS = ["handover_summary", "handover_generated_at"]

# Column shown as a breakdown in the dry run, per table.
BREAKDOWN = {
    "alerts": "alert_code",
    "machine_health_snapshots": "machine_id",
    "maintenance_predictions": "machine_id",
    "fleet_metrics_weekly": "entity_type",
    "chat_messages": "role",
    "training_recommendations": "module_id",
    "incidents": "incident_type",
    "safety_events": "event_type",
    "fatigue_log": "fatigue_level",
}


class ResetError(Exception):
    """A problem the user must fix; printed without a traceback."""


@dataclass
class Load:
    xid: str  # the loader transaction's xmin, as text
    ts: object  # the loader transaction's now()
    max_id: dict[str, int]  # per mixed table


@dataclass
class Step:
    """One statement of the reset. `where` selects the affected rows; the dry run counts them,
    --apply runs `action` over them and checks it hits the same number."""

    table: str
    label: str
    where: sql.Composable
    action: sql.Composable


# ---------------------------------------------------------------------------
# Finding the load
# ---------------------------------------------------------------------------
def connect() -> psycopg.Connection:
    load_dotenv(DATA_ENV)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ResetError(f"DATABASE_URL is not set in {DATA_ENV}")
    try:
        return psycopg.connect(url, connect_timeout=15)
    except psycopg.OperationalError as e:
        raise ResetError(f"Cannot connect to the database: {e}") from e


def find_load(conn: psycopg.Connection) -> Load:
    row = conn.execute(
        "select t.xmin::text, t.updated_at from tasks t "
        "where t.updated_at = (select min(updated_at) from tasks) "
        "group by 1, 2 order by count(*) desc limit 1"
    ).fetchone()
    if row is None:
        raise ResetError("tasks is empty: nothing is loaded, run load_to_supabase.py instead")
    xid, ts = row
    max_id: dict[str, int] = {}
    for table in MIXED_TABLES:
        loaded = sql.SQL("xmin::text = {x}").format(x=sql.Literal(xid))
        if table == "incidents":
            loaded = sql.SQL("({} or created_at = {})").format(loaded, sql.Literal(ts))
        n_rows, loaded_max = conn.execute(
            sql.SQL("select count(*), max(id) filter (where {}) from {}").format(
                loaded, sql.Identifier(table)
            )
        ).fetchone()
        if n_rows and loaded_max is None:
            raise ResetError(
                f"{table} has {n_rows} rows but none from the load (xmin {xid}); the database "
                "may have been restored or reloaded differently. Reload with load_to_supabase.py."
            )
        max_id[table] = loaded_max or 0
    return Load(xid=xid, ts=ts, max_id=max_id)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
def any_not_null(cols: list[str]) -> sql.Composable:
    return sql.SQL(" or ").join(sql.SQL("{} is not null").format(sql.Identifier(c)) for c in cols)


def set_null(table: str, cols: list[str], where: sql.Composable) -> sql.Composable:
    return sql.SQL("update {} set {} where {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.SQL("{} = null").format(sql.Identifier(c)) for c in cols),
        where,
    )


def delete(table: str, where: sql.Composable) -> sql.Composable:
    return sql.SQL("delete from {} where {}").format(sql.Identifier(table), where)


def plan(load: Load) -> list[Step]:
    new = {t: sql.SQL("id > {}").format(sql.Literal(load.max_id[t])) for t in MIXED_TABLES}
    kept = {t: sql.SQL("id <= {}").format(sql.Literal(load.max_id[t])) for t in MIXED_TABLES}
    steps: list[Step] = []

    # FK order: incidents -> safety_events -> alerts. Kept rows that the app linked to a live
    # event or alert are unlinked first, so the deletes can't fail.
    steps.append(
        Step(
            "incidents",
            "delete: added after the load",
            new["incidents"],
            delete("incidents", new["incidents"]),
        )
    )
    w = sql.SQL("{} and (linked_alert_id is not null or linked_event_id > {})").format(
        kept["incidents"], sql.Literal(load.max_id["safety_events"])
    )
    steps.append(
        Step(
            "incidents",
            "unlink loaded rows from live alerts/events",
            w,
            sql.SQL(
                "update incidents set linked_alert_id = null, linked_event_id = "
                "case when linked_event_id > {} then null else linked_event_id end "
                "where {}"
            ).format(sql.Literal(load.max_id["safety_events"]), w),
        )
    )
    steps.append(
        Step(
            "safety_events",
            "delete: added after the load",
            new["safety_events"],
            delete("safety_events", new["safety_events"]),
        )
    )
    w = sql.SQL("{} and alert_id is not null").format(kept["safety_events"])
    steps.append(
        Step(
            "safety_events",
            "unlink loaded rows from live alerts",
            w,
            set_null("safety_events", ["alert_id"], w),
        )
    )
    steps.append(
        Step(
            "fatigue_log",
            "delete: added after the load",
            new["fatigue_log"],
            delete("fatigue_log", new["fatigue_log"]),
        )
    )

    everything = sql.SQL("true")
    for table in LIVE_TABLES:
        steps.append(Step(table, "delete: all rows", everything, delete(table, everything)))

    w = any_not_null(TASK_PREDICTION_COLS)
    steps.append(
        Step(
            "tasks",
            "clear " + ", ".join(TASK_PREDICTION_COLS),
            w,
            set_null("tasks", TASK_PREDICTION_COLS, w),
        )
    )
    w = any_not_null(SHIFT_HANDOVER_COLS)
    steps.append(
        Step(
            "shifts",
            "clear " + ", ".join(SHIFT_HANDOVER_COLS),
            w,
            set_null("shifts", SHIFT_HANDOVER_COLS, w),
        )
    )
    return steps


def count(conn: psycopg.Connection, step: Step) -> int:
    q = sql.SQL("select count(*) from {} where {}").format(sql.Identifier(step.table), step.where)
    return conn.execute(q).fetchone()[0]


def describe(conn: psycopg.Connection, step: Step) -> str:
    """Id range and a breakdown of the affected rows, for the dry run."""
    t = sql.Identifier(step.table)
    if step.table in BREAKDOWN:
        lo, hi = conn.execute(
            sql.SQL("select min(id), max(id) from {} where {}").format(t, step.where)
        ).fetchone()
        col = sql.Identifier(BREAKDOWN[step.table])
        parts = conn.execute(
            sql.SQL(
                "select {c}::text, count(*) from {t} where {w} group by 1 order by 2 desc"
            ).format(c=col, t=t, w=step.where)
        ).fetchall()
        by = ", ".join(f"{k} {n}" for k, n in parts[:8]) + (" ..." if len(parts) > 8 else "")
        return f"ids {lo}-{hi}; {BREAKDOWN[step.table]}: {by}"
    key = {"tasks": "task_id", "shifts": "shift_id"}[step.table]
    ids = [
        r[0]
        for r in conn.execute(
            sql.SQL("select {k} from {t} where {w} order by 1").format(
                k=sql.Identifier(key), t=t, w=step.where
            )
        ).fetchall()
    ]
    return ", ".join(ids[:6]) + (f" ... (+{len(ids) - 6})" if len(ids) > 6 else "")


def reset_sequences(cur: psycopg.Cursor, tables: list[str]) -> None:
    """As the loader does: each identity sequence points just past the table's max id."""
    for table in tables:
        cur.execute(
            sql.SQL(
                "select setval(pg_get_serial_sequence({q}, 'id'), "
                "coalesce((select max(id) from {t}), 0) + 1, false)"
            ).format(q=sql.Literal(f"public.{table}"), t=sql.Identifier(table))
        )


# ---------------------------------------------------------------------------
# Replay check
# ---------------------------------------------------------------------------
def _get(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"{}")


def manager_token() -> str:
    env = dotenv_values(BACKEND_ENV)
    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise ResetError(f"SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing in {BACKEND_ENV}")
    body = json.dumps({"email": DEMO_MANAGER[0], "password": DEMO_MANAGER[1]}).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/auth/v1/token?grant_type=password",
        data=body,
        headers={"apikey": key, "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def replay_state(backend: str, token: str | None) -> str | None:
    """None if the backend is down or idle; otherwise why the reset must not run."""
    try:
        _get(f"{backend}/health")
    except (urllib.error.URLError, OSError):
        return None  # backend not running, so no replay either
    try:
        status = _get(
            f"{backend}/replay/status",
            {"authorization": f"Bearer {token or manager_token()}"},
        )
    except (urllib.error.URLError, OSError, KeyError, ValueError, ResetError) as e:
        return f"the backend is up at {backend} but /replay/status could not be read ({e})"
    if status.get("running"):
        return (
            f"a replay is running ({', '.join(status.get('machine_ids') or [])} at "
            f"{status.get('replay_ts')}); POST /replay/stop first"
        )
    if status.get("scenarios"):
        return f"a scenario is running ({status['scenarios']}); stop it first"
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", default=True, help="print what would change (default)"
    )
    mode.add_argument("--apply", action="store_true", help="make the changes, in one transaction")
    ap.add_argument("--backend", default="http://localhost:8000", help="backend base URL")
    ap.add_argument(
        "--token",
        default=os.environ.get("TOKEN"),
        help="bearer token for /replay/status (default $TOKEN, else a priya login)",
    )
    args = ap.parse_args()

    try:
        if args.apply:
            reason = replay_state(args.backend.rstrip("/"), args.token)
            if reason:
                raise ResetError(f"Not resetting: {reason}.")
        with connect() as conn:
            conn.read_only = not args.apply  # the dry run can't write even by mistake
            with conn.transaction():
                load = find_load(conn)
                steps = plan(load)
                print(
                    f"Load: transaction {load.xid} at {load.ts:%Y-%m-%d %H:%M:%S} UTC. "
                    "Loaded maximum id: " + ", ".join(f"{t} {n}" for t, n in load.max_id.items())
                )
                print("DRY RUN, nothing is changed" if not args.apply else "APPLYING")
                total = 0
                for step in steps:
                    n = count(conn, step)
                    total += n
                    line = f"  {step.table:<25} {n:>7,}  {step.label}"
                    if n and not args.apply:
                        line += f"\n  {'':<25} {'':>7}  {describe(conn, step)}"
                    print(line)
                    if args.apply and n:
                        done = conn.execute(step.action).rowcount
                        if done != n:
                            raise ResetError(
                                f"{step.table}: expected {n} rows, statement hit {done}; rolled back"
                            )
                if args.apply:
                    touched = sorted({s.table for s in steps} & set(LIVE_TABLES + MIXED_TABLES))
                    reset_sequences(conn.cursor(), touched)
                    print(f"  sequences reset: {', '.join(touched)}")
                print(f"{total:,} rows {'changed' if args.apply else 'would change'}.")
                if not args.apply:
                    print("Run with --apply to do it.")
    except ResetError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# Throwaway Postgres verification for supabase/migrations + seed.sql.
# Not part of the repo workflow: creates a scratch cluster in /tmp, applies the
# migrations with minimal stubs, loads the generated CSVs, cross-checks
# v_operator_efficiency against ml/inference/task_time.py, and stops the cluster.
set -euo pipefail

PGBIN=/opt/homebrew/opt/postgresql@15/bin
PORT=5544
DIR=$(mktemp -d /tmp/supa-verify.XXXXXX)
export PGHOST=127.0.0.1 PGPORT=$PORT PGUSER=postgres PGDATABASE=test
PSQL=("$PGBIN/psql" -v ON_ERROR_STOP=1 -X)

cleanup() { "$PGBIN/pg_ctl" -D "$DIR/data" stop -m fast >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== initdb + start throwaway cluster on :$PORT =="
"$PGBIN/initdb" -D "$DIR/data" --auth=trust -U postgres >/dev/null
"$PGBIN/pg_ctl" -D "$DIR/data" -l "$DIR/log" \
  -o "-p $PORT -k $DIR -c listen_addresses=127.0.0.1" start >/dev/null
"$PGBIN/createdb" test

psql() { "$PGBIN/psql" -v ON_ERROR_STOP=1 -X "$@"; }

echo "== Supabase stubs (auth schema, roles, realtime publication)"
psql -q <<'SQL'
create role anon nologin;
create role authenticated nologin;
create role service_role nologin;
create schema auth;
create table auth.users (id uuid primary key, raw_user_meta_data jsonb);
create function auth.uid() returns uuid language sql as $$ select null::uuid $$;
create publication supabase_realtime;
SQL

echo "== 001_init.sql (pgvector stubbed in a /tmp copy)"
sed -e 's/^create extension if not exists vector with schema extensions;/create schema if not exists extensions;/' \
    -e 's/extensions\.vector(384)/text/' \
    -e 's/^create index on document_chunks using hnsw.*/-- stubbed: hnsw index on pgvector requires the vector extension/' \
    -e 's/1 - (c\.embedding <=> query_embedding) as similarity/0.5::float as similarity/' \
    -e 's/where 1 - (c\.embedding <=> query_embedding) >= min_similarity/where true/' \
    -e 's/order by c\.embedding <=> query_embedding/order by c.id/' \
    supabase/migrations/001_init.sql > "$DIR/001_stub.sql"
psql -q -f "$DIR/001_stub.sql"
echo "001 applied"

echo "== 002_task_efficiency.sql"
psql -q -f supabase/migrations/002_task_efficiency.sql
echo "002 applied"

echo "== seed.sql (twice: idempotency)"
psql -q -f supabase/seed.sql
psql -q -f supabase/seed.sql
n_modules=$(psql -t -A -c "select count(*) from training_modules where module_id like 'TM-TECH-%'")
echo "seed applied; technique modules: $n_modules (expected 6)"
[ "$n_modules" = "6" ]

echo "== load generated CSVs"
psql -q -c "\copy sites (site_id, name, site_type, lat, lon, timezone) from stdin with (format csv, header true)" < data/output/sites.csv
psql -q -c "\copy operators (operator_id, site_id, full_name, experience_years, certification_level, languages, preferred_shift, skill_score, personality) from stdin with (format csv, header true)" < data/output/operators.csv
psql -q -c "\copy machines (machine_id, site_id, machine_type, model, serial_no, year, total_engine_hours, hours_since_service, service_interval_hours, status, health_score) from stdin with (format csv, header true)" < data/output/machines.csv
psql -q -c "\copy shifts (shift_id, site_id, operator_id, machine_id, shift_type, shift_date, start_time, end_time, fuel_start_pct, fuel_end_pct, handover_notes, issues_reported, handover_summary, handover_generated_at) from stdin with (format csv, header true)" < data/output/shifts.csv
psql -q -c "\copy weather (site_id, ts, temp_c, humidity_pct, rain_mm, wind_kmh, visibility_m, dust_index) from stdin with (format csv, header true)" < data/output/weather.csv
# tasks + the standard_min the inference module computes (stored like the backend would)
python - <<'PY'
import numpy as np
import pandas as pd
from ml.inference import task_time as tt

frames = {}
for n in ("tasks", "shifts", "operators", "machines", "weather", "machine_health_daily"):
    frames[n] = pd.read_csv(f"data/output/{n}.csv")
frames["weather"]["ts"] = pd.to_datetime(frames["weather"]["ts"], utc=True, format="ISO8601")
frames["shifts"]["start_time"] = pd.to_datetime(frames["shifts"]["start_time"], utc=True, format="ISO8601")
frames["tasks"]["scheduled_start"] = pd.to_datetime(frames["tasks"]["scheduled_start"], utc=True, format="ISO8601")
tt.reload_state("data/output", **frames)
state = tt._state()
std = tt._ensure_standard(state)
tasks = frames["tasks"].copy()
tasks["standard_min"] = std["standard_min"].to_numpy()
comp = tasks["status"] == "completed"
tasks["efficiency"] = np.where(
    comp, tasks["standard_min"] / tasks["actual_duration_min"], None
)
tasks.drop(columns=["day"], errors="ignore").to_csv("/tmp/tasks_db.csv", index=False)
print("tasks with standard_min written")
PY
psql -q -c "\copy tasks (task_id, site_id, shift_id, machine_id, operator_id, sequence_no, task_date, task_type, material_type, quantity, unit, terrain_slope_deg, haul_distance_m, priority, scheduled_start, predicted_p10_min, predicted_p50_min, predicted_p90_min, prediction_factors, actual_start, actual_end, actual_duration_min, status, delay_reason, standard_min, efficiency) from stdin with (format csv, header true)" < /tmp/tasks_db.csv
echo "tasks loaded"

echo "== view vs ml/inference/task_time.py"
psql -c "\copy (select * from v_operator_efficiency order by operator_id, task_type) to stdout with csv header" > /tmp/view_out.csv
python - <<'PY'
import pandas as pd
from ml.inference import task_time as tt

view = pd.read_csv("/tmp/view_out.csv")
f = tt._frames()
rows = []
for op in sorted(f["operators"]["operator_id"].unique()):
    for r in tt.efficiency_summary(op):
        rows.append(
            {"operator_id": op, "task_type": r.task_type,
             "avg_efficiency": None if r.avg_efficiency is None else round(r.avg_efficiency, 3),
             "prev_efficiency": None if r.prev_efficiency is None else round(r.prev_efficiency, 3),
             "n_avg": r.n_tasks,
             "n_prev": None if r.prev_efficiency is None and r.avg_efficiency is None else None,
             "trend": r.trend,
             "fleet_median": None if r.fleet_median is None else round(r.fleet_median, 3)}
        )
py = pd.DataFrame(rows).sort_values(["operator_id", "task_type"]).reset_index(drop=True)
v = view[["operator_id", "task_type", "avg_efficiency", "prev_efficiency", "n_avg", "trend", "fleet_median"]]
v = v.sort_values(["operator_id", "task_type"]).reset_index(drop=True)

assert len(v) == len(py), (len(v), len(py))
for col in ("avg_efficiency", "prev_efficiency", "fleet_median"):
    a = pd.to_numeric(v[col], errors="coerce")
    b = pd.to_numeric(py[col], errors="coerce")
    assert (a - b).abs().max() <= 0.0011, col  # both round to 3 dp; boundary cases differ by 1 ulp
assert (v["trend"].isna() == py["trend"].isna()).all(), "trend nulls"
both = v["trend"].notna()
assert (v.loc[v["trend"].notna(), "trend"].to_numpy() == py.loc[py["trend"].notna(), "trend"].astype(str).to_numpy()).all(), "trend values"
assert (v["n_avg"].to_numpy() == py["n_avg"].to_numpy()).all()
print(f"view matches python for {len(v)} (operator, task_type) rows")
print(v.head(5).to_string(index=False))
PY

echo "== cleanup"
rm -f /tmp/tasks_db.csv /tmp/view_out.csv
echo "OK: 001+002+seed apply cleanly on the throwaway cluster"

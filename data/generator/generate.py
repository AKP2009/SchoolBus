"""Synthetic data generator — master data, weather, shifts, maintenance, health, tasks.

Usage: python data/generator/generate.py --config data/generator/config.yaml
"""

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from gen import LOCAL_TZ, rng_for
from gen import io as gio
from gen.health import build_health_daily
from gen.machines import build_machines, finalize_machines
from gen.maintenance import (
    ALLOWED,
    COMPONENT_MIX,
    MIN_FAILURE_DAY,
    EngineClock,
    attach_drift_windows,
    build_maintenance_log,
    plan_failures,
)
from gen.operators import PERSONALITY_SHARES, build_operators
from gen.shifts import apply_downtime, build_candidate_shifts
from gen.sites import build_sites
from gen.tasks import DELAY_REASONS, TYPES_BY_MACHINE, build_tasks
from gen.weather import build_weather

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> dict[str, Any]:
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    cfg.setdefault("future_days", 0)
    return cfg


def generate(cfg: dict[str, Any]) -> dict[str, pd.DataFrame]:
    seed = int(cfg["seed"])
    sites = build_sites(cfg)
    machines = build_machines(cfg, rng_for(seed, "machines"))
    operators = build_operators(cfg, rng_for(seed, "operators"))
    weather = build_weather(cfg, sites, rng_for(seed, "weather"))

    candidates = build_candidate_shifts(cfg, operators, machines, rng_for(seed, "shifts"))
    failures = plan_failures(cfg, machines, candidates, rng_for(seed, "failures"))
    shifts = apply_downtime(candidates, failures)
    clock = EngineClock(machines, shifts)
    failures = attach_drift_windows(failures, clock, rng_for(seed, "drift"))
    maintenance, down_windows = build_maintenance_log(
        cfg, machines, shifts, failures, clock, rng_for(seed, "maintenance")
    )
    health = build_health_daily(cfg, machines, failures, clock, rng_for(seed, "health"))
    tasks, tasks_truth = build_tasks(
        cfg, machines, operators, shifts, weather, health, failures, rng_for(seed, "tasks")
    )

    first_future_day = pd.Timestamp(cfg["start_date"]) + pd.Timedelta(days=int(cfg["days"]))
    now_utc = (first_future_day + pd.Timedelta(hours=6)).tz_localize(LOCAL_TZ).tz_convert("UTC")
    machines = finalize_machines(machines, now_utc, clock, maintenance, down_windows, health)

    return {
        "sites": sites,
        "machines": machines,
        "operators": operators,
        "weather": weather,
        "shifts": shifts,
        "maintenance_log": maintenance,
        "machine_health_daily": health,
        "tasks": tasks,
        # internal, used by validation only
        "_failures": failures,
        "_down_windows": down_windows,
        "_tasks_truth": tasks_truth,
    }


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------
ID_PATTERNS = {
    "site_id": r"S\d+",
    "machine_id": r"M\d{2}",
    "operator_id": r"OP\d{2}",
    "shift_id": r"SH-\d{4}-\d{2}-\d{2}-M\d{2}-[DN]",
}


def _ids_ok(series: pd.Series, key: str) -> bool:
    return bool(series.dropna().astype(str).str.fullmatch(ID_PATTERNS[key]).all())


def validate(cfg: dict[str, Any], t: dict[str, pd.DataFrame]) -> None:
    sites, machines, operators = t["sites"], t["machines"], t["operators"]
    shifts, weather, mlog = t["shifts"], t["weather"], t["maintenance_log"]
    health, failures, windows = t["machine_health_daily"], t["_failures"], t["_down_windows"]
    site_ids, mach_ids, op_ids = (
        set(sites["site_id"]),
        set(machines["machine_id"]),
        set(operators["operator_id"]),
    )

    # no nulls in NOT NULL columns
    for table, cols in gio.NOT_NULL.items():
        df = gio.to_table(t[table], table)
        nulls = {c: int(df[c].isna().sum()) for c in cols if df[c].isna().any()}
        assert not nulls, f"{table}: nulls in NOT NULL columns {nulls}"

    # primary keys / unique
    for table, key in [
        ("sites", "site_id"),
        ("machines", "machine_id"),
        ("operators", "operator_id"),
        ("shifts", "shift_id"),
    ]:
        assert t[table][key].is_unique, f"{table}.{key} not unique"
    assert machines["serial_no"].is_unique, "serial_no not unique"
    assert not weather.duplicated(["site_id", "ts"]).any(), "weather (site_id, ts) not unique"

    # ID formats
    assert _ids_ok(sites["site_id"], "site_id")
    assert _ids_ok(machines["machine_id"], "machine_id")
    assert _ids_ok(operators["operator_id"], "operator_id")
    assert _ids_ok(shifts["shift_id"], "shift_id")
    expect = (
        "SH-"
        + pd.to_datetime(shifts["shift_date"]).dt.strftime("%Y-%m-%d")
        + "-"
        + shifts["machine_id"]
        + "-"
        + shifts["shift_type"].map({"day": "D", "night": "N"})
    )
    assert (expect == shifts["shift_id"]).all(), "shift_id does not match date/machine/type"

    # foreign keys
    assert set(operators["site_id"]) <= site_ids and set(machines["site_id"]) <= site_ids
    assert set(weather["site_id"]) <= site_ids and set(shifts["site_id"]) <= site_ids
    assert set(shifts["operator_id"]) <= op_ids and set(shifts["machine_id"]) <= mach_ids
    assert set(mlog["machine_id"]) <= mach_ids and set(health["machine_id"]) <= mach_ids
    op_site = operators.set_index("operator_id")["site_id"]
    m_site = machines.set_index("machine_id")["site_id"]
    m_type = machines.set_index("machine_id")["machine_type"]
    assert (shifts["operator_id"].map(op_site) == shifts["site_id"]).all(), "operator at wrong site"
    assert (shifts["machine_id"].map(m_site) == shifts["site_id"]).all(), "machine at wrong site"

    # master data
    n_sites = len(cfg["sites"])
    assert len(machines) == n_sites * sum(cfg["machines"].values())
    assert len(operators) == cfg["operators"]
    assert machines["total_engine_hours"].between(2000, 12000).all()
    assert machines["year"].between(2016, 2024).all()
    assert (machines["service_interval_hours"] == 500).all()
    assert machines["health_score"].between(0, 1).all()
    pers = operators["personality"].value_counts()
    assert set(pers.index) == set(PERSONALITY_SHARES), "missing personality"
    for p, share in PERSONALITY_SHARES.items():
        assert abs(pers[p] - share * len(operators)) <= 1, f"personality share off for {p}"
    assert operators["skill_score"].between(0.1, 0.98).all()

    # weather
    n_hours = (cfg["days"] + cfg["future_days"] + 1) * 24
    assert len(weather) == n_sites * n_hours
    assert weather["temp_c"].between(15, 45).all()

    # shifts
    night_share = (shifts["shift_type"] == "night").mean()
    assert 0.30 <= night_share <= 0.40, f"night share {night_share:.3f}"
    assert not shifts.duplicated(["operator_id", "shift_date"]).any(), "operator >1 shift per day"
    assert not shifts.duplicated(["machine_id", "start_time"]).any(), "machine double-booked"
    assert shifts["fuel_start_pct"].between(90, 100).all()
    local_start = shifts["start_time"].dt.tz_convert(LOCAL_TZ)
    assert (local_start.dt.date == shifts["shift_date"]).all(), "shift_date != local start date"
    n_weeks = (cfg["days"] + cfg["future_days"]) / 7
    per_week = shifts.groupby("operator_id").size() / n_weeks
    assert per_week.between(5.0, 6.2).all(), f"working days/week out of range:\n{per_week}"
    # no day shift straight after a night shift
    nights = set(
        zip(
            shifts.loc[shifts["shift_type"] == "night", "operator_id"],
            shifts.loc[shifts["shift_type"] == "night", "shift_date"],
        )
    )
    day_rows = shifts[shifts["shift_type"] == "day"]
    prev = zip(day_rows["operator_id"], day_rows["shift_date"] - pd.Timedelta(days=1))
    assert not any(k in nights for k in prev), "day shift right after a night shift"
    # no shift while a machine is down
    for w in windows.itertuples():
        s = shifts[shifts["machine_id"] == w.machine_id]
        assert not ((s["start_time"] < w.end) & (s["end_time"] > w.start)).any(), (
            f"shift during downtime {w}"
        )

    # maintenance
    fail = mlog[mlog["event_type"] == "failure"]
    rep = mlog[mlog["event_type"] == "repair"]
    assert len(fail) == cfg["failures_total"] == len(failures), f"failures {len(fail)}"
    assert len(rep) == len(fail)
    assert fail["downtime_hours"].between(4, 24).all()
    start_local = pd.Timestamp(cfg["start_date"]).tz_localize(LOCAL_TZ)
    fail_day = (fail["event_date"].dt.tz_convert(LOCAL_TZ) - start_local).dt.days
    assert (fail_day >= MIN_FAILURE_DAY).all() and (fail_day < cfg["days"]).all(), (
        "failure day out of range"
    )
    for f in failures.itertuples():
        r = rep[(rep["machine_id"] == f.machine_id) & (rep["event_date"] == f.repair_ts)]
        assert len(r) == 1 and r["component"].iloc[0] == f.component, f"no repair for {f}"
        assert 50 <= f.drift_window_hours <= 200
        assert f.component in ALLOWED[m_type[f.machine_id]], (
            f"{f.component} on {m_type[f.machine_id]}"
        )
    mix = failures["component"].value_counts()
    for c, share in COMPONENT_MIX.items():
        assert abs(mix.get(c, 0) - share * len(failures)) < 1, f"component mix off for {c}"
    svc = mlog[mlog["event_type"] == "scheduled_service"]
    for m, g in svc.groupby("machine_id"):
        gaps = g["engine_hours_at_event"].diff().dropna()
        assert gaps.between(500, 510).all(), f"service spacing off on {m}: {gaps.tolist()}"
    assert machines["hours_since_service"].between(0, 510).all()

    # tasks
    days = int(cfg["days"])
    tasks, truth = t["tasks"], t["_tasks_truth"]
    assert tasks["task_id"].is_unique, "task_id not unique"
    assert tasks["task_id"].str.fullmatch(r"T-SH-\d{4}-\d{2}-\d{2}-M\d{2}-[DN]-\d+").all()
    s_ix = shifts.set_index("shift_id")
    assert set(tasks["shift_id"]) <= set(s_ix.index), "task shift FK broken"
    for col in ("site_id", "machine_id", "operator_id"):
        assert (tasks[col].to_numpy() == tasks["shift_id"].map(s_ix[col]).to_numpy()).all(), col
    assert (
        tasks["task_date"].to_numpy() == tasks["shift_id"].map(s_ix["shift_date"]).to_numpy()
    ).all()
    mtype = tasks["machine_id"].map(m_type)
    assert all(r.task_type in TYPES_BY_MACHINE[mt] for r, mt in zip(tasks.itertuples(), mtype)), (
        "task type not allowed for machine type"
    )
    is_haul = tasks["task_type"] == "haul"
    assert (tasks.loc[is_haul, "unit"] == "tons").all() and (
        tasks.loc[~is_haul, "unit"] == "m3"
    ).all()
    assert tasks.loc[is_haul, "haul_distance_m"].between(300, 2500).all()
    assert tasks.loc[~is_haul, "haul_distance_m"].isna().all()
    assert tasks["terrain_slope_deg"].between(0, 15).all()
    assert tasks["priority"].between(1, 3).all() and (tasks["quantity"] > 0).all()
    for sid, g in tasks.groupby("shift_id"):
        g = g.sort_values("sequence_no")
        assert g["sequence_no"].tolist() == list(range(1, len(g) + 1)), f"sequence gap in {sid}"
        assert (
            g["task_id"].to_numpy() == ("T-" + sid + "-" + g["sequence_no"].astype(str)).to_numpy()
        ).all()
    day_idx = tasks["shift_id"].map(s_ix["day_idx"])
    hist, fut = day_idx < days, day_idx >= days
    assert (tasks.loc[hist, "status"] == "completed").all()
    assert (tasks.loc[fut, "status"] == "scheduled").all()
    assert tasks.loc[fut, "scheduled_start"].notna().all()
    assert (
        tasks.loc[hist, ["actual_start", "actual_end", "actual_duration_min"]].notna().all().all()
    )
    assert (
        tasks.loc[fut, ["actual_start", "actual_end", "actual_duration_min", "delay_reason"]]
        .isna()
        .all()
        .all()
    )
    assert (
        tasks.loc[hist, "actual_start"] >= tasks.loc[hist, "shift_id"].map(s_ix["start_time"])
    ).all()
    for f in failures.itertuples():
        assert not (
            (tasks["shift_id"] == f.shift_id) & (tasks["actual_start"] >= f.fail_ts)
        ).any(), f"task starts after failure in {f.shift_id}"
    span = tasks.loc[hist, "actual_end"] - tasks.loc[hist, "actual_start"]
    recon = pd.to_timedelta(tasks.loc[hist, "actual_duration_min"], unit="min")
    assert ((span - recon).abs() < pd.Timedelta(seconds=1)).all(), "actual_end != start + duration"
    delays = tasks.loc[hist, "delay_reason"]
    share = delays.notna().mean()
    assert 0.025 <= share <= 0.075, f"delay share {share:.3f}"
    assert set(delays.dropna()) <= set(DELAY_REASONS), "bad delay_reason"
    assert truth["task_id"].is_unique and set(truth["task_id"]) == set(tasks.loc[hist, "task_id"])

    # health
    n_days = cfg["days"] + cfg["future_days"]
    assert len(health) == len(machines) * n_days
    assert health["health_score"].between(0, 1).all()
    last = health.sort_values("date").groupby("machine_id")["health_score"].last()
    assert (machines.set_index("machine_id")["health_score"] == last).all()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)
    out_dir = Path(cfg["output_dir"])
    out_dir = out_dir if out_dir.is_absolute() else REPO_ROOT / out_dir

    tables = generate(cfg)
    validate(cfg, tables)
    for name, df in tables.items():
        if name.startswith("_"):
            continue
        gio.write_csv(df, name, out_dir)
    tables["_tasks_truth"].to_csv(out_dir / "tasks_truth.csv", index=False)

    print(f"wrote {out_dir}")
    for name, df in tables.items():
        if not name.startswith("_"):
            print(f"  {name:22s} {len(df):>7,d} rows")
    print(f"  {'tasks_truth':22s} {len(tables['_tasks_truth']):>7,d} rows  (validation only)")
    s = tables["shifts"]
    f = tables["_failures"]
    print(f"  night share            {(s['shift_type'] == 'night').mean():.1%}")
    print(f"  failures               {len(f)}  {f['component'].value_counts().to_dict()}")
    tasks = tables["tasks"]
    hist = tasks[tasks["status"] == "completed"]
    d = hist["actual_duration_min"]
    print(
        f"  duration min           P10 {d.quantile(0.1):.0f} / P50 {d.quantile(0.5):.0f}"
        f" / P90 {d.quantile(0.9):.0f}   in 20-120: {d.between(20, 120).mean():.0%}"
    )
    print(f"  delay share            {hist['delay_reason'].notna().mean():.1%}")
    print("  validation             OK")


if __name__ == "__main__":
    main()

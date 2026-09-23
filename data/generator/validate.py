"""Standalone validation of data/output artifacts.

Checks docs/synthetic_data.md "Validation checks" for everything generated so far (master
data, weather, shifts, maintenance, tasks). Telemetry-dependent checks are skipped until
the telemetry generator exists. Writes data/output/validation.html (plain tables + plots
embedded as base64 PNG). Exits non-zero if any check fails.

Usage: python data/generator/validate.py [--config data/generator/config.yaml]
"""

import argparse
import base64
import html
import io
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from gen import io as gio
from gen.operators import PERSONALITY_SHARES
from gen.tasks import BASE_RATE_M3_H, DELAY_REASONS, HAUL_TPH_AT_1KM, TYPES_BY_MACHINE
from sklearn.linear_model import LinearRegression

REPO_ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_SKIPPED = "skipped: telemetry not generated yet"

ID_PATTERNS = {
    "site_id": r"S\d+",
    "machine_id": r"M\d{2}",
    "operator_id": r"OP\d{2}",
    "shift_id": r"SH-\d{4}-\d{2}-\d{2}-M\d{2}-[DN]",
    "task_id": r"T-SH-\d{4}-\d{2}-\d{2}-M\d{2}-[DN]-\d+",
}
FAILURES_RANGE = (20, 30)
DELAY_SHARE = (0.03, 0.07)
NIGHT_SHARE = (0.30, 0.40)
WEEKLY_SHIFTS = (5.0, 6.2)
R2_THRESHOLD = 0.6
TRAIN_DAYS, TEST_DAYS = (1, 70), (81, 90)


@dataclass
class Result:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIPPED"
    detail: str


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def load_tables(out_dir: Path) -> dict[str, pd.DataFrame]:
    rd = lambda name, **kw: pd.read_csv(out_dir / f"{name}.csv", **kw)
    t = {
        "sites": rd("sites"),
        "machines": rd("machines"),
        "operators": rd("operators"),
        "weather": rd("weather"),
        "shifts": rd("shifts"),
        "tasks": rd("tasks"),
        "maintenance_log": rd("maintenance_log"),
        "machine_health_daily": rd("machine_health_daily"),
        "tasks_truth": rd("tasks_truth"),
    }
    t["weather"]["ts"] = pd.to_datetime(t["weather"]["ts"], utc=True, format="ISO8601")
    for col in ("start_time", "end_time"):
        t["shifts"][col] = pd.to_datetime(t["shifts"][col], utc=True, format="ISO8601")
    for col in ("scheduled_start", "actual_start", "actual_end"):
        t["tasks"][col] = pd.to_datetime(t["tasks"][col], utc=True, format="ISO8601")
    t["maintenance_log"]["event_date"] = pd.to_datetime(
        t["maintenance_log"]["event_date"], utc=True, format="ISO8601"
    )
    return t


def base_expectation_min(tasks: pd.DataFrame, machines: pd.DataFrame) -> pd.Series:
    """Nominal duration: quantity / base rate * 60 (haul rate scales with distance)."""
    mtype = tasks["machine_id"].map(machines.set_index("machine_id")["machine_type"])
    rate = pd.Series(index=tasks.index, dtype=float)
    haul = tasks["task_type"] == "haul"
    rate.loc[haul] = HAUL_TPH_AT_1KM * 1000.0 / tasks.loc[haul, "haul_distance_m"]
    rate.loc[~haul] = [
        BASE_RATE_M3_H[mt][tt] for mt, tt in zip(mtype[~haul], tasks.loc[~haul, "task_type"])
    ]
    return tasks["quantity"] / rate * 60.0


def task_features(cfg: dict, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Completed tasks with rain at the scheduled hour, shift start, and the base ratio."""
    tasks = tables["tasks"]
    hist = tasks[tasks["status"] == "completed"].copy()
    s_ix = tables["shifts"].set_index("shift_id")
    hist["night"] = (hist["shift_id"].map(s_ix["shift_type"]) == "night").astype(float)
    hist["shift_start"] = hist["shift_id"].map(s_ix["start_time"])
    hist["hours_into_shift"] = (
        hist["scheduled_start"] - hist["shift_start"]
    ).dt.total_seconds() / 3600
    hist["skill_score"] = hist["operator_id"].map(
        tables["operators"].set_index("operator_id")["skill_score"]
    )
    we = tables["weather"]
    m = hist.assign(hour=hist["scheduled_start"].dt.floor("h")).merge(
        we[["site_id", "ts", "rain_mm"]],
        left_on=["site_id", "hour"],
        right_on=["site_id", "ts"],
        how="left",
    )
    hist["rain_mm"] = m["rain_mm"].to_numpy()
    hist["ratio"] = hist["actual_duration_min"] / base_expectation_min(hist, tables["machines"])
    start = pd.Timestamp(cfg["start_date"])
    hist["day"] = (pd.to_datetime(hist["task_date"]) - start).dt.days + 1
    return hist


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------
def check_row_counts(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    n_sites = len(cfg["sites"])
    n_mach = n_sites * sum(cfg["machines"].values())
    n_fail = int((t["maintenance_log"]["event_type"] == "failure").sum())
    expected = {
        "sites": n_sites,
        "machines": n_mach,
        "operators": int(cfg["operators"]),
        "weather": n_sites * (int(cfg["days"]) + int(cfg["future_days"]) + 1) * 24,
        "machine_health_daily": n_mach * (int(cfg["days"]) + int(cfg["future_days"])),
    }
    bad = {k: f"{len(t[k])} != {v}" for k, v in expected.items() if len(t[k]) != v}
    n_shifts, n_tasks = len(t["shifts"]), len(t["tasks"])
    lo = 4 * n_shifts - 5 * n_fail
    hi = 6 * n_shifts
    if not (lo <= n_tasks <= hi):
        bad["tasks"] = f"{n_tasks} not in [{lo}, {hi}]"
    per_shift = t["tasks"].groupby("shift_id").size()
    if not per_shift.between(1, int(max(cfg["tasks_per_shift"]))).all():
        bad["tasks/shift"] = f"out of range: {per_shift.min()}..{per_shift.max()}"
    if n_fail != int(cfg["failures_total"]):
        bad["failures"] = f"{n_fail} != {cfg['failures_total']}"
    detail = (
        " · ".join(
            f"{k} {len(t[k])}" + (f"/{v}" if k in expected else "") for k, v in expected.items()
        )
        + f" · shifts {n_shifts} · tasks {n_tasks} · maintenance_log {len(t['maintenance_log'])}"
    )
    return Result("row counts vs config", "PASS" if not bad else "FAIL", detail or "; ".join(bad))


def check_not_null(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    bad = []
    for table, cols in gio.NOT_NULL.items():
        if table not in t:
            continue
        df = t[table]
        nulls = {c: int(df[c].isna().sum()) for c in cols if df[c].isna().any()}
        if nulls:
            bad.append(f"{table}: {nulls}")
    hist = t["tasks"][t["tasks"]["status"] == "completed"]
    miss = hist[["actual_start", "actual_end", "actual_duration_min"]].isna().sum()
    if miss.any():
        bad.append(f"completed tasks: {dict(miss[miss > 0])}")
    return Result(
        "no nulls in required columns",
        "PASS" if not bad else "FAIL",
        "; ".join(bad) or "all required columns filled",
    )


def check_fk(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    bad = []
    sites = set(t["sites"]["site_id"])
    machs = set(t["machines"]["machine_id"])
    ops = set(t["operators"]["operator_id"])
    m = t["machines"]
    o = t["operators"]
    s = t["shifts"]
    tk = t["tasks"]
    ml = t["maintenance_log"]
    w = t["weather"]
    hd = t["machine_health_daily"]

    def fk(df, col, target, label):
        if not set(df[col].dropna()) <= target:
            bad.append(label)

    fk(o, "site_id", sites, "operators.site_id")
    fk(m, "site_id", sites, "machines.site_id")
    fk(w, "site_id", sites, "weather.site_id")
    fk(s, "site_id", sites, "shifts.site_id")
    fk(s, "operator_id", ops, "shifts.operator_id")
    fk(s, "machine_id", machs, "shifts.machine_id")
    fk(ml, "machine_id", machs, "maintenance_log.machine_id")
    fk(hd, "machine_id", machs, "machine_health_daily.machine_id")
    fk(tk, "shift_id", set(s["shift_id"]), "tasks.shift_id")

    s_ix = s.set_index("shift_id")
    for col in ("site_id", "machine_id", "operator_id"):
        if not (tk[col].to_numpy() == tk["shift_id"].map(s_ix[col]).to_numpy()).all():
            bad.append(f"tasks.{col} does not match its shift")
    if not (
        tk["task_date"].astype(str) == tk["shift_id"].map(s_ix["shift_date"]).astype(str)
    ).all():
        bad.append("tasks.task_date does not match its shift")
    op_site = o.set_index("operator_id")["site_id"]
    m_site = m.set_index("machine_id")["site_id"]
    if not (s["operator_id"].map(op_site) == s["site_id"]).all():
        bad.append("operator at foreign site in shifts")
    if not (s["machine_id"].map(m_site) == s["site_id"]).all():
        bad.append("machine at foreign site in shifts")

    types_ok = all(
        r.task_type in TYPES_BY_MACHINE[mt]
        for r, mt in zip(
            tk.itertuples(), tk["machine_id"].map(m.set_index("machine_id")["machine_type"])
        )
    )
    if not types_ok:
        bad.append("task_type not allowed for machine_type")
    is_haul = tk["task_type"] == "haul"
    if (
        not (tk.loc[is_haul, "unit"] == "tons").all()
        or not (tk.loc[~is_haul, "unit"] == "m3").all()
    ):
        bad.append("unit/haul mismatch")
    if (
        not tk.loc[is_haul, "haul_distance_m"].between(300, 2500).all()
        or tk.loc[~is_haul, "haul_distance_m"].notna().any()
    ):
        bad.append("haul_distance_m out of range or set on non-haul")

    for (mm, comp), g in ml.groupby(["machine_id", "component"]):
        if comp == "other":
            continue
        f = g[g["event_type"] == "failure"].sort_values("event_date").reset_index(drop=True)
        r = g[g["event_type"] == "repair"].sort_values("event_date").reset_index(drop=True)
        if len(f) != len(r):
            bad.append(f"unpaired failure/repair {mm}/{comp}")
            continue
        gap_h = (r["event_date"] - f["event_date"]).dt.total_seconds() / 3600
        if ((gap_h - f["downtime_hours"]).abs() > 0.01).any() or not gap_h.between(4, 24).all():
            bad.append(f"repair not after failure downtime {mm}/{comp}")

    for table, key, pat in [
        ("sites", "site_id", ID_PATTERNS["site_id"]),
        ("machines", "machine_id", ID_PATTERNS["machine_id"]),
        ("operators", "operator_id", ID_PATTERNS["operator_id"]),
        ("shifts", "shift_id", ID_PATTERNS["shift_id"]),
        ("tasks", "task_id", ID_PATTERNS["task_id"]),
    ]:
        if not t[table][key].astype(str).str.fullmatch(pat).all():
            bad.append(f"{table}.{key} format")
        if not t[table][key].is_unique:
            bad.append(f"{table}.{key} not unique")
    if not m["serial_no"].is_unique:
        bad.append("machines.serial_no not unique")
    if s.duplicated(["machine_id", "start_time"]).any():
        bad.append("machine double-booked in shifts")
    if s.duplicated(["operator_id", "shift_date"]).any():
        bad.append("operator with >1 shift per day")
    if w.duplicated(["site_id", "ts"]).any():
        bad.append("weather (site_id, ts) not unique")
    return Result(
        "FK integrity",
        "PASS" if not bad else "FAIL",
        "; ".join(bad)
        if bad
        else f"{len(tk)} tasks, {len(s)} shifts, {len(ml)} maintenance rows fully consistent",
    )


def check_shift_rhythm(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    s = t["shifts"]
    s_date = pd.to_datetime(s["shift_date"])
    night = (s["shift_type"] == "night").mean()
    n_weeks = (int(cfg["days"]) + int(cfg["future_days"])) / 7
    per_week = s.groupby("operator_id").size() / n_weeks
    nights = set(
        zip(s.loc[s["shift_type"] == "night", "operator_id"], s_date[s["shift_type"] == "night"])
    )
    day_rows = s["shift_type"] == "day"
    turn = sum(
        1
        for o, d in zip(s.loc[day_rows, "operator_id"], s_date[day_rows] - pd.Timedelta(days=1))
        if (o, d) in nights
    )
    bad = []
    if not NIGHT_SHARE[0] <= night <= NIGHT_SHARE[1]:
        bad.append(f"night share {night:.1%}")
    if not per_week.between(*WEEKLY_SHIFTS).all():
        bad.append(f"shifts/week {per_week.min():.2f}..{per_week.max():.2f}")
    if turn:
        bad.append(f"{turn} day shifts right after nights")
    detail = f"night share {night:.1%} · shifts/operator/week {per_week.mean():.2f} (min {per_week.min():.2f})"
    return Result("shift rhythm", "PASS" if not bad else "FAIL", "; ".join(bad) if bad else detail)


def check_delay_share(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    hist = t["tasks"][t["tasks"]["status"] == "completed"]
    share = hist["delay_reason"].notna().mean()
    ok = DELAY_SHARE[0] <= share <= DELAY_SHARE[1] and set(hist["delay_reason"].dropna()) <= set(
        DELAY_REASONS
    )
    return Result(
        "delay share ~5%", "PASS" if ok else "FAIL", f"{share:.1%} of {len(hist)} completed tasks"
    )


def check_personalities(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    vc = t["operators"]["personality"].value_counts()
    missing = set(PERSONALITY_SHARES) - set(vc.index)
    off = {
        p: f"{vc[p]} != {share * len(t['operators']):.0f}"
        for p, share in PERSONALITY_SHARES.items()
        if abs(vc[p] - share * len(t["operators"])) > 1
    }
    ok = not missing and not off
    return Result(
        "every personality present",
        "PASS" if ok else "FAIL",
        f"missing {missing}"
        if missing
        else (
            "share off: " + str(off)
            if off
            else ", ".join(f"{p} {vc[p]}" for p in PERSONALITY_SHARES)
        ),
    )


def check_failures_range(cfg: dict, t: dict[str, pd.DataFrame]) -> Result:
    n = int((t["maintenance_log"]["event_type"] == "failure").sum())
    ok = FAILURES_RANGE[0] <= n <= FAILURES_RANGE[1]
    mix = (
        t["maintenance_log"]
        .loc[t["maintenance_log"]["event_type"] == "failure", "component"]
        .value_counts()
        .to_dict()
    )
    return Result(
        "failures in 20-30",
        "PASS" if ok else "FAIL",
        f"{n} failures {mix}" if ok else f"{n} outside {FAILURES_RANGE}",
    )


def check_correlations(cfg: dict, t: dict[str, pd.DataFrame], hist: pd.DataFrame) -> Result:
    valid = hist["rain_mm"].notna()
    r_ratio, r_rain = hist.loc[valid, "ratio"], hist.loc[valid, "rain_mm"]
    sp_rain = r_ratio.corr(r_rain, method="spearman")
    pe_rain = r_ratio.corr(r_rain)
    sp_skill = hist["ratio"].corr(hist["skill_score"], method="spearman")
    pe_skill = hist["ratio"].corr(hist["skill_score"])
    ok = sp_rain > 0 and pe_rain > 0 and sp_skill < 0 and pe_skill < 0
    detail = (
        f"ratio vs rain: spearman {sp_rain:+.3f}, pearson {pe_rain:+.3f}"
        f" · ratio vs skill: spearman {sp_skill:+.3f}, pearson {pe_skill:+.3f}"
    )
    return Result("duration ratio vs rain > 0, vs skill < 0", "PASS" if ok else "FAIL", detail)


def check_baseline_r2(hist: pd.DataFrame) -> Result:
    dum = pd.get_dummies(hist[["task_type", "material_type"]], drop_first=True).astype(float)
    X = pd.concat(
        [
            dum,
            np.log(hist["quantity"]).rename("log_quantity"),
            hist["terrain_slope_deg"].rename("slope"),
            hist["rain_mm"].rename("rain"),
            hist["skill_score"].rename("skill"),
            hist["night"].rename("night"),
            hist["hours_into_shift"].rename("hours_into_shift"),
        ],
        axis=1,
    )
    y = np.log(hist["actual_duration_min"])
    train = hist["day"].between(*TRAIN_DAYS)
    test = hist["day"].between(*TEST_DAYS)
    model = LinearRegression().fit(X[train], y[train])
    r2 = model.score(X[test], y[test])
    ok = r2 > R2_THRESHOLD
    detail = (
        f"linear model on log(duration): R2 = {r2:.3f} on days {TEST_DAYS[0]}-{TEST_DAYS[1]}"
        f" (n_test {int(test.sum())}, trained on {int(train.sum())} tasks from days {TRAIN_DAYS})"
        f"  threshold > {R2_THRESHOLD}"
    )
    return Result("baseline R2 on time split", "PASS" if ok else "FAIL", detail)


def skipped_telemetry() -> list[Result]:
    return [
        Result(
            "telemetry: normal-day / anomaly / failure-drift plots", "SKIPPED", TELEMETRY_SKIPPED
        ),
        Result("telemetry: injected anomaly rate 2.5-3.5%", "SKIPPED", TELEMETRY_SKIPPED),
        Result("fatigue: perclos vs hours_into_shift > 0", "SKIPPED", TELEMETRY_SKIPPED),
    ]


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------
def _png(fig: "plt.Figure") -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def plots(hist: pd.DataFrame) -> list[tuple[str, str]]:
    figs = []
    fig, ax = plt.subplots(figsize=(7, 4))
    for tt, g in hist.groupby("task_type"):
        ax.hist(g["actual_duration_min"], bins=np.linspace(0, 240, 49), alpha=0.5, label=tt)
    ax.set(xlabel="actual_duration_min", ylabel="tasks", title="Duration by task type")
    ax.legend()
    figs.append(("Duration histogram by task type", _png(fig)))

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.scatter(hist["rain_mm"], hist["ratio"], s=6, alpha=0.25)
    ax.set(
        xlabel="rain_mm (hour of scheduled_start)",
        ylabel="actual / base expectation",
        title=f"Duration ratio vs rain (spearman {hist['ratio'].corr(hist['rain_mm'], method='spearman'):+.3f})",
    )
    figs.append(("Duration ratio vs rain", _png(fig)))

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.scatter(hist["skill_score"], hist["ratio"], s=6, alpha=0.25)
    ax.set(
        xlabel="operator skill_score",
        ylabel="actual / base expectation",
        title=f"Duration ratio vs skill (spearman {hist['ratio'].corr(hist['skill_score'], method='spearman'):+.3f})",
    )
    figs.append(("Duration ratio vs skill", _png(fig)))
    return figs


def render_html(results: list[Result], figs: list[tuple[str, str]]) -> str:
    rows = "\n".join(
        f"<tr><td>{html.escape(r.name)}</td><td class='{r.status.lower()}'>{r.status}</td>"
        f"<td>{html.escape(r.detail)}</td></tr>"
        for r in results
    )
    imgs = "\n".join(
        f"<h3>{html.escape(title)}</h3><img src='data:image/png;base64,{b64}' />"
        for title, b64 in figs
    )
    fails = sum(r.status == "FAIL" for r in results)
    overall = "FAIL" if fails else "PASS"
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Generator validation</title><style>
body{{font-family:monospace;margin:2rem}}table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px 10px;font-size:14px}}
.pass{{color:#067d06}}.fail{{color:#c00;font-weight:bold}}.skipped{{color:#888}}</style></head><body>
<h1>Synthetic data validation — {html.escape(datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))}</h1>
<p>Overall: <b class='{overall.lower()}'>{overall}</b> ({sum(r.status == "PASS" for r in results)} passed,
{fails} failed, {sum(r.status == "SKIPPED" for r in results)} skipped)</p>
<table><tr><th>check</th><th>status</th><th>detail</th></tr>{rows}</table>
<h2>Task plots</h2>
{imgs}
<p><small>skipped: telemetry not generated yet</small></p>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "data/generator/config.yaml")
    ap.add_argument("--output-dir", type=Path, default=REPO_ROOT / "data/output")
    args = ap.parse_args()
    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)
    cfg.setdefault("future_days", 0)

    tables = load_tables(args.output_dir)
    hist = task_features(cfg, tables)
    results = [
        check_row_counts(cfg, tables),
        check_not_null(cfg, tables),
        check_fk(cfg, tables),
        check_shift_rhythm(cfg, tables),
        check_delay_share(cfg, tables),
        check_correlations(cfg, tables, hist),
        check_personalities(cfg, tables),
        check_failures_range(cfg, tables),
        check_baseline_r2(hist),
        *skipped_telemetry(),
    ]

    for r in results:
        icon = {"PASS": "PASS  ", "FAIL": "FAIL  ", "SKIPPED": "SKIP  "}[r.status]
        print(f"  {icon} {r.name:44s} {r.detail}")
    n_fail = sum(r.status == "FAIL" for r in results)
    html_path = args.output_dir / "validation.html"
    html_path.write_text(render_html(results, plots(hist)))
    print(f"wrote {html_path}")
    if n_fail:
        print(f"validation FAILED ({n_fail} checks)")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()

"""Rank 60-minute demo windows in the 14 days loaded in Supabase (2026-08-16 → 08-29).

    python scripts/find_demo_window.py                      # OP03 first, all S1 operators if OP03 has nothing good
    python scripts/find_demo_window.py --operator OP07 --top 10
    python scripts/find_demo_window.py --any-operator       # skip the OP03 attempt

Reads DATABASE_URL from data/.env (read-only queries). A window scores higher for (docs/demo_script.md):
  night       a night shift, about 3 h in
  rain        rain starting in or just before the window (weather, hourly per site)
  overrun     a task running in the window that takes longer than the planner expected
  fault       a real fault on the machine (or another S1 machine) in or near the window
  fatigue     the operator's fatigue_score rising, reaching `high`
  proximity   a proximity_breach / blindspot_intrusion by this machine in the window

`anomaly_type` is read only to *locate* real faults for the demo. It is ground truth and never
a model input (CLAUDE.md rule 1); nothing here trains or feeds a model.

The planner estimate for overrun uses only what the planner knows (docs/synthetic_data.md,
"Planner"): base rate, material, slope and the operator's skill.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
FIRST_DAY, LAST_DAY = "2026-08-16", "2026-08-29"
WINDOW = timedelta(minutes=60)
IST = "Asia/Kolkata"

WEIGHTS = {
    "night": 3.0,
    "rain": 2.0,
    "overrun": 2.0,
    "fault": 3.0,
    "fatigue": 2.0,
    "proximity": 2.0,
}
GOOD_ENOUGH = 8.0  # of 14; below this the preferred operator "has nothing good"

# Real faults worth showing. excessive_idle / sensor_glitch / unsafe_operation are behaviour or
# data issues, not the "machine is getting sick" beat of the demo.
REAL_FAULTS = {"overheating", "hydraulic_leak", "battery_fault"}
FAULT_NEAR = timedelta(minutes=20)  # a fault this close outside the window still counts (half)

BASE_RATE = {  # per hour, docs/synthetic_data.md "Tasks — hidden duration formula"
    ("excavator", "dig"): 110,
    ("excavator", "trench"): 60,
    ("excavator", "load"): 140,
    ("wheel_loader", "load"): 180,
    ("wheel_loader", "backfill"): 150,
    ("dozer", "grade"): 90,
    ("dozer", "backfill"): 120,
}
F_MATERIAL = {"sand": 0.9, "topsoil": 0.9, "clay": 1.0, "gravel": 1.05, "rock": 1.4}


@dataclass
class Window:
    shift_id: str
    machine_id: str
    operator_id: str
    site_id: str
    shift_type: str
    start: pd.Timestamp
    hours_in: float
    parts: dict[str, float] = field(default_factory=dict)
    events: list[tuple[float, str]] = field(default_factory=list)  # (minute offset, text)

    @property
    def score(self) -> float:
        return sum(WEIGHTS[k] * v for k, v in self.parts.items())


def load(conn: psycopg.Connection) -> dict[str, pd.DataFrame]:
    """Everything the scorer needs for the 14 days, with tz-aware UTC timestamps."""
    queries = {
        "machines": "select machine_id, site_id, machine_type from machines",
        "operators": "select operator_id, full_name, site_id, skill_score from operators",
        "shifts": f"""select shift_id, site_id, operator_id, machine_id, shift_type::text, shift_date,
                             start_time, end_time
                      from shifts where shift_date between '{FIRST_DAY}' and '{LAST_DAY}'""",
        "tasks": f"""select task_id, shift_id, machine_id, sequence_no, task_type::text,
                            material_type::text, quantity, terrain_slope_deg, haul_distance_m,
                            actual_start, actual_end, actual_duration_min, status::text, delay_reason
                     from tasks where task_date between '{FIRST_DAY}' and '{LAST_DAY}'""",
        "weather": "select site_id, ts, rain_mm, visibility_m from weather",
        # anomaly_type only locates real faults for the demo, see module docstring.
        "telemetry": """select ts, machine_id, shift_id, fault_code, anomaly_type,
                               coolant_temp_c, hydraulic_oil_temp_c, battery_voltage
                        from telemetry
                        where fault_code is not null or anomaly_type is not null""",
        "safety": """select ts, machine_id, operator_id, event_type::text, severity::text,
                            distance_m, sector::text, approaching
                     from safety_events""",
        "fatigue": """select ts, operator_id, shift_id, perclos_60s, fatigue_score, fatigue_level::text
                      from fatigue_log""",
    }
    out = {}
    for name, q in queries.items():
        with conn.cursor() as cur:
            cur.execute(q)
            cols = [d.name for d in cur.description]
            out[name] = pd.DataFrame(cur.fetchall(), columns=cols)
    for df in out.values():
        for c in df.columns:
            if c in {"ts", "start_time", "end_time", "actual_start", "actual_end"}:
                df[c] = pd.to_datetime(df[c], utc=True)
            elif df[c].dtype == object and c in {
                "quantity",
                "terrain_slope_deg",
                "haul_distance_m",
                "actual_duration_min",
                "rain_mm",
                "visibility_m",
                "coolant_temp_c",
                "hydraulic_oil_temp_c",
                "battery_voltage",
                "distance_m",
                "perclos_60s",
                "fatigue_score",
                "skill_score",
            }:
                df[c] = pd.to_numeric(df[c])
    return out


def planner_estimate_min(task: pd.Series, machine_type: str, skill: float) -> float | None:
    """What the planner expects: base rate, material, slope, skill (no rain, night, personality)."""
    if machine_type == "articulated_truck":
        rate = 70 * 1000 / max(task.haul_distance_m or 1000, 1)
    else:
        rate = BASE_RATE.get((machine_type, task.task_type))
    if not rate:
        return None
    base = task.quantity / rate * 60
    return (
        base
        * (1.25 - 0.5 * skill)
        * F_MATERIAL[task.material_type]
        * (1 + 0.02 * task.terrain_slope_deg)
    )


def fault_episodes(tel: pd.DataFrame) -> pd.DataFrame:
    """One row per contiguous real-fault episode per machine: start, end, type, peak values, code."""
    t = tel[tel.anomaly_type.isin(REAL_FAULTS)].sort_values(["machine_id", "ts"])
    if t.empty:
        return pd.DataFrame(columns=["machine_id", "start", "end", "kind", "code", "peak"])
    new = (
        (t.machine_id != t.machine_id.shift())
        | (t.anomaly_type != t.anomaly_type.shift())
        | (t.ts.diff() > pd.Timedelta(minutes=2))
    )
    t = t.assign(ep=new.cumsum())
    rows = []
    for _, g in t.groupby("ep"):
        kind = g.anomaly_type.iloc[0]
        peak = {
            "overheating": f"coolant peaks {g.coolant_temp_c.max():.0f} °C",
            "hydraulic_leak": f"hyd oil peaks {g.hydraulic_oil_temp_c.max():.0f} °C, pressure drops",
            "battery_fault": f"battery sags to {g.battery_voltage.min():.1f} V",
        }[kind]
        codes = sorted(set(g.fault_code.dropna()))
        rows.append(
            {
                "machine_id": g.machine_id.iloc[0],
                "start": g.ts.min(),
                "end": g.ts.max(),
                "kind": kind,
                "code": ",".join(codes) or None,
                "peak": peak,
            }
        )
    return pd.DataFrame(rows)


def score_shift(
    shift: pd.Series, d: dict[str, pd.DataFrame], faults: pd.DataFrame, step_min: int
) -> list[Window]:
    machines = d["machines"].set_index("machine_id")
    mtype = machines.at[shift.machine_id, "machine_type"]
    skill = float(d["operators"].set_index("operator_id").at[shift.operator_id, "skill_score"])
    site_machines = set(machines.index[machines.site_id == shift.site_id])

    weather = d["weather"][d["weather"].site_id == shift.site_id].set_index("ts").sort_index()
    tasks = d["tasks"][d["tasks"].shift_id == shift.shift_id].sort_values("sequence_no")
    fat = d["fatigue"][d["fatigue"].shift_id == shift.shift_id].sort_values("ts")
    saf = d["safety"][d["safety"].machine_id == shift.machine_id]
    tel_codes = d["telemetry"][
        (d["telemetry"].machine_id == shift.machine_id) & d["telemetry"].fault_code.notna()
    ]
    site_faults = faults[faults.machine_id.isin(site_machines)]

    out: list[Window] = []
    s = shift.start_time + timedelta(minutes=30)
    while s + WINDOW <= shift.end_time:
        e = s + WINDOW
        hours_in = (s - shift.start_time).total_seconds() / 3600
        w = Window(
            shift.shift_id,
            shift.machine_id,
            shift.operator_id,
            shift.site_id,
            shift.shift_type,
            s,
            hours_in,
        )

        def at(ts: pd.Timestamp, s: pd.Timestamp = s) -> float:
            return (ts - s).total_seconds() / 60  # minute offset in the window

        # night, ~3 h in (window centre within ±2 h of 3 h scores > 0)
        centre = hours_in + 0.5
        w.parts["night"] = (shift.shift_type == "night") * (
            0.5 + 0.5 * max(0.0, 1 - abs(centre - 3) / 2)
        )

        # rain starting: an hour in [s-1h, e) with rain after a dry hour
        hrs = weather.loc[s.floor("h") - timedelta(hours=1) : e]
        rain = 0.0
        for ts, mm in hrs.rain_mm.items():
            if ts >= e:
                break
            prev = weather.rain_mm.get(ts - timedelta(hours=1), 0.0)
            if mm > 0 and prev == 0:
                rain = max(rain, 1.0 if ts >= s - timedelta(minutes=30) else 0.6)
                w.events.append(
                    (
                        at(ts),
                        f"rain starts ({mm:.1f} mm/h, visibility {hrs.visibility_m[ts]:.0f} m)",
                    )
                )
            elif mm > 0:
                rain = max(rain, 0.4)
        w.parts["rain"] = rain

        # task overrun: a task running in the window whose actual time passes the planner estimate
        over = 0.0
        for _, t in tasks.iterrows():
            if pd.isna(t.actual_start) or t.status == "cancelled":
                continue
            t_end = t.actual_end if pd.notna(t.actual_end) else shift.end_time
            if not (t.actual_start < e and t_end > s):
                continue
            est = planner_estimate_min(t, mtype, skill)
            if not est:
                continue
            actual = (t_end - t.actual_start).total_seconds() / 60
            ratio = actual / est
            est_end = t.actual_start + timedelta(minutes=est)
            if ratio > 1.05:
                v = min((ratio - 1) / 0.3, 1.0) * (1.0 if s <= est_end < e else 0.6)
                over = max(over, v)
                label = f"{t.task_id} {t.task_type} {t.quantity:.0f} {t.material_type}"
                why = f", {t.delay_reason}" if t.delay_reason else ""
                w.events.append(
                    (
                        at(t.actual_start),
                        f"{label} starts (planner ~{est:.0f} min, actual {actual:.0f} min{why})",
                    )
                )
                if s <= est_end < e:
                    w.events.append(
                        (at(est_end), f"task {t.sequence_no} passes its planned end → overrun")
                    )
        w.parts["overrun"] = over

        # real fault: starts in window = 1 (other site machine 0.5), already running 0.7 (0.35), own machine near 0.5
        fault = 0.0
        for _, f in site_faults.iterrows():
            own = f.machine_id == shift.machine_id
            if s <= f.start < e:
                v = 1.0 if own else 0.5
            elif f.start < s <= f.end:
                v = 0.7 if own else 0.35  # already running at replay start: less of a moment
            elif own and (s - FAULT_NEAR <= f.end < s or e <= f.start < e + FAULT_NEAR):
                v = 0.5
            else:
                continue
            fault = max(fault, v)
            code = f" [{f.code}]" if f.code else ""
            w.events.append(
                (
                    at(f.start),
                    f"{f.machine_id} {f.kind}{code} starts, {f.peak}, ends +{at(f.end):.0f} min",
                )
            )
        codes = tel_codes[(tel_codes.ts >= s) & (tel_codes.ts < e)]
        if not codes.empty and fault < 1:
            fault = max(fault, 0.7)
            w.events.append(
                (
                    at(codes.ts.min()),
                    f"fault code {','.join(sorted(set(codes.fault_code)))} on {shift.machine_id}",
                )
            )
        w.parts["fault"] = fault

        # fatigue rising
        fw = fat[(fat.ts >= s) & (fat.ts < e)]
        fatigue = 0.0
        if len(fw) >= 10:
            head, tail = fw.fatigue_score.iloc[:10].mean(), fw.fatigue_score.iloc[-10:].mean()
            rise = tail - head
            highs = fw[fw.fatigue_level == "high"]
            fatigue = 0.6 * min(max(rise, 0) / 0.1, 1) + 0.4 * (not highs.empty)
            w.events.append(
                (
                    0,
                    f"fatigue {head:.2f} → {tail:.2f} (level {fw.fatigue_level.iloc[0]} → {fw.fatigue_level.iloc[-1]})",
                )
            )
            if not highs.empty:
                w.events.append(
                    (
                        at(highs.ts.min()),
                        f"fatigue level turns high (score {highs.fatigue_score.iloc[0]:.2f})",
                    )
                )
        w.parts["fatigue"] = fatigue

        # proximity / blindspot + other safety events by this machine
        sw = saf[(saf.ts >= s) & (saf.ts < e)]
        prox = sw[sw.event_type.isin(["proximity_breach", "blindspot_intrusion"])]
        w.parts["proximity"] = (
            0.0 if prox.empty else (1.0 if (prox.severity == "critical").any() else 0.7)
        )
        for _, ev in sw.iterrows():
            extra = f" {ev.distance_m:.1f} m {ev.sector}" if pd.notna(ev.distance_m) else ""
            extra += " approaching" if ev.approaching else ""
            w.events.append((at(ev.ts), f"{ev.event_type} ({ev.severity}){extra}"))

        out.append(w)
        s += timedelta(minutes=step_min)
    return out


def rank(
    d: dict[str, pd.DataFrame], faults: pd.DataFrame, shifts: pd.DataFrame, step: int
) -> list[Window]:
    windows = [w for _, sh in shifts.iterrows() for w in score_shift(sh, d, faults, step)]
    windows.sort(key=lambda w: w.score, reverse=True)
    # one window per shift in the ranking: overlapping windows of one shift add nothing
    best, seen = [], set()
    for w in windows:
        if w.shift_id not in seen:
            best.append(w)
            seen.add(w.shift_id)
    return best


def other_active(d: dict[str, pd.DataFrame], w: Window) -> list[str]:
    """Site machines with a shift covering the window, the chosen machine first."""
    sh, m = d["shifts"], d["machines"]
    site = set(m.machine_id[m.site_id == w.site_id])
    act = sh[
        sh.machine_id.isin(site) & (sh.start_time <= w.start) & (sh.end_time >= w.start + WINDOW)
    ]
    return [w.machine_id] + sorted(set(act.machine_id) - {w.machine_id})


def show(d: dict[str, pd.DataFrame], ranked: list[Window], top: int) -> None:
    names = d["operators"].set_index("operator_id").full_name
    mtypes = d["machines"].set_index("machine_id").machine_type
    for i, w in enumerate(ranked[:top], 1):
        ist = w.start.tz_convert(IST)
        print(
            f"\n#{i}  score {w.score:.1f}/14  {w.machine_id} ({mtypes[w.machine_id]})  {w.shift_id}  "
            f"{w.operator_id} {names[w.operator_id]}"
        )
        print(
            f"    start {w.start:%Y-%m-%dT%H:%M:%SZ}  ({ist:%d %b %H:%M} IST, {w.hours_in:.1f} h into {w.shift_type} shift)"
        )
        print("    parts " + "  ".join(f"{k} {v:.2f}" for k, v in w.parts.items()))
        print(f"    replay machines (on shift at S-time): {', '.join(other_active(d, w))}")
        for m, text in sorted(w.events, key=lambda x: x[0]):
            print(f"    {m:+5.0f} min  {text}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--operator", default="OP03", help="preferred demo operator (default OP03, Ravi)"
    )
    ap.add_argument(
        "--site", default="S1", help="site for the fallback search (default S1, the demo site)"
    )
    ap.add_argument(
        "--any-operator", action="store_true", help="rank all operators at --site directly"
    )
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument(
        "--step", type=int, default=15, help="window start step in minutes (default 15)"
    )
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    load_dotenv(REPO_ROOT / "data" / ".env")
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL missing: copy data/.env.example to data/.env", file=sys.stderr)
        return 1
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.read_only = True
        d = load(conn)
    faults = fault_episodes(d["telemetry"])
    shifts = d["shifts"]

    if not args.any_operator:
        mine = shifts[shifts.operator_id == args.operator]
        ranked = rank(d, faults, mine, args.step)
        nights = (mine.shift_type == "night").sum()
        print(f"{args.operator}: {len(mine)} shifts ({nights} night) in {FIRST_DAY} → {LAST_DAY}")
        if ranked and ranked[0].score >= GOOD_ENOUGH and nights:
            show(d, ranked, args.top)
            return 0
        best = f"best score {ranked[0].score:.1f}" if ranked else "no windows"
        print(
            f"{args.operator} has nothing good ({best}, need ≥ {GOOD_ENOUGH} and a night shift); "
            f"ranking every operator at {args.site}."
        )

    site_shifts = shifts[shifts.site_id == args.site]
    show(d, rank(d, faults, site_shifts, args.step), args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())

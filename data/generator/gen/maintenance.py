"""Failures, engine-hour clock, scheduled services and repairs -> maintenance_log.

Order (docs/synthetic_data.md: "place failures first"):
1. `plan_failures` picks failure moments inside candidate shifts (history only, day >= 10,
   >= MIN_GAP_DAYS apart on the same machine) with a 4-24 h downtime.
2. shifts.apply_downtime removes/truncates shifts -> final roster.
3. `EngineClock` turns the final roster into engine hours (engine runs for the whole shift).
4. `attach_drift_windows` sets t_f (engine hours at failure) and W ~ U(50, 200).
5. `build_maintenance_log` adds a scheduled_service each time hours_since_service reaches
   service_interval_hours (done right after the shift, within the gap before the next shift).
"""

from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd

COMPONENT_MIX = {
    "hydraulics": 0.35,
    "engine": 0.25,
    "cooling": 0.15,
    "electrical": 0.15,
    "undercarriage": 0.10,
}
_ALL = set(COMPONENT_MIX)
ALLOWED = {
    "excavator": _ALL,  # tracked
    "dozer": _ALL,  # tracked
    "wheel_loader": _ALL - {"undercarriage"},
    "articulated_truck": _ALL - {"undercarriage", "hydraulics"},
}
MIN_FAILURE_DAY = 10
MIN_GAP_DAYS = 25  # > max W (200 h) at ~12 engine h/day, so drift windows never overlap
MAX_FAILURES_PER_MACHINE = 3
DOWNTIME_H = (4.0, 24.0)
DRIFT_W_H = (50.0, 200.0)
SERVICE_DOWNTIME_H = (2.0, 4.0)
SERVICE_COST_INR = (18_000, 40_000)
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")

REPAIR_COST_INR = {
    "hydraulics": (80_000, 300_000),
    "engine": (150_000, 600_000),
    "cooling": (40_000, 150_000),
    "electrical": (20_000, 80_000),
    "undercarriage": (100_000, 400_000),
}
FAILURE_NOTES = {
    "hydraulics": [
        "Hydraulic hose burst, oil on ground",
        "Main pump lost pressure",
        "Boom cylinder seal leak",
    ],
    "engine": [
        "Engine low oil pressure shutdown",
        "Heavy knocking, engine stopped",
        "Turbo failure, black smoke",
    ],
    "cooling": [
        "Coolant boiling over, radiator blocked",
        "Water pump failure",
        "Fan belt snapped, overheating",
    ],
    "electrical": [
        "Machine would not crank, low voltage",
        "Alternator stopped charging",
        "Wiring harness short",
    ],
    "undercarriage": ["Track roller seized", "Track chain came off", "Final drive noise, stopped"],
}
REPAIR_NOTES = {
    "hydraulics": ["Replaced hose and topped up oil", "Pump rebuilt", "Cylinder resealed"],
    "engine": ["Oil pump replaced", "Bearings replaced", "Turbocharger replaced"],
    "cooling": ["Radiator cleaned and flushed", "Water pump replaced", "Fan belt replaced"],
    "electrical": ["Batteries replaced", "Alternator replaced", "Harness repaired"],
    "undercarriage": [
        "Rollers replaced",
        "Track re-tensioned and pinned",
        "Final drive seals replaced",
    ],
}


def component_quotas(n: int) -> list[str]:
    raw = {k: v * n for k, v in COMPONENT_MIX.items()}
    counts = {k: int(np.floor(v)) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[
        : n - sum(counts.values())
    ]:
        counts[k] += 1
    return [k for k, c in counts.items() for _ in range(c)]


def _failure_counts(machine_ids: list[str], total: int, rng: np.random.Generator) -> dict[str, int]:
    counts = {m: 0 for m in machine_ids}
    if total >= len(machine_ids):
        counts = {m: 1 for m in machine_ids}
    while sum(counts.values()) < total:
        open_ = [m for m in machine_ids if counts[m] < MAX_FAILURES_PER_MACHINE]
        counts[open_[int(rng.integers(len(open_)))]] += 1
    return counts


def _failure_days(k: int, lo: int, hi: int, rng: np.random.Generator) -> list[int]:
    for _ in range(10_000):
        days = sorted(rng.choice(np.arange(lo, hi + 1), size=k, replace=False).tolist())
        if all(b - a >= MIN_GAP_DAYS for a, b in pairwise(days)):
            return days
    raise RuntimeError(f"cannot place {k} failures {MIN_GAP_DAYS} days apart in [{lo}, {hi}]")


def plan_failures(
    cfg: dict[str, Any], machines: pd.DataFrame, shifts: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    days = int(cfg["days"])
    total = int(cfg["failures_total"])
    counts = _failure_counts(machines["machine_id"].tolist(), total, rng)
    rows = []
    for m, k in counts.items():
        if k == 0:
            continue
        ms = shifts[(shifts["machine_id"] == m) & (shifts["day_idx"] < days)].sort_values(
            "start_time"
        )
        for d in _failure_days(k, MIN_FAILURE_DAY, days - 2, rng):
            s = ms[ms["day_idx"] >= d].iloc[0]
            length_h = (s["end_time"] - s["start_time"]).total_seconds() / 3600
            fail_ts = (
                s["start_time"] + pd.Timedelta(hours=rng.uniform(1.0, length_h - 1.0))
            ).floor("min")
            downtime = round(float(rng.uniform(*DOWNTIME_H)), 1)
            rows.append(
                {
                    "machine_id": m,
                    "shift_id": s["shift_id"],
                    "fail_ts": fail_ts,
                    "downtime_hours": downtime,
                    "repair_ts": fail_ts + pd.Timedelta(hours=downtime),
                }
            )
    failures = pd.DataFrame(rows).sort_values("fail_ts").reset_index(drop=True)
    mtype = failures["machine_id"].map(machines.set_index("machine_id")["machine_type"])
    failures["component"] = _assign_components(mtype.tolist(), rng)
    return failures


def _assign_components(machine_types: list[str], rng: np.random.Generator) -> list[str]:
    """Exact COMPONENT_MIX quotas, but only components the machine type can show in telemetry:
    undercarriage only on tracked machines, no hydraulics on trucks (hydraulic pressure is 0)."""
    for _ in range(1000):
        pool = list(rng.permutation(component_quotas(len(machine_types))))
        out: list[str | None] = [None] * len(machine_types)
        # most restricted failures pick first
        order = sorted(
            range(len(machine_types)), key=lambda i: (len(ALLOWED[machine_types[i]]), rng.random())
        )
        for i in order:
            ok = [j for j, c in enumerate(pool) if c in ALLOWED[machine_types[i]]]
            if not ok:
                break
            out[i] = pool.pop(ok[0])
        else:
            return [str(c) for c in out]
    raise RuntimeError("cannot assign failure components to machine types")


class EngineClock:
    """Engine hours of each machine at any instant, from the final shift roster."""

    def __init__(self, machines: pd.DataFrame, shifts: pd.DataFrame) -> None:
        self._initial = dict(zip(machines["machine_id"], machines["initial_engine_hours"]))
        self._spans: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for m, g in shifts.groupby("machine_id"):
            g = g.sort_values("start_time")
            self._spans[str(m)] = (
                (g["start_time"] - EPOCH).dt.total_seconds().to_numpy(),
                (g["end_time"] - EPOCH).dt.total_seconds().to_numpy(),
            )

    def hours_at(self, machine_id: str, ts: pd.Timestamp) -> float:
        starts, ends = self._spans.get(machine_id, (np.array([]), np.array([])))
        t = (ts - EPOCH).total_seconds()
        run_s = np.clip(np.minimum(ends, t) - starts, 0, None).sum()
        return float(self._initial[machine_id] + run_s / 3600.0)


def attach_drift_windows(
    failures: pd.DataFrame, clock: EngineClock, rng: np.random.Generator
) -> pd.DataFrame:
    df = failures.copy()
    t_f, w = [], []
    last_repair_h: dict[str, float] = {}
    for f in df.itertuples():
        h = clock.hours_at(f.machine_id, f.fail_ts)
        width = float(rng.uniform(*DRIFT_W_H))
        prev = last_repair_h.get(f.machine_id)
        if prev is not None and h - width <= prev:  # drift must start after the previous repair
            width = h - prev - 1.0
        assert width >= DRIFT_W_H[0], f"drift window too short for {f.machine_id} at {f.fail_ts}"
        t_f.append(round(h, 1))
        w.append(round(width, 1))
        last_repair_h[f.machine_id] = clock.hours_at(f.machine_id, f.repair_ts)
    df["engine_hours_at_failure"] = t_f
    df["drift_window_hours"] = w
    return df


def _pick(options: list[str], rng: np.random.Generator) -> str:
    return options[int(rng.integers(len(options)))]


def build_maintenance_log(
    cfg: dict[str, Any],
    machines: pd.DataFrame,
    shifts: pd.DataFrame,
    failures: pd.DataFrame,
    clock: EngineClock,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (maintenance_log, down_windows[machine_id, start, end, reason])."""
    days = int(cfg["days"])
    rows, windows = [], []

    for f in failures.itertuples():
        rows.append(
            {
                "machine_id": f.machine_id,
                "event_date": f.fail_ts,
                "engine_hours_at_event": f.engine_hours_at_failure,
                "component": f.component,
                "event_type": "failure",
                "downtime_hours": f.downtime_hours,
                "cost_inr": None,
                "notes": _pick(FAILURE_NOTES[f.component], rng),
            }
        )
        lo, hi = REPAIR_COST_INR[f.component]
        rows.append(
            {
                "machine_id": f.machine_id,
                "event_date": f.repair_ts,
                "engine_hours_at_event": round(clock.hours_at(f.machine_id, f.repair_ts), 1),
                "component": f.component,
                "event_type": "repair",
                "downtime_hours": 0.0,
                "cost_inr": round(float(rng.uniform(lo, hi)), -2),
                "notes": _pick(REPAIR_NOTES[f.component], rng),
            }
        )
        windows.append(
            {
                "machine_id": f.machine_id,
                "start": f.fail_ts,
                "end": f.repair_ts,
                "reason": "failure",
            }
        )

    repair_of_shift = dict(zip(failures["shift_id"], failures["repair_ts"]))
    for m in machines.itertuples():
        ms = (
            shifts[shifts["machine_id"] == m.machine_id]
            .sort_values("start_time")
            .reset_index(drop=True)
        )
        interval = float(m.service_interval_hours)
        due = m.initial_engine_hours + interval - m.initial_hours_since_service
        for i, s in ms.iterrows():
            if s["day_idx"] >= days:
                break
            if clock.hours_at(m.machine_id, s["end_time"]) < due:
                continue
            ts = repair_of_shift.get(s["shift_id"], s["end_time"])
            gap_h = (
                (ms.loc[i + 1, "start_time"] - ts).total_seconds() / 3600
                if i + 1 < len(ms)
                else 24.0
            )
            downtime = float(np.floor(min(rng.uniform(*SERVICE_DOWNTIME_H), gap_h) * 10) / 10)
            hours = round(clock.hours_at(m.machine_id, ts), 1)
            rows.append(
                {
                    "machine_id": m.machine_id,
                    "event_date": ts,
                    "engine_hours_at_event": hours,
                    "component": "other",
                    "event_type": "scheduled_service",
                    "downtime_hours": downtime,
                    "cost_inr": round(float(rng.uniform(*SERVICE_COST_INR)), -2),
                    "notes": f"{int(interval)} h service: engine oil, filters, greasing",
                }
            )
            windows.append(
                {
                    "machine_id": m.machine_id,
                    "start": ts,
                    "end": ts + pd.Timedelta(hours=downtime),
                    "reason": "service",
                }
            )
            due = hours + interval

    log = (
        pd.DataFrame(rows)
        .sort_values(["machine_id", "event_date"], kind="stable")
        .reset_index(drop=True)
    )
    return log, pd.DataFrame(windows)

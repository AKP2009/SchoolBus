"""Shift roster.

Two steps, because failures must be placed before we know which shifts survive:
1. `build_candidate_shifts` — the planned roster for every day (history + future_days).
2. `apply_downtime` — the shift in which a machine fails ends at the failure time, and any
   other shift of that machine overlapping the downtime is cancelled (dropped).

Rules: operator works only at their own site, max 1 shift per local date, has a fixed weekly
rest day (~6 working days/week), never works a day shift right after a night shift, and
~35% of shifts are nights.
"""

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from gen import LOCAL_TZ
from gen.operators import LEAVE_P

NIGHT_SHARE = 0.35
SHIFT_CODE = {"day": "D", "night": "N"}


def shift_bounds(
    date: dt.date, shift_type: str, cfg: dict[str, Any]
) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_s, end_s = cfg["shifts"][shift_type]
    start = pd.Timestamp(f"{date} {start_s}").tz_localize(LOCAL_TZ)
    end = pd.Timestamp(f"{date} {end_s}").tz_localize(LOCAL_TZ)
    if end <= start:
        end += pd.Timedelta(days=1)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def shift_id(date: dt.date, machine_id: str, shift_type: str) -> str:
    return f"SH-{date:%Y-%m-%d}-{machine_id}-{SHIFT_CODE[shift_type]}"


def build_candidate_shifts(
    cfg: dict[str, Any], operators: pd.DataFrame, machines: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    start_date = pd.Timestamp(cfg["start_date"]).date()
    n_days = int(cfg["days"]) + int(cfg["future_days"])
    rows = []
    for site_id in [s["site_id"] for s in cfg["sites"]]:
        ops = operators[operators["site_id"] == site_id]
        op_ids = ops["operator_id"].tolist()
        rest = dict(zip(ops["operator_id"], ops["rest_weekday"]))
        prefers_night = dict(zip(ops["operator_id"], ops["preferred_shift"] == "night"))
        mach_ids = machines.loc[machines["site_id"] == site_id, "machine_id"].tolist()
        n_slots = len(mach_ids)  # one operator per machine per shift
        # each operator has a usual machine
        primary = dict(
            zip(rng.permutation(op_ids), [mach_ids[i % n_slots] for i in range(len(op_ids))])
        )

        worked_night: set[str] = set()
        for d in range(n_days):
            date = start_date + dt.timedelta(days=d)
            leave = rng.random(len(op_ids)) < LEAVE_P
            avail = [o for o, lv in zip(op_ids, leave) if rest[o] != date.weekday() and not lv]
            order = {o: r for o, r in zip(avail, rng.random(len(avail)))}

            must_night = sorted((o for o in avail if o in worked_night), key=order.get)
            n_night = min(n_slots, max(round(NIGHT_SHARE * len(avail)), len(must_night)))
            others = sorted(
                (o for o in avail if o not in worked_night),
                key=lambda o: (not prefers_night[o], order[o]),
            )
            night_ops = (must_night + others)[:n_night]
            # night-before operators left over (slots full) get the day off
            day_ops = [o for o in others if o not in night_ops][:n_slots]

            for stype, crew in (("day", day_ops), ("night", night_ops)):
                free = list(mach_ids)
                for o in sorted(crew, key=order.get):
                    m = primary[o] if primary[o] in free else free[int(rng.integers(len(free)))]
                    free.remove(m)
                    start, end = shift_bounds(date, stype, cfg)
                    rows.append(
                        {
                            "shift_id": shift_id(date, m, stype),
                            "site_id": site_id,
                            "operator_id": o,
                            "machine_id": m,
                            "shift_type": stype,
                            "shift_date": date,
                            "start_time": start,
                            "end_time": end,
                            "fuel_start_pct": round(float(rng.uniform(90, 100)), 2),
                            "day_idx": d,
                        }
                    )
            worked_night = set(night_ops)
    df = pd.DataFrame(rows).sort_values(["start_time", "machine_id"]).reset_index(drop=True)
    for col in (
        "fuel_end_pct",
        "handover_notes",
        "issues_reported",
        "handover_summary",
        "handover_generated_at",
    ):
        df[col] = None
    return df


def apply_downtime(shifts: pd.DataFrame, failures: pd.DataFrame) -> pd.DataFrame:
    """Truncate the failing shift at the failure; drop the machine's shifts inside downtime."""
    df = shifts.copy()
    drop = np.zeros(len(df), dtype=bool)
    for f in failures.itertuples():
        df.loc[df["shift_id"] == f.shift_id, "end_time"] = f.fail_ts
        overlap = (
            (df["machine_id"] == f.machine_id)
            & (df["shift_id"] != f.shift_id)
            & (df["start_time"] < f.repair_ts)
            & (df["end_time"] > f.fail_ts)
        )
        drop |= overlap.to_numpy()
    return df[~drop].reset_index(drop=True)

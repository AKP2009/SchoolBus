"""Stage 3: shifts. Assign operators to machines per day (synthetic_data.md, step 3).

Rules: one shift per operator per day, >= 10 h rest between shifts, one weekly rest day,
preference for the operator's preferred shift and their usual machine.
Night shifts are ~night_shift_share of all shifts (fewer if operators run out).
Machines are staffed in a random order each shift, so when operators run out the idle
machine is a random one, not always the last in the list (the trucks).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from common import ist_to_utc, rng_for, to_frame

MIN_REST = pd.Timedelta(hours=10)
CODE = {"day": "D", "night": "N"}


def shift_id(d: Any, machine_id: str, shift_type: str) -> str:
    return f"SH-{d.isoformat()}-{machine_id}-{CODE[shift_type]}"


def build_shifts(
    cfg: dict[str, Any], machines: pd.DataFrame, operators: pd.DataFrame
) -> pd.DataFrame:
    rng = rng_for(cfg["seed"], "shifts")
    share = cfg["night_shift_share"]
    p_night = share / (1 - share)  # night shifts per day shift
    rows = []

    for site_id, site_machines in machines.groupby("site_id", sort=True):
        ops = operators[operators.site_id == site_id].reset_index(drop=True)
        last_end: dict[str, pd.Timestamp] = {}
        last_machine: dict[str, str] = {}
        rest_dow = {op: int(rng.integers(7)) for op in ops.operator_id}

        for day in range(cfg["days"]):
            d = cfg["start_date"] + timedelta(days=day)
            worked_today: set[str] = set()
            for shift_type in ("day", "night"):
                start_s, end_s = cfg["shifts"][shift_type]
                order = rng.permutation(len(site_machines))
                for m in site_machines.iloc[order].itertuples():
                    if (
                        shift_type == "night"
                        and not cfg["force_night_shift"]
                        and rng.random() >= p_night
                    ):
                        continue
                    start = ist_to_utc(d, start_s)
                    end = ist_to_utc(d, end_s)
                    if end <= start:
                        end += pd.Timedelta(days=1)

                    free = [
                        op not in worked_today
                        and (cfg["is_sample"] or rest_dow[op] != d.weekday())
                        and (op not in last_end or start - last_end[op] >= MIN_REST)
                        for op in ops.operator_id
                    ]
                    available = ops[free]
                    if available.empty:
                        continue
                    score = (
                        rng.random(len(available))
                        + (available.preferred_shift.to_numpy() == shift_type) * 1.0
                        + np.array(
                            [last_machine.get(op) == m.machine_id for op in available.operator_id]
                        )
                        * 0.5
                    )
                    op = available.operator_id.iloc[int(np.argmax(score))]
                    worked_today.add(op)
                    last_end[op] = end
                    last_machine[op] = m.machine_id
                    rows.append(
                        {
                            "shift_id": shift_id(d, m.machine_id, shift_type),
                            "site_id": site_id,
                            "operator_id": op,
                            "machine_id": m.machine_id,
                            "shift_type": shift_type,
                            "shift_date": d,
                            "start_time": start,
                            "end_time": end,
                        }
                    )
    return to_frame("shifts", rows).sort_values("start_time", kind="stable").reset_index(drop=True)

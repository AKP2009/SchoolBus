"""Stage 5: tasks. Plan each shift, then compute the actual duration with the hidden formula.

The planner sizes quantities from what a planner knows (base rate, material, slope, the
operator's skill) to fill ~95% of the shift. What it cannot know (personality, rain, night,
fatigue, machine health, noise) makes real durations differ, so some shifts overrun: a task
still running at shift end is 'delayed' with no actual_end, and tasks never reached are
'cancelled'. A machine breakdown (maintenance.py) ends the shift early at the failure minute,
with delay_reason 'machine breakdown'.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common import rng_for, to_frame
from weather import weather_at

BASE_RATE = {  # m3/h, or tons/h at a 1 km haul
    "excavator": {"dig": 110, "trench": 60, "load": 140},
    "wheel_loader": {"load": 180, "backfill": 150},
    "dozer": {"grade": 90, "backfill": 120},
    "articulated_truck": {"haul": 70},
}
F_MATERIAL = {"sand": 0.9, "topsoil": 0.9, "clay": 1.0, "gravel": 1.05, "rock": 1.4}
F_PERSONALITY = {"efficient": 0.92, "average": 1.0, "idler": 1.08, "aggressive": 0.9, "novice": 1.3}
MATERIALS = {
    "highway": (["clay", "sand", "gravel", "topsoil"], [0.3, 0.25, 0.25, 0.2]),
    "quarry": (["rock", "gravel", "sand"], [0.5, 0.35, 0.15]),
}
DELAY_REASONS = ["waiting for truck", "blocked access", "refuelling"]
PLAN_FILL = 0.95
DELAY_P = 0.05


def base_rate(machine_type: str, task_type: str, haul_distance_m: float | None) -> float:
    rate = BASE_RATE[machine_type][task_type]
    return rate * 1000 / haul_distance_m if haul_distance_m else rate


def build_tasks(
    cfg: dict[str, Any],
    shifts: pd.DataFrame,
    shift_state: pd.DataFrame,
    machines: pd.DataFrame,
    operators: pd.DataFrame,
    sites: pd.DataFrame,
    weather: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (tasks, task_work) where task_work has the worked interval and quantity left."""
    rng = rng_for(cfg["seed"], "tasks")
    lo, hi = cfg["tasks_per_shift"]
    mtype = machines.set_index("machine_id").machine_type
    ops = operators.set_index("operator_id")
    site_type = sites.set_index("site_id").site_type
    state = shift_state.set_index("shift_id")
    health, failure_ts = state.machine_health, state.failure_ts
    rows, work = [], []

    for s in shifts.itertuples():
        mt, op = mtype[s.machine_id], ops.loc[s.operator_id]
        shift_min = (s.end_time - s.start_time).total_seconds() / 60
        broke = pd.notna(failure_ts[s.shift_id])
        stop = failure_ts[s.shift_id] if broke else s.end_time
        stop_reason = "machine breakdown" if broke else None
        n = int(rng.integers(lo, hi + 1))
        gaps = rng.uniform(5, 15, n)
        planned = np.clip(
            rng.dirichlet(np.full(n, 3.0)) * (PLAN_FILL * shift_min - gaps.sum()), 20, 120
        )
        materials, probs = MATERIALS[site_type[s.site_id]]
        t = s.start_time

        for k in range(n):
            task_type = str(rng.choice(list(BASE_RATE[mt])))
            material = str(rng.choice(materials, p=probs))
            slope = round(float(rng.uniform(0, 12 if mt == "dozer" else 8)), 1)
            haul = float(round(rng.uniform(500, 2500) / 50) * 50) if task_type == "haul" else None
            rate = base_rate(mt, task_type, haul)
            f_mat, f_slope, f_skill = (
                F_MATERIAL[material],
                1 + 0.02 * slope,
                1.25 - 0.5 * op.skill_score,
            )
            quantity = max(5.0, round(planned[k] / 60 * rate / (f_mat * f_slope * f_skill) / 5) * 5)

            row: dict[str, Any] = {
                "task_id": f"T-{s.shift_id}-{k + 1}",
                "site_id": s.site_id,
                "shift_id": s.shift_id,
                "machine_id": s.machine_id,
                "operator_id": s.operator_id,
                "sequence_no": k + 1,
                "task_date": s.shift_date,
                "task_type": task_type,
                "material_type": material,
                "quantity": quantity,
                "unit": "tons" if task_type == "haul" else "m3",
                "terrain_slope_deg": slope,
                "haul_distance_m": haul,
                "priority": int(rng.choice([1, 2, 3], p=[0.2, 0.6, 0.2])),
                "scheduled_start": t + pd.Timedelta(minutes=float(gaps[k])),
                "status": "cancelled",
                "delay_reason": stop_reason or "not reached before shift end",
            }
            start = row["scheduled_start"]
            if start >= stop:
                rows.append(row)
                continue

            hours_into = (start - s.start_time).total_seconds() / 3600
            rain = float(weather_at(weather, s.site_id, start).rain_mm)
            duration = (
                quantity
                / rate
                * 60
                * f_skill
                * f_mat
                * (1 + 0.015 * min(rain, 20))
                * f_slope
                * (1.10 if s.shift_type == "night" else 1.0)
                * (1 + 0.02 * max(0.0, hours_into - 6))
                * (1 + 0.3 * (1 - health[s.shift_id]))
                * F_PERSONALITY[op.personality]
                * rng.lognormal(0, 0.08)
            )
            delay_reason = None
            if rng.random() < DELAY_P:
                duration += rng.uniform(15, 60)
                delay_reason = str(rng.choice(DELAY_REASONS))
            end = start + pd.Timedelta(minutes=duration)

            row["actual_start"] = start
            if end <= stop:
                row.update(
                    actual_end=end,
                    actual_duration_min=duration,
                    status="completed",
                    delay_reason=delay_reason,
                )
                done = 1.0
            else:
                row.update(
                    status="delayed", delay_reason=stop_reason or "not finished before shift end"
                )
                end = stop
                done = (end - start).total_seconds() / 60 / duration
            rows.append(row)
            work.append(
                {
                    "task_id": row["task_id"],
                    "shift_id": s.shift_id,
                    "work_start": start,
                    "work_end": end,
                    "quantity_left": round(quantity * (1 - done), 1),
                }
            )
            t = end

    return to_frame("tasks", rows), pd.DataFrame(work)

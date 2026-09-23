"""tasks: planned work per shift, durations from the hidden duration formula.

docs/synthetic_data.md "Tasks - hidden duration formula". The work order targets a base_min
(quantity / base_rate * 60) in 20-120 min; the realised duration then gets the full hidden
formula - skill, material, rain, slope, night, lateness, machine health, personality and
lognormal noise - which is the signal the models must recover.

History tasks are 'completed' with actuals; tasks on `future_days` shifts are 'scheduled' with
null actuals (live-demo material) and a chain based on the nominal target duration. No task
starts at or after the machine's failure time, so the failing shift simply runs fewer tasks.

Returns (tasks, truth). truth (task_id + every f_* factor + the noise) is for validation only:
never load it into the database, never use it as a model feature.
"""

from typing import Any

import numpy as np
import pandas as pd

BASE_RATE_M3_H = {
    "excavator": {"dig": 110.0, "trench": 60.0, "load": 140.0},
    "wheel_loader": {"load": 180.0, "backfill": 150.0},
    "dozer": {"grade": 90.0, "backfill": 120.0},
}
HAUL_TPH_AT_1KM = 70.0  # tons/h at 1 km, scaled by 1 km / haul_distance_m
HAUL_DISTANCE_M = (300.0, 2500.0)

TYPES_BY_MACHINE = {
    "excavator": ["dig", "trench", "load"],
    "wheel_loader": ["load", "backfill"],
    "dozer": ["grade", "backfill"],
    "articulated_truck": ["haul"],
}
MATERIAL_F = {"sand": 0.9, "topsoil": 0.9, "clay": 1.0, "gravel": 1.05, "rock": 1.4}
PERSONALITY_F = {
    "efficient": 0.92,
    "average": 1.0,
    "idler": 1.08,
    "aggressive": 0.9,
    "novice": 1.3,
}

TARGET_MIN = (20.0, 120.0)
SLOPE_GAMMA = (1.2, 2.5)  # k, theta; skewed low, clipped to 15 deg
FIRST_START_MIN = (10.0, 20.0)
GAP_MIN = (5.0, 15.0)
PRIORITY_P = (0.2, 0.55, 0.25)
DELAY_P = 0.05
DELAY_MIN = (15.0, 60.0)
DELAY_REASONS = ["waiting for truck", "blocked access", "refuelling"]


def _pick(options: list[str], rng: np.random.Generator) -> str:
    return options[int(rng.integers(len(options)))]


def build_tasks(
    cfg: dict[str, Any],
    machines: pd.DataFrame,
    operators: pd.DataFrame,
    shifts: pd.DataFrame,
    weather: pd.DataFrame,
    health_daily: pd.DataFrame,
    failures: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = int(cfg["days"])
    lo, hi = (int(v) for v in cfg["tasks_per_shift"])
    m_type = dict(zip(machines["machine_id"], machines["machine_type"]))
    skill = dict(zip(operators["operator_id"], operators["skill_score"]))
    pers = dict(zip(operators["operator_id"], operators["personality"]))
    rain_at = {(r.site_id, r.ts.floor("h")): float(r.rain_mm) for r in weather.itertuples()}
    health_at = {(r.machine_id, r.date): float(r.health_score) for r in health_daily.itertuples()}
    fail_ts = dict(zip(failures["shift_id"], failures["fail_ts"]))

    rows: list[dict[str, Any]] = []
    truth: list[dict[str, Any]] = []
    for s in shifts.itertuples():
        mtype = m_type[s.machine_id]
        is_future = s.day_idx >= days
        fail = fail_ts.get(s.shift_id)
        prev_end: pd.Timestamp | None = None
        prev_sched: pd.Timestamp | None = None
        for seq in range(1, int(rng.integers(lo, hi + 1)) + 1):
            ttype = _pick(TYPES_BY_MACHINE[mtype], rng)
            material = _pick(list(MATERIAL_F), rng)
            haul_m = round(float(rng.uniform(*HAUL_DISTANCE_M)), 1) if ttype == "haul" else None
            rate = (
                HAUL_TPH_AT_1KM * 1000.0 / haul_m
                if ttype == "haul"
                else BASE_RATE_M3_H[mtype][ttype]
            )
            unit = "tons" if ttype == "haul" else "m3"
            slope = round(float(min(rng.gamma(*SLOPE_GAMMA), 15.0)), 1)
            target = float(rng.uniform(*TARGET_MIN))
            priority = int(rng.choice((1, 2, 3), p=PRIORITY_P))
            # base_min (quantity / rate * 60) targets the drawn range; the full hidden
            # formula, including material and slope, then applies to the realised duration
            qty = round(target / 60.0 * rate, 2)
            delayed = rng.random() < DELAY_P
            delay_min = float(rng.uniform(*DELAY_MIN)) if delayed else 0.0
            reason = _pick(DELAY_REASONS, rng) if delayed else None

            task_id = f"T-{s.shift_id}-{seq}"
            if seq == 1:
                sched = (
                    s.start_time + pd.Timedelta(minutes=float(rng.uniform(*FIRST_START_MIN)))
                ).floor("s")
            elif is_future:
                sched = (
                    prev_sched + pd.Timedelta(minutes=target + float(rng.uniform(*GAP_MIN)))
                ).floor("s")
            else:
                sched = (prev_end + pd.Timedelta(minutes=float(rng.uniform(*GAP_MIN)))).floor("s")

            common = {
                "task_id": task_id,
                "site_id": s.site_id,
                "shift_id": s.shift_id,
                "machine_id": s.machine_id,
                "operator_id": s.operator_id,
                "sequence_no": seq,
                "task_date": s.shift_date,
                "task_type": ttype,
                "material_type": material,
                "quantity": qty,
                "unit": unit,
                "terrain_slope_deg": slope,
                "haul_distance_m": haul_m,
                "priority": priority,
                "scheduled_start": sched,
                "predicted_p10_min": None,
                "predicted_p50_min": None,
                "predicted_p90_min": None,
                "prediction_factors": None,
            }

            if is_future:
                prev_sched = sched
                rows.append(
                    {
                        **common,
                        "actual_start": None,
                        "actual_end": None,
                        "actual_duration_min": None,
                        "status": "scheduled",
                        "delay_reason": None,
                    }
                )
                continue

            if fail is not None and sched >= fail:
                break

            hours_in = (sched - s.start_time).total_seconds() / 3600.0
            f_skill = 1.25 - 0.5 * float(skill[s.operator_id])
            f_material = MATERIAL_F[material]
            # missing grid hour only for extreme overruns past the weather horizon
            f_rain = 1.0 + 0.015 * min(rain_at.get((s.site_id, sched.floor("h")), 0.0), 20.0)
            f_slope = 1.0 + 0.02 * slope
            f_night = 1.10 if s.shift_type == "night" else 1.0
            f_late = 1.0 + 0.02 * max(0.0, hours_in - 6.0)
            f_health = 1.0 + 0.3 * (1.0 - health_at[(s.machine_id, s.shift_date)])
            f_personality = PERSONALITY_F[pers[s.operator_id]]
            noise = float(rng.lognormal(0.0, 0.08))
            base_min = qty / rate * 60.0
            dur = round(
                base_min
                * f_skill
                * f_material
                * f_rain
                * f_slope
                * f_night
                * f_late
                * f_health
                * f_personality
                * noise
                + delay_min,
                1,
            )
            start = sched
            # whole seconds: a 0.1-min duration is an exact multiple of 6 s, keeps CSVs clean
            end = start + pd.Timedelta(seconds=round(dur * 60))
            prev_end = end
            rows.append(
                {
                    **common,
                    "actual_start": start,
                    "actual_end": end,
                    "actual_duration_min": dur,
                    "status": "completed",
                    "delay_reason": reason,
                }
            )
            truth.append(
                {
                    "task_id": task_id,
                    "base_min": base_min,
                    "f_skill": f_skill,
                    "f_material": f_material,
                    "f_rain": f_rain,
                    "f_slope": f_slope,
                    "f_night": f_night,
                    "f_late": f_late,
                    "f_health": f_health,
                    "f_personality": f_personality,
                    "noise": noise,
                    "delay_min": delay_min,
                    "duration_min": dur,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(truth)

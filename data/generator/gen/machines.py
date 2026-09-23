"""machines table.

Engine hours are simulated: we draw the hours at start_date here (`initial_engine_hours`,
helper column) and `maintenance.EngineClock` adds the shift hours. The final
`total_engine_hours`, `hours_since_service`, `status` and `health_score` are filled in by
`finalize_machines` once maintenance and health are known.
"""

from typing import Any

import numpy as np
import pandas as pd

MODEL_BY_TYPE = {
    "excavator": "Cat 320",
    "wheel_loader": "Cat 950",
    "dozer": "Cat D6",
    "articulated_truck": "Cat 745",
}
SERIAL_PREFIX = {
    "excavator": "EXA",
    "wheel_loader": "WLB",
    "dozer": "DZC",
    "articulated_truck": "ATD",
}
SERVICE_INTERVAL_H = 500.0
MAX_START_HOURS = 10_500.0  # leaves room for ~90 days of use while staying <= 12,000


def build_machines(cfg: dict[str, Any], rng: np.random.Generator) -> pd.DataFrame:
    ref_year = pd.Timestamp(cfg["start_date"]).year
    rows = []
    n = 0
    for site in cfg["sites"]:
        for mtype, count in cfg["machines"].items():
            for _ in range(count):
                n += 1
                year = int(rng.integers(2016, 2025))
                age = ref_year - year
                # older machines have more hours
                hours = 2000.0 + age * rng.uniform(650, 850) + rng.normal(0, 300)
                rows.append(
                    {
                        "machine_id": f"M{n:02d}",
                        "site_id": site["site_id"],
                        "machine_type": mtype,
                        "model": MODEL_BY_TYPE[mtype],
                        "year": year,
                        "service_interval_hours": SERVICE_INTERVAL_H,
                        "initial_engine_hours": round(
                            float(np.clip(hours, 2000, MAX_START_HOURS)), 1
                        ),
                        "initial_hours_since_service": round(
                            float(rng.uniform(0, SERVICE_INTERVAL_H)), 1
                        ),
                    }
                )
    df = pd.DataFrame(rows)
    serials = rng.choice(np.arange(10_000, 100_000), size=len(df), replace=False)
    df["serial_no"] = [f"{SERIAL_PREFIX[t]}{s:05d}" for t, s in zip(df["machine_type"], serials)]
    return df


def finalize_machines(
    machines: pd.DataFrame,
    now_utc: pd.Timestamp,
    clock: Any,
    maintenance: pd.DataFrame,
    down_windows: pd.DataFrame,
    health_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Fill the state columns as of `now_utc` (start of the first future day)."""
    df = machines.copy()
    total, since, status, health = [], [], [], []
    for m in df.itertuples():
        hours = clock.hours_at(m.machine_id, now_utc)
        svc = maintenance[
            (maintenance["machine_id"] == m.machine_id)
            & (maintenance["event_type"] == "scheduled_service")
            & (maintenance["event_date"] <= now_utc)
        ]
        if len(svc):
            since.append(round(hours - float(svc["engine_hours_at_event"].max()), 1))
        else:
            since.append(round(m.initial_hours_since_service + hours - m.initial_engine_hours, 1))
        total.append(round(hours, 1))
        w = down_windows[down_windows["machine_id"] == m.machine_id]
        is_down = ((w["start"] <= now_utc) & (w["end"] > now_utc)).any()
        status.append("down" if is_down else "active")
        h = health_daily[health_daily["machine_id"] == m.machine_id].sort_values("date")
        health.append(float(h["health_score"].iloc[-1]))
    df["total_engine_hours"] = total
    df["hours_since_service"] = since
    df["status"] = status
    df["health_score"] = health
    return df

"""machine_health_daily: generator-only helper (not a DB table).

health = baseline - age term - sum over failures of 0.4 * p^2, evaluated at the end of each
local day, where p = clip((t - (t_f - W)) / W, 0, 1) and t is engine hours at that moment.
A failure stops counting once its repair is done (reset after repair).
Used later by the task formula (f_health) and telemetry drift.
"""

from typing import Any

import numpy as np
import pandas as pd

from gen import LOCAL_TZ

AGE_PENALTY_PER_YEAR = 0.005
DRIFT_PENALTY = 0.4


def build_health_daily(
    cfg: dict[str, Any],
    machines: pd.DataFrame,
    failures: pd.DataFrame,
    clock: Any,
    rng: np.random.Generator,
) -> pd.DataFrame:
    ref_year = pd.Timestamp(cfg["start_date"]).year
    n_days = int(cfg["days"]) + int(cfg["future_days"])
    dates = pd.date_range(pd.Timestamp(cfg["start_date"]), periods=n_days, freq="D")
    day_end_utc = [
        (d + pd.Timedelta(days=1)).tz_localize(LOCAL_TZ).tz_convert("UTC") for d in dates
    ]

    rows = []
    for m in machines.itertuples():
        baseline = float(rng.uniform(0.9, 1.0)) - AGE_PENALTY_PER_YEAR * (ref_year - m.year)
        mf = failures[failures["machine_id"] == m.machine_id]
        for date, tau in zip(dates, day_end_utc):
            t = clock.hours_at(m.machine_id, tau)
            drift = 0.0
            for f in mf.itertuples():
                if tau >= f.repair_ts:
                    continue
                w = f.drift_window_hours
                p = float(np.clip((t - (f.engine_hours_at_failure - w)) / w, 0.0, 1.0))
                drift += DRIFT_PENALTY * p**2
            rows.append(
                {
                    "machine_id": m.machine_id,
                    "date": date.date(),
                    "health_score": round(float(np.clip(baseline - drift, 0.0, 1.0)), 3),
                }
            )
    return pd.DataFrame(rows)

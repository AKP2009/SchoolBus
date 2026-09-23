"""Feature builder for Model 2 — task time estimation (docs/models.md §2).

One module for BOTH training and the backend, so the two can never drift apart.

Leakage rules baked in (docs/models.md "General rules": time-based evaluation, no future):
- `operator_avg_time_ratio_30d` uses only completed, non-delayed tasks with task_date in
  [d - 30, d - 1] — strictly earlier dates than the row being featurized.
- the `standard_min` fallback (per-task_type expanding median of duration per unit quantity)
  is likewise computed from strictly earlier dates only.
- no operator_id, personality, delay_reason, actual_*, anomaly_* or tasks_truth column is
  ever part of the output. The training target is joined by the trainer, not carried here.

`standard_min` is returned as an extra (non-feature) column so the ratio can be recomputed
later: pass a tasks frame that already carries a real `standard_min` column and the ratio is
computed against it instead of the fallback.
"""

import datetime as dt

import numpy as np
import pandas as pd

# Fixed category lists = the DB enums in supabase/migrations/001_init.sql, in enum order.
TASK_TYPES = ["dig", "trench", "load", "haul", "grade", "backfill"]
MATERIAL_TYPES = ["clay", "sand", "gravel", "rock", "topsoil"]
MACHINE_TYPES = ["excavator", "wheel_loader", "dozer", "articulated_truck"]
UNITS = ["m3", "tons"]
SHIFT_TYPES = ["day", "night"]

CATEGORICAL = {
    "task_type": TASK_TYPES,
    "material_type": MATERIAL_TYPES,
    "machine_type": MACHINE_TYPES,
    "unit": UNITS,
    "shift_type": SHIFT_TYPES,
}

STANDARD_FEATURES = [
    "task_type",
    "material_type",
    "machine_type",
    "unit",
    "quantity",
    "log_quantity",
    "terrain_slope_deg",
    "haul_distance_m",
    "machine_health",
    "temp_c",
    "rain_mm",
    "wind_kmh",
    "visibility_m",
    "dust_index",
    "shift_type",
    "hours_into_shift",
    "day_of_week",
]
PERSONAL_FEATURES = STANDARD_FEATURES + [
    "skill_score",
    "experience_years",
    "certification_level",
    "operator_avg_time_ratio_30d",
]

FORBIDDEN = (
    "personality",
    "operator_id",
    "delay_reason",
    "anomaly_label",
    "anomaly_type",
    # tasks_truth.csv columns
    "base_min",
    "f_skill",
    "f_material",
    "f_rain",
    "f_slope",
    "f_night",
    "f_late",
    "f_health",
    "f_personality",
    "noise",
    "delay_min",
    "duration_min",
)

RATIO_WINDOW_DAYS = 30
RATIO_MIN_TASKS = 3


def _as_utc(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return pd.to_datetime(series, utc=True, format="ISO8601")


def _day_int(series: pd.Series) -> np.ndarray:
    return pd.to_datetime(series).dt.date.map(dt.date.toordinal).to_numpy()


def _fallback_standard_min(df: pd.DataFrame, pool: pd.DataFrame) -> pd.Series:
    """Per task_type, expanding median of duration per unit quantity over pool tasks with
    strictly earlier dates, scaled by the row's quantity. NaN before any earlier data."""
    out = pd.Series(np.nan, index=df.index, name="standard_min")
    if pool.empty:
        return out
    p = pool.assign(day=_day_int(pool["task_date"]))
    p = p.assign(per_unit=p["actual_duration_min"] / p["quantity"])
    med: dict[tuple[str, int], float] = {}
    for tt, g in p.groupby("task_type", sort=False):
        g = g.sort_values("day")
        days = g["day"].to_numpy()
        vals = (g["actual_duration_min"] / g["quantity"]).to_numpy()
        for d in np.unique(days):
            k = int(np.searchsorted(days, d, side="left"))
            if k:
                med[(str(tt), int(d))] = float(np.median(vals[:k]))
    day = _day_int(df["task_date"])
    vals = [
        med.get((str(tt), int(d)), np.nan) * q
        for tt, d, q in zip(df["task_type"], day, df["quantity"])
    ]
    return pd.Series(vals, index=df.index, name="standard_min")


def _ratio_pool(tasks: pd.DataFrame, history: pd.DataFrame | None) -> pd.DataFrame:
    """Completed, non-delayed tasks that may inform the rolling ratio. `history` (optional)
    supplies additional completed tasks, e.g. everything before a live prediction batch."""
    parts = []
    for src in (tasks, history):
        if src is None or "actual_duration_min" not in src.columns:
            continue
        p = src[src["actual_duration_min"].notna()]
        if "delay_reason" in p.columns:
            p = p[p["delay_reason"].isna()]
        if "status" in p.columns:
            p = p[p["status"] == "completed"]
        if p.empty:
            continue
        keep = [
            c
            for c in (
                "task_id",
                "operator_id",
                "task_type",
                "task_date",
                "quantity",
                "actual_duration_min",
                "standard_min",
            )
            if c in p.columns
        ]
        parts.append(p[keep])
    if not parts:
        return pd.DataFrame()
    pool = pd.concat(parts, ignore_index=True)
    if "task_id" in pool.columns:
        pool = pool.drop_duplicates(subset="task_id", keep="first")
    if "standard_min" not in pool.columns:
        pool["standard_min"] = _fallback_standard_min(pool, pool).to_numpy()
    return pool


def _operator_ratio_30d(df: pd.DataFrame, pool: pd.DataFrame) -> pd.Series:
    """Per (operator_id, task_type): mean of actual/standard over pool tasks with task_date
    in [d - 30, d - 1]; NaN when fewer than RATIO_MIN_TASKS qualify."""
    out = np.full(len(df), np.nan)
    if pool.empty:
        return pd.Series(out, index=df.index, name="operator_avg_time_ratio_30d")
    p = pool.assign(
        day=_day_int(pool["task_date"]),
        ratio=pool["actual_duration_min"] / pool["standard_min"],
    )
    p = p[np.isfinite(p["ratio"])]
    groups = {
        (str(op), str(tt)): (g["day"].to_numpy(), g["ratio"].to_numpy())
        for (op, tt), g in p.groupby(["operator_id", "task_type"], sort=False)
    }
    keys = list(
        zip(df["operator_id"].astype(str), df["task_type"].astype(str), _day_int(df["task_date"]))
    )
    cache: dict[tuple, float] = {}
    for i, key in enumerate(keys):
        if key in cache:
            out[i] = cache[key]
            continue
        _, _, d = key
        arr = groups.get((key[0], key[1]))
        val = np.nan
        if arr is not None:
            days, vals = arr
            lo = int(np.searchsorted(days, d - RATIO_WINDOW_DAYS, side="left"))
            hi = int(np.searchsorted(days, d, side="left"))
            if hi - lo >= RATIO_MIN_TASKS:
                val = float(vals[lo:hi].mean())
        cache[key] = val
        out[i] = val
    return pd.Series(out, index=df.index, name="operator_avg_time_ratio_30d")


def build_features(
    tasks: pd.DataFrame,
    shifts: pd.DataFrame,
    operators: pd.DataFrame,
    machines: pd.DataFrame,
    weather: pd.DataFrame,
    machine_health_daily: pd.DataFrame,
    history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Feature frame for task time estimation, one row per task.

    Weather is joined at the hour containing `scheduled_start` for the task's site; operator
    and machine attributes and the machine's daily health score are joined by id. All joins
    are plan-time information only. `history` (optional) adds completed tasks to the pool
    for `operator_avg_time_ratio_30d`; with None the pool is `tasks` itself, and the
    strictly-earlier-date rule makes the feature leak-free for training.
    """
    df = tasks.copy()
    df["scheduled_start"] = _as_utc(df["scheduled_start"])
    df["task_date"] = df["task_date"].astype(str)

    we = weather.copy()
    we["hour"] = _as_utc(we["ts"]).dt.floor("h")
    df["hour"] = df["scheduled_start"].dt.floor("h")
    df = df.merge(
        we[["site_id", "hour", "temp_c", "rain_mm", "wind_kmh", "visibility_m", "dust_index"]],
        on=["site_id", "hour"],
        how="left",
    ).drop(columns="hour")

    df = df.merge(
        operators[["operator_id", "skill_score", "experience_years", "certification_level"]],
        on="operator_id",
        how="left",
    )
    df = df.merge(machines[["machine_id", "machine_type"]], on="machine_id", how="left")

    mh = machine_health_daily.copy()
    mh = mh.rename(columns={"health_score": "machine_health", "date": "task_date"})
    mh["task_date"] = mh["task_date"].astype(str)
    df = df.merge(
        mh[["machine_id", "task_date", "machine_health"]],
        on=["machine_id", "task_date"],
        how="left",
    )

    sh = shifts.copy()
    sh["start_time"] = _as_utc(sh["start_time"])
    df = df.merge(sh[["shift_id", "shift_type", "start_time"]], on="shift_id", how="left")
    df["hours_into_shift"] = (df["scheduled_start"] - df["start_time"]).dt.total_seconds() / 3600
    df["day_of_week"] = pd.to_datetime(df["task_date"]).dt.dayofweek.astype(float)
    df["log_quantity"] = np.log(df["quantity"].astype(float))

    pool = _ratio_pool(tasks, history)
    if "standard_min" in tasks.columns:
        df["standard_min"] = df["standard_min"].astype(float)
    else:
        df["standard_min"] = _fallback_standard_min(df, pool).to_numpy()
    df["operator_avg_time_ratio_30d"] = _operator_ratio_30d(df, pool).to_numpy()

    for col, cats in CATEGORICAL.items():
        df[col] = pd.Categorical(df[col], categories=cats)

    out = df[["task_id", "task_date", *PERSONAL_FEATURES, "standard_min"]].reset_index(drop=True)
    return out

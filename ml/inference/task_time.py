"""Task time estimation (models.md §2).

`predict_task_time(df)` takes one row per task with the columns in `INPUT_COLUMNS` and returns
the output JSON from models.md §2 (p10 / p50 / p90 minutes, SHAP factors in minutes, the
operator's usual time and the expected efficiency).

The feature table is built here too (`build_feature_table`), so the training notebook
(`ml/02_task_time.ipynb`) and the backend compute exactly the same features. Every function
selects columns explicitly. Hidden generator fields (`operators.personality`,
`data/output/truth/`) are never read.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "task_time"

QUANTILES = (0.1, 0.5, 0.9)
CATEGORICAL = ["task_type", "material_type", "machine_type", "unit", "shift_type"]
NUMERIC = [
    "quantity",
    "terrain_slope_deg",
    "haul_distance_m",
    "skill_score",
    "experience_years",
    "certification_level",
    "operator_avg_time_ratio",
    "health_score",
    "temp_c",
    "rain_mm",
    "wind_kmh",
    "visibility_m",
    "dust_index",
    "hours_into_shift",
    "day_of_week",
    "operator_fatigue_hour_avg",
]
FEATURE_COLUMNS = [*CATEGORICAL, *NUMERIC]
INPUT_COLUMNS = ["task_id", *FEATURE_COLUMNS]

RATIO_WINDOW = pd.Timedelta(days=30)  # operator_avg_time_ratio looks back this far
WEATHER_COLUMNS = ["temp_c", "rain_mm", "wind_kmh", "visibility_m", "dust_index"]
# Size of the job, not a reason it runs fast or slow: never shown as a factor.
NOT_A_FACTOR = {"quantity", "unit"}
TOP_N = 3

LABELS: dict[str, str] = {
    "quantity": "Quantity",
    "unit": "Unit",
    "terrain_slope_deg": "Slope",
    "haul_distance_m": "Haul distance",
    "skill_score": "Operator skill",
    "experience_years": "Operator experience",
    "certification_level": "Certification level",
    "operator_avg_time_ratio": "Operator's usual pace",
    "health_score": "Machine health",
    "temp_c": "Temperature",
    "rain_mm": "Rain",
    "wind_kmh": "Wind",
    "visibility_m": "Visibility",
    "dust_index": "Dust",
    "hours_into_shift": "Hours into shift",
    "day_of_week": "Day of week",
    "operator_fatigue_hour_avg": "Usual fatigue at this hour",
    "task_type": "Task",
    "material_type": "Material",
    "machine_type": "Machine",
    "shift_type": "Shift",
}


# ---------------------------------------------------------------------------------------------
# Baseline and operator history
# ---------------------------------------------------------------------------------------------
def fit_baseline(tasks: pd.DataFrame) -> dict[str, float]:
    """Median minutes per unit of quantity, per task_type (fit on training tasks only)."""
    t = tasks[["task_type", "quantity", "actual_duration_min"]].dropna()
    per_unit = t.actual_duration_min / t.quantity
    return {str(k): float(v) for k, v in per_unit.groupby(t.task_type).median().items()}


def baseline_minutes(df: pd.DataFrame, baseline: dict[str, float]) -> pd.Series:
    """The baseline prediction: median minutes per unit for the task type × quantity."""
    per_unit = df["task_type"].astype(str).map(baseline).astype(float)
    return per_unit * df["quantity"].astype(float)


def work_units(df: pd.DataFrame) -> pd.Series:
    """Amount of work: quantity, and for haul tasks tons × km (haul time scales with distance)."""
    q = df["quantity"].astype(float)
    km = pd.to_numeric(df["haul_distance_m"], errors="coerce") / 1000
    return q.where(df["task_type"].astype(str) != "haul", q * km)


def fit_reference(tasks: pd.DataFrame) -> dict[str, float]:
    """Median minutes per work unit, per task_type (train only). The yardstick for the
    operator's usual pace; unlike the baseline it accounts for haul distance."""
    t = tasks[["task_type", "quantity", "haul_distance_m", "actual_duration_min"]]
    t = t[t.actual_duration_min.notna()]
    per_unit = t.actual_duration_min / work_units(t)
    return {str(k): float(v) for k, v in per_unit.groupby(t.task_type).median().items()}


def reference_minutes(df: pd.DataFrame, reference: dict[str, float]) -> pd.Series:
    """Typical minutes for this amount of work of this task type."""
    return df["task_type"].astype(str).map(reference).astype(float) * work_units(df)


def _utc_ns(ts: pd.Series) -> np.ndarray:
    """int64 nanoseconds since epoch (UTC) for searchsorted on tz-aware timestamps."""
    return pd.to_datetime(ts, utc=True).astype("int64").to_numpy()


def operator_time_ratio(
    history: pd.DataFrame, targets: pd.DataFrame, reference: dict[str, float]
) -> pd.Series:
    """Mean actual / reference time ratio of the operator for the target's task_type.

    Uses only the operator's tasks that ended before the target's scheduled start and within
    `RATIO_WINDOW` of it, so a task never sees itself or the future. NaN without history.

    history: completed tasks (operator_id, task_type, quantity, haul_distance_m, actual_end,
    actual_duration_min). targets: operator_id, task_type, scheduled_start.
    Returns a Series aligned to targets.
    """
    cols = ["operator_id", "task_type", "quantity", "haul_distance_m", "actual_end"]
    h = history[[*cols, "actual_duration_min"]]
    h = h.dropna(subset=["operator_id", "actual_end", "actual_duration_min"]).copy()
    h["ratio"] = h.actual_duration_min / reference_minutes(h, reference)
    h = h.dropna(subset=["ratio"]).sort_values("actual_end", kind="stable")
    t = targets[["operator_id", "task_type", "scheduled_start"]]
    out = pd.Series(np.nan, index=targets.index, dtype=float)

    groups = {k: g for k, g in h.groupby(["operator_id", "task_type"])}
    for key, q in t.groupby(["operator_id", "task_type"]):
        g = groups.get(key)
        if g is None:
            continue
        end = _utc_ns(g.actual_end)
        cs = np.concatenate([[0.0], np.cumsum(g.ratio.to_numpy())])
        hi = np.searchsorted(end, _utc_ns(q.scheduled_start), side="left")  # ended before start
        lo = np.searchsorted(end, _utc_ns(q.scheduled_start - RATIO_WINDOW), side="left")
        n = hi - lo
        with np.errstate(invalid="ignore", divide="ignore"):
            out.loc[q.index] = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
    return out


# ---------------------------------------------------------------------------------------------
# Machine health and fatigue at task start
# ---------------------------------------------------------------------------------------------
def machine_health_at_shift_start(
    shifts: pd.DataFrame, machines: pd.DataFrame, maintenance_log: pd.DataFrame
) -> pd.Series:
    """Service-based machine health at each shift start, indexed by shift_id.

    health = clip(1 - 0.2 * hours_since_service / service_interval_hours, 0.3, 1), the part of
    the generator's health that the maintenance log shows. Engine hours are rebuilt per shift
    (full shift length, cut at a logged failure) and anchored to the end-of-run
    `machines.total_engine_hours`. The live backend reads `health_score` from
    `v_machine_health_latest` instead.
    """
    sh = shifts[["shift_id", "machine_id", "start_time", "end_time"]].copy()
    mlog = maintenance_log[["machine_id", "event_date", "engine_hours_at_event", "event_type"]]
    fails = mlog[mlog.event_type == "failure"]
    services = mlog[mlog.event_type == "scheduled_service"].sort_values("event_date")

    # A failure inside a shift cuts its engine hours at the failure time.
    cut = sh.merge(fails[["machine_id", "event_date"]], on="machine_id", how="left")
    cut = cut[(cut.event_date >= cut.start_time) & (cut.event_date < cut.end_time)]
    shift_end = sh.shift_id.map(cut.groupby("shift_id").event_date.min()).fillna(sh.end_time)
    sh["hours"] = (pd.to_datetime(shift_end, utc=True) - sh.start_time).dt.total_seconds() / 3600
    sh = sh.sort_values(["machine_id", "start_time"], kind="stable")
    total_end = sh.machine_id.map(machines.set_index("machine_id").total_engine_hours)
    worked_from_here = sh.groupby("machine_id").hours.transform(lambda x: x[::-1].cumsum()[::-1])
    sh["eh_start"] = total_end - worked_from_here

    sh = pd.merge_asof(
        sh.sort_values("start_time"),
        services[["machine_id", "event_date", "engine_hours_at_event"]].rename(
            columns={"event_date": "start_time"}
        ),
        on="start_time",
        by="machine_id",
        direction="backward",
    )
    interval = sh.machine_id.map(machines.set_index("machine_id").service_interval_hours)
    hss = (sh.eh_start - sh.engine_hours_at_event).clip(lower=0)
    health = np.clip(1 - 0.2 * hss / interval, 0.3, 1.0)
    return pd.Series(health.to_numpy(), index=sh.shift_id.to_numpy(), name="health_score")


def fatigue_hour_average(fatigue_log: pd.DataFrame, shifts: pd.DataFrame) -> pd.DataFrame:
    """Operator's mean fatigue_score per whole hour into shift, from earlier shifts only.

    Returns shift_id, hour_of_shift, operator_fatigue_hour_avg (NaN when the operator has no
    earlier shift that reached that hour).
    """
    f = fatigue_log[["ts", "operator_id", "shift_id", "fatigue_score"]]
    s = shifts[["shift_id", "start_time"]]
    f = f.merge(s, on="shift_id")
    f["hour_of_shift"] = ((f.ts - f.start_time).dt.total_seconds() // 3600).astype(int)
    per = (
        f.groupby(["operator_id", "shift_id", "start_time", "hour_of_shift"], as_index=False)
        .fatigue_score.agg(["sum", "count"])
        .sort_values("start_time", kind="stable")
    )
    g = per.groupby(["operator_id", "hour_of_shift"])
    prev_sum = g["sum"].cumsum() - per["sum"]
    prev_cnt = g["count"].cumsum() - per["count"]
    per["operator_fatigue_hour_avg"] = (prev_sum / prev_cnt.replace(0, np.nan)).astype(float)
    return per[["shift_id", "hour_of_shift", "operator_fatigue_hour_avg"]]


# ---------------------------------------------------------------------------------------------
# Feature table
# ---------------------------------------------------------------------------------------------
def build_feature_table(
    tasks: pd.DataFrame,
    weather: pd.DataFrame,
    operators: pd.DataFrame,
    machines: pd.DataFrame,
    shifts: pd.DataFrame,
    fatigue_log: pd.DataFrame,
    maintenance_log: pd.DataFrame,
    reference: dict[str, float],
    history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per task: keys, FEATURE_COLUMNS and (if present) actual_duration_min / status.

    Timestamps must be tz-aware UTC. `history` = completed tasks for operator_avg_time_ratio
    (default: the completed tasks in `tasks`); `reference` from `fit_reference` on train.
    """
    t = tasks[
        [
            "task_id",
            "site_id",
            "shift_id",
            "machine_id",
            "operator_id",
            "task_date",
            "task_type",
            "material_type",
            "quantity",
            "unit",
            "terrain_slope_deg",
            "haul_distance_m",
            "scheduled_start",
            *[c for c in ("actual_end", "actual_duration_min", "status") if c in tasks],
        ]
    ].copy()

    # Weather for the hour containing the scheduled start.
    w = weather[["site_id", "ts", *WEATHER_COLUMNS]].sort_values("ts")
    t = pd.merge_asof(
        t.sort_values("scheduled_start"),
        w.rename(columns={"ts": "scheduled_start"}),
        on="scheduled_start",
        by="site_id",
        direction="backward",
    )
    op = operators[["operator_id", "skill_score", "experience_years", "certification_level"]]
    t = t.merge(op, on="operator_id", how="left")
    t = t.merge(machines[["machine_id", "machine_type"]], on="machine_id", how="left")
    t = t.merge(shifts[["shift_id", "shift_type", "start_time"]], on="shift_id", how="left")

    t["hours_into_shift"] = (t.scheduled_start - t.start_time).dt.total_seconds() / 3600
    t["day_of_week"] = pd.to_datetime(t.task_date).dt.dayofweek
    health = machine_health_at_shift_start(shifts, machines, maintenance_log)
    t["health_score"] = t.shift_id.map(health)

    t["hour_of_shift"] = t.hours_into_shift.clip(lower=0).astype(int)
    fat = fatigue_hour_average(fatigue_log, shifts)
    t = t.merge(fat, on=["shift_id", "hour_of_shift"], how="left")

    hist = history if history is not None else tasks
    if "status" in hist:
        hist = hist[hist.status == "completed"]
    t["operator_avg_time_ratio"] = operator_time_ratio(hist, t, reference)
    return (
        t.drop(columns=["start_time", "hour_of_shift"])
        .sort_values("task_id")
        .reset_index(drop=True)
    )


def to_model_frame(df: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    """FEATURE_COLUMNS only, categoricals with the training levels (unseen level -> NaN)."""
    X = df[FEATURE_COLUMNS].copy()
    for c in CATEGORICAL:
        X[c] = pd.Categorical(X[c].astype("string"), categories=categories[c])
    for c in NUMERIC:
        X[c] = pd.to_numeric(X[c], errors="coerce").astype(float)
    return X


# ---------------------------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_artifacts() -> dict[str, Any]:
    """Loads the three quantile models, encoders and config once per process."""
    feats = json.loads((ARTIFACT_DIR / "feature_list.json").read_text())
    if feats != FEATURE_COLUMNS:
        raise ValueError("feature_list.json does not match FEATURE_COLUMNS in task_time.py")
    enc = json.loads((ARTIFACT_DIR / "encoders.json").read_text())
    cfg = json.loads((ARTIFACT_DIR / "config.json").read_text())
    models = {q: joblib.load(ARTIFACT_DIR / f"lgbm_p{round(q * 100)}.joblib") for q in QUANTILES}
    return {
        "models": models,
        "categories": enc["categories"],
        "reference": enc["reference"],
        "interval_offset": float(cfg["interval_offset_log"]),
    }


def _explainer() -> Any:
    if not hasattr(_explainer, "cache"):
        import shap

        _explainer.cache = shap.TreeExplainer(load_artifacts()["models"][0.5])  # type: ignore[attr-defined]
    return _explainer.cache  # type: ignore[attr-defined]


def factor_name(feature: str, value: Any) -> tuple[str, str]:
    """(machine key, human label), e.g. ('material_type=rock', 'Material: rock')."""
    if feature in CATEGORICAL:
        v = "unknown" if pd.isna(value) else str(value)
        label = f"{v.capitalize()} shift" if feature == "shift_type" else f"{LABELS[feature]}: {v}"
        return f"{feature}={v}", label
    return feature, LABELS.get(feature, feature)


def explain(X: pd.DataFrame, log_p50: np.ndarray, top_n: int = TOP_N) -> list[list[dict]]:
    """Top SHAP factors per row in minutes.

    SHAP values are in log1p space. A factor's minutes = p50 minus the p50 without that
    feature's contribution: expm1(log_p50) - expm1(log_p50 - shap).
    """
    sv = np.asarray(_explainer().shap_values(X))
    p50 = np.expm1(log_p50)
    out = []
    for i in range(len(X)):
        impact = p50[i] - np.expm1(log_p50[i] - sv[i])
        order = np.argsort(-np.abs(impact))
        row = []
        for j in order:
            feat = FEATURE_COLUMNS[j]
            if feat in NOT_A_FACTOR:
                continue
            key, label = factor_name(feat, X.iloc[i, j])
            row.append({"feature": key, "label": label, "impact_min": round(float(impact[j]), 1)})
            if len(row) == top_n:
                break
        out.append(row)
    return out


def calibrate(raw: np.ndarray, offset: float) -> np.ndarray:
    """Sorts raw log1p quantiles, then widens p10 / p90 by the conformal offset.

    offset comes from conformalized quantile regression on the validation days (config.json).
    Sorting first enforces p10 <= p50 <= p90; a non-negative offset keeps that order.
    """
    q = np.sort(raw, axis=1)
    q[:, 0] -= offset
    q[:, 2] += offset
    return np.sort(q, axis=1)


def predict_quantiles(X: pd.DataFrame) -> np.ndarray:
    """(n, 3) array of calibrated p10, p50, p90 in log1p space, p10 <= p50 <= p90."""
    art = load_artifacts()
    raw = np.column_stack([art["models"][q].predict(X) for q in QUANTILES])
    return calibrate(raw, art["interval_offset"])


def predict_task_time(df: pd.DataFrame, explain_factors: bool = True) -> list[dict[str, Any]]:
    """Predicts p10 / p50 / p90 minutes per task (output JSON of models.md §2).

    df: one row per task with INPUT_COLUMNS (build them with `build_feature_table`). Missing
    numeric values are allowed (LightGBM routes NaN); missing columns raise KeyError.
    operator_avg_min = operator_avg_time_ratio × reference minutes for the task (None without
    history); expected_efficiency = p50 / operator_avg_min (< 1 means faster than usual).
    """
    art = load_artifacts()
    missing = [c for c in INPUT_COLUMNS if c not in df]
    if missing:
        raise KeyError(f"missing columns: {missing}")
    X = to_model_frame(df, art["categories"])
    q = predict_quantiles(X)
    mins = np.expm1(q)
    factors = explain(X, q[:, 1]) if explain_factors else [[] for _ in range(len(X))]
    usual = (X["operator_avg_time_ratio"] * reference_minutes(df, art["reference"])).to_numpy()

    out = []
    for i, task_id in enumerate(df["task_id"].astype(str)):
        has_usual = bool(np.isfinite(usual[i]) and usual[i] > 0)
        out.append(
            {
                "task_id": task_id,
                "p10_min": round(float(mins[i, 0]), 1),
                "p50_min": round(float(mins[i, 1]), 1),
                "p90_min": round(float(mins[i, 2]), 1),
                "factors": factors[i],
                "operator_avg_min": round(float(usual[i]), 1) if has_usual else None,
                "expected_efficiency": (
                    round(float(mins[i, 1] / usual[i]), 2) if has_usual else None
                ),
            }
        )
    return out

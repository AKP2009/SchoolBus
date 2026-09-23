"""Model 2 inference — task time predictions, efficiency summaries, training recommendations.

Loads the artifacts from ml/artifacts/task_time/v1 once (lazy singleton) and reuses
ml/task_time/features.py, so training and serving share the exact same feature builder.

Definitions:
- efficiency (per completed task) = standard_min / actual_min, only for completed tasks
  with delay_reason null.
- avg_efficiency: duration-weighted mean over [as_of-29, as_of];
  prev_efficiency: the same over [as_of-59, as_of-30];
  last_task_efficiency: most recent completed non-delayed task;
  trend: 'up' if avg >= 1.03*prev, 'down' if avg <= 0.97*prev, else 'flat';
  fleet_median: site median task efficiency for the task_type over the avg window.
  Any windowed metric with n < 3 is null.
- expected_efficiency (upcoming task) = standard_min / personal p50.
- as_of defaults to the latest task_date that has a completed task (the synthetic data
  ends before today).

Frames may be injected via `reload_state(...)` (backend/DB) or loaded from the generator's
CSVs under data/output.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import shap
from pydantic import BaseModel

from ml.inference.schemas import (
    EfficiencyRow,
    Factor,
    Recommendation,
    TaskTimeContext,
    TaskTimePrediction,
)
from ml.task_time.features import PERSONAL_FEATURES, STANDARD_FEATURES, build_features

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data/output"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "ml/artifacts/task_time/v1"

TECH_MODULE = {
    "dig": "TM-TECH-DIG-01",
    "trench": "TM-TECH-TRENCH-01",
    "load": "TM-TECH-LOAD-01",
    "haul": "TM-TECH-HAUL-01",
    "grade": "TM-TECH-GRADE-01",
    "backfill": "TM-TECH-BACKFILL-01",
}
EFFICIENCY_TRIGGER = 0.83
DROPPING_RATIO = 0.90
MIN_AVG_TASKS = 5
MAX_OPEN_RECS = 2
WINDOW = 30

FACTOR_LABELS = {
    "rain_mm": "Rain",
    "terrain_slope_deg": "Slope",
    "machine_health": "Machine health",
    "temp_c": "Temperature",
    "wind_kmh": "Wind",
    "visibility_m": "Visibility",
    "dust_index": "Dust",
    "hours_into_shift": "Hours into shift",
    "quantity": "Quantity",
    "log_quantity": "Quantity",
    "haul_distance_m": "Haul distance",
    "skill_score": "Skill",
    "experience_years": "Experience",
    "certification_level": "Certification",
    "day_of_week": "Day of week",
    "operator_avg_time_ratio_30d": "Operator's recent pace",
}


def _factor_label(feature: str, row: pd.Series) -> str:
    if feature in ("material_type", "task_type", "machine_type"):
        return f"{feature.split('_')[0].capitalize()}: {row[feature]}"
    if feature == "unit":
        return f"Unit: {row[feature]}"
    if feature == "shift_type":
        return "Night shift" if str(row[feature]) == "night" else "Day shift"
    return FACTOR_LABELS.get(feature, feature.replace("_", " ").title())


@lru_cache(maxsize=1)
def _models(artifact_dir: str) -> dict[str, Any]:
    """Lazy singleton: load the trained artifacts (and the standard SHAP explainer) once."""
    out = Path(artifact_dir)
    standard = joblib.load(out / "standard.joblib")
    return {
        "standard": standard,
        "p10": joblib.load(out / "p10.joblib"),
        "p50": joblib.load(out / "p50.joblib"),
        "p90": joblib.load(out / "p90.joblib"),
        "shap": shap.TreeExplainer(standard),
        "conformal_offset": json.loads((out / "conformal_offset.json").read_text())["offset_min"],
    }


@dataclass
class _State:
    data_dir: Path
    frames: dict[str, pd.DataFrame] | None = None
    standard_frame: pd.DataFrame | None = None
    efficiency: pd.DataFrame | None = None


_STATE: _State | None = None


def reload_state(
    data_dir: Path | None = None,
    *,
    tasks: pd.DataFrame | None = None,
    shifts: pd.DataFrame | None = None,
    operators: pd.DataFrame | None = None,
    machines: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    machine_health_daily: pd.DataFrame | None = None,
) -> None:
    """Reset the singleton. Injected frames win over CSV loading."""
    global _STATE
    _STATE = _State(data_dir=Path(data_dir) if data_dir else DEFAULT_DATA_DIR)
    if tasks is not None:
        _STATE.frames = {
            "tasks": tasks,
            "shifts": shifts,
            "operators": operators,
            "machines": machines,
            "weather": weather,
            "machine_health_daily": machine_health_daily,
        }


def _frames() -> dict[str, pd.DataFrame]:
    if _STATE is None:
        reload_state()
    assert _STATE is not None
    if _STATE.frames is None:
        rd = lambda name: pd.read_csv(_STATE.data_dir / f"{name}.csv")
        f = {
            name: rd(name)
            for name in (
                "tasks",
                "shifts",
                "operators",
                "machines",
                "weather",
                "machine_health_daily",
            )
        }
        f["weather"]["ts"] = pd.to_datetime(f["weather"]["ts"], utc=True, format="ISO8601")
        f["shifts"]["start_time"] = pd.to_datetime(
            f["shifts"]["start_time"], utc=True, format="ISO8601"
        )
        f["tasks"]["scheduled_start"] = pd.to_datetime(
            f["tasks"]["scheduled_start"], utc=True, format="ISO8601"
        )
        _STATE.frames = f
    return _STATE.frames


def _context_frames() -> TaskTimeContext:
    f = _frames()
    return TaskTimeContext(
        shifts=f["shifts"],
        operators=f["operators"],
        machines=f["machines"],
        weather=f["weather"],
        machine_health_daily=f["machine_health_daily"],
    )


# ---------------------------------------------------------------------------------------------
# Pure math (unit-tested directly; no global state)
# ---------------------------------------------------------------------------------------------
def _efficiency_rows(tasks: pd.DataFrame, standard_min: Any) -> pd.DataFrame:
    """Pure: completed, non-delayed tasks -> per-task efficiency = standard_min / actual."""
    t = tasks.copy()
    t["standard_min"] = np.asarray(standard_min, dtype=float)
    ok = t["actual_duration_min"].notna()
    if "status" in t.columns:
        ok &= t["status"] == "completed"
    if "delay_reason" in t.columns:
        ok &= t["delay_reason"].isna()
    t = t[ok].copy()
    t["day"] = pd.to_datetime(t["task_date"]).map(pd.Timestamp.toordinal)
    t["efficiency"] = t["standard_min"] / t["actual_duration_min"]
    cols = [
        "operator_id",
        "task_type",
        "site_id",
        "task_date",
        "day",
        "actual_duration_min",
        "standard_min",
        "efficiency",
    ]
    return t[cols].reset_index(drop=True)


def _window_eff(df: pd.DataFrame) -> tuple[float | None, int]:
    """Duration-weighted mean efficiency (= sum standard / sum actual) and n."""
    if df.empty:
        return None, 0
    return float(df["standard_min"].sum() / df["actual_duration_min"].sum()), len(df)


def _summary_from(
    eff: pd.DataFrame,
    operator_id: str,
    op_site: str,
    task_type: str | None,
    as_of: str,
) -> list[EfficiencyRow]:
    d = int(pd.Timestamp(as_of).toordinal())
    own = eff[(eff["operator_id"] == operator_id) & (eff["day"] <= d)]
    types = [task_type] if task_type else sorted(own["task_type"].unique())
    rows = []
    for tt in types:
        g = own[own["task_type"] == tt]
        avg, n_avg = _window_eff(g[g["day"] >= d - WINDOW + 1])
        prev, n_prev = _window_eff(g[(g["day"] >= d - 2 * WINDOW + 1) & (g["day"] <= d - WINDOW)])
        avg = avg if n_avg >= 3 else None  # any metric with n < 3 is null
        prev = prev if n_prev >= 3 else None
        last = float(g.sort_values("day").iloc[-1]["efficiency"]) if len(g) else None
        fleet = eff[
            (eff["site_id"] == op_site)
            & (eff["task_type"] == tt)
            & (eff["day"] >= d - WINDOW + 1)
            & (eff["day"] <= d)
        ]
        fleet_median = float(fleet["efficiency"].median()) if len(fleet) >= 3 else None
        trend = None
        if avg is not None and prev is not None:
            trend = "up" if avg >= 1.03 * prev else "down" if avg <= 0.97 * prev else "flat"
        rows.append(
            EfficiencyRow(
                task_type=str(tt),
                avg_efficiency=avg,
                prev_efficiency=prev,
                last_task_efficiency=last,
                trend=trend,
                fleet_median=fleet_median,
                n_tasks=n_avg,
            )
        )
    return rows


def recommend_from(
    summary: list[EfficiencyRow], operator_id: str, open_recs: list[Any] | None = None
) -> list[Recommendation]:
    """Triggers per docs/models.md §10: (avg < 0.83 and n >= 5) or (avg <= 0.90*prev with
    both windows >= 3). Worst gap first, max 2 open, no duplicate module."""
    open_ids: list[str] = []
    n_open = 0
    for r in open_recs or []:
        if isinstance(r, BaseModel):
            open_ids.append(r.module_id)
            if r.status in ("pending", "accepted"):
                n_open += 1
        else:
            open_ids.append(str(r))
            n_open += 1
    slots = max(0, MAX_OPEN_RECS - n_open)
    triggers = []
    for row in summary:
        if row.avg_efficiency is None:
            continue
        low = row.avg_efficiency < EFFICIENCY_TRIGGER and row.n_tasks >= MIN_AVG_TASKS
        dropping = (
            row.prev_efficiency is not None
            and row.avg_efficiency <= DROPPING_RATIO * row.prev_efficiency
        )
        if not (low or dropping):
            continue
        gap = (
            row.fleet_median if row.fleet_median is not None else EFFICIENCY_TRIGGER
        ) - row.avg_efficiency
        triggers.append((gap, row))
    triggers.sort(key=lambda t: (-t[0], t[1].task_type))
    out = []
    for gap, row in triggers:
        if len(out) >= slots:
            break
        module_id = TECH_MODULE[row.task_type]
        if module_id in open_ids:
            continue
        site_txt = f"{row.fleet_median:.2f}" if row.fleet_median is not None else "n/a"
        out.append(
            Recommendation(
                operator_id=operator_id,
                task_type=row.task_type,
                module_id=module_id,
                reason=(
                    f"{row.task_type.capitalize()}: efficiency {row.avg_efficiency:.2f}"
                    f" vs site {site_txt} over {row.n_tasks} tasks"
                ),
                trigger_metric="efficiency",
                trigger_value=row.avg_efficiency,
                sim_module_id=None,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# Public API (singleton-backed)
# ---------------------------------------------------------------------------------------------
def _state() -> _State:
    if _STATE is None:
        reload_state()
    assert _STATE is not None
    return _STATE


def _ensure_standard(state: _State) -> pd.DataFrame:
    """Featurize all known tasks once; standard_min = the standard p50 prediction."""
    if state.standard_frame is None:
        f = _frames()
        feats = build_features(
            f["tasks"],
            f["shifts"],
            f["operators"],
            f["machines"],
            f["weather"],
            f["machine_health_daily"],
        )
        m = _models(DEFAULT_ARTIFACT_DIR.as_posix())
        feats["standard_min"] = np.expm1(m["standard"].predict(feats[STANDARD_FEATURES]))
        state.standard_frame = feats
    return state.standard_frame


def predict(tasks_df: pd.DataFrame, context: TaskTimeContext) -> list[TaskTimePrediction]:
    """Predict p10/p50/p90 (conformal-widened) for the given tasks. Personal features use
    the standard model's own predictions as standard_min; the 30d operator ratio pools the
    singleton's completed tasks unless context.history is given."""
    m = _models(DEFAULT_ARTIFACT_DIR.as_posix())
    history = context.history if context.history is not None else _frames()["tasks"]
    all_t = pd.concat([tasks_df, history], ignore_index=True)

    feats = build_features(
        all_t,
        context.shifts,
        context.operators,
        context.machines,
        context.weather,
        context.machine_health_daily,
    )
    z_std = np.asarray(m["standard"].predict(feats[STANDARD_FEATURES]))  # log1p space
    with_std = all_t.copy()
    with_std["standard_min"] = np.expm1(z_std)
    feats = build_features(
        with_std,
        context.shifts,
        context.operators,
        context.machines,
        context.weather,
        context.machine_health_daily,
    )
    X = feats[PERSONAL_FEATURES]
    q = np.sort(
        np.column_stack(
            [
                np.expm1(m["p10"].predict(X)),
                np.expm1(m["p50"].predict(X)),
                np.expm1(m["p90"].predict(X)),
            ]
        ),
        axis=1,
    )
    offset = m["conformal_offset"]

    n = len(tasks_df)
    feats_own = feats.iloc[:n].reset_index(drop=True)
    src = tasks_df.reset_index(drop=True)  # identity columns live on the input frame
    shap_values = pd.DataFrame(
        np.asarray(m["shap"].shap_values(feats_own[STANDARD_FEATURES])), columns=STANDARD_FEATURES
    )

    f = _frames()
    eff = _efficiency_rows(f["tasks"], _ensure_standard(_state())["standard_min"])
    op_sites = f["operators"].set_index("operator_id")["site_id"]

    out = []
    for i in range(n):
        row = feats_own.iloc[i]
        src_row = src.iloc[i]
        as_of = str(src_row["task_date"])
        op_id = str(src_row["operator_id"])
        tt_ = str(src_row["task_type"])
        d = int(pd.Timestamp(as_of).toordinal())
        op_site = op_sites.get(op_id)
        avg_min = avg = prev = None
        if op_site is not None:
            own = eff[(eff["operator_id"] == op_id) & (eff["task_type"] == tt_) & (eff["day"] <= d)]
            win = own[own["day"] >= d - WINDOW + 1]
            avg_min = float(win["actual_duration_min"].mean()) if len(win) >= 3 else None
            avg, n_avg = _window_eff(win)
            avg = avg if n_avg >= 3 else None
            prev, n_prev = _window_eff(
                own[(own["day"] >= d - 2 * WINDOW + 1) & (own["day"] <= d - WINDOW)]
            )
            prev = prev if n_prev >= 3 else None
        srow = shap_values.iloc[i] if isinstance(shap_values, pd.DataFrame) else shap_values[i]
        top = srow.abs().sort_values(ascending=False).head(3)
        factors = [
            Factor(
                feature=feat,
                label=_factor_label(feat, row),
                impact_min=float(np.expm1(z_std[i]) - np.expm1(z_std[i] - srow[feat])),
            )
            for feat in top.index
        ]
        out.append(
            TaskTimePrediction(
                task_id=str(row["task_id"]),
                p10_min=float(q[i, 0] - offset),
                p50_min=float(q[i, 1]),
                p90_min=float(q[i, 2] + offset),
                standard_min=float(np.expm1(z_std[i])),
                expected_efficiency=float(np.expm1(z_std[i]) / q[i, 1]),
                operator_avg_min=avg_min,
                operator_avg_efficiency=avg,
                operator_prev_efficiency=prev,
                factors=factors,
            )
        )
    return out


def default_as_of() -> str | None:
    """Latest task_date with a completed, non-delayed task (the summary default anchor)."""
    t = _frames()["tasks"]
    done = t[(t["status"] == "completed") & (t["actual_duration_min"].notna())]
    if "delay_reason" in t.columns:
        done = done[done["delay_reason"].isna()]
    if done.empty:
        return None
    return str(done["task_date"].max())


def efficiency_summary(
    operator_id: str, task_type: str | None = None, as_of: str | None = None
) -> list[EfficiencyRow]:
    state = _state()
    f = _frames()
    eff = state.efficiency
    if eff is None:
        eff = _efficiency_rows(f["tasks"], _ensure_standard(state)["standard_min"])
        state.efficiency = eff
    if as_of is None:
        if eff.empty:
            return []
        as_of = str(eff["task_date"].max())  # latest task_date with a completed task
    op_site = f["operators"].set_index("operator_id").loc[operator_id, "site_id"]
    return _summary_from(eff, operator_id, op_site, task_type, as_of)


def recommend(
    operator_id: str, as_of: str | None = None, open_recs: list[Any] | None = None
) -> list[Recommendation]:
    return recommend_from(
        efficiency_summary(operator_id, as_of=as_of), operator_id, open_recs or []
    )

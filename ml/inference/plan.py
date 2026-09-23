"""Plan re-evaluation (models.md §12).

`detect_triggers(...)` says whether the plan of a running shift must be re-checked, and
`re_evaluate_plan(...)` re-plans the rest of the shift. It returns the `POST /plan/re-evaluate`
response of docs/api_contract.md:

1. re-predict every remaining task with `ml.inference.task_time.predict_task_time` under the
   current conditions (weather now, machine health now, hours into shift now),
2. sort by priority (1 = most important), then by p50,
3. fill the time left before shift end minus a 10-minute buffer, greedily: a task that doesn't
   fit is skipped and the next one is tried,
4. tasks that don't fit move to the next shift.

A running task stays first: it can't be re-ordered, and its time left is its new p50 minus the
minutes it has already run (at least `MIN_LEFT_MIN`, since a task past its p90 is not done yet).
Nothing here writes to the database; `POST /plan/accept` applies the result.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import pandas as pd

RAIN_MM_H = 2.0  # rain starts: > 2 mm in the hour
HEALTH_MIN = 0.6  # machine overall health below this
BUFFER_MIN = 10.0  # kept free before shift end
MIN_LEFT_MIN = 5.0  # a running task needs at least this long to finish
LOCAL_TZ = "Asia/Kolkata"
REMAINING_STATUSES = ("scheduled", "in_progress", "delayed")

TRIGGERS = ("task_overrun", "rain", "low_health", "fatigue_high", "manager_edit")
TRIGGER_TEXT = {
    "task_overrun": "A task ran past its expected maximum time",
    "rain": "Rain started",
    "low_health": "Machine health dropped",
    "fatigue_high": "Operator fatigue is high",
    "manager_edit": "The manager changed the plan",
}
# Current conditions that replace the task's own feature values before re-predicting
CONDITION_COLUMNS = ("temp_c", "rain_mm", "wind_kmh", "visibility_m", "dust_index", "health_score")

Predictor = Callable[[pd.DataFrame], list[dict[str, Any]]]


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _utc(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ---------------------------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------------------------
def detect_triggers(
    now: Any = None,
    running_task: Mapping[str, Any] | None = None,
    rain_mm_h: float | None = None,
    health_overall: float | None = None,
    fatigue_level: str | None = None,
    manager_edit: bool = False,
) -> list[str]:
    """Trigger reasons that hold now (models.md §12), in TRIGGERS order. Missing inputs don't fire.

    running_task: the in-progress task with `actual_start` and `predicted_p90_min`; it overruns
    when it has run longer than its p90. rain_mm_h: rain in the current hour (weather.rain_mm).
    """
    out: list[str] = []
    if running_task is not None and now is not None:
        p90 = _num(running_task.get("predicted_p90_min"))
        start = running_task.get("actual_start")
        if p90 is not None and start is not None and not pd.isna(start):
            ran = (_utc(now) - _utc(start)).total_seconds() / 60
            if ran > p90:
                out.append("task_overrun")
    rain = _num(rain_mm_h)
    if rain is not None and rain > RAIN_MM_H:
        out.append("rain")
    health = _num(health_overall)
    if health is not None and health < HEALTH_MIN:
        out.append("low_health")
    if isinstance(fatigue_level, str) and fatigue_level.lower() == "high":
        out.append("fatigue_high")
    if manager_edit:
        out.append("manager_edit")
    return out


# ---------------------------------------------------------------------------------------------
# Re-planning
# ---------------------------------------------------------------------------------------------
def current_features(
    tasks: pd.DataFrame,
    now: pd.Timestamp,
    shift_start: pd.Timestamp | None,
    conditions: Mapping[str, Any] | None,
) -> pd.DataFrame:
    """Task feature rows with the current conditions in place of the planned ones.

    Weather and health_score come from `conditions` (a missing or NaN value keeps the task's own
    value); hours_into_shift = now - shift start, the same for every task (their start times
    depend on the order being decided).
    """
    X = tasks.copy()
    for c in CONDITION_COLUMNS:
        v = _num((conditions or {}).get(c))
        if v is not None:
            X[c] = v
    if shift_start is not None:
        X["hours_into_shift"] = max((now - shift_start).total_seconds() / 3600, 0.0)
    return X


def _predict(X: pd.DataFrame, predictor: Predictor | None) -> dict[str, float]:
    """task_id -> new p50 (minutes). Falls back to the stored predicted_p50_min per task when
    the model can't run (missing columns or artifacts)."""
    out: dict[str, float] = {}
    if predictor is None:
        from ml.inference.task_time import predict_task_time

        def predictor(df: pd.DataFrame) -> list[dict[str, Any]]:
            return predict_task_time(df, explain_factors=False)

    try:
        preds = predictor(X)
        for p in preds:
            v = _num(p.get("p50_min"))
            if v is not None and v > 0:
                out[str(p["task_id"])] = v
    except (KeyError, ValueError, FileNotFoundError, RuntimeError):
        pass
    if "predicted_p50_min" in X:
        for tid, v in zip(X["task_id"].astype(str), X["predicted_p50_min"], strict=True):
            v = _num(v)
            if tid not in out and v is not None and v > 0:
                out[tid] = v
    return out


def _task_label(row: Mapping[str, Any]) -> str:
    n = row.get("sequence_no")
    n = int(n) if _num(n) is not None else str(row["task_id"]).rsplit("-", 1)[-1]
    return f"task {n}"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def re_evaluate_plan(
    shift_id: str,
    tasks: pd.DataFrame,
    now: Any,
    shift_end: Any,
    shift_start: Any = None,
    conditions: Mapping[str, Any] | None = None,
    reason: str | Iterable[str] | None = None,
    predictor: Predictor | None = None,
    buffer_min: float = BUFFER_MIN,
) -> dict[str, Any]:
    """Re-plan the rest of a shift (models.md §12, /plan/re-evaluate response).

    tasks: the shift's tasks, one row each, with task_id, priority, sequence_no, status,
      actual_start (for the running task), optionally predicted_p50_min (fallback), and the
      task_time INPUT_COLUMNS. Only scheduled / in_progress / delayed tasks are planned.
    conditions: current weather columns and health_score (overall health now).
    reason: trigger code(s) from `detect_triggers` or the API request; used in the explanation.
    predictor: replaces `predict_task_time` (tests).

    Returns shift_id, fits (fitting tasks in their original order), moved_to_next_shift,
    new_order (the running task, then the rest in the new order), explanation, and extra fields
    `triggers`, `available_min` and `schedule` (per task: start, end, p50_min, fits).
    """
    now, shift_end = _utc(now), _utc(shift_end)
    start = _utc(shift_start) if shift_start is not None else None
    reasons = [reason] if isinstance(reason, str) else list(reason or [])
    t = tasks if tasks is not None else pd.DataFrame(columns=["task_id"])
    if "status" in t:
        t = t[t["status"].isin(REMAINING_STATUSES)]
    t = t.reset_index(drop=True)
    available = max((shift_end - now).total_seconds() / 60 - buffer_min, 0.0)
    end_local = shift_end.tz_convert(LOCAL_TZ).strftime("%H:%M")
    lead = "; ".join(TRIGGER_TEXT.get(r, r.replace("_", " ").capitalize()) for r in reasons)

    base = {"shift_id": shift_id, "triggers": reasons, "available_min": round(available, 1)}
    if t.empty:
        text = "No tasks left in this shift."
        return {
            **base,
            "fits": [],
            "moved_to_next_shift": [],
            "new_order": [],
            "explanation": f"{lead}. {text}" if lead else text,
            "schedule": [],
        }

    p50 = _predict(current_features(t, now, start, conditions), predictor)
    rows = t.to_dict("records")
    for r in rows:
        r["task_id"] = str(r["task_id"])
        r["_p50"] = p50.get(r["task_id"])
        pr = _num(r.get("priority"))
        r["_priority"] = pr if pr is not None else 2.0  # schema default
        seq = _num(r.get("sequence_no"))
        r["_seq"] = seq if seq is not None else math.inf

    running = [r for r in rows if r.get("status") == "in_progress"]
    waiting = [r for r in rows if r.get("status") != "in_progress"]
    # Unknown duration last within its priority; stable on the original sequence
    waiting.sort(
        key=lambda r: (r["_priority"], r["_p50"] if r["_p50"] is not None else math.inf, r["_seq"])
    )

    used = 0.0
    schedule: list[dict[str, Any]] = []
    for r in running:
        ran = 0.0
        if r.get("actual_start") is not None and not pd.isna(r.get("actual_start")):
            ran = max((now - _utc(r["actual_start"])).total_seconds() / 60, 0.0)
        left = max((r["_p50"] or 0.0) - ran, MIN_LEFT_MIN)
        schedule.append({"r": r, "minutes": left, "fits": True})  # already running: stays
        used += left
    for r in waiting:
        m = r["_p50"]
        ok = m is not None and used + m <= available
        schedule.append({"r": r, "minutes": m, "fits": ok})
        if ok:
            used += m

    new_order = [s["r"]["task_id"] for s in schedule if s["fits"]]
    fits = [
        r["task_id"] for r in sorted(rows, key=lambda r: r["_seq"]) if r["task_id"] in new_order
    ]
    moved_rows = [s["r"] for s in schedule if not s["fits"]]
    moved = [r["task_id"] for r in sorted(moved_rows, key=lambda r: r["_seq"])]

    clock = now
    out_schedule = []
    for s in schedule:
        item = {
            "task_id": s["r"]["task_id"],
            "priority": int(s["r"]["_priority"]),
            "p50_min": round(s["r"]["_p50"], 1) if s["r"]["_p50"] is not None else None,
            "fits": s["fits"],
            "start": None,
            "end": None,
        }
        if s["fits"]:
            end = clock + pd.Timedelta(minutes=s["minutes"])
            item["start"] = clock.isoformat().replace("+00:00", "Z")
            item["end"] = end.isoformat().replace("+00:00", "Z")
            clock = end
        out_schedule.append(item)

    # Plain-language explanation
    parts = [lead] if lead else []
    by_id = {r["task_id"]: r for r in rows}
    if moved:
        names = [_task_label(by_id[m]) for m in moved]
        verb = "no longer fits" if len(names) == 1 else "no longer fit"
        target = "it moves" if len(names) == 1 else "they move"
        cap = _join(names)
        parts.append(
            f"{cap[0].upper()}{cap[1:]} {verb} before {end_local}; {target} to the next shift"
        )
        unknown = [_task_label(by_id[m]) for m in moved if by_id[m]["_p50"] is None]
        if unknown:
            parts.append(f"No time estimate for {_join(unknown)}")
    else:
        parts.append(f"All remaining tasks still fit before {end_local}")
    original = [
        r["task_id"] for r in sorted(rows, key=lambda r: r["_seq"]) if r["task_id"] in new_order
    ]
    if new_order and new_order != original:
        parts.append("New order: " + ", ".join(_task_label(by_id[i]) for i in new_order))
    if not new_order:
        parts.append("Nothing fits in the time left")
    explanation = ". ".join(parts) + "."

    return {
        **base,
        "fits": fits,
        "moved_to_next_shift": moved,
        "new_order": new_order,
        "explanation": explanation,
        "schedule": out_schedule,
    }

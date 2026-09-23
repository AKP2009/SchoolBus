"""Task time predictions (`POST /predict/task-time`) and plan re-evaluation (`/plan/*`).

Both build the task_time feature table from the database with
`ml.inference.task_time.build_feature_table`, the same code the model was trained with, then
replace `health_score` with the machine's live overall health (models.md §2: "the live backend
passes health_score from v_machine_health_latest"; the running replay's value when there is one).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from app.core.config import REPO_ROOT
from app.core.errors import ApiError, model_not_loaded
from app.repo import Repo, iso
from app.services.events import fatigue_for_plan

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.inference import plan as P  # noqa: E402
from ml.inference import task_time as T  # noqa: E402

log = logging.getLogger(__name__)

HISTORY = T.RATIO_WINDOW  # operator_avg_time_ratio looks back 30 days
WEATHER_LOOKBACK = timedelta(hours=6)  # the weather row at or before the start is hourly
FATIGUE_LOOKBACK = timedelta(days=30)  # operator_fatigue_hour_avg over earlier shifts
PENDING_TTL_S = 30 * 60  # an unaccepted plan expires after 30 minutes
MOVED = "moved_to_next_shift"  # tasks.delay_reason of a task /plan/accept moved out


def load_task_model() -> dict[str, Any]:
    try:
        return T.load_artifacts()
    except Exception as e:  # noqa: BLE001 - missing file, version mismatch, ...
        raise model_not_loaded("Task time", "ml/02_task_time.ipynb", e) from e


def live_health(repo: Repo, machine_ids: list[str], engine: Any | None) -> dict[str, float]:
    """machine_id -> overall health now: the running replay, else v_machine_health_latest."""
    out: dict[str, float] = {}
    for mid in machine_ids:
        h = engine.machine_health(mid) if engine is not None else None
        if h is not None and h.get("overall") is not None:
            out[mid] = float(h["overall"])
            continue
        snap = repo.latest_health(mid)
        if snap is not None and snap.get("overall_score") is not None:
            out[mid] = float(snap["overall_score"])
    return out


def feature_table(repo: Repo, tasks: pd.DataFrame, health: Mapping[str, float]) -> pd.DataFrame:
    """task_time feature rows (INPUT_COLUMNS + keys) for `tasks`, from the database."""
    reference = load_task_model()["reference"]
    t = tasks.copy()
    ops = sorted(t.operator_id.dropna().unique())
    mids = sorted(t.machine_id.dropna().unique())
    sites = sorted(t.site_id.dropna().unique())
    shifts = repo.shifts(machine_ids=mids, operator_ids=ops)
    # a task without a scheduled start starts with its shift
    starts = t.shift_id.map(shifts.set_index("shift_id").start_time)
    t["scheduled_start"] = t.scheduled_start.fillna(starts)
    t = t[t.scheduled_start.notna()]
    if t.empty:
        return pd.DataFrame(columns=[*T.INPUT_COLUMNS, "shift_id", "machine_id"])
    lo, hi = t.scheduled_start.min(), t.scheduled_start.max()
    ft = T.build_feature_table(
        t,
        weather=repo.weather(sites, lo - WEATHER_LOOKBACK, hi),
        operators=repo.operators(ops),
        machines=repo.machines(mids),
        shifts=shifts,
        fatigue_log=repo.fatigue_log(ops, lo - FATIGUE_LOOKBACK, hi),
        maintenance_log=repo.maintenance_log(mids),
        reference=reference,
        history=repo.task_history(ops, lo - HISTORY, hi),
    )
    live = ft.machine_id.map(dict(health))
    ft["health_score"] = live.fillna(ft.health_score).astype(float)
    return ft


# ---------------------------------------------------------------------------------------------
# POST /predict/task-time
# ---------------------------------------------------------------------------------------------
def predict_and_store(repo: Repo, task_ids: list[str], engine: Any | None) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(task_ids))
    tasks = repo.tasks(task_ids=ids)
    missing = sorted(set(ids) - set(tasks.task_id))
    if missing:
        raise ApiError(404, "NOT_FOUND", f"Unknown task(s): {', '.join(missing)}")
    health = live_health(repo, sorted(tasks.machine_id.unique()), engine)
    ft = feature_table(repo, tasks, health)
    no_start = sorted(set(ids) - set(ft.task_id))
    if no_start:
        raise ApiError(400, "VALIDATION_ERROR", f"No start time for: {', '.join(no_start)}")
    try:
        preds = T.predict_task_time(ft.loc[:, T.INPUT_COLUMNS])
    except (KeyError, ValueError) as e:
        raise ApiError(400, "VALIDATION_ERROR", f"Can't build task features: {e}") from e
    now = iso(datetime.now(UTC))
    for p in preds:
        repo.update_task(
            p["task_id"],
            {
                "predicted_p10_min": p["p10_min"],
                "predicted_p50_min": p["p50_min"],
                "predicted_p90_min": p["p90_min"],
                "prediction_factors": p["factors"],
                "updated_at": now,
            },
        )
    order = {tid: i for i, tid in enumerate(ids)}
    return sorted(preds, key=lambda p: order[p["task_id"]])


# ---------------------------------------------------------------------------------------------
# POST /plan/re-evaluate, /plan/accept
# ---------------------------------------------------------------------------------------------
class PendingPlans:
    """The last re-evaluation per shift, waiting for /plan/accept. In memory: a restart drops
    unaccepted plans (the client re-evaluates)."""

    def __init__(self) -> None:
        self._plans: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def put(self, shift_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._plans[shift_id] = (time.monotonic(), result)

    def take(self, shift_id: str) -> dict[str, Any]:
        with self._lock:
            hit = self._plans.get(shift_id)
            if hit is None:
                raise ApiError(
                    409,
                    "NO_PENDING_PLAN",
                    f"No re-evaluated plan for {shift_id}. Call /plan/re-evaluate first.",
                )
            if time.monotonic() - hit[0] > PENDING_TTL_S:
                del self._plans[shift_id]
                raise ApiError(
                    409, "PLAN_EXPIRED", "That plan is more than 30 minutes old. Re-evaluate it."
                )
            del self._plans[shift_id]
            return hit[1]


pending_plans = PendingPlans()


def plan_clock(shift: Mapping[str, Any], engine: Any | None, now: datetime | None) -> datetime:
    """The plan's "now": the request's `now`, else the replay time when the shift's machine is
    being replayed, else the wall clock."""
    if now is not None:
        return now.astimezone(UTC)
    if engine is not None and getattr(engine, "running", False) and engine.replay_ts is not None:
        if shift["machine_id"] in engine.streams:
            return engine.replay_ts.astimezone(UTC)
    return datetime.now(UTC)


def _ts(v: Any) -> datetime:
    t = pd.Timestamp(v)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def re_evaluate(
    repo: Repo, shift_id: str, reason: str, engine: Any | None, now: datetime | None = None
) -> dict[str, Any]:
    shift = repo.shift(shift_id)
    if shift is None:
        raise ApiError(404, "NOT_FOUND", f"Unknown shift {shift_id}.")
    load_task_model()
    clock = plan_clock(shift, engine, now)
    tasks = repo.tasks(shift_ids=[shift_id])
    if not tasks.empty:
        tasks = tasks[tasks.delay_reason.fillna("") != MOVED]
    mid, op, site = shift["machine_id"], shift["operator_id"], shift["site_id"]
    health = live_health(repo, [mid], engine)

    remaining = tasks[tasks.status.isin(P.REMAINING_STATUSES)] if not tasks.empty else tasks
    if remaining.empty:
        planned = remaining
    else:
        ft = feature_table(repo, remaining, health).drop(columns=["status"], errors="ignore")
        keep = ["task_id", "priority", "sequence_no", "status", "actual_start"]
        keep += ["predicted_p50_min", "predicted_p90_min"]
        # a task the features can't be built for still takes part with its stored p50
        planned = remaining[keep].merge(ft, on="task_id", how="left")

    # current conditions: weather now at the site, machine health now
    wx = repo.weather([site], clock - WEATHER_LOOKBACK, clock)
    conditions: dict[str, Any] = {}
    if not wx.empty:
        last = wx.sort_values("ts").iloc[-1]
        conditions = {c: last[c] for c in T.WEATHER_COLUMNS}
    if mid in health:
        conditions["health_score"] = health[mid]

    fatigue = fatigue_for_plan(repo, op, clock)
    running = remaining[remaining.status == "in_progress"] if not remaining.empty else remaining
    detected = P.detect_triggers(
        now=clock,
        running_task=running.iloc[0].to_dict() if not running.empty else None,
        rain_mm_h=conditions.get("rain_mm"),
        health_overall=health.get(mid),
        fatigue_level=(fatigue or {}).get("fatigue_level"),
        manager_edit=reason == "manager_edit",
    )
    reasons = list(dict.fromkeys([reason, *detected]))
    result = P.re_evaluate_plan(
        shift_id,
        planned,
        now=clock,
        shift_end=_ts(shift["end_time"]),
        shift_start=_ts(shift["start_time"]),
        conditions=conditions,
        reason=reasons,
    )
    result["now"] = iso(clock)
    result["fatigue"] = (
        {"fatigue_level": fatigue["fatigue_level"], "ts": fatigue["ts"]} if fatigue else None
    )
    pending_plans.put(shift_id, result)
    return result


def accept(repo: Repo, shift_id: str) -> dict[str, Any]:
    """Apply the pending plan: tasks that fit get their new sequence_no and scheduled start
    (the running task keeps its own); tasks that don't fit are marked delayed with
    delay_reason 'moved_to_next_shift' and left out of later re-evaluations."""
    plan = pending_plans.take(shift_id)
    now = iso(datetime.now(UTC))
    schedule = {s["task_id"]: s for s in plan.get("schedule", [])}
    tasks = repo.tasks(shift_ids=[shift_id])
    status = dict(zip(tasks.task_id, tasks.status, strict=True))
    order = [*plan["new_order"], *plan["moved_to_next_shift"]]
    # tasks outside the plan (done, cancelled) keep their numbers; the plan follows them
    others = tasks[~tasks.task_id.isin(order)].sequence_no.dropna()
    base = int(others.max()) if not others.empty else 0
    for i, tid in enumerate(order, start=1):
        fields: dict[str, Any] = {"sequence_no": base + i, "updated_at": now}
        if tid in plan["moved_to_next_shift"]:
            fields.update(status="delayed", delay_reason=MOVED, scheduled_start=None)
        elif status.get(tid) != "in_progress" and schedule.get(tid, {}).get("start"):
            fields["scheduled_start"] = schedule[tid]["start"]
        repo.update_task(tid, fields)
    return {"shift_id": shift_id, "applied": True}

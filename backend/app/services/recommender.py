"""Training recommender (models.md §10): rules on the operator's last 7 days.

| Trigger (last 7 days)                          | Module                         |
|------------------------------------------------|--------------------------------|
| tip_risk events >= 1                           | TM-SAFE-03 Working on slopes   |
| seatbelt_unfastened >= 1                       | TM-SAFE-02 Seatbelt and ROPS   |
| proximity_breach + blindspot_intrusion >= 2    | TM-SAFE-01 Blind spots         |
| cluster_label = 'needs safety coaching'        | TM-SIM-01 Scenario pack        |
| harsh_maneuver >= 3                            | TM-SMTH-01 Smooth controls     |
| time_ratio > 1.2 for a task type (>= 2 tasks)  | TM-EXC-01 (dig, trench), else TM-CYC-01 |
| idle_pct > 25%                                 | TM-IDLE-01, else TM-FUEL-01    |

Safety triggers come first. At most 2 open (pending or accepted) recommendations per operator;
a module that is already open, or was recommended in the last 7 days (e.g. just dismissed), is
not suggested again. The window ends at `as_of`: by default the end of the latest shift in the
database (the synthetic data is in the past), so "last 7 days" means the data's last 7 days.

idle_pct = idle minutes / engine-on minutes of the operator's shifts (as in clustering);
time_ratio = actual minutes / fleet p50 of the task-time model (the operator's own pace left
out, as in clustering), summed per task type over completed tasks.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from app.ai_repo import OPEN_RECOMMENDATION, AiRepo
from app.core.config import REPO_ROOT
from app.repo import Repo
from app.services.telemetry import TelemetryHistory

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger(__name__)

WINDOW = timedelta(days=7)
MAX_OPEN = 2
IDLE_PCT = 25.0
HARSH = 3
TIME_RATIO = 1.2
TIME_RATIO_MIN_TASKS = 2
PROXIMITY = 2
SEATBELT = 1
TIP_RISK = 1
SAFETY_CLUSTER = "needs safety coaching"
EXCAVATOR_TASKS = {"dig", "trench"}
# (safety_events type, minimum count, module), in priority order
SAFETY_RULES = (
    ("tip_risk", TIP_RISK, "TM-SAFE-03"),
    ("seatbelt_unfastened", SEATBELT, "TM-SAFE-02"),
    ("proximity_breach", PROXIMITY, "TM-SAFE-01"),  # blindspot_intrusion counted in
)
# Plain-language reasons shown to the operator: help, not blame.
REASONS = {
    "tip_risk": "The tilt warning (tip risk) came on {times} in the last 7 days. This module "
    "shows how to work safely on slopes and near edges.",
    "seatbelt_unfastened": "The seatbelt was unfastened while working {times} in the last 7 "
    "days. The seatbelt keeps you inside the cab's protection if the machine tips.",
    "proximity_breach": "People or vehicles came too close to your machine {times} in the last "
    "7 days. This module covers your machine's blind spots and what to do.",
    "cluster_label": "Your safety numbers last week were higher than most operators'. These "
    "short scenarios let you practise the right reaction before it happens for real.",
    "harsh_maneuver": "{moves} (sudden jerks or hard braking) in the last 7 days. Smoother "
    "controls are safer, save fuel and reduce wear.",
    "time_ratio": "Your {task_type} tasks took {pct}% longer than expected in the last 7 days "
    "({n} tasks). This module shows faster, smoother work cycles.",
    "idle_pct": "You idled {idle}% of engine time in the last 7 days (goal: under 25%). Cutting "
    "idle time saves fuel and engine hours.",
}


@dataclass
class Trigger:
    metric: str
    value: float
    modules: tuple[str, ...]  # first one not already open / recent is used
    reason: str


def default_as_of(repo: Repo) -> datetime | None:
    """End of the latest shift in the database."""
    d = repo.latest_shift_date()
    if d is None:
        return None
    s = repo.shifts(date_from=d, date_to=d)
    return s.end_time.max().to_pydatetime() if not s.empty else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _time_ratios(repo: Repo, tasks: pd.DataFrame) -> dict[str, tuple[float, int]]:
    """task_type -> (sum actual / sum fleet p50, number of tasks)."""
    from ml.inference import clustering as C

    from app.services.tasks import feature_table

    done = tasks[(tasks.status == "completed") & tasks.actual_duration_min.notna()]
    if done.empty:
        return {}
    ft = feature_table(repo, done, health={})
    if ft.empty:
        return {}
    done = done.assign(p50=done.task_id.map(C.fleet_p50(ft))).dropna(subset=["p50"])
    out = {}
    for tt, g in done.groupby("task_type"):
        if g.p50.sum() > 0:
            out[str(tt)] = (float(g.actual_duration_min.sum() / g.p50.sum()), len(g))
    return out


def triggers(
    repo: Repo, ai: AiRepo, telemetry: TelemetryHistory, operator_id: str, as_of: datetime
) -> list[Trigger]:
    """Every rule that fires for the operator in (as_of - 7 days, as_of], in priority order."""
    start = as_of - WINDOW
    first_day = start.date() - timedelta(days=1)
    shifts = repo.shifts(operator_ids=[operator_id], date_from=first_day, date_to=as_of.date())
    shifts = shifts[(shifts.start_time >= start) & (shifts.end_time <= as_of)]
    ev = repo.safety_events(start, as_of)
    n = {k: int(v) for k, v in ev[ev.operator_id == operator_id].event_type.value_counts().items()}
    n["proximity_breach"] = n.get("proximity_breach", 0) + n.pop("blindspot_intrusion", 0)
    out: list[Trigger] = []

    def fire(metric: str, value: float, modules: tuple[str, ...], **fmt: Any) -> None:
        out.append(Trigger(metric, value, modules, REASONS[metric].format(**fmt)))

    for metric, limit, module in SAFETY_RULES:
        if (v := n.get(metric, 0)) >= limit:
            fire(metric, v, (module,), times=_plural(v, "time"))
    week_from = (start - timedelta(days=start.weekday())).date()
    if any(
        m.get("cluster_label") == SAFETY_CLUSTER
        for m in ai.fleet_metrics(operator_id, week_from, as_of.date())
    ):
        fire("cluster_label", 1, ("TM-SIM-01",))
    if (v := n.get("harsh_maneuver", 0)) >= HARSH:
        fire("harsh_maneuver", v, ("TM-SMTH-01",), moves=_plural(v, "harsh movement"))
    if shifts.empty:
        return out

    try:
        ratios = _time_ratios(repo, repo.tasks(shift_ids=shifts.shift_id.tolist()))
    except Exception:  # noqa: BLE001 - the other rules still apply
        log.exception("time_ratio for %s failed; skipping that rule", operator_id)
        ratios = {}
    for tt, (ratio, k) in sorted(ratios.items(), key=lambda kv: -kv[1][0]):
        if ratio > TIME_RATIO and k >= TIME_RATIO_MIN_TASKS:
            module = "TM-EXC-01" if tt in EXCAVATOR_TASKS else "TM-CYC-01"
            pct = round((ratio - 1) * 100)
            fire("time_ratio", round(ratio, 3), (module,), task_type=tt, pct=pct, n=k)

    tel = telemetry.frame(sorted(shifts.machine_id.unique()), start, as_of)
    tel = tel[tel.shift_id.isin(shifts.shift_id)] if not tel.empty else tel
    if len(tel) and (idle := float(100 * tel.is_idle.astype(bool).mean())) > IDLE_PCT:
        fire("idle_pct", round(idle, 1), ("TM-IDLE-01", "TM-FUEL-01"), idle=round(idle))
    return out


def recommend(
    repo: Repo,
    ai: AiRepo,
    telemetry: TelemetryHistory,
    operator_id: str,
    as_of: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write up to 2 - (open recommendations) new rows. Returns what fired and what was written."""
    as_of = as_of or default_as_of(repo)
    now = now or datetime.now(UTC)
    if as_of is None:
        return {"operator_id": operator_id, "as_of": None, "triggers": [], "created": []}
    modules = ai.training_modules()
    existing = ai.recommendations(operator_id)
    open_ids = {r["module_id"] for r in existing if r["status"] in OPEN_RECOMMENDATION}
    recent = {
        r["module_id"]
        for r in existing
        if pd.Timestamp(r["created_at"]).tz_convert("UTC") >= pd.Timestamp(now - WINDOW)
    }
    slots = MAX_OPEN - len({r["id"] for r in existing if r["status"] in OPEN_RECOMMENDATION})
    fired = triggers(repo, ai, telemetry, operator_id, as_of)
    rows: list[dict[str, Any]] = []
    for t in fired:
        if slots - len(rows) <= 0:
            break
        taken = open_ids | recent | {r["module_id"] for r in rows}
        module = next((m for m in t.modules if m in modules and m not in taken), None)
        if module is None:
            continue
        rows.append(
            {
                "operator_id": operator_id,
                "module_id": module,
                "reason": t.reason,
                "trigger_metric": t.metric,
                "trigger_value": t.value,
                "status": "pending",
            }
        )
    created = ai.insert_recommendations(rows)
    return {
        "operator_id": operator_id,
        "as_of": as_of,
        "triggers": [
            {"metric": t.metric, "value": t.value, "modules": list(t.modules)} for t in fired
        ],
        "created": created,
    }

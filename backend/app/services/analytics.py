"""Fleet clustering for one week (`POST /analytics/cluster`, daily job; models.md §4).

Inputs as in `ml/03_clustering.ipynb`: the week's shifts, their telemetry (fuel, idle, and
model 1's machine_fault events), completed tasks against the fleet p50 (task-time model without
the operator's own pace), and safety events. Output rows are upserted into
`fleet_metrics_weekly` on (entity_type, entity_id, week_start); a manager's verification
(`verified_by`, `verified_at`) is kept because those columns aren't sent.
"""

from __future__ import annotations

import logging
import math
import sys
from collections import Counter
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.core.config import REPO_ROOT
from app.core.errors import ApiError, model_not_loaded
from app.repo import Repo
from app.services.tasks import feature_table
from app.services.telemetry import TelemetryHistory

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.inference import clustering as C  # noqa: E402
from ml.inference import maintenance as M  # noqa: E402

log = logging.getLogger(__name__)


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if hasattr(v, "item"):  # numpy scalar
        return _clean(v.item())
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def summarise(rows: pd.DataFrame) -> str:
    parts = []
    for et in ("operator", "machine"):
        r = rows[rows.entity_type == et]
        if r.empty:
            continue
        counts = Counter(r.cluster_label)
        groups = ", ".join(f"{n} {label}" for label, n in counts.most_common())
        parts.append(f"{len(r)} {et}s ({groups})")
    outliers = rows[rows.is_outlier]
    text = "; ".join(parts) if parts else "No activity"
    if len(outliers):
        names = ", ".join(outliers.entity_id.astype(str).tolist()[:5])
        more = "" if len(outliers) <= 5 else f" and {len(outliers) - 5} more"
        text += f". {len(outliers)} outlier(s) to verify: {names}{more}"
    return text + "."


def cluster_week(repo: Repo, telemetry: TelemetryHistory, week_start: date) -> dict[str, Any]:
    if week_start.weekday() != 0:
        raise ApiError(400, "VALIDATION_ERROR", f"week_start {week_start} is not a Monday.")
    try:
        C.load_artifacts()
    except Exception as e:  # noqa: BLE001
        raise model_not_loaded("Fleet clustering", "ml/03_clustering.ipynb", e) from e

    shifts = repo.shifts(date_from=week_start, date_to=week_start + timedelta(days=6))
    if shifts.empty:
        raise ApiError(404, "NOT_FOUND", f"No shifts in the week of {week_start}.")
    machines = repo.machines()
    mtype = dict(zip(machines.machine_id, machines.machine_type, strict=True))
    start, end = shifts.start_time.min(), shifts.end_time.max()

    tel = telemetry.frame(sorted(shifts.machine_id.unique()), start, end)
    tel = tel[tel.shift_id.isin(shifts.shift_id)].reset_index(drop=True)
    if tel.empty:
        anomaly_events = pd.DataFrame(columns=["machine_id", "ts"])
    else:
        scores = M.score_minutes(tel, mtype)
        anomaly_events = C.anomaly_event_starts(scores.ts, scores.machine_id, scores.kind)

    tasks = repo.tasks(shift_ids=shifts.shift_id.tolist())
    tasks["fleet_p50_min"] = float("nan")
    done = tasks[(tasks.status == "completed") & tasks.actual_duration_min.notna()]
    if not done.empty:
        ft = feature_table(repo, done, health={})  # health at the time, not today's
        if not ft.empty:
            tasks["fleet_p50_min"] = tasks.task_id.map(C.fleet_p50(ft))

    safety = repo.safety_events(start, end)
    segments = C.build_weekly_segments(tel, tasks, shifts, machines, safety, anomaly_events)
    out = C.cluster_week(segments)
    records = [
        {k: _clean(v) for k, v in r.items()} | {"week_start": week_start.isoformat()}
        for r in out.to_dict("records")
    ]
    written = repo.upsert_fleet_metrics(records)
    return {
        "week_start": week_start,
        "rows_written": written,
        "summary": summarise(out) if not out.empty else "No activity in that week.",
        "telemetry_source": telemetry.source,
    }


def latest_week(repo: Repo) -> date | None:
    """Monday of the week holding the latest shift (the daily job clusters it)."""
    d = repo.latest_shift_date()
    return d - timedelta(days=d.weekday()) if d else None

"""Stage 4: maintenance_log, failures, and per-shift machine state (engine hours, health).

Scheduled service every service_interval_hours. Engine hours are counted as full shift
hours; the engine-off tail when a shift finishes early (telemetry.py) is ignored, so hours
are slightly overstated (< 5%).

Failures are placed first (synthetic_data.md, Pre-failure drift), in engine-hour space:
each gets a component, a drift window W ~ U(50, 200) engine hours, and a failure minute
inside a shift. Drift windows on one machine never overlap and start at least GAP_H engine
hours after the previous failure, so signals are back to normal in between. The machine
stops at the failure minute (rest of that shift has no telemetry), is down 4-24 h, and shifts
that start while it is down are dropped. The failure row carries the downtime; the repair
row (when it's back) carries the cost.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common import ist_to_utc, rng_for, to_frame

AVG_ENGINE_HOURS_PER_DAY = 14.0
SERVICE_NOTES = "Scheduled service: engine oil, filters, hydraulic filter, greasing."

FAILURE_MIX = {
    "hydraulics": 0.35,
    "engine": 0.25,
    "cooling": 0.15,
    "electrical": 0.15,
    "undercarriage": 0.10,
}
DRIFT_WINDOW_H = (50.0, 200.0)
LABEL_SHARE = 0.2  # last 20% of W is labelled pre_failure_<component>
GAP_H = 48.0  # engine hours between a failure and the next drift window on that machine
END_MARGIN_H = 40.0  # last failure leaves time for repair before the run ends
DOWNTIME_H = (4.0, 24.0)
HEALTH_DRIFT_PENALTY = 0.4  # health = service part - 0.4 * drift progress
REPAIR_COST_INR = {
    "hydraulics": (60_000, 250_000),
    "engine": (150_000, 600_000),
    "cooling": (40_000, 150_000),
    "electrical": (15_000, 80_000),
    "undercarriage": (80_000, 300_000),
}
FAILURE_NOTES = {
    "hydraulics": "Hydraulic hose burst under load, oil on the ground. Machine stopped.",
    "engine": "Engine lost oil pressure and was shut down. Knocking noise reported.",
    "cooling": "Engine overheated, coolant boiling over. Machine stopped.",
    "electrical": "Machine would not restart, electrical fault. Battery and alternator suspect.",
    "undercarriage": "Track came off / heavy rattle from undercarriage. Machine stopped.",
}
REPAIR_NOTES = {
    "hydraulics": "Replaced burst hose and seals, topped up hydraulic oil, pressure tested.",
    "engine": "Replaced oil pump and bearings, new engine oil and filter.",
    "cooling": "Replaced water pump and thermostat, flushed radiator.",
    "electrical": "Replaced alternator and batteries, cleaned ground straps.",
    "undercarriage": "Replaced track rollers and idler, re-tensioned track.",
}


def drift_progress(hours: Any, t_fail: float, window: float) -> Any:
    """p = clip((t - (t_f - W)) / W, 0, 1); 0 after the failure."""
    h = np.asarray(hours, dtype=float)
    p = np.clip((h - (t_fail - window)) / window, 0.0, 1.0)
    return np.where(h > t_fail, 0.0, p)


def machine_health(hours_since_service: float, interval: float, drift_p: float) -> float:
    """1 - 0.2 * hss/interval (0.8 when a service is due) minus 0.4 * pending-failure drift."""
    return float(
        np.clip(1 - 0.2 * hours_since_service / interval - HEALTH_DRIFT_PENALTY * drift_p, 0.3, 1)
    )


def _service_row(
    rng: np.random.Generator, machine_id: str, ts: pd.Timestamp, hours: float
) -> dict[str, Any]:
    return {
        "machine_id": machine_id,
        "event_date": ts,
        "engine_hours_at_event": hours,
        "component": "other",
        "event_type": "scheduled_service",
        "downtime_hours": float(rng.uniform(2, 4)),
        "cost_inr": float(rng.uniform(25_000, 45_000)),
        "notes": SERVICE_NOTES,
    }


def _place_failures(
    rng: np.random.Generator, cfg: dict[str, Any], machines: pd.DataFrame, shifts: pd.DataFrame
) -> dict[str, dict[str, Any]]:
    """Pick failures on the planned schedule. Returns {shift_id: failure}."""
    plans = {}
    for m in machines.itertuples():
        ms = shifts[shifts.machine_id == m.machine_id]
        hours = ((ms.end_time - ms.start_time).dt.total_seconds() / 3600).to_numpy()
        starts = m.total_engine_hours + np.concatenate([[0.0], np.cumsum(hours)[:-1]])
        plans[m.machine_id] = (ms.reset_index(drop=True), starts, hours)

    ids = list(plans)
    comps = rng.choice(list(FAILURE_MIX), size=cfg["failures_total"], p=list(FAILURE_MIX.values()))
    taken: dict[str, list[tuple[float, float]]] = {mid: [] for mid in ids}
    by_shift: dict[str, dict[str, Any]] = {}
    for comp in comps:
        for _ in range(500):
            mid = str(rng.choice(ids))
            ms, starts, hours = plans[mid]
            if ms.empty:
                continue
            w = float(rng.uniform(*DRIFT_WINDOW_H))
            h0, h1 = starts[0] + w, starts[-1] + hours[-1] - END_MARGIN_H
            if h1 <= h0:
                continue
            k = int(np.searchsorted(starts, rng.uniform(h0, h1), side="right") - 1)
            # Fail early in the shift (while the machine is surely running).
            minute = int(rng.uniform(20, 0.6 * hours[k] * 60))
            t_f = starts[k] + minute / 60
            lo, hi = t_f - w, t_f + GAP_H
            if lo < starts[0] or any(lo < b + GAP_H and a < hi for a, b in taken[mid]):
                continue
            taken[mid].append((lo, t_f))
            by_shift[ms.shift_id[k]] = {
                "machine_id": mid,
                "shift_id": ms.shift_id[k],
                "component": str(comp),
                "minute": minute,
                "planned_t_fail": t_f,
                "drift_window_h": w,
                "downtime_h": float(rng.uniform(*DOWNTIME_H)),
            }
            break
        else:
            raise RuntimeError("Could not place all failures; lower failures_total")
    return by_shift


def build_maintenance(
    cfg: dict[str, Any], machines: pd.DataFrame, shifts: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (maintenance_log, shift_state, machines at end of run, shifts, failures).

    shifts loses the shifts that start while a machine is down. failures is not a schema
    table: one row per failure with its drift window (ground truth for telemetry and ML).
    """
    rng = rng_for(cfg["seed"], "maintenance")
    fail_rng = rng_for(cfg["seed"], "failures")
    by_shift = _place_failures(fail_rng, cfg, machines, shifts) if cfg["failures"] else {}
    first_day = ist_to_utc(cfg["start_date"], "06:00")
    rows, state, failures, dropped = [], [], [], set()
    machines = machines.copy()

    for i, m in machines.iterrows():
        total, hss, interval = m.total_engine_hours, m.hours_since_service, m.service_interval_hours
        # Last service before the simulated period, so every machine has history.
        last = first_day - pd.Timedelta(days=hss / AVG_ENGINE_HOURS_PER_DAY)
        rows.append(_service_row(rng, m.machine_id, last, total - hss))

        ms = shifts[shifts.machine_id == m.machine_id]
        pending = sorted(
            (f for f in by_shift.values() if f["machine_id"] == m.machine_id),
            key=lambda f: f["planned_t_fail"],
        )
        lost = 0.0  # planned minus actual engine hours (dropped and cut-short shifts)
        down_until: pd.Timestamp | None = None
        for s in ms.itertuples():
            if down_until is not None and s.start_time < down_until:
                dropped.add(s.shift_id)
                lost += (s.end_time - s.start_time).total_seconds() / 3600
                continue
            nxt = pending[0] if pending else None
            p = (
                float(drift_progress(total, nxt["planned_t_fail"] - lost, nxt["drift_window_h"]))
                if nxt
                else 0.0
            )
            f = by_shift.get(s.shift_id)
            fail_ts = s.start_time + pd.Timedelta(minutes=f["minute"]) if f else pd.NaT
            state.append(
                {
                    "shift_id": s.shift_id,
                    "engine_hours_start": total,
                    "hours_since_service_start": hss,
                    "machine_health": machine_health(hss, interval, p),
                    "failure_ts": fail_ts,
                }
            )
            full = (s.end_time - s.start_time).total_seconds() / 3600
            hours = f["minute"] / 60 if f else full
            if f:
                pending.pop(0)
                lost += full - hours
                t_fail = total + hours
                down_until = fail_ts + pd.Timedelta(hours=f["downtime_h"])
                comp = f["component"]
                rows.append(
                    {
                        "machine_id": m.machine_id,
                        "event_date": fail_ts,
                        "engine_hours_at_event": t_fail,
                        "component": comp,
                        "event_type": "failure",
                        "downtime_hours": f["downtime_h"],
                        "cost_inr": None,
                        "notes": FAILURE_NOTES[comp],
                    }
                )
                rows.append(
                    {
                        "machine_id": m.machine_id,
                        "event_date": down_until,
                        "engine_hours_at_event": t_fail,
                        "component": comp,
                        "event_type": "repair",
                        "downtime_hours": 0.0,
                        "cost_inr": float(rng.uniform(*REPAIR_COST_INR[comp])),
                        "notes": REPAIR_NOTES[comp],
                    }
                )
                failures.append(
                    {
                        "machine_id": m.machine_id,
                        "shift_id": s.shift_id,
                        "component": comp,
                        "failure_ts": fail_ts,
                        "repair_ts": down_until,
                        "engine_hours_at_failure": t_fail,
                        "drift_window_h": f["drift_window_h"],
                        "downtime_h": f["downtime_h"],
                    }
                )
            total += hours
            hss += hours
            if hss >= interval:
                done = (down_until if f else s.end_time) + pd.Timedelta(minutes=30)
                rows.append(_service_row(rng, m.machine_id, done, total))
                hss = 0.0

        machines.loc[i, ["total_engine_hours", "hours_since_service", "health_score"]] = [
            round(total, 1),
            round(hss, 1),
            round(machine_health(hss, interval, 0.0), 3),
        ]

    log = (
        to_frame("maintenance_log", rows)
        .sort_values("event_date", kind="stable")
        .reset_index(drop=True)
    )
    log["id"] = np.arange(1, len(log) + 1)
    shifts = shifts[~shifts.shift_id.isin(dropped)].reset_index(drop=True)
    failure_cols = [
        "machine_id",
        "shift_id",
        "component",
        "failure_ts",
        "repair_ts",
        "engine_hours_at_failure",
        "drift_window_h",
        "downtime_h",
    ]
    return log, pd.DataFrame(state), machines, shifts, pd.DataFrame(failures, columns=failure_cols)

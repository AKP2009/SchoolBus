"""Stage 6: telemetry, one row per minute while a shift runs (synthetic_data.md, Telemetry).

Each minute gets an activity state from the task timeline (working / idle / travelling /
off). Off = engine stopped, no rows: outside shifts, and after the last task when the work
finished early (5 min cool-down idle, then the operator parks and switches off).
Signals move toward a state-dependent target that wanders slowly inside the spec range,
with AR(1) smoothing:
    x_t = a x_{t-1} + (1 - a) target_t + noise
a = 0.85 (spec) for temperatures, which have thermal mass. Signals that follow the throttle
(rpm, load, pressures, fuel rate, voltage, vibration, speed) use a = 0.3: with 0.85 they take
~15 min to settle, so a short idle spell would show 1300+ rpm and 6+ L/h, which is the
`excessive_idle` anomaly signature, and normal data would look anomalous.
Pre-failure drift and injected anomalies are added on top by anomalies.py.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from anomalies import (
    DRIFT_CODE,
    FAULT_CODE_P,
    IDLE,
    TRAVELLING,
    WORKING,
    apply_drift,
    inject,
    plan_anomalies,
)
from common import columns, rng_for

ALPHA = 0.85  # thermal signals
ALPHA_FAST = 0.3  # throttle-following signals
SLOW_SIGNALS = {"coolant_temp_c", "engine_oil_temp_c"}

# (working, idle, travelling) ranges per signal.
RANGES: dict[str, tuple[tuple[float, float], ...]] = {
    "engine_rpm": ((1600, 2000), (800, 1000), (1400, 1800)),
    "engine_load_pct": ((45, 85), (5, 15), (30, 65)),
    "coolant_temp_c": ((82, 95), (78, 88), (80, 92)),
    "engine_oil_temp_c": ((90, 105), (80, 92), (88, 100)),
    "oil_pressure_kpa": ((280, 420), (150, 250), (260, 380)),
    "hydraulic_pressure_bar": ((180, 320), (20, 40), (30, 60)),
    "battery_voltage": ((27.2, 28.4), (26.8, 27.8), (27.2, 28.4)),
    "vibration_rms_g": ((0.3, 0.7), (0.1, 0.2), (0.3, 0.6)),
}
NOISE_SD = {
    "engine_rpm": 15,
    "engine_load_pct": 2.0,
    "coolant_temp_c": 0.2,
    "engine_oil_temp_c": 0.3,
    "oil_pressure_kpa": 5,
    "hydraulic_pressure_bar": 6,
    "battery_voltage": 0.03,
    "vibration_rms_g": 0.02,
}
FUEL_WORKING = {
    "excavator": (14, 20),
    "wheel_loader": (12, 18),
    "dozer": (18, 26),
    "articulated_truck": (20, 30),
}
TANK_L = {"excavator": 345, "wheel_loader": 290, "dozer": 424, "articulated_truck": 540}
IDLE_TARGET = {  # share of shift minutes spent idle, per hidden personality
    "efficient": (0.06, 0.10),
    "average": (0.12, 0.18),
    "idler": (0.28, 0.40),
    "aggressive": (0.09, 0.13),  # keeps the machine busy
    "novice": (0.19, 0.25),  # hesitates between moves
}
UNBUCKLED_SHARE = {"novice": 0.05}  # others 0.01; see events.py for moving-time episodes
ZONE_DEG = {"articulated_truck": 0.010}  # work-zone radius; others 0.003 (~330 m)
KM_PER_DEG = 111.0
COOLDOWN_MIN = 5


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True values."""
    edges = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def _states(
    rng: np.random.Generator,
    minutes: pd.DatetimeIndex,
    work: pd.DataFrame,
    tasks: pd.DataFrame,
    is_truck: bool,
    idle_target: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Activity state, terrain slope and engine-on mask per minute."""
    n = len(minutes)
    state = np.full(n, IDLE)
    slope = np.zeros(n)
    task_rows = tasks.set_index("task_id")
    first = last = None
    for w in work.itertuples():
        a = minutes.searchsorted(w.work_start)
        b = minutes.searchsorted(w.work_end)
        first = a if first is None else first
        last = b
        slope[a:b] = task_rows.loc[w.task_id, "terrain_slope_deg"]
        if is_truck:
            # Haul cycle: queue + load 3-6 min, drive loaded, dump 1 min, drive back.
            # Waiting under the loader counts as working (engine under load, not idle).
            km = task_rows.loc[w.task_id, "haul_distance_m"] / 1000
            drive = max(1, round(km / 22 * 60))
            i = a
            while i < b:
                for st, length in (
                    (WORKING, int(rng.integers(3, 7))),
                    (TRAVELLING, drive),
                    (WORKING, 1),
                    (TRAVELLING, drive),
                ):
                    state[i : min(i + length, b)] = st
                    i += length
        else:
            state[a:b] = WORKING
    # Repositioning between tasks.
    if first is not None:
        between = np.zeros(n, dtype=bool)
        between[first:last] = True
        for w in work.itertuples():
            between[minutes.searchsorted(w.work_start) : minutes.searchsorted(w.work_end)] = False
        for a, b in _runs(between):
            state[a:b] = TRAVELLING

    engine_on = np.ones(n, dtype=bool)
    if last is not None and last < n:
        engine_on[last + COOLDOWN_MIN :] = False

    # Top up idle to the personality's share: short idle bursts inside working time.
    extra = int(idle_target * engine_on.sum()) - int((state[engine_on] == IDLE).sum())
    working_idx = np.flatnonzero(state == WORKING)
    while extra > 0 and len(working_idx):
        length = int(min(extra, rng.integers(2, 9)))
        a = int(rng.choice(working_idx))
        seg = np.arange(a, min(a + length, n))
        seg = seg[state[seg] == WORKING]
        state[seg] = IDLE
        extra -= len(seg)
        working_idx = np.flatnonzero(state == WORKING)
    return state, slope, engine_on


def _wander(rng: np.random.Generator, n: int, sd: float = 0.08) -> np.ndarray:
    """Slow random walk in [0, 1]: where in its range a signal's target sits."""
    x = rng.random()
    steps = rng.normal(0, sd, n).tolist()
    u = [x] * n
    for i in range(1, n):
        x += steps[i]
        x = 1.0 if x > 1.0 else 0.0 if x < 0.0 else x
        u[i] = x
    return np.array(u)


def _ar1(target: np.ndarray, noise: np.ndarray, alpha: float = ALPHA) -> np.ndarray:
    """x_0 = target_0, x_t = alpha x_(t-1) + (1 - alpha) target_t + noise_t, as a linear filter."""
    u = (1 - alpha) * target + noise
    u[0] = target[0]
    return lfilter([1.0], [1.0, -alpha], u)


def _shift_telemetry(
    rng: np.random.Generator,
    shift: Any,
    minutes: pd.DatetimeIndex,
    machine: Any,
    operator: Any,
    work: pd.DataFrame,
    tasks: pd.DataFrame,
    ambient: np.ndarray,
    zone_center: tuple[float, float],
) -> dict[str, Any]:
    n = len(minutes)
    mt, personality = machine.machine_type, operator.personality
    is_truck = mt == "articulated_truck"
    state, slope, engine_on = _states(
        rng, minutes, work, tasks, is_truck, rng.uniform(*IDLE_TARGET[personality])
    )
    idle, travelling = state == IDLE, state == TRAVELLING
    noise_mult = {"efficient": 0.7, "aggressive": 1.5}.get(personality, 1.0)

    out: dict[str, Any] = {"ts": minutes}
    for sig, ranges in RANGES.items():
        lo = np.choose(state, [r[0] for r in ranges])
        hi = np.choose(state, [r[1] for r in ranges])
        if sig == "engine_rpm" and personality == "idler":
            lo, hi = np.where(idle, 1200, lo), np.where(idle, 1400, hi)
        target = lo + (hi - lo) * _wander(rng, n)
        if sig == "coolant_temp_c":
            target = target + 0.15 * (ambient - 30)
        alpha = ALPHA if sig in SLOW_SIGNALS else ALPHA_FAST
        out[sig] = _ar1(target, rng.normal(0, NOISE_SD[sig] * noise_mult, n), alpha)

    # Hydraulics: spiky while working, zero for trucks; oil temp slowly follows load.
    hp = out["hydraulic_pressure_bar"] + np.where(state == WORKING, rng.normal(0, 25, n), 0)
    out["hydraulic_pressure_bar"] = np.zeros(n) if is_truck else np.clip(hp, 0, None)
    out["hydraulic_oil_temp_c"] = _ar1(
        45 + 0.4 * out["engine_load_pct"] + 0.1 * (ambient - 30), rng.normal(0, 0.1, n), alpha=0.97
    )

    # Fuel.
    w_lo, w_hi = FUEL_WORKING[mt]
    light = travelling & (not is_truck)  # tracking between spots burns less than digging
    fuel_lo = np.where(idle, 5 if personality == "idler" else 2, np.where(light, 0.6 * w_lo, w_lo))
    fuel_hi = np.where(idle, 7 if personality == "idler" else 4, np.where(light, 0.6 * w_hi, w_hi))
    fuel_rate = _ar1(
        fuel_lo + (fuel_hi - fuel_lo) * _wander(rng, n), rng.normal(0, 0.2, n), ALPHA_FAST
    )
    if personality == "aggressive":
        fuel_rate = np.where(idle, fuel_rate, fuel_rate * 1.15)
    fuel_rate = np.clip(fuel_rate, 0.5, None)
    start_level = rng.uniform(90, 100)  # refuelled at shift start
    out["fuel_rate_lph"] = fuel_rate
    out["fuel_level_pct"] = (
        start_level - np.concatenate([[0], np.cumsum(fuel_rate[:-1])]) / 60 / TANK_L[mt] * 100
    )

    # Motion.
    if is_truck:
        speed_lo, speed_hi = np.where(travelling, 10, 0), np.where(travelling, 35, 3)
    else:
        speed_lo, speed_hi = np.where(travelling, 2, 0), np.where(travelling, 5, 2)
    speed = _ar1(
        speed_lo + (speed_hi - speed_lo) * _wander(rng, n, 0.15), rng.normal(0, 0.3, n), ALPHA_FAST
    )
    out["ground_speed_kmh"] = np.where(idle, 0.0, np.clip(speed, 0, None))

    heading = rng.uniform(0, 2 * np.pi)
    tilt_sd = np.where(idle, 0.5, 2.0)
    out["pitch_deg"] = _ar1(
        slope * np.cos(heading) + rng.normal(0, 1, n) * tilt_sd, np.zeros(n), alpha=0.5
    )
    out["roll_deg"] = _ar1(
        slope * np.sin(heading) + rng.normal(0, 1, n) * tilt_sd, np.zeros(n), alpha=0.5
    )

    # GPS: random walk inside the machine's work zone, fixed while idle.
    radius = ZONE_DEG.get(mt, 0.003)
    c_lat, c_lon = zone_center
    y, x = (float(v) for v in np.array(zone_center) + rng.normal(0, radius / 3, 2))
    direction = rng.uniform(0, 2 * np.pi)
    turns = rng.normal(0, 0.3, n).tolist()
    steps = (np.maximum(out["ground_speed_kmh"], 0.2) / 60 / KM_PER_DEG).tolist()
    moving = (~idle).tolist()
    lat, lon = [0.0] * n, [0.0] * n
    for i in range(n):
        if moving[i]:
            if math.hypot(y - c_lat, x - c_lon) > radius:
                direction = math.atan2(c_lat - y, c_lon - x)  # head back toward centre
            direction += turns[i]
            y += steps[i] * math.sin(direction)
            x += steps[i] * math.cos(direction)
        lat[i], lon[i] = y, x
    out["gps_lat"], out["gps_lon"] = np.array(lat), np.array(lon)

    # Seatbelt: unbuckled only while idle here (operator steps out during an idle stretch),
    # whole stretches up to 6 min until the personality's share is reached.
    # events.py adds the moving-time episodes that go with seatbelt_unfastened events.
    belt = np.ones(n, dtype=bool)
    todo = round(UNBUCKLED_SHARE.get(personality, 0.01) * engine_on.sum())
    runs = _runs(idle & engine_on)
    for r in rng.permutation(len(runs)):
        if todo <= 0:
            break
        a, b = runs[r]
        take = min(b - a, todo, 6)
        belt[a : a + take] = False
        todo -= take
    out["seatbelt_fastened"] = belt
    out["is_idle"] = idle
    out["_state"] = state
    out["_minute"] = np.arange(n)

    out = {k: v[engine_on] for k, v in out.items()}
    m = int(engine_on.sum())
    out.update(
        fault_code=np.full(m, None, dtype=object),
        anomaly_label=np.zeros(m, dtype=bool),
        anomaly_type=np.full(m, None, dtype=object),
    )
    return out


def _fuel_level(out: dict[str, Any], tank_l: float) -> np.ndarray:
    """Level from the shift-start level and the (possibly modified) fuel rate."""
    rate = out["fuel_rate_lph"]
    used = np.concatenate([[0], np.cumsum(rate[:-1])]) / 60 / tank_l * 100
    return out["fuel_level_pct"][0] - used


def build_telemetry(
    cfg: dict[str, Any],
    shifts: pd.DataFrame,
    shift_state: pd.DataFrame,
    task_work: pd.DataFrame,
    tasks: pd.DataFrame,
    machines: pd.DataFrame,
    operators: pd.DataFrame,
    sites: pd.DataFrame,
    weather: pd.DataFrame,
    failures: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (telemetry, anomaly_events).

    anomaly_events is ground truth, not a schema table: one row per injected event and per
    labelled pre-failure window.
    """
    rng = rng_for(cfg["seed"], "telemetry")
    mach = machines.set_index("machine_id")
    ops = operators.set_index("operator_id")
    state = shift_state.set_index("shift_id")
    site_rows = sites.set_index("site_id")
    zone_rng = rng_for(cfg["seed"], "work_zones")
    zones = {
        m.machine_id: (
            site_rows.loc[m.site_id, "lat"] + zone_rng.uniform(-0.004, 0.004),
            site_rows.loc[m.site_id, "lon"] + zone_rng.uniform(-0.004, 0.004),
        )
        for m in machines.itertuples()
    }
    work_by_shift = dict(tuple(task_work.groupby("shift_id", sort=False)))
    no_work = task_work.iloc[:0]
    wx = dict(tuple(weather.groupby("site_id")))

    outs: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    for s in shifts.itertuples():
        st = state.loc[s.shift_id]
        stop = st.failure_ts if pd.notna(st.failure_ts) else s.end_time
        minutes = pd.date_range(s.start_time, stop, freq="1min", inclusive="left")
        w = wx[s.site_id]
        ambient = w.temp_c.to_numpy()[
            np.clip(w.ts.searchsorted(minutes, side="right") - 1, 0, len(w) - 1)
        ]
        machine, operator = mach.loc[s.machine_id], ops.loc[s.operator_id]
        out = _shift_telemetry(
            rng,
            s,
            minutes,
            machine,
            operator,
            work_by_shift.get(s.shift_id, no_work),
            tasks,
            ambient.astype(float),
            zones[s.machine_id],
        )
        out["_eh"] = st.engine_hours_start + out.pop("_minute") / 60
        outs.append(out)
        meta.append(
            {
                "shift_id": s.shift_id,
                "machine_id": s.machine_id,
                "operator_id": s.operator_id,
                "personality": operator.personality,
                "is_truck": machine.machine_type == "articulated_truck",
                "tank_l": TANK_L[machine.machine_type],
            }
        )

    truth: list[dict[str, Any]] = []
    if cfg["failures"] and len(failures):
        drift_rng = rng_for(cfg["seed"], "drift")
        by_machine: dict[str, list[int]] = {mid: [] for mid in machines.machine_id}
        for i, m in enumerate(meta):
            by_machine[m["machine_id"]].append(i)
        for f in failures.itertuples():
            coded = bool(drift_rng.random() < FAULT_CODE_P)
            ts = pd.DatetimeIndex(
                np.concatenate(
                    [
                        outs[i]["ts"][apply_drift(outs[i], f, coded)].to_numpy()
                        for i in by_machine[f.machine_id]
                    ]
                )
            )
            label = f"pre_failure_{f.component}"
            truth.append(
                {
                    "machine_id": f.machine_id,
                    "shift_id": f.shift_id,
                    "anomaly_type": label,
                    "start_ts": ts.min(),
                    "end_ts": f.failure_ts,
                    "duration_min": len(ts),
                    "fault_code": DRIFT_CODE.get(f.component) if coded else None,
                    "details": {"drift_window_h": round(float(f.drift_window_h), 1)},
                }
            )

    if cfg["anomalies"]:
        a_rng = rng_for(cfg["seed"], "anomalies")
        total = sum(len(o["ts"]) for o in outs)
        events = plan_anomalies(a_rng, outs, meta, round(cfg["anomaly_rate"] * total))
        touched = set()
        for ev in sorted(events, key=lambda e: (e["k"], e["start"])):
            k = ev["k"]
            detail = inject(a_rng, outs[k], ev, meta[k]["is_truck"])
            touched.add(k)
            start = outs[k]["ts"][ev["start"]]
            truth.append(
                {
                    "machine_id": meta[k]["machine_id"],
                    "shift_id": meta[k]["shift_id"],
                    "anomaly_type": ev["anomaly_type"],
                    "start_ts": start,
                    "end_ts": start + pd.Timedelta(minutes=ev["duration_min"]),
                    "duration_min": ev["duration_min"],
                    "fault_code": detail.pop("fault_code"),
                    "details": detail,
                }
            )
        for k in touched:
            outs[k]["fuel_level_pct"] = _fuel_level(outs[k], meta[k]["tank_l"])

    frames = []
    for out, m in zip(outs, meta, strict=True):
        del out["_state"], out["_eh"]
        out.update(machine_id=m["machine_id"], operator_id=m["operator_id"], shift_id=m["shift_id"])
        frames.append(pd.DataFrame(out))

    df = pd.concat(frames, ignore_index=True)
    df["id"] = np.arange(1, len(df) + 1)
    truth_df = pd.DataFrame(
        truth,
        columns=[
            "machine_id",
            "shift_id",
            "anomaly_type",
            "start_ts",
            "end_ts",
            "duration_min",
            "fault_code",
            "details",
        ],
    )
    truth_df = truth_df.sort_values("start_ts", kind="stable").reset_index(drop=True)
    return df.reindex(columns=columns("telemetry")), truth_df

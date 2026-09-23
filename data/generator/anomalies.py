"""Stage 6b: pre-failure drift and injected anomalies on top of normal telemetry.

Works on one shift's telemetry at a time, as a dict of numpy arrays (see telemetry.py).
Private keys: `_state` (activity per row) and `_eh` (engine hours per row).

Drift (synthetic_data.md, Pre-failure drift): p = clip((t - (t_f - W)) / W, 0, 1) in engine
hours; the last 20% of W is labelled `pre_failure_<component>`.

Injected anomalies (synthetic_data.md, Injected anomalies): events are placed over the whole
run until injected minutes reach anomaly_rate x telemetry rows. They never overlap each other
or a labelled pre-failure window, and keep BUFFER_MIN normal minutes around them, so every
event starts and ends on normal data. Each event is self-contained: temperatures ramp up and
come back down inside the labelled minutes, with no unlabelled tail.
Fault codes: half of the real faults (coin flip per event or failure) carry a code on the rows
past the fault threshold. sensor_glitch, excessive_idle and unsafe_operation never do (no code
exists for behaviour, and a glitch is not a fault).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import uniform_filter1d

from maintenance import LABEL_SHARE, drift_progress

WORKING, IDLE, TRAVELLING = 0, 1, 2
BUFFER_MIN = 15
FAULT_CODE_P = 0.5

# share of events, duration range in minutes
ANOMALY_TYPES: dict[str, tuple[float, tuple[int, int]]] = {
    "overheating": (0.18, (20, 40)),
    "hydraulic_leak": (0.15, (10, 30)),
    "excessive_idle": (0.20, (20, 60)),
    "battery_fault": (0.12, (30, 90)),
    "sensor_glitch": (0.20, (1, 1)),
    "unsafe_operation": (0.15, (5, 15)),
}
# "aggressive: fast but more anomalies" (synthetic_data.md); idlers idle; novices misjudge slopes.
PERSONALITY_WEIGHT = {
    "aggressive": {"overheating": 2.0, "hydraulic_leak": 2.0, "unsafe_operation": 3.0},
    "idler": {"excessive_idle": 3.0},
    "novice": {"unsafe_operation": 2.0},
}
STARTS_WORKING = {"overheating", "hydraulic_leak", "unsafe_operation"}
DRIFT_CODE = {
    "engine": "E-215",
    "cooling": "E-110",
    "hydraulics": "E-365",
    "electrical": "E-410",
}
DRIFT_CODE_FROM_P = 0.9  # pre-failure codes show in the last 10% of the drift window


# ---------------------------------------------------------------------------
# Pre-failure drift
# ---------------------------------------------------------------------------
def apply_drift(out: dict[str, Any], failure: Any, coded: bool) -> np.ndarray:
    """Add one failure's drift to a shift, label the last 20% of W. Returns the label mask."""
    p = drift_progress(out["_eh"], failure.engine_hours_at_failure, failure.drift_window_h)
    if not p.any():
        return np.zeros(len(p), dtype=bool)
    p2 = p**2
    comp = failure.component
    if comp == "hydraulics":
        out["hydraulic_oil_temp_c"] += 15 * p2
        hp = out["hydraulic_pressure_bar"]
        mean = uniform_filter1d(hp, 15, mode="nearest")
        out["hydraulic_pressure_bar"] = np.clip(mean + (hp - mean) * (1 + 2 * p), 0, None)
    elif comp == "engine":
        out["oil_pressure_kpa"] -= 120 * p2
        out["vibration_rms_g"] += 0.6 * p2
        out["coolant_temp_c"] += 8 * p2
    elif comp == "cooling":
        out["coolant_temp_c"] += 14 * p2
    elif comp == "electrical":
        out["battery_voltage"] -= 2.5 * p2
    elif comp == "undercarriage":
        out["vibration_rms_g"] += 0.8 * p2 * (out["_state"] == TRAVELLING)

    label = p >= 1 - LABEL_SHARE
    out["anomaly_label"][label] = True
    out["anomaly_type"][label] = f"pre_failure_{comp}"
    if coded and comp in DRIFT_CODE:
        out["fault_code"][p >= DRIFT_CODE_FROM_P] = DRIFT_CODE[comp]
    return label


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------
def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if not mask.any():
        return mask.copy()
    return np.convolve(mask.astype(int), np.ones(2 * r + 1, dtype=int), "same") > 0


def plan_anomalies(
    rng: np.random.Generator,
    outs: list[dict[str, Any]],
    meta: list[dict[str, Any]],
    target_minutes: int,
) -> list[dict[str, Any]]:
    """Choose (shift, type, start, duration) for every injected event."""
    n = np.array([len(o["ts"]) for o in outs], dtype=float)
    blocked = [_dilate(o["anomaly_label"], BUFFER_MIN) for o in outs]
    types = list(ANOMALY_TYPES)
    shares = np.array([ANOMALY_TYPES[t][0] for t in types])
    pick_p = {}
    for t in types:
        w = n * np.array([PERSONALITY_WEIGHT.get(m["personality"], {}).get(t, 1.0) for m in meta])
        if t == "hydraulic_leak":  # trucks show no hydraulic pressure in our data
            w *= np.array([not m["is_truck"] for m in meta])
        pick_p[t] = w / w.sum()

    events: list[dict[str, Any]] = []
    used = 0
    while used < target_minutes:
        t = str(rng.choice(types, p=shares))
        lo, hi = ANOMALY_TYPES[t][1]
        d = int(rng.integers(lo, hi + 1))
        for _ in range(50):
            k = int(rng.choice(len(outs), p=pick_p[t]))
            b = blocked[k]
            if len(b) < d:
                continue
            cs = np.concatenate([[0], np.cumsum(b)])
            ok = cs[d:] - cs[:-d] == 0
            if t in STARTS_WORKING:
                ok &= outs[k]["_state"][: len(ok)] == WORKING
            starts = np.flatnonzero(ok)
            if starts.size:
                s = int(rng.choice(starts))
                break
        else:
            continue
        b[max(0, s - BUFFER_MIN) : s + d + BUFFER_MIN] = True
        events.append({"k": k, "anomaly_type": t, "start": s, "duration_min": d})
        used += d
    return events


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------
def _ramp(d: int, up: int, down: int) -> np.ndarray:
    """0 -> 1 over `up` rows, hold, 1 -> 0 over the last `down` rows."""
    k = np.arange(d)
    return np.minimum(
        np.minimum((k + 1) / max(up, 1), 1.0), np.minimum((d - k) / max(down, 1), 1.0)
    )


def _settle_idle(
    rng: np.random.Generator,
    out: dict[str, Any],
    sl: slice,
    rpm: tuple[float, float],
    fuel: tuple[float, float],
    load: tuple[float, float],
    is_truck: bool,
) -> None:
    m = sl.stop - sl.start
    out["engine_rpm"][sl] = rng.uniform(*rpm) + rng.normal(0, 15, m)
    out["engine_load_pct"][sl] = rng.uniform(*load) + rng.normal(0, 1.5, m)
    out["fuel_rate_lph"][sl] = rng.uniform(*fuel) + rng.normal(0, 0.15, m)
    out["oil_pressure_kpa"][sl] = rng.uniform(170, 240) + rng.normal(0, 5, m)
    out["hydraulic_pressure_bar"][sl] = (
        0.0 if is_truck else rng.uniform(20, 40) + rng.normal(0, 3, m)
    )
    out["vibration_rms_g"][sl] = rng.uniform(0.12, 0.22) + rng.normal(0, 0.015, m)
    out["ground_speed_kmh"][sl] = 0.0
    out["gps_lat"][sl] = out["gps_lat"][sl.start]
    out["gps_lon"][sl] = out["gps_lon"][sl.start]
    out["is_idle"][sl] = True
    out["_state"][sl] = IDLE


def inject(
    rng: np.random.Generator, out: dict[str, Any], ev: dict[str, Any], is_truck: bool
) -> dict[str, Any]:
    """Apply one event to a shift's arrays. Returns details for the ground-truth table."""
    t, s, d = ev["anomaly_type"], ev["start"], ev["duration_min"]
    sl = slice(s, s + d)
    k = np.arange(d)
    coded = t in ("overheating", "hydraulic_leak", "battery_fault") and rng.random() < FAULT_CODE_P
    code_rows = np.zeros(d, dtype=bool)
    code = None
    detail: dict[str, Any] = {}

    if t == "overheating":
        # Coolant climbs ~1 C/min to the peak, then the operator idles it down.
        cool = max(8, round(0.3 * d))
        ramp = d - cool
        peak = rng.uniform(105, 112)
        c0 = out["coolant_temp_c"][s]
        rise = peak - c0
        rate = max(1.0, rise / ramp)
        off = np.minimum(rate * (k + 1), rise)
        top = off[ramp - 1]
        off[ramp:] = top * (0.5 / top) ** ((np.arange(cool) + 1) / cool)
        out["coolant_temp_c"][sl] += off
        out["engine_oil_temp_c"][sl] += 0.6 * off
        _settle_idle(rng, out, slice(s + ramp, s + d), (850, 950), (2, 4), (6, 12), is_truck)
        code, code_rows = "E-110", out["coolant_temp_c"][sl] > 105
        detail = {"peak_c": round(float(out["coolant_temp_c"][sl].max()), 1)}
    elif t == "hydraulic_leak":
        drop = rng.uniform(0.35, 0.60)
        out["hydraulic_pressure_bar"][sl] *= 1 - drop * np.minimum((k + 1) / 3, 1.0)
        rise = rng.uniform(8, 15)
        out["hydraulic_oil_temp_c"][sl] += rise * _ramp(d, round(0.7 * d), max(2, round(0.3 * d)))
        code, code_rows = "E-360", k >= 2
        detail = {"pressure_drop": round(float(drop), 2), "oil_temp_rise_c": round(float(rise), 1)}
    elif t == "excessive_idle":
        _settle_idle(rng, out, sl, (1300, 1500), (6, 8), (10, 20), is_truck)
    elif t == "battery_fault":
        v = rng.uniform(22.5, 23.8)
        shape = _ramp(d, 3, 3)
        sag = v + rng.normal(0, 0.08, d)  # sensor noise stays on the sagging reading
        out["battery_voltage"][sl] += (sag - out["battery_voltage"][sl]) * shape
        code, code_rows = "E-410", out["battery_voltage"][sl] < 24
        detail = {"min_voltage": round(float(v), 2)}
    elif t == "sensor_glitch":
        sig, value = [
            ("coolant_temp_c", 150.0),
            ("coolant_temp_c", 0.0),
            ("oil_pressure_kpa", 0.0),
            ("battery_voltage", 0.0),
            ("hydraulic_oil_temp_c", 150.0),
        ][int(rng.choice(5, p=[0.3, 0.2, 0.2, 0.15, 0.15]))]
        out[sig][s] = value
        detail = {"signal": sig, "value": value}
    elif t == "unsafe_operation":
        variant = "tilt" if is_truck or rng.random() < 0.5 else "harsh"
        out["is_idle"][sl] = False
        out["_state"][sl] = WORKING
        if variant == "tilt":
            # Moving fast across a steep side slope.
            tilt = rng.uniform(15, 22) * rng.choice([-1, 1])
            axis = "roll_deg" if rng.random() < 0.7 else "pitch_deg"
            out[axis][sl] = tilt * _ramp(d, 2, 2) + rng.normal(0, 0.8, d)
            out[axis][sl][1:-1] = np.where(
                np.abs(out[axis][sl][1:-1]) < 15, np.sign(tilt) * 15.5, out[axis][sl][1:-1]
            )
            lo, hi = (25, 35) if is_truck else (3.5, 6)
            out["ground_speed_kmh"][sl] = rng.uniform(lo, hi) + rng.normal(0, 0.8, d)
            out["engine_load_pct"][sl] = rng.uniform(60, 80) + rng.normal(0, 3, d)
            detail = {"variant": variant, "axis": axis, "tilt_deg": round(float(tilt), 1)}
        else:
            # Harsh lever work: load and pressure slam between extremes every minute.
            hard = k % 2 == 0
            out["engine_load_pct"][sl] = np.where(
                hard, rng.uniform(95, 100, d), rng.uniform(35, 50, d)
            )
            out["engine_rpm"][sl] = np.where(
                hard, rng.uniform(2000, 2150, d), rng.uniform(1500, 1650, d)
            )
            out["hydraulic_pressure_bar"][sl] = np.where(
                hard, rng.uniform(340, 380, d), rng.uniform(120, 180, d)
            )
            out["vibration_rms_g"][sl] = rng.uniform(0.9, 1.4, d)
            detail = {"variant": variant}

    out["anomaly_label"][sl] = True
    out["anomaly_type"][sl] = t
    if coded and code:
        out["fault_code"][s : s + d][code_rows] = code
    return {**detail, "fault_code": code if coded and code_rows.any() else None}

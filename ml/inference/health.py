"""Digital twin health score (models.md §11).

`compute_health(...)` combines the three live inputs of one machine into subsystem scores and
returns the `GET /machine/{machine_id}/health` response of docs/api_contract.md:

* rule states: the alert rule engine's open alerts (models.md §R),
* the anomaly detector's latest output (`ml.inference.anomaly.score_anomaly`),
* the latest failure prediction (`ml.inference.maintenance.predict_failure`).

Per subsystem: score = 1 − max(rule_penalty, 0.6·anomaly_contrib, failure_prob_if_component);
overall = min of the subsystems. Every input may be missing: a missing input adds no penalty and
its output field is null. Malformed values are ignored, never raised.

`rule_states_frame(telemetry)` is a stateless approximation of the §R threshold rules for
notebooks and tests (no hysteresis, no graded stages, no HYD_PRESSURE_DROP). The live backend
passes its rule engine's open alerts instead.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

SUBSYSTEMS = ("engine", "cooling", "hydraulics", "electrical", "undercarriage")
ANOMALY_WEIGHT = 0.6
RULE_PENALTY = {"info": 0.0, "warning": 0.3, "critical": 0.7, "emergency": 0.7}
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
# Colour bands (models.md §11): >= 0.75 green, 0.5-0.75 orange, < 0.5 red
GREEN_MIN = 0.75
ORANGE_MIN = 0.5

# Rule code -> subsystem. Rules not listed (SEATBELT, TIP_RISK, EXCESS_IDLE, OVERSPEED) are about
# how the machine is used, not its health.
RULE_SUBSYSTEM = {
    "COOLANT_HIGH": "cooling",
    "COOLANT_CRITICAL": "cooling",
    "HYD_OIL_HIGH": "hydraulics",
    "HYD_PRESSURE_DROP": "hydraulics",
    "OIL_PRESSURE_LOW": "engine",
    "BATTERY_LOW": "electrical",
}
# Fault code table (synthetic_data.md) -> subsystem. E-520 (seatbelt switch) is not machine health.
FAULT_CODE_SUBSYSTEM = {
    "E-110": "cooling",
    "E-215": "engine",
    "E-360": "hydraulics",
    "E-365": "hydraulics",
    "E-410": "electrical",
}
FAULT_CODE_SEVERITY = "warning"  # the fault code table has no severity column

# Anomaly signal -> subsystems (models.md §11). Vibration counts for the undercarriage while the
# machine travels, else for the engine. Signals not listed (load, fuel, pitch, roll, speed, idle)
# describe how the machine is worked, not a subsystem.
SIGNAL_SUBSYSTEMS: dict[str, tuple[str, ...]] = {
    "engine_rpm": ("engine",),
    "oil_pressure_kpa": ("engine",),
    "engine_oil_temp_c": ("engine",),
    "vibration_rms_g": ("engine",),
    "rpm_per_load": ("engine",),
    "coolant_temp_c": ("cooling",),
    "hydraulic_pressure_bar": ("hydraulics",),
    "hydraulic_oil_temp_c": ("hydraulics",),
    "coolant_minus_hyd_oil_c": ("cooling", "hydraulics"),
    "battery_voltage": ("electrical",),
}
FEATURE_SUFFIXES = ("_mean5", "_std5", "_slope15")
TRAVEL_KMH = 2.0  # same "travelling" limit as the maintenance model's undercarriage signal

SIGNAL_LABELS = {
    "engine_rpm": "engine speed",
    "oil_pressure_kpa": "oil pressure",
    "engine_oil_temp_c": "engine oil temperature",
    "vibration_rms_g": "vibration",
    "rpm_per_load": "engine speed per load",
    "coolant_temp_c": "coolant temperature",
    "hydraulic_pressure_bar": "hydraulic pressure",
    "hydraulic_oil_temp_c": "hydraulic oil temperature",
    "coolant_minus_hyd_oil_c": "coolant vs hydraulic oil temperature",
    "battery_voltage": "battery voltage",
}


# ---------------------------------------------------------------------------------------------
# Input parsing (lenient: anything malformed is skipped)
# ---------------------------------------------------------------------------------------------
def _num(x: Any) -> float | None:
    """A finite float, or None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def band(score: float) -> str:
    """Colour band of a health score: 'green', 'orange' or 'red'."""
    if score >= GREEN_MIN:
        return "green"
    return "orange" if score >= ORANGE_MIN else "red"


def _rule_subsystem(code: str, fault_code: str | None) -> str | None:
    if code in RULE_SUBSYSTEM:
        return RULE_SUBSYSTEM[code]
    if code == "FAULT_CODE":
        return FAULT_CODE_SUBSYSTEM.get(str(fault_code)) if fault_code else None
    return FAULT_CODE_SUBSYSTEM.get(code)  # a fault code given directly, e.g. "E-365"


def _iter_rules(rule_states: Any) -> Iterable[tuple[str, str, str | None]]:
    """(code, severity, fault_code) of each open rule alert.

    Accepts a mapping {code: severity} (code may be a fault code such as "E-365") or an iterable
    of alert dicts with `alert_code`, `severity` and optionally `stage` (resolved ones are
    skipped) and a fault code in `fault_code` or `evidence.fault_code`.
    """
    if rule_states is None:
        return
    if isinstance(rule_states, Mapping):
        for code, sev in rule_states.items():
            if isinstance(sev, str):
                yield str(code), sev, None
        return
    if isinstance(rule_states, str | bytes):
        return
    try:
        items = list(rule_states)
    except TypeError:
        return
    for a in items:
        if not isinstance(a, Mapping) or a.get("stage") == "resolved":
            continue
        code, sev = a.get("alert_code"), a.get("severity")
        if not isinstance(code, str) or not isinstance(sev, str):
            continue
        evidence = a.get("evidence")
        fc = a.get("fault_code") or (
            evidence.get("fault_code") if isinstance(evidence, Mapping) else None
        )
        yield code, sev, fc


def _signal_of(feature: str) -> str:
    for suf in FEATURE_SUFFIXES:
        if feature.endswith(suf):
            return feature[: -len(suf)]
    return feature


def signal_subsystems(feature: str, travelling: bool = False) -> tuple[str, ...]:
    """Subsystems a model-1 feature (e.g. 'hydraulic_oil_temp_c_mean5') belongs to."""
    sig = _signal_of(feature)
    if sig == "vibration_rms_g" and travelling:
        return ("undercarriage",)
    return SIGNAL_SUBSYSTEMS.get(sig, ())


# ---------------------------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------------------------
def _iso(ts: Any) -> str:
    try:
        t = pd.Timestamp(ts)
        if pd.isna(t):
            raise ValueError
    except (TypeError, ValueError):
        t = pd.Timestamp(datetime.now(UTC))
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.isoformat().replace("+00:00", "Z")


def compute_health(
    machine_id: str,
    ts: Any = None,
    rule_states: Any = None,
    anomaly: Mapping[str, Any] | None = None,
    maintenance: Mapping[str, Any] | None = None,
    travelling: bool = False,
) -> dict[str, Any]:
    """Subsystem health of one machine (models.md §11, /machine/{id}/health response).

    rule_states: open rule alerts (see `_iter_rules`), or None.
    anomaly: `score_anomaly` output, or None. It counts only when `kind == 'machine_fault'`:
      the detector's score on normal minutes has median 0.20 and p90 0.49, so counting it always
      would turn about 1 in 10 normal minutes orange. Glitches are sensor, not machine, faults.
      The fault's score then applies to every subsystem among its `top_signals`.
    maintenance: `predict_failure` output, or None. Its probability applies to `likely_component`.
    travelling: ground speed > 2 km/h at `ts`; vibration then counts for the undercarriage.
    ts: defaults to the anomaly's ts, else now (UTC).

    Returns the contract fields plus `band`, `subsystem_bands` (green / orange / red) and
    `reasons` (the penalties behind the scores, largest first).
    """
    anomaly = anomaly if isinstance(anomaly, Mapping) else None
    maintenance = maintenance if isinstance(maintenance, Mapping) else None
    penalty = dict.fromkeys(SUBSYSTEMS, 0.0)
    reasons: list[dict[str, Any]] = []

    def hit(sub: str, value: float, source: str, text: str) -> None:
        value = min(max(value, 0.0), 1.0)
        if value <= 0:
            return
        penalty[sub] = max(penalty[sub], value)
        reasons.append(
            {"subsystem": sub, "source": source, "penalty": round(value, 3), "text": text}
        )

    # Rule states: worst open alert per subsystem
    worst: dict[str, tuple[int, str, str]] = {}
    for code, sev, fc in _iter_rules(rule_states):
        sev = sev.lower()
        sub = _rule_subsystem(code, fc)
        if sub is None or sev not in RULE_PENALTY:
            continue
        name = fc if code == "FAULT_CODE" and fc else code
        if sub not in worst or SEVERITY_RANK[sev] > worst[sub][0]:
            worst[sub] = (SEVERITY_RANK[sev], sev, name)
    for sub, (_, sev, name) in worst.items():
        hit(sub, RULE_PENALTY[sev], "rule", f"{name} {sev}")

    # Anomaly detector
    anomaly_score = _num(anomaly.get("anomaly_score")) if anomaly else None
    if anomaly and anomaly_score is not None and anomaly.get("kind") == "machine_fault":
        subs: dict[str, str] = {}
        top = anomaly.get("top_signals")
        for s in top if isinstance(top, list) else []:
            feat = s.get("feature") if isinstance(s, Mapping) else None
            if isinstance(feat, str):
                for sub in signal_subsystems(feat, travelling):
                    subs.setdefault(sub, SIGNAL_LABELS.get(_signal_of(feat), _signal_of(feat)))
        for sub, label in subs.items():
            hit(sub, ANOMALY_WEIGHT * anomaly_score, "anomaly", f"Unusual {label}")

    # Failure prediction
    p = _num(maintenance.get("failure_probability")) if maintenance else None
    component = maintenance.get("likely_component") if maintenance else None
    component = component if isinstance(component, str) else None
    if p is not None and component in SUBSYSTEMS:
        hit(component, p, "failure_probability", f"{round(100 * p)}% failure risk in the next 48 h")

    scores = {s: round(1.0 - penalty[s], 3) for s in SUBSYSTEMS}
    overall = min(scores.values())
    reasons.sort(key=lambda r: -r["penalty"])
    return {
        "machine_id": str(machine_id),
        "ts": _iso(ts if ts is not None else (anomaly or {}).get("ts")),
        "overall": overall,
        "subsystems": scores,
        "anomaly_score": round(anomaly_score, 4) if anomaly_score is not None else None,
        "failure_probability": round(p, 3) if p is not None else None,
        "likely_component": component,
        "band": band(overall),
        "subsystem_bands": {s: band(v) for s, v in scores.items()},
        "reasons": reasons,
    }


def to_snapshot_row(health: Mapping[str, Any]) -> dict[str, Any]:
    """`machine_health_snapshots` row (001_init.sql) from a `compute_health` result."""
    sub = health["subsystems"]
    return {
        "machine_id": health["machine_id"],
        "ts": health["ts"],
        "overall_score": health["overall"],
        **{f"{s}_score": sub[s] for s in SUBSYSTEMS},
        "anomaly_score": health.get("anomaly_score"),
        "details": {
            k: health.get(k) for k in ("failure_probability", "likely_component", "band", "reasons")
        },
    }


# ---------------------------------------------------------------------------------------------
# Rule states from telemetry (offline approximation of models.md §R)
# ---------------------------------------------------------------------------------------------
RULE_INPUT_COLUMNS = [
    "ts",
    "machine_id",
    "coolant_temp_c",
    "hydraulic_oil_temp_c",
    "oil_pressure_kpa",
    "engine_rpm",
    "battery_voltage",
    "fault_code",
]


def rule_states_frame(telemetry: pd.DataFrame) -> pd.DataFrame:
    """Per telemetry minute: machine_id, ts and `rule_states` ({code: severity}, possibly empty).

    Threshold rules of models.md §R that concern machine health, on glitch-cleaned readings
    (model 1's rule, so a single 150 °C spike raises nothing): COOLANT_HIGH (> 100 for 2 min),
    COOLANT_CRITICAL (> 105), HYD_OIL_HIGH (> 90 warning, > 95 critical), OIL_PRESSURE_LOW
    (< 100 kPa at > 1200 rpm), BATTERY_LOW (< 24 V for 5 min) and fault codes. Stateless: no
    hysteresis, no graded stages, no HYD_PRESSURE_DROP. Returned sorted by (machine_id, ts).
    """
    from ml.inference import anomaly as A

    cols = list(dict.fromkeys([*RULE_INPUT_COLUMNS, *A.SIGNALS]))
    df = telemetry.loc[:, cols].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)
    clean, _ = A.mark_glitches(df)

    def lasting(cond: pd.Series, minutes: int) -> pd.Series:
        """cond held on every reading of the trailing window (a minute-rolling min)."""
        s = pd.Series(cond.astype(float).to_numpy(), index=clean["ts"])
        held = s.groupby(clean["machine_id"].to_numpy()).transform(
            lambda x: x.rolling(f"{minutes}min").min()
        )
        return pd.Series(held.to_numpy() >= 1, index=clean.index)

    cool = clean["coolant_temp_c"].astype(float)
    hyd = clean["hydraulic_oil_temp_c"].astype(float)
    fires = {
        "COOLANT_CRITICAL": np.where(cool > 105, "critical", None),
        "COOLANT_HIGH": np.where(lasting(cool > 100, 2) & (cool <= 105), "warning", None),
        "HYD_OIL_HIGH": np.where(hyd > 95, "critical", np.where(hyd > 90, "warning", None)),
        "OIL_PRESSURE_LOW": np.where(
            (clean["oil_pressure_kpa"].astype(float) < 100)
            & (clean["engine_rpm"].astype(float) > 1200),
            "critical",
            None,
        ),
        "BATTERY_LOW": np.where(
            lasting(clean["battery_voltage"].astype(float) < 24.0, 5), "warning", None
        ),
    }
    fault = df["fault_code"].where(df["fault_code"].notna(), None).to_numpy()
    states = []
    for i in range(len(df)):
        st = {code: str(v[i]) for code, v in fires.items() if v[i] is not None}
        if fault[i] is not None and fault[i] in FAULT_CODE_SUBSYSTEM:
            st[str(fault[i])] = FAULT_CODE_SEVERITY
        states.append(st)
    return pd.DataFrame({"machine_id": df["machine_id"], "ts": df["ts"], "rule_states": states})

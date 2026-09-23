"""Stage 7-8: fatigue_log, safety_events, incidents, training_records.

Fatigue (synthetic_data.md, Fatigue and safety; score and levels from models.md §5):
    perclos = clip(0.04 + 0.012*hours_into_shift + 0.04*night + N(0, 0.015), 0, 0.6)
    yawns/min     ~ Poisson(0.02  + 1.2 * max(0, perclos - 0.08))
    head-down/min ~ Poisson(0.005 + 0.8 * max(0, perclos - 0.08))
These rates are our choice: they make late night shifts reach 'high' about half the time,
so the fatigue x2.5 multiplier on safety events is visible in the data.

Each injected unsafe_operation anomaly also logs the safety event the cab would raise
(tip_risk for the tilt variant, harsh_maneuver for harsh lever work). Incidents: 3% of
critical safety events, plus one per failure (spill_leak for hydraulics, else
equipment_damage).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common import columns, ist_to_utc, rng_for, to_frame, uuid_from

# Base rates per operator-hour; `moving` = only while the machine is not idle.
SAFETY_RATES = {
    "proximity_breach": (0.08, False),
    "blindspot_intrusion": (0.04, False),
    "seatbelt_unfastened": (0.02, True),
    "phone_use": (0.03, False),
    "harsh_maneuver": (0.05, True),
    "tip_risk": (0.01, True),
    "geofence_breach": (0.01, True),
}
PERSONALITY_MULT = {
    "novice": {"proximity_breach": 2.0, "seatbelt_unfastened": 2.0},
    "aggressive": {"harsh_maneuver": 3.0},
}
FATIGUE_HIGH_MULT = 2.5
INCIDENT_P = 0.03
FATIGUE_HIGH_REFRACTORY_MIN = 60

INCIDENT_TEXT = {
    "near_miss": "Worker walked into the {sector} swing zone; operator stopped in time.",
    "near_miss_tip": "Machine lifted a track on a side slope; operator lowered the load.",
    "collision": "Machine touched a parked light vehicle while reversing.",
    "injury": "Ground worker twisted an ankle stepping away from the machine.",
    "equipment_damage": "Mirror broken against a stockpile edge.",
}
FAILURE_INCIDENT = {
    "hydraulics": (
        "spill_leak",
        "Hydraulic hose burst, about {litres} L of oil on the ground. Spill kit used.",
    ),
    "engine": ("equipment_damage", "Engine failure, machine stopped and parked for repair."),
    "cooling": ("equipment_damage", "Engine overheated and was shut down, coolant lost."),
    "electrical": ("equipment_damage", "Electrical failure, machine would not restart."),
    "undercarriage": ("equipment_damage", "Undercarriage failure, track damaged."),
}
FAILURE_ROOT_CAUSE = {
    "hydraulics": "Worn hose; hydraulic oil ran hot for days before the failure.",
    "engine": "Oil pump wear; oil pressure fell over the last days.",
    "cooling": "Water pump failing; coolant temperature crept up.",
    "electrical": "Alternator failing; battery voltage sagged over the last days.",
    "undercarriage": "Worn rollers; vibration while travelling rose over the last days.",
}


def fatigue_level(score: np.ndarray) -> np.ndarray:
    return np.where(score < 0.35, "low", np.where(score < 0.6, "medium", "high"))


def _fatigue(rng: np.random.Generator, n: int, night: bool) -> dict[str, np.ndarray]:
    hours = np.arange(n) / 60
    perclos = np.clip(0.04 + 0.012 * hours + 0.04 * night + rng.normal(0, 0.015, n), 0, 0.6)
    excess = np.maximum(0, perclos - 0.08)
    yawns = rng.poisson(0.02 + 1.2 * excess)
    head_down = rng.poisson(0.005 + 0.8 * excess)
    last10 = np.ones(10)
    yawns_10 = np.convolve(yawns, last10)[:n]
    head_10 = np.convolve(head_down, last10)[:n]
    score = (
        0.45 * np.minimum(perclos / 0.3, 1)
        + 0.15 * np.minimum(yawns_10 / 3, 1)
        + 0.15 * np.minimum(head_10 / 3, 1)
        + 0.15 * np.minimum(hours / 10, 1)
        + 0.10 * night
    )
    return {
        "ear_avg": np.clip(0.31 - 0.25 * perclos + rng.normal(0, 0.01, n), 0.15, 0.36),
        "perclos_60s": perclos,
        "yawn_count": yawns,
        "head_down_events": head_down,
        "fatigue_score": score,
        "fatigue_level": fatigue_level(score),
    }


def _safety_row(
    rng: np.random.Generator,
    event_type: str,
    ts: pd.Timestamp,
    shift: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": ts,
        "site_id": shift.site_id,
        "machine_id": shift.machine_id,
        "operator_id": shift.operator_id,
        "event_type": event_type,
        "resolved": True,
        "details": {"source": "synthetic"},
    }
    if event_type in ("proximity_breach", "blindspot_intrusion"):
        dist = float(rng.uniform(0.8, 7.0))
        sectors = (
            ["front", "rear", "left", "right"]
            if event_type == "proximity_breach"
            else ["rear", "left", "right"]
        )
        row.update(
            distance_m=dist,
            sector=str(rng.choice(sectors)),
            approaching=bool(rng.random() < 0.4),
            severity="critical" if dist < 3 else "warning",
        )
        row["details"] |= {"class": "person", "conf": round(float(rng.uniform(0.5, 0.95)), 2)}
    elif event_type == "phone_use":
        row.update(sector="cab", severity="warning")
    elif event_type == "harsh_maneuver":
        row.update(severity="warning" if rng.random() < 0.3 else "info")
    elif event_type == "tip_risk":
        row.update(severity="critical")
    else:  # seatbelt_unfastened, geofence_breach, fatigue_high
        row.update(severity="warning")
    if extra:
        row["details"] |= extra
    return row


def build_events(
    cfg: dict[str, Any],
    shifts: pd.DataFrame,
    telemetry: pd.DataFrame,
    operators: pd.DataFrame,
    anomaly_events: pd.DataFrame,
    failures: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (fatigue_log, safety_events, incidents).

    Also patches telemetry.seatbelt_fastened in place for seatbelt_unfastened events.
    """
    rng = rng_for(cfg["seed"], "events")
    personality = operators.set_index("operator_id").personality
    fatigue_rows: list[pd.DataFrame] = []
    safety: list[dict[str, Any]] = []
    rows_by_shift = telemetry.groupby("shift_id", sort=False).indices
    unbuckle: list[np.ndarray] = []

    for s in shifts.itertuples():
        idx = rows_by_shift.get(s.shift_id, np.array([], dtype=int))
        tel = telemetry.iloc[idx]
        n = len(tel)
        f = _fatigue(rng, n, s.shift_type == "night")
        moving = ~tel.is_idle.to_numpy(dtype=bool)
        high = f["fatigue_level"] == "high"
        phone = np.zeros(n, dtype=bool)
        mults = PERSONALITY_MULT.get(personality[s.operator_id], {})

        for event_type, (rate, needs_moving) in SAFETY_RATES.items():
            p = rate / 60 * mults.get(event_type, 1.0) * np.where(high, FATIGUE_HIGH_MULT, 1.0)
            hits = rng.random(n) < p
            if needs_moving:
                hits &= moving
            for i in np.flatnonzero(hits):
                ts = tel.ts.iloc[i] + pd.Timedelta(seconds=int(rng.integers(0, 60)))
                safety.append(_safety_row(rng, event_type, ts, s))
                if event_type == "phone_use":
                    phone[i] = True
                if event_type == "seatbelt_unfastened":
                    # Matching unbuckled stretch in telemetry while moving.
                    seg = np.arange(i, min(i + int(rng.integers(1, 6)), n))
                    unbuckle.append(idx[seg[moving[seg]]])

        last_high = -FATIGUE_HIGH_REFRACTORY_MIN
        for i in np.flatnonzero(high & ~np.roll(high, 1)):
            if i - last_high >= FATIGUE_HIGH_REFRACTORY_MIN:
                safety.append(
                    _safety_row(
                        rng,
                        "fatigue_high",
                        tel.ts.iloc[i],
                        s,
                        {"fatigue_score": round(float(f["fatigue_score"][i]), 3)},
                    )
                )
                last_high = i

        fatigue_rows.append(
            pd.DataFrame(
                {
                    "ts": tel.ts.to_numpy(),
                    "operator_id": s.operator_id,
                    "shift_id": s.shift_id,
                    **f,
                    "phone_detected": phone,
                }
            )
        )

    if unbuckle:
        belt = telemetry["seatbelt_fastened"].to_numpy(dtype=bool, copy=True)
        belt[np.concatenate(unbuckle)] = False
        telemetry["seatbelt_fastened"] = belt

    shift_rows = shifts.set_index("shift_id")
    for ev in anomaly_events[anomaly_events.anomaly_type == "unsafe_operation"].itertuples():
        s = shift_rows.loc[ev.shift_id]
        s_row = pd.Series({**s.to_dict(), "shift_id": ev.shift_id})
        if ev.details.get("variant") == "tilt":
            extra = {"axis": ev.details["axis"], "tilt_deg": ev.details["tilt_deg"]}
            safety.append(_safety_row(rng, "tip_risk", ev.start_ts, s_row, extra))
        else:
            row = _safety_row(rng, "harsh_maneuver", ev.start_ts, s_row, {"repeated": True})
            row["severity"] = "warning"
            safety.append(row)

    fatigue = pd.concat(fatigue_rows, ignore_index=True) if fatigue_rows else pd.DataFrame()
    fatigue["id"] = np.arange(1, len(fatigue) + 1)
    fatigue = fatigue.reindex(columns=columns("fatigue_log"))

    safety_df = (
        to_frame("safety_events", safety).sort_values("ts", kind="stable").reset_index(drop=True)
    )
    safety_df["id"] = np.arange(1, len(safety_df) + 1)

    incidents = []
    for e in safety_df[safety_df.severity == "critical"].itertuples():
        if rng.random() >= INCIDENT_P:
            continue
        itype = str(
            rng.choice(
                ["near_miss", "collision", "injury", "equipment_damage"], p=[0.8, 0.1, 0.05, 0.05]
            )
        )
        via = "voice" if rng.random() < 0.3 else "form"
        key = "near_miss_tip" if itype == "near_miss" and e.event_type == "tip_risk" else itype
        desc = INCIDENT_TEXT[key].format(sector=e.sector if isinstance(e.sector, str) else "rear")
        incidents.append(
            {
                "client_id": uuid_from(rng),
                "ts": e.ts + pd.Timedelta(minutes=float(rng.uniform(5, 40))),
                "site_id": e.site_id,
                "machine_id": e.machine_id,
                "operator_id": e.operator_id,
                "incident_type": itype,
                "severity": "warning" if itype == "near_miss" else "critical",
                "description": desc,
                "injury": itype == "injury",
                "damage_description": desc if itype in ("collision", "equipment_damage") else None,
                "reported_via": via,
                "voice_transcript": desc.lower() if via == "voice" else None,
                "linked_event_id": e.id,
                "media_paths": [],
                "status": "closed",
            }
        )
    site_of = shifts.set_index("shift_id").site_id
    for f in failures.itertuples():
        itype, text = FAILURE_INCIDENT[f.component]
        desc = text.format(litres=int(rng.integers(20, 120)))
        incidents.append(
            {
                "client_id": uuid_from(rng),
                "ts": f.failure_ts + pd.Timedelta(minutes=float(rng.uniform(5, 30))),
                "site_id": site_of[f.shift_id],
                "machine_id": f.machine_id,
                "operator_id": shift_rows.loc[f.shift_id, "operator_id"],
                "incident_type": itype,
                "severity": "critical",
                "description": desc,
                "injury": False,
                "damage_description": desc,
                "root_cause": FAILURE_ROOT_CAUSE[f.component],
                "reported_via": "form",
                "voice_transcript": None,
                "linked_event_id": None,
                "media_paths": [],
                "status": "closed",
            }
        )
    incidents = sorted(incidents, key=lambda r: r["ts"])
    incidents_df = to_frame("incidents", incidents)
    incidents_df["id"] = np.arange(1, len(incidents_df) + 1)
    return fatigue, safety_df, incidents_df


def build_training_records(
    cfg: dict[str, Any], operators: pd.DataFrame, modules: pd.DataFrame
) -> pd.DataFrame:
    """0-3 modules per operator, started in the 60 days before the simulated period."""
    rng = rng_for(cfg["seed"], "training_records")
    start = ist_to_utc(cfg["start_date"], "00:00")
    rows = []
    for op in operators.itertuples():
        for mi in rng.choice(len(modules), size=int(rng.integers(0, 4)), replace=False):
            mod = modules.iloc[mi]
            started = start - pd.Timedelta(days=float(rng.uniform(1, 60)))
            row: dict[str, Any] = {
                "client_id": uuid_from(rng),
                "operator_id": op.operator_id,
                "module_id": mod.module_id,
                "started_at": started,
            }
            if rng.random() < 0.85:
                score = float(
                    np.clip(rng.normal(62 if op.personality == "novice" else 80, 12), 20, 100)
                )
                row.update(
                    completed_at=started
                    + pd.Timedelta(minutes=float(mod.duration_min * rng.uniform(1, 2))),
                    score=score,
                    passed=score >= 70,
                )
            rows.append(row)
    df = to_frame("training_records", rows)
    df["id"] = np.arange(1, len(df) + 1)
    return df

"""Stage 9: shifts.handover_notes, issues_reported and fuel from what happened in the shift.

Notes are messy on purpose (occasional typos) so the LLM handover summary has to cope.
A breakdown is always noted. Faults the operator can see on the dash (overheating,
hydraulic pressure loss, battery warning) are noted 70% of the time and a sensor glitch 30%;
behaviour anomalies (excessive idle, unsafe operation) never are.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common import ist_hhmm, rng_for

HYD_HOT_C = 78.0  # top of the normal working range; the rule engine warns at 90
TYPO_P = 0.25
NOTICED_P = 0.7
GLITCH_NOTICED_P = 0.3
BREAKDOWN_TEXT = {
    "hydraulics": "hyd hose burst",
    "engine": "engine lost oil pressure",
    "cooling": "engine overheated, coolant boiling",
    "electrical": "electrical fault, would not restart",
    "undercarriage": "track problem, heavy rattle",
}


def _fault_note(ev: Any) -> tuple[str, str, float] | None:
    """(text, tag, probability the operator writes it down) for an injected anomaly."""
    t = ist_hhmm(ev.start_ts)
    d = ev.details
    if ev.anomaly_type == "overheating":
        return (
            f"Coolant went up to {d['peak_c']:.0f} around {t}, idled it down.",
            "coolant_high",
            NOTICED_P,
        )
    if ev.anomaly_type == "hydraulic_leak":
        return (
            f"Hyd pressure dropped around {t}, check for leak.",
            "hydraulic_pressure_low",
            NOTICED_P,
        )
    if ev.anomaly_type == "battery_fault":
        return (f"Battery warning light came on around {t}.", "battery_low", NOTICED_P)
    if ev.anomaly_type == "sensor_glitch":
        return (
            f"Gauge jumped for a moment around {t}, probably a sensor.",
            "sensor_glitch_suspected",
            GLITCH_NOTICED_P,
        )
    return None


def _typo(rng: np.random.Generator, text: str) -> str:
    words = text.split(" ")
    long = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    if not long:
        return text
    i = int(rng.choice(long))
    j = int(rng.integers(1, len(words[i]) - 1))
    w = words[i]
    words[i] = w[: j - 1] + w[j] + w[j - 1] + w[j + 1 :]
    return " ".join(words)


def build_handover(
    cfg: dict[str, Any],
    shifts: pd.DataFrame,
    machines: pd.DataFrame,
    tasks: pd.DataFrame,
    task_work: pd.DataFrame,
    telemetry: pd.DataFrame,
    safety_events: pd.DataFrame,
    anomaly_events: pd.DataFrame,
    failures: pd.DataFrame,
) -> pd.DataFrame:
    rng = rng_for(cfg["seed"], "handover")
    shifts = shifts.copy()
    mtype = machines.set_index("machine_id").machine_type
    task_rows = tasks.set_index("task_id")
    rows_by_shift = telemetry.groupby("shift_id", sort=False).indices
    events_by_shift = dict(tuple(anomaly_events.groupby("shift_id", sort=False)))
    failure_by_shift = failures.set_index("shift_id")
    out: dict[str, list[Any]] = {
        "fuel_start_pct": [],
        "fuel_end_pct": [],
        "handover_notes": [],
        "issues_reported": [],
    }

    for s in shifts.itertuples():
        tel = telemetry.iloc[rows_by_shift[s.shift_id]]
        fuel_start = float(tel.fuel_level_pct.iloc[0])
        fuel_end = float(tel.fuel_level_pct.iloc[-1])
        candidates: list[tuple[str, str]] = []
        must: list[tuple[str, str]] = []
        if s.shift_id in failure_by_shift.index:
            f = failure_by_shift.loc[s.shift_id]
            must.append(
                (
                    f"Machine broke down at {ist_hhmm(f.failure_ts)}, "
                    f"{BREAKDOWN_TEXT[f.component]}. Maintenance called.",
                    "breakdown",
                )
            )
        if s.shift_id in events_by_shift:
            for ev in events_by_shift[s.shift_id].itertuples():
                note = _fault_note(ev)
                if note and rng.random() < note[2]:
                    candidates.append(note[:2])

        hot = tel.hydraulic_oil_temp_c.to_numpy()
        if hot.max() > HYD_HOT_C:
            ts = tel.ts.iloc[int(hot.argmax())]
            candidates.append(
                (
                    f"Hydraulic oil ran hot around {ist_hhmm(ts)}, kept load light.",
                    "hydraulic_temp_high",
                )
            )

        for w in task_work[
            (task_work.shift_id == s.shift_id) & (task_work.quantity_left > 0)
        ].itertuples():
            t = task_rows.loc[w.task_id]
            unit = "t" if t.unit == "tons" else "m3"
            candidates.append(
                (
                    f"Task {t.sequence_no} not finished, {w.quantity_left:g} {unit} left.",
                    "unfinished_task",
                )
            )

        near = safety_events[
            (safety_events.machine_id == s.machine_id)
            & (safety_events.ts >= s.start_time)
            & (safety_events.ts < s.end_time)
            & safety_events.event_type.isin(["proximity_breach", "blindspot_intrusion"])
        ]
        if len(near) >= 2:
            sector = near.sector.mode().iloc[0]
            candidates.append(
                (
                    f"People walking near the machine, watch the {sector} side.",
                    "people_near_machine",
                )
            )

        if mtype[s.machine_id] in ("excavator", "wheel_loader") and rng.random() < 0.08:
            candidates.append(("Left bucket teeth worn, check.", "bucket_teeth_worn"))
        if rng.random() < 0.35:
            candidates.append((f"Refuelled to {round(fuel_start)}%.", "refuelled"))

        order = rng.permutation(len(candidates))[: 3 - len(must)]
        picked = must + [candidates[k] for k in sorted(order)]
        notes = [text for text, _ in picked] or ["No issues."]
        if rng.random() < TYPO_P:
            k = int(rng.integers(len(notes)))
            notes[k] = _typo(rng, notes[k])

        out["fuel_start_pct"].append(fuel_start)
        out["fuel_end_pct"].append(fuel_end)
        out["handover_notes"].append(" ".join(notes))
        out["issues_reported"].append([tag for _, tag in picked if tag != "refuelled"])

    for col, values in out.items():
        shifts[col] = pd.Series(values, index=shifts.index, dtype=object)
    return shifts

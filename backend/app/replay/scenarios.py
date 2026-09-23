"""Scripted demo scenarios spliced into the live replay stream (POST /scenario/{name}).

A scenario takes over one machine's stream for its duration: the replay engine stops emitting
that machine's recorded rows and emits the scenario's rows instead, one every `period_s` of data
time, starting from the machine's latest recorded row (the template: GPS, fuel, operator, shift
stay real). Recorded rows keep updating the template in the background and take over again when
the scenario ends.

Each script ends with the signal back to normal for more than 2 minutes, so its alerts resolve
(hysteresis) and the scenario is self-contained. Timings (data minutes from the trigger) with the
default thresholds.yaml:

overheating     coolant 90 -> 112 °C, then idle cool-down. COOLANT_HIGH warn @5;
                COOLANT_CRITICAL warn @7, derate @9, recommend_shutdown @10 (value rising),
                escalated @12 (not acknowledged; @14 if acknowledged), resolved @22.
                COOLANT_HIGH resolved @23.
hydraulic_leak  stationary working, pressure 250 -> 112 bar over 3 min (-55 %), oil temp +12 °C;
                HYD_PRESSURE_DROP warn @9, derate @11, recommend_shutdown @14, escalated @16,
                pressure restored @18, resolved @19.
tip_risk        moving 4 km/h on a side slope, roll 8 -> 27°. TIP_RISK warn @2 (warning),
                critical @4, escalated @6, resolved @9.
seatbelt        5-second samples, moving 3 km/h unbuckled for 3 min. SEATBELT warn @0 s,
                critical @25 s, escalated @145 s, buckled @185 s, resolved @300 s.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

Overrides = dict[str, Any]


@dataclass
class Scenario:
    name: str
    period_s: int
    duration_s: int
    script: Callable[[float, dict[str, Any]], Overrides]  # (offset_s, template) -> overrides
    start_ts: datetime | None = None
    template: dict[str, Any] = field(default_factory=dict)
    next_offset_s: float = 0.0

    @property
    def end_ts(self) -> datetime:
        assert self.start_ts is not None
        return self.start_ts + timedelta(seconds=self.duration_s)

    def due_rows(self, until: datetime) -> list[dict[str, Any]]:
        """Scenario rows with ts <= until that haven't been emitted yet."""
        assert self.start_ts is not None
        out = []
        while self.next_offset_s < self.duration_s:
            ts = self.start_ts + timedelta(seconds=self.next_offset_s)
            if ts > until:
                break
            row = {**self.template, **self.script(self.next_offset_s, self.template)}
            row["ts"] = ts
            row["_period_s"] = float(self.period_s)
            row["_scenario"] = self.name
            out.append(row)
            self.next_offset_s += self.period_s
        return out

    @property
    def finished(self) -> bool:
        return self.next_offset_s >= self.duration_s


def _lerp(a: float, b: float, x: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, x))


def _wiggle(m: float, amp: float) -> float:
    """Deterministic small variation so gauges don't look frozen."""
    return amp * math.sin(1.7 * m + 0.3)


def working(m: float, tpl: dict[str, Any]) -> Overrides:
    """A consistent working profile. Every signal a rule reads is set, so recorded rows going idle
    in the background can't mix with the script (full load with idle hydraulic pressure would
    look like a leak). Trucks have no working hydraulics."""
    truck = tpl.get("_machine_type") == "articulated_truck"
    return {
        "is_idle": False,
        "engine_rpm": 1820.0 + _wiggle(m, 40),
        "engine_load_pct": 72.0 + _wiggle(m, 4),
        "coolant_temp_c": 88.0 + _wiggle(m, 0.5),
        "engine_oil_temp_c": 96.0 + _wiggle(m, 0.5),
        "oil_pressure_kpa": 350.0 + _wiggle(m, 10),
        "hydraulic_pressure_bar": 0.0 if truck else 240.0 + _wiggle(m, 8),
        "hydraulic_oil_temp_c": 68.0 + _wiggle(m, 0.5),
        "fuel_rate_lph": 16.0 + _wiggle(m, 0.5),
        "battery_voltage": 27.8,
        "vibration_rms_g": 0.5,
        "ground_speed_kmh": 1.5,
        "pitch_deg": 1.0,
        "roll_deg": 1.0,
        "seatbelt_fastened": True,
        "fault_code": None,
    }


# ---------------------------------------------------------------------------------------------
def overheating(offset_s: float, tpl: dict[str, Any]) -> Overrides:
    m = offset_s / 60
    c0 = min(float(tpl.get("coolant_temp_c") or 88.0), 92.0)
    if m <= 4:
        coolant = _lerp(c0, 101.0, m / 4)
    elif m <= 12:
        coolant = min(101.0 + 1.5 * (m - 4), 112.0)
    elif m <= 18:
        coolant = 112.0 + _wiggle(m, 0.3)
    else:  # idle cool-down after the operator follows the shutdown steps
        coolant = 88.0 + 24.0 * math.exp(-(m - 18) / 3)
    out = {
        **working(m, tpl),
        "coolant_temp_c": round(coolant, 2),
        "engine_oil_temp_c": round(95.0 + 0.6 * (coolant - 90.0), 2),
    }
    if m > 18:  # idle to cool
        truck = tpl.get("_machine_type") == "articulated_truck"
        out.update(
            is_idle=True,
            engine_rpm=900.0 + _wiggle(m, 15),
            engine_load_pct=10.0,
            fuel_rate_lph=3.0,
            ground_speed_kmh=0.0,
            oil_pressure_kpa=200.0,
            hydraulic_pressure_bar=0.0 if truck else 30.0,
        )
    return out


def hydraulic_leak(offset_s: float, tpl: dict[str, Any]) -> Overrides:
    m = offset_s / 60
    p0 = 250.0
    if m < 6:
        pressure = p0 + _wiggle(m, 6)
    elif m < 9:  # falls 55 % over 3 min
        pressure = _lerp(p0, 0.45 * p0, (m - 5) / 4)
    elif m < 18:
        pressure = 0.45 * p0 + _wiggle(m, 3)
    else:  # hose fixed / pressure restored
        pressure = p0 + _wiggle(m, 6)
    t0 = 68.0
    oil = t0 + (_lerp(0, 12, (m - 6) / 8) if m < 18 else _lerp(12, 0, (m - 18) / 4))
    return {
        **working(m, tpl),
        "engine_load_pct": 66.0 + _wiggle(m, 5),
        "ground_speed_kmh": 0.0,
        "hydraulic_pressure_bar": round(pressure, 1),
        "hydraulic_oil_temp_c": round(oil, 2),
    }


def tip_risk(offset_s: float, tpl: dict[str, Any]) -> Overrides:
    m = offset_s / 60
    roll = [8, 13, 17, 20, 26, 27, 26, 18, 12, 8, 6, 5][min(int(m), 11)]
    return {
        **working(m, tpl),
        "ground_speed_kmh": 4.0 if m < 7 else 1.0,
        "roll_deg": float(roll),
        "pitch_deg": 3.0,
    }


def seatbelt(offset_s: float, tpl: dict[str, Any]) -> Overrides:
    return {
        **working(offset_s / 60, tpl),
        "engine_load_pct": 55.0,
        "ground_speed_kmh": 3.0,
        "seatbelt_fastened": offset_s >= 185,
    }


SCENARIOS: dict[str, tuple[int, int, Callable[[float, dict[str, Any]], Overrides]]] = {
    # name: (period_s, duration_s, script)
    "overheating": (60, 26 * 60, overheating),
    "hydraulic_leak": (60, 22 * 60, hydraulic_leak),
    "tip_risk": (60, 12 * 60, tip_risk),
    "seatbelt": (5, 330, seatbelt),
}


def make(name: str) -> Scenario:
    period, duration, script = SCENARIOS[name]
    return Scenario(name=name, period_s=period, duration_s=duration, script=script)

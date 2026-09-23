from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

T0 = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)

NORMAL: dict[str, Any] = {
    "machine_id": "M04",
    "operator_id": "OP03",
    "shift_id": "SH-2026-08-20-M04-D",
    "engine_rpm": 1800.0,
    "engine_load_pct": 70.0,
    "coolant_temp_c": 88.0,
    "engine_oil_temp_c": 96.0,
    "oil_pressure_kpa": 350.0,
    "hydraulic_pressure_bar": 240.0,
    "hydraulic_oil_temp_c": 68.0,
    "fuel_rate_lph": 16.0,
    "fuel_level_pct": 70.0,
    "battery_voltage": 27.8,
    "vibration_rms_g": 0.5,
    "ground_speed_kmh": 1.0,
    "pitch_deg": 1.0,
    "roll_deg": 1.0,
    "gps_lat": 12.90,
    "gps_lon": 77.60,
    "seatbelt_fastened": True,
    "is_idle": False,
    "fault_code": None,
}


def row(minute: float, **over: Any) -> dict[str, Any]:
    """A normal working telemetry row at T0 + minute, with overrides."""
    return {**NORMAL, "ts": T0 + timedelta(minutes=minute), "_period_s": 60.0, **over}


@pytest.fixture
def mkrow():
    return row

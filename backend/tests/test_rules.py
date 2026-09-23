"""Rule engine: thresholds, holds, hysteresis and the graded response state machine."""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import Any

import pandas as pd
import pytest
from conftest import T0, row

from app.alerts import rules as R
from app.alerts.rules import STAGE_ORDER, MachineRuleEngine
from app.core.config import REPO_ROOT


def engine(machine_type: str = "wheel_loader", **kw: Any) -> MachineRuleEngine:
    return MachineRuleEngine("M04", machine_type, "S1", **kw)


def feed(eng: MachineRuleEngine, rows: list[dict[str, Any]]) -> list[tuple[float, R.AlertEvent]]:
    """Process rows; return (minute offset from T0, event) pairs."""
    out = []
    for r in rows:
        for e in eng.process(r):
            out.append(((r["ts"] - T0).total_seconds() / 60, e))
    return out


def timeline(events, code: str) -> list[tuple[float, str, str]]:
    return [(m, e.reason, e.row["stage"]) for m, e in events if e.alert.alert_code == code]


def warm(n: int = 10) -> list[dict[str, Any]]:
    return [row(i - n) for i in range(n)]


# ---------------------------------------------------------------------------------------------
# thresholds, holds, hysteresis
# ---------------------------------------------------------------------------------------------
def test_normal_rows_raise_nothing():
    assert feed(engine(), [row(i) for i in range(60)]) == []


def test_hold_time_coolant_high_needs_two_minutes():
    ev = feed(engine(), warm() + [row(0, coolant_temp_c=101), row(1, coolant_temp_c=101)])
    assert timeline(ev, "COOLANT_HIGH") == [(1.0, "open", "warn")]


def test_hysteresis_clears_only_5pct_inside_for_2_min():
    rows = warm() + [row(i, coolant_temp_c=102) for i in range(3)]
    rows += [row(3 + i, coolant_temp_c=97) for i in range(5)]  # below 100 but not below 95
    rows += [row(8, coolant_temp_c=94), row(9, coolant_temp_c=99), row(10, coolant_temp_c=94)]
    rows += [row(11, coolant_temp_c=94)]
    ev = feed(engine(), rows)
    # 94 at 8 then 99 at 9 resets the clear timer; 94 at 10 and 11 = 2 min inside -> resolved
    assert timeline(ev, "COOLANT_HIGH") == [(1.0, "open", "warn"), (11.0, "resolve", "resolved")]
    resolved = [e for _, e in ev if e.reason == "resolve"][0]
    assert resolved.row["resolved_at"] is not None
    assert resolved.row["evidence"]["time_to_resolve_min"] == 10.0


def test_below_limit_rule_hysteresis_battery():
    rows = warm() + [row(i, battery_voltage=23.5) for i in range(6)]
    rows += [row(6 + i, battery_voltage=24.8) for i in range(4)]  # above 24 but not 25.2
    rows += [row(10 + i, battery_voltage=27.0) for i in range(2)]
    ev = feed(engine(), rows)
    # 5-min hold: rows 0..4 -> fires at minute 4
    assert timeline(ev, "BATTERY_LOW") == [(4.0, "open", "warn"), (11.0, "resolve", "resolved")]


def test_oil_pressure_low_only_while_rpm_above_1200():
    ev = feed(engine(), warm() + [row(i, oil_pressure_kpa=80, engine_rpm=1000) for i in range(5)])
    assert timeline(ev, "OIL_PRESSURE_LOW") == []
    ev = feed(engine(), warm() + [row(0, oil_pressure_kpa=80, engine_rpm=1500)])
    assert timeline(ev, "OIL_PRESSURE_LOW") == [(0.0, "open", "warn")]


def test_data_gap_restarts_holds():
    rows = warm() + [row(0, coolant_temp_c=101), row(10, coolant_temp_c=101)]  # 10-min gap
    assert timeline(feed(engine(), rows), "COOLANT_HIGH") == []


# ---------------------------------------------------------------------------------------------
# graded response
# ---------------------------------------------------------------------------------------------
def test_graded_timings_flat_critical():
    """Critical and not rising: warn T, derate T+2, recommend_shutdown T+5, escalated T+7."""
    rows = warm() + [row(i, coolant_temp_c=106) for i in range(10)]
    ev = feed(engine(), rows)
    assert timeline(ev, "COOLANT_CRITICAL") == [
        (0.0, "open", "warn"),
        (2.0, "stage", "derate"),
        (5.0, "stage", "recommend_shutdown"),
        (7.0, "stage", "escalated"),
    ]
    last = [e for _, e in ev if e.alert.alert_code == "COOLANT_CRITICAL"][-1]
    assert last.row["severity"] == "critical"
    assert [s["stage"] for s in last.row["evidence"]["stages"]] == STAGE_ORDER[:4]


def test_graded_rising_value_skips_ahead_and_ignored_escalates():
    temps = [106, 107, 108.5, 110, 111.5, 112, 112, 112, 112, 112]
    ev = feed(engine(), warm() + [row(i, coolant_temp_c=t) for i, t in enumerate(temps)])
    tl = timeline(ev, "COOLANT_CRITICAL")
    assert tl == [
        (0.0, "open", "warn"),
        (2.0, "stage", "derate"),
        (3.0, "stage", "recommend_shutdown"),  # rising 4 °C over 3 min, 1 min into derate
        (5.0, "stage", "escalated"),  # not acknowledged 2 min after recommend_shutdown
    ]
    stages = [e for _, e in ev if e.alert.alert_code == "COOLANT_CRITICAL"][-1].row["evidence"][
        "stages"
    ]
    assert stages[2]["why"] == "value rising" and stages[3]["why"] == "not acknowledged"


def test_acknowledged_alert_escalates_only_at_7_min():
    eng = engine()
    temps = [106, 107, 108.5, 110, 111.5, 112, 112, 112, 112, 112]
    rows = warm() + [row(i, coolant_temp_c=t) for i, t in enumerate(temps)]
    ev = []
    for r in rows:
        for e in eng.process(r):
            ev.append(((r["ts"] - T0).total_seconds() / 60, e))
            if e.row["stage"] == "recommend_shutdown":
                e.alert.db_id = 42
                eng.acknowledge(42)
    assert timeline(ev, "COOLANT_CRITICAL")[-1] == (7.0, "stage", "escalated")


def test_graded_runs_only_while_critical():
    """HYD_OIL_HIGH at warning never derates; timing starts when it turns critical."""
    rows = warm() + [row(i, hydraulic_oil_temp_c=92) for i in range(10)]
    rows += [row(10 + i, hydraulic_oil_temp_c=96) for i in range(3)]
    ev = feed(engine(), rows)
    assert timeline(ev, "HYD_OIL_HIGH") == [
        (0.0, "open", "warn"),
        (10.0, "severity", "warn"),
        (12.0, "stage", "derate"),
    ]


def test_resolve_after_escalation_and_never_a_stop():
    rows = warm() + [row(i, coolant_temp_c=106) for i in range(8)]
    rows += [row(8 + i, coolant_temp_c=90) for i in range(3)]
    ev = feed(engine(), rows)
    tl = timeline(ev, "COOLANT_CRITICAL")
    assert [s for _, _, s in tl] == [
        "warn",
        "derate",
        "recommend_shutdown",
        "escalated",
        "resolved",
    ]
    assert tl[-1][0] == 9.0
    for _, e in ev:
        assert e.row["stage"] in STAGE_ORDER
        action = (e.row["recommended_action"] or "").lower()
        assert "automatic" not in action and "cut power" not in action


def test_stage_never_goes_backwards():
    temps = [106] * 6 + [103] * 3 + [106] * 3  # dips below 105 but stays outside the band
    ev = feed(engine(), warm() + [row(i, coolant_temp_c=t) for i, t in enumerate(temps)])
    stages = [STAGE_ORDER.index(s) for _, _, s in timeline(ev, "COOLANT_CRITICAL")]
    assert stages == sorted(stages)
    # recommend_shutdown at 5; paused 6..8 (no stage change while not critical); critical again
    # at 9 and still not acknowledged 4 min after the recommendation -> escalated at 9
    assert timeline(ev, "COOLANT_CRITICAL")[3:] == [(9.0, "stage", "escalated")]
    assert [m for m, _, _ in timeline(ev, "COOLANT_CRITICAL")] == [0.0, 2.0, 5.0, 9.0]


def test_warn_only_rule_stays_warn():
    rows = warm() + [row(i, coolant_temp_c=102) for i in range(15)]
    assert [s for _, _, s in timeline(feed(engine(), rows), "COOLANT_HIGH")] == ["warn"]


def test_plain_language_text():
    ev = feed(engine(), warm() + [row(0, hydraulic_oil_temp_c=94)])
    e = ev[0][1]
    assert e.row["title"] == "Hydraulic oil hot — 94 °C"
    assert e.row["recommended_action"] == "Switch to economy mode and reduce load."
    assert "HYD_OIL_HIGH" not in e.row["title"]
    assert e.row["source"] == "rule" and e.row["category"] == "internal"
    assert e.stream == {
        "id": -1,
        "alert_code": "HYD_OIL_HIGH",
        "severity": "warning",
        "stage": "warn",
        "title": "Hydraulic oil hot — 94 °C",
        "recommended_action": "Switch to economy mode and reduce load.",
    }


# ---------------------------------------------------------------------------------------------
# other rules
# ---------------------------------------------------------------------------------------------
def test_seatbelt_on_minute_data_is_critical_at_once():
    ev = feed(engine(), warm() + [row(0, seatbelt_fastened=False, ground_speed_kmh=3.0)])
    assert [(e.row["severity"], e.row["stage"]) for _, e in ev] == [("critical", "warn")]


def test_seatbelt_parked_and_idle_is_fine():
    rows = warm() + [
        row(i, seatbelt_fastened=False, ground_speed_kmh=0.0, engine_load_pct=10.0, is_idle=True)
        for i in range(5)
    ]
    assert timeline(feed(engine(), rows), "SEATBELT") == []


def test_tip_risk_levels():
    ev = feed(engine(), warm() + [row(0, roll_deg=-17.0), row(1, pitch_deg=26.0)])
    assert [(e.reason, e.row["severity"]) for _, e in ev] == [
        ("open", "warning"),
        ("severity", "critical"),
    ]
    assert ev[0][1].row["title"] == "Machine tilting — 17°"


def test_excess_idle_info_then_warning():
    rows = warm() + [row(i, is_idle=True, engine_rpm=1350, engine_load_pct=12) for i in range(22)]
    ev = feed(engine(), rows)
    assert [(m, e.reason, e.row["severity"]) for m, e in ev] == [
        (10.0, "open", "info"),
        (20.0, "severity", "warning"),
    ]


def test_excess_idle_low_rpm_is_fine():
    rows = warm() + [row(i, is_idle=True, engine_rpm=900, engine_load_pct=12) for i in range(30)]
    assert feed(engine(), rows) == []


def test_overspeed_type_limit_and_geofence():
    assert timeline(feed(engine("excavator"), warm() + [row(0, ground_speed_kmh=6.0)]), "OVERSPEED")
    assert not feed(engine("wheel_loader"), warm() + [row(0, ground_speed_kmh=6.0)])
    fence = {
        "id": 1,
        "name": "Pedestrian lane",
        "site_id": "S1",
        "speed_limit_kmh": 5,
        "polygon": {
            "type": "Polygon",
            "coordinates": [[[77.5, 12.8], [77.7, 12.8], [77.7, 13.0], [77.5, 13.0], [77.5, 12.8]]],
        },
    }
    ev = feed(engine("wheel_loader", geofences=[fence]), warm() + [row(0, ground_speed_kmh=6.0)])
    e = ev[0][1]
    assert e.row["title"] == "Too fast — 6 km/h (limit 5)"
    assert e.row["evidence"]["limit_source"] == "geofence:Pedestrian lane"


def test_hyd_pressure_drop_latched_until_restored():
    rows = [row(i - 10, hydraulic_pressure_bar=250, ground_speed_kmh=0.0) for i in range(10)]
    pressures = [250, 250, 190, 150, 115] + [115] * 10 + [250] * 3
    rows += [
        row(i, hydraulic_pressure_bar=p, ground_speed_kmh=0.0) for i, p in enumerate(pressures)
    ]
    tl = timeline(feed(engine(), rows), "HYD_PRESSURE_DROP")
    assert [s for _, _, s in tl] == [
        "warn",
        "derate",
        "recommend_shutdown",
        "escalated",
        "resolved",
    ]
    # windows compare low with low after a few minutes, but the latch keeps it open
    assert tl[-1][0] == 16.0


def test_hyd_pressure_drop_needs_load_and_standstill():
    rows = [row(i - 10, hydraulic_pressure_bar=250, ground_speed_kmh=3.0) for i in range(10)]
    rows += [
        row(i, hydraulic_pressure_bar=p, ground_speed_kmh=3.0)
        for i, p in enumerate([250, 190, 150, 115, 115])
    ]
    assert timeline(feed(engine(), rows), "HYD_PRESSURE_DROP") == []


def test_fault_code_opens_and_resolves():
    rows = warm() + [row(0, fault_code="E-365"), row(1, fault_code="E-365"), row(2), row(3)]
    ev = feed(engine(), rows)
    assert [(m, e.reason) for m, e in ev] == [(0.0, "open"), (3.0, "resolve")]
    assert ev[0][1].row["title"] == "Fault E-365 — high hydraulic oil temperature"
    assert ev[0][1].row["evidence"]["fault_code"] == "E-365"


# ---------------------------------------------------------------------------------------------
# glitches and the anomaly model's alerts
# ---------------------------------------------------------------------------------------------
def test_single_minute_glitch_raises_no_rule():
    ev = feed(engine(), warm() + [row(0, coolant_temp_c=150), row(1), row(2)])
    assert ev == []


def test_two_minute_implausible_reading_is_not_a_glitch():
    ev = feed(engine(), warm() + [row(0, coolant_temp_c=150), row(1, coolant_temp_c=150)])
    assert timeline(ev, "COOLANT_CRITICAL")[0][:2] == (1.0, "open")


def test_glitch_rule_matches_model_1():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from ml.inference import anomaly as A

    assert R.PLAUSIBLE_RANGE == A.PLAUSIBLE_RANGE
    assert R.ENGINE_RUNNING_RPM == A.ENGINE_RUNNING_RPM
    rows = warm(3) + [
        row(0, coolant_temp_c=150),
        row(1, coolant_temp_c=150),
        row(2),
        row(3, battery_voltage=0.0, oil_pressure_kpa=0.0),
        row(4, oil_pressure_kpa=0.0, engine_rpm=300),
        row(5, hydraulic_oil_temp_c=150),
        row(20, coolant_temp_c=0.0),  # after a gap: not contiguous
    ]
    df = pd.DataFrame(rows).drop(columns=["_period_s"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    _, expected = A.mark_glitches(df)
    prev = None
    got = []
    for r in rows:
        contiguous = prev is not None and 0 < (r["ts"] - prev["ts"]).total_seconds() <= 120
        got.append(R.is_glitch(r, prev, contiguous))
        prev = r
    assert got == [None if pd.isna(x) else x for x in expected]


def test_anomaly_alerts():
    eng = engine()
    fault = {
        "kind": "machine_fault",
        "anomaly_score": 0.8,
        "top_signals": [{"feature": "hydraulic_oil_temp_c_mean5", "z": 4.1}],
    }
    normal = {"kind": "normal", "anomaly_score": 0.2, "top_signals": []}
    ev = eng.observe_anomaly(fault, T0)
    assert len(ev) == 1
    e = ev[0]
    assert (e.row["alert_code"], e.row["source"], e.row["category"], e.row["severity"]) == (
        "UNUSUAL_BEHAVIOUR",
        "anomaly_model",
        "behaviour",
        "warning",
    )
    assert e.row["title"] == "Unusual hydraulic oil temperature"
    assert e.row["anomaly_score"] == 0.8
    assert eng.observe_anomaly(normal, T0 + timedelta(minutes=1)) == []
    ev = eng.observe_anomaly(normal, T0 + timedelta(minutes=2))
    assert [x.reason for x in ev] == ["resolve"]
    glitch = {
        "kind": "sensor_glitch",
        "anomaly_score": 0.5,
        "top_signals": [{"feature": "coolant_temp_c", "z": 9}],
    }
    ev = eng.observe_anomaly(glitch, T0 + timedelta(minutes=3))
    assert ev[0].row["alert_code"] == "SENSOR_GLITCH" and ev[0].row["severity"] == "info"


def test_health_state_shape_feeds_compute_health():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from ml.inference import health as H

    eng = engine()
    feed(eng, warm() + [row(0, coolant_temp_c=107), row(1, fault_code="E-365", coolant_temp_c=107)])
    states = [a.health_state() for a in eng.open_alerts()]
    h = H.compute_health("M04", T0, rule_states=states)
    assert h["subsystems"]["cooling"] == pytest.approx(0.3)  # critical
    assert h["subsystems"]["hydraulics"] == pytest.approx(0.7)  # fault code warning

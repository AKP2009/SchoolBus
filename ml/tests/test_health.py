"""Tests for ml/inference/health.py (models.md §11).

The formula tests need no data. The rule-state tests use small synthetic frames and the committed
1-day sample; the last test runs the real anomaly model when its artifacts exist.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import ml.inference.health as H

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "output" / "sample" / "telemetry.csv"
CONTRACT_KEYS = {
    "machine_id",
    "ts",
    "overall",
    "subsystems",
    "anomaly_score",
    "failure_probability",
    "likely_component",
}


def fault(score: float, *features: str, kind: str = "machine_fault") -> dict:
    return {
        "anomaly_score": score,
        "kind": kind,
        "top_signals": [{"feature": f, "z": 4.0} for f in features],
    }


# ---------------------------------------------------------------------------------------------
# Formula
# ---------------------------------------------------------------------------------------------
def test_no_inputs_is_fully_healthy() -> None:
    h = H.compute_health("M04", "2026-08-25T02:00:00Z")
    assert CONTRACT_KEYS <= set(h)
    assert set(h["subsystems"]) == set(H.SUBSYSTEMS)
    assert h["overall"] == 1.0 and h["band"] == "green"
    assert h["anomaly_score"] is None and h["failure_probability"] is None
    assert h["likely_component"] is None and h["reasons"] == []
    assert h["ts"] == "2026-08-25T02:00:00Z"


def test_contract_example() -> None:
    """api_contract.md: 72 % hydraulics failure risk -> hydraulics 0.28 = overall, red."""
    h = H.compute_health(
        "M04", None, None, None, {"failure_probability": 0.72, "likely_component": "hydraulics"}
    )
    assert h["subsystems"]["hydraulics"] == pytest.approx(0.28)
    assert h["overall"] == pytest.approx(0.28)
    assert h["band"] == "red" and h["subsystem_bands"]["engine"] == "green"
    assert h["failure_probability"] == 0.72 and h["likely_component"] == "hydraulics"
    assert h["reasons"][0]["source"] == "failure_probability"


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("info", 1.0), ("warning", 0.7), ("critical", 0.3), ("emergency", 0.3)],
)
def test_rule_penalty(severity: str, expected: float) -> None:
    h = H.compute_health("M01", rule_states={"COOLANT_HIGH": severity})
    assert h["subsystems"]["cooling"] == pytest.approx(expected)
    assert h["overall"] == pytest.approx(expected)


def test_rule_mapping_and_worst_alert_wins() -> None:
    alerts = [
        {"alert_code": "HYD_OIL_HIGH", "severity": "warning", "stage": "warn"},
        {"alert_code": "HYD_PRESSURE_DROP", "severity": "critical", "stage": "derate"},
        {"alert_code": "OIL_PRESSURE_LOW", "severity": "critical", "stage": "resolved"},  # closed
        {"alert_code": "BATTERY_LOW", "severity": "warning"},
        {"alert_code": "SEATBELT", "severity": "critical"},  # usage, not health
        {"alert_code": "TIP_RISK", "severity": "critical"},
    ]
    s = H.compute_health("M01", rule_states=alerts)["subsystems"]
    assert s == {
        "engine": 1.0,
        "cooling": 1.0,
        "hydraulics": 0.3,
        "electrical": 0.7,
        "undercarriage": 1.0,
    }


def test_fault_codes_map_to_subsystems() -> None:
    alerts = [
        {"alert_code": "FAULT_CODE", "severity": "warning", "evidence": {"fault_code": "E-215"}},
        {"alert_code": "FAULT_CODE", "severity": "critical", "fault_code": "E-110"},
        {"alert_code": "FAULT_CODE", "severity": "critical", "evidence": {"fault_code": "E-520"}},
        {"alert_code": "FAULT_CODE", "severity": "critical"},  # no code: nothing to map
    ]
    s = H.compute_health("M01", rule_states=alerts)["subsystems"]
    assert s["engine"] == pytest.approx(0.7) and s["cooling"] == pytest.approx(0.3)
    assert min(s["hydraulics"], s["electrical"], s["undercarriage"]) == 1.0
    # Mapping form with the fault code as key (what rule_states_frame returns)
    s = H.compute_health("M01", rule_states={"E-365": "warning", "E-410": "critical"})["subsystems"]
    assert s["hydraulics"] == pytest.approx(0.7) and s["electrical"] == pytest.approx(0.3)


def test_anomaly_counts_only_for_machine_faults() -> None:
    a = fault(
        0.8, "hydraulic_oil_temp_c_mean5", "hydraulic_pressure_bar_std5", "fuel_rate_lph_mean5"
    )
    h = H.compute_health("M04", anomaly=a)
    assert h["subsystems"]["hydraulics"] == pytest.approx(1 - 0.6 * 0.8)
    assert h["subsystems"]["engine"] == 1.0  # fuel rate is not mapped to a subsystem
    assert h["anomaly_score"] == 0.8
    for kind in ("normal", "sensor_glitch"):
        h = H.compute_health("M04", anomaly=fault(0.8, "coolant_temp_c", kind=kind))
        assert h["overall"] == 1.0 and h["anomaly_score"] == 0.8


def test_anomaly_hits_every_subsystem_in_top_signals() -> None:
    a = fault(0.5, "coolant_temp_c_slope15", "battery_voltage_mean5", "coolant_minus_hyd_oil_c")
    s = H.compute_health("M04", anomaly=a)["subsystems"]
    assert s["cooling"] == s["electrical"] == s["hydraulics"] == pytest.approx(0.7)
    assert s["engine"] == s["undercarriage"] == 1.0


def test_vibration_while_travelling_is_undercarriage() -> None:
    a = fault(0.5, "vibration_rms_g_mean5")
    parked = H.compute_health("M08", anomaly=a, travelling=False)["subsystems"]
    moving = H.compute_health("M08", anomaly=a, travelling=True)["subsystems"]
    assert parked["engine"] == pytest.approx(0.7) and parked["undercarriage"] == 1.0
    assert moving["undercarriage"] == pytest.approx(0.7) and moving["engine"] == 1.0


def test_max_not_sum_and_overall_is_min() -> None:
    h = H.compute_health(
        "M04",
        rule_states={"HYD_OIL_HIGH": "warning"},  # 0.3
        anomaly=fault(0.9, "hydraulic_oil_temp_c_mean5", "coolant_temp_c_mean5"),  # 0.54
        maintenance={"failure_probability": 0.4, "likely_component": "hydraulics"},  # 0.4
    )
    s = h["subsystems"]
    assert s["hydraulics"] == pytest.approx(0.46)
    assert s["cooling"] == pytest.approx(0.46)
    assert h["overall"] == min(s.values())
    assert [r["penalty"] for r in h["reasons"]] == sorted(
        (r["penalty"] for r in h["reasons"]), reverse=True
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, "green"),
        (0.75, "green"),
        (0.7499, "orange"),
        (0.5, "orange"),
        (0.4999, "red"),
        (0.0, "red"),
    ],
)
def test_band_edges(score: float, expected: str) -> None:
    assert H.band(score) == expected


@pytest.mark.parametrize(
    "maintenance",
    [
        None,
        {},
        {"failure_probability": None, "likely_component": "hydraulics"},
        {"failure_probability": float("nan"), "likely_component": "hydraulics"},
        {"failure_probability": "high", "likely_component": "hydraulics"},
        {"failure_probability": 0.9},  # no component
        {"failure_probability": 0.9, "likely_component": "brakes"},  # not a twin subsystem
        {"failure_probability": 0.9, "likely_component": None},
        "not a mapping",
    ],
)
def test_missing_or_bad_maintenance_never_crashes(maintenance: object) -> None:
    h = H.compute_health("M04", maintenance=maintenance)  # type: ignore[arg-type]
    assert h["overall"] == 1.0


def test_bad_maintenance_probability_is_null() -> None:
    h = H.compute_health("M04", maintenance={"failure_probability": float("nan")})
    assert h["failure_probability"] is None
    h = H.compute_health(
        "M04", maintenance={"failure_probability": 0.9, "likely_component": "brakes"}
    )
    assert h["failure_probability"] == 0.9 and h["likely_component"] == "brakes"


@pytest.mark.parametrize(
    "rule_states",
    [
        None,
        [],
        {},
        "COOLANT_HIGH",
        42,
        [None, 3, "x", {"alert_code": None}, {"alert_code": "COOLANT_HIGH"}],
        {"COOLANT_HIGH": None, "HYD_OIL_HIGH": "catastrophic"},
    ],
)
def test_missing_or_bad_rule_states_never_crash(rule_states: object) -> None:
    assert H.compute_health("M04", rule_states=rule_states)["overall"] == 1.0


@pytest.mark.parametrize(
    "anomaly",
    [
        None,
        {},
        {"kind": "machine_fault"},
        {"anomaly_score": None, "kind": "machine_fault", "top_signals": []},
        {"anomaly_score": 0.9, "kind": "machine_fault", "top_signals": None},
        {
            "anomaly_score": 0.9,
            "kind": "machine_fault",
            "top_signals": [None, {"z": 1}, {"feature": 3}],
        },
        {"anomaly_score": "abc", "kind": "machine_fault"},
    ],
)
def test_missing_or_bad_anomaly_never_crashes(anomaly: object) -> None:
    assert H.compute_health("M04", anomaly=anomaly)["overall"] == 1.0  # type: ignore[arg-type]


def test_timestamps() -> None:
    assert H.compute_health("M01", pd.Timestamp("2026-08-25 02:00"))["ts"] == "2026-08-25T02:00:00Z"
    ist = pd.Timestamp("2026-08-25 07:30", tz="Asia/Kolkata")
    assert H.compute_health("M01", ist)["ts"] == "2026-08-25T02:00:00Z"
    a = {"ts": "2026-08-25T01:00:00Z", "anomaly_score": 0.1, "kind": "normal"}
    assert H.compute_health("M01", anomaly=a)["ts"] == "2026-08-25T01:00:00Z"
    assert H.compute_health("M01", "not a time")["ts"].endswith("Z")  # falls back to now


def test_snapshot_row_matches_migration_columns() -> None:
    h = H.compute_health(
        "M04", maintenance={"failure_probability": 0.6, "likely_component": "cooling"}
    )
    row = H.to_snapshot_row(h)
    assert set(row) == {
        "machine_id",
        "ts",
        "overall_score",
        "engine_score",
        "hydraulics_score",
        "cooling_score",
        "electrical_score",
        "undercarriage_score",
        "anomaly_score",
        "details",
    }
    assert row["cooling_score"] == pytest.approx(0.4) and row["details"]["band"] == "red"


# ---------------------------------------------------------------------------------------------
# Rule states from telemetry
# ---------------------------------------------------------------------------------------------
def _frame(n: int, **signals: object) -> pd.DataFrame:
    base = {
        "coolant_temp_c": 88.0,
        "hydraulic_oil_temp_c": 60.0,
        "oil_pressure_kpa": 350.0,
        "engine_rpm": 1700.0,
        "battery_voltage": 27.0,
        "engine_load_pct": 50.0,
        "hydraulic_pressure_bar": 200.0,
        "fuel_rate_lph": 20.0,
        "vibration_rms_g": 0.5,
        "fault_code": None,
    }
    df = pd.DataFrame({k: [v] * n for k, v in base.items()})
    for k, v in signals.items():
        df[k] = v
    df.insert(0, "ts", pd.date_range("2026-08-25", periods=n, freq="1min", tz="UTC"))
    df.insert(1, "machine_id", "M04")
    return df


def test_rule_states_thresholds_and_durations() -> None:
    cool = [88, 101, 101, 101, 106, 106, 88, 88]
    batt = [27, 23, 23, 23, 23, 23, 23, 27]
    codes = [None, None, None, "E-410", None, None, "E-520", None]
    st = H.rule_states_frame(_frame(8, coolant_temp_c=cool, battery_voltage=batt, fault_code=codes))
    s = st.rule_states.tolist()
    assert s[0] == {}
    assert "COOLANT_HIGH" not in s[1]  # > 100 for only 1 minute
    assert s[2]["COOLANT_HIGH"] == "warning"
    assert s[4]["COOLANT_CRITICAL"] == "critical" and "COOLANT_HIGH" not in s[4]
    assert "BATTERY_LOW" not in s[4] and s[5]["BATTERY_LOW"] == "warning"  # < 24 V for 5 min
    assert s[3]["E-410"] == "warning"
    assert "E-520" not in s[6]  # seatbelt switch fault is not machine health
    assert s[7] == {}


def test_rule_states_oil_pressure_and_hydraulic_oil() -> None:
    st = H.rule_states_frame(
        _frame(
            4,
            oil_pressure_kpa=[350, 80, 80, 80],
            engine_rpm=[1700, 1700, 900, 1700],
            hydraulic_oil_temp_c=[60, 92, 96, 60],
        )
    ).rule_states.tolist()
    assert st[1] == {"OIL_PRESSURE_LOW": "critical", "HYD_OIL_HIGH": "warning"}
    assert st[2] == {"HYD_OIL_HIGH": "critical"}  # low oil pressure only counts above 1200 rpm
    assert st[3] == {"OIL_PRESSURE_LOW": "critical"}


def test_rule_states_ignore_single_glitch() -> None:
    st = H.rule_states_frame(_frame(4, coolant_temp_c=[88, 150, 88, 88])).rule_states.tolist()
    assert st == [{}, {}, {}, {}]


def test_rule_states_on_sample_feed_compute_health() -> None:
    tel = pd.read_csv(SAMPLE, parse_dates=["ts"])
    st = H.rule_states_frame(tel)
    assert len(st) == len(tel)
    assert st.groupby("machine_id").ts.is_monotonic_increasing.all()
    for states in st.rule_states.iloc[::97]:
        h = H.compute_health("M01", rule_states=states)
        assert 0.0 <= h["overall"] <= 1.0


@pytest.mark.skipif(
    not (ROOT / "ml" / "artifacts" / "anomaly" / "config.json").exists(),
    reason="anomaly artifacts not built",
)
def test_real_anomaly_output_feeds_compute_health() -> None:
    import ml.inference.anomaly as A

    tel = pd.read_csv(SAMPLE, parse_dates=["ts"])
    window = tel[tel.machine_id == "M01"].sort_values("ts").iloc[:60]
    a = A.score_anomaly(window, machine_type="excavator")
    h = H.compute_health(
        "M01", anomaly=a, rule_states=H.rule_states_frame(window).rule_states.iloc[-1]
    )
    assert h["ts"] == a["ts"] and h["anomaly_score"] == a["anomaly_score"]
    assert all(0.0 <= v <= 1.0 for v in h["subsystems"].values())
    assert not math.isnan(h["overall"]) and h["overall"] == min(h["subsystems"].values())
    assert np.isclose(h["overall"], 1.0) or h["reasons"]

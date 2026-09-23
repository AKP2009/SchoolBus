"""Tests for ml/task_time/features.py — leakage, forbidden columns, dtypes, rolling ratio."""

import numpy as np
import pandas as pd
import pytest

from ml.task_time.features import (
    CATEGORICAL,
    FORBIDDEN,
    PERSONAL_FEATURES,
    STANDARD_FEATURES,
    build_features,
)

DATES = pd.date_range("2026-06-01", periods=33).strftime("%Y-%m-%d").tolist()


def _task(
    tid,
    date,
    op,
    tt,
    machine,
    actual=None,
    *,
    qty=100.0,
    delay=None,
    status=None,
    material="clay",
    unit="m3",
    standard=None,
):
    status = status or ("completed" if actual is not None else "scheduled")
    row = {
        "task_id": tid,
        "site_id": "S1",
        "shift_id": f"SH-{date}-{machine}-D",
        "machine_id": machine,
        "operator_id": op,
        "task_type": tt,
        "material_type": material,
        "quantity": qty,
        "unit": unit,
        "terrain_slope_deg": 2.0,
        "haul_distance_m": np.nan,
        "task_date": date,
        "scheduled_start": pd.Timestamp(f"{date} 06:30", tz="UTC"),
        "status": status,
        "delay_reason": delay,
    }
    if actual is not None:
        row["actual_duration_min"] = actual
    if standard is not None:
        row["standard_min"] = standard
    return row


def _env(rows, days=33):
    dates = DATES[:days]
    operators = pd.DataFrame(
        {
            "operator_id": ["OP01", "OP02"],
            "skill_score": [0.6, 0.8],
            "experience_years": [4.0, 12.0],
            "certification_level": [2, 3],
            "personality": ["average", "efficient"],  # must never leak
        }
    )
    machines = pd.DataFrame(
        {"machine_id": ["M01", "M02"], "machine_type": ["excavator", "wheel_loader"]}
    )
    shifts = pd.DataFrame(
        [
            {
                "shift_id": f"SH-{d}-{m}-D",
                "shift_type": "day",
                "start_time": pd.Timestamp(f"{d} 06:00", tz="UTC"),
            }
            for d in dates
            for m in ("M01", "M02")
        ]
    )
    ts = pd.date_range(dates[0], periods=days * 24, freq="h", tz="UTC")
    weather = pd.DataFrame(
        {
            "site_id": "S1",
            "ts": ts,
            "temp_c": 30.0 - ts.hour * 0.1,
            "rain_mm": ts.hour.astype(float),
            "wind_kmh": 5.0,
            "visibility_m": 8000.0,
            "dust_index": 0.2,
        }
    )
    health = pd.DataFrame(
        [(m, d, 0.9 if m == "M01" else 0.8) for d in dates for m in ("M01", "M02")],
        columns=["machine_id", "date", "health_score"],
    )
    return pd.DataFrame(rows), shifts, operators, machines, weather, health


def _main_tasks():
    """OP01 dig daily; day 3 delayed; OP02 trench on day 10; one scheduled row on day 10."""
    durations = {
        1: 60.0,
        2: 66.0,
        3: 120.0,
        4: 60.0,
        5: 90.0,
        6: 60.0,
        7: 60.0,
        8: 60.0,
        9: 60.0,
        10: 60.0,
    }
    rows = [
        _task(
            f"dig-{d}",
            DATES[d - 1],
            "OP01",
            "dig",
            "M01",
            a,
            delay="refuelling" if d == 3 else None,
        )
        for d, a in durations.items()
    ]
    rows.append(_task("trench-10", DATES[9], "OP02", "trench", "M02", 80.0, material="sand"))
    rows.append(_task("sched-10", DATES[9], "OP01", "dig", "M01"))  # scheduled, no actual
    return rows


def _ratio(feats, tid):
    return feats.loc[feats["task_id"] == tid, "operator_avg_time_ratio_30d"].iloc[0]


def test_leakage_actual_duration_does_not_change_earlier_features():
    tasks = pd.DataFrame(_main_tasks())
    env = _env(tasks)
    f1 = build_features(*env)

    probe_date = DATES[4]  # day 5
    mod = tasks.copy()
    mod.loc[mod["task_id"] == "dig-5", "actual_duration_min"] += 100.0
    f2 = build_features(mod, env[1], env[2], env[3], env[4], env[5])

    early = f1["task_date"] <= probe_date
    pd.testing.assert_frame_equal(
        f1[early].reset_index(drop=True), f2[early].reset_index(drop=True)
    )

    # sensitivity: the rebuild must actually move a later task's rolling feature
    assert _ratio(f1, "dig-6") != _ratio(f2, "dig-6")


def test_forbidden_columns_absent():
    for col in FORBIDDEN:
        assert col not in STANDARD_FEATURES
        assert col not in PERSONAL_FEATURES
    feats = build_features(*_env(_main_tasks()))
    built = set(feats.columns)
    assert not (set(FORBIDDEN) & built)
    assert not any(c.startswith("actual_") for c in built)


def test_categorical_dtypes_and_fixed_categories():
    feats = build_features(*_env(_main_tasks()))
    for col, cats in CATEGORICAL.items():
        assert isinstance(feats[col].dtype, pd.CategoricalDtype)
        assert list(feats[col].cat.categories) == cats
        assert not feats[col].cat.ordered


def test_operator_ratio_window_semantics():
    """[d-30, d-1] window, strictly earlier dates, >=3 tasks, provided standard_min used;
    same-day and d-31 tasks excluded."""
    rows = []
    for d, actual in [(1, 60.0), (2, 72.0), (3, 60.0), (31, 90.0), (32, 60.0)]:
        rows.append(_task(f"dig-{d}", DATES[d - 1], "OP01", "dig", "M01", actual, standard=60.0))
    rows.append(
        _task("probe-32", DATES[31], "OP01", "dig", "M01", status="scheduled", standard=60.0)
    )
    rows.append(
        _task("probe-33", DATES[32], "OP01", "dig", "M01", status="scheduled", standard=60.0)
    )
    feats = build_features(*_env(rows))

    # probe day 32: window days 2..31 -> ratios {1.2, 1.0, 1.5}; day 1 and same-day excluded
    assert _ratio(feats, "probe-32") == pytest.approx((1.2 + 1.0 + 1.5) / 3)
    # probe day 33: window days 3..32 -> {1.0, 1.5, 1.0}; day 2 (d-31) excluded
    assert _ratio(feats, "probe-33") == pytest.approx((1.0 + 1.5 + 1.0) / 3)


def test_operator_ratio_fallback_and_history():
    """Without a standard_min column the expanding per-task_type fallback is used; delayed
    tasks are excluded; fewer than 3 window tasks -> NaN; history fills earlier days."""
    feats = build_features(*_env(_main_tasks(), days=10))

    assert np.isnan(_ratio(feats, "dig-1"))
    assert np.isnan(_ratio(feats, "dig-5"))  # 2 finite ratios before day 5 (delayed d3 excluded)
    assert _ratio(feats, "dig-6") == pytest.approx((66 / 60 + 60 / 63 + 90 / 60) / 3)
    assert _ratio(feats, "dig-7") == pytest.approx((66 / 60 + 60 / 63 + 90 / 60 + 60 / 63) / 4)
    assert _ratio(feats, "sched-10") == pytest.approx(
        (66 / 60 + 60 / 63 + 90 / 60 + 60 / 63 + 1.0 + 1.0 + 1.0) / 7
    )

    tasks = pd.DataFrame([_task("probe-5", DATES[4], "OP01", "dig", "M01")])
    env = _env(tasks, days=10)
    history = pd.DataFrame(
        [
            _task(f"h-{d}", DATES[d - 1], "OP01", "dig", "M01", 60.0, standard=60.0)
            for d in (1, 2, 3)
        ]
    )
    feats_h = build_features(tasks, env[1], env[2], env[3], env[4], env[5], history=history)
    assert _ratio(feats_h, "probe-5") == pytest.approx(1.0)
    assert np.isnan(_ratio(build_features(*env), "probe-5"))


def test_joins_and_derived_columns():
    feats = build_features(*_env(_main_tasks(), days=10))
    row = feats[feats["task_id"] == "dig-1"].iloc[0]
    assert row["rain_mm"] == pytest.approx(6.0)  # weather hour containing 06:30
    assert row["temp_c"] == pytest.approx(30.0 - 6 * 0.1)
    assert row["machine_health"] == pytest.approx(0.9)
    assert row["skill_score"] == pytest.approx(0.6)
    assert row["hours_into_shift"] == pytest.approx(0.5)
    assert row["day_of_week"] == 0.0  # 2026-06-01 is a Monday
    assert row["log_quantity"] == pytest.approx(np.log(100.0))

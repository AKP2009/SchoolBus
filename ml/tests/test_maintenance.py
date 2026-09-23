"""Tests for ml/inference/maintenance.py.

Feature tests run on the committed 1-day sample (data/output/sample/) with the committed anomaly
artifacts. Inference tests fit a small throwaway XGBoost on synthetic rows, so they don't depend on
the full dataset. The last test also checks the real artifacts when they exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from xgboost import XGBClassifier

import ml.inference.maintenance as M

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "output" / "sample"
OUTPUT_KEYS = {
    "machine_id",
    "horizon_hours",
    "failure_probability",
    "risk_band",
    "likely_component",
    "top_factors",
}
INPUT = [*M.KEY_COLUMNS, *M.SPEC_FEATURES, *M.RAW_EXTRA_COLUMNS]


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def raw() -> dict[str, pd.DataFrame]:
    tel = pd.read_csv(SAMPLE / "telemetry.csv", parse_dates=["ts"])
    tel = tel.sort_values(["machine_id", "ts"]).reset_index(drop=True)
    return {
        "tel": tel,
        "machines": pd.read_csv(SAMPLE / "machines.csv"),
        "shifts": pd.read_csv(SAMPLE / "shifts.csv"),
        "mlog": pd.read_csv(SAMPLE / "maintenance_log.csv"),
    }


def _build(
    raw: dict[str, pd.DataFrame], tel: pd.DataFrame | None = None
) -> pd.DataFrame:
    tel = raw["tel"] if tel is None else tel
    mtype = raw["machines"].set_index("machine_id").machine_type
    scores = M.score_minutes(tel, mtype)
    return M.build_hourly_features(
        tel, scores, raw["shifts"], raw["machines"], raw["mlog"]
    )


@pytest.fixture(scope="module")
def feats(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return _build(raw)


def _synthetic_rows(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """Feature rows where battery deviation drives the label."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        rng.normal(size=(n, len(M.SPEC_FEATURES) + len(M.RAW_EXTRA_COLUMNS))),
        columns=[*M.SPEC_FEATURES, *M.RAW_EXTRA_COLUMNS],
    )
    df["machine_id"] = rng.choice(["M01", "M06"], n)
    df["machine_type"] = np.where(
        df.machine_id == "M01", "excavator", "articulated_truck"
    )
    df["ts"] = pd.Timestamp("2026-08-20", tz="UTC")
    df["engine_h"] = 1000.0
    df["label"] = (df.battery_voltage_dev24 < -1.0).astype(int)
    return df


@pytest.fixture()
def tiny_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    df = _synthetic_rows()
    scales = M.fit_scales(df)
    X = M.add_derived(df, scales)[M.FEATURE_COLUMNS]
    model = XGBClassifier(n_estimators=30, max_depth=3, random_state=42).fit(
        X, df.label
    )
    joblib.dump(model, tmp_path / "xgb_failure.joblib")
    (tmp_path / "feature_list.json").write_text(json.dumps(M.FEATURE_COLUMNS))
    (tmp_path / "config.json").write_text(json.dumps({"scales": scales}))
    monkeypatch.setattr(M, "ARTIFACT_DIR", tmp_path)
    M.load_artifacts.cache_clear()
    yield tmp_path
    M.load_artifacts.cache_clear()


# ---------------------------------------------------------------------------------------------
# Engine hours and features
# ---------------------------------------------------------------------------------------------
def test_engine_hours_anchor_and_order(raw):
    she = M.shift_engine_hours(raw["shifts"], raw["machines"], raw["mlog"])
    total = raw["machines"].set_index("machine_id").total_engine_hours
    last = she.sort_values("start_time").groupby("machine_id").eh_end.last()
    assert np.allclose(last, total.loc[last.index])
    eh = pd.Series(M.engine_hours(raw["tel"], she))
    assert eh.notna().all()
    assert (eh.groupby(raw["tel"].machine_id).diff().dropna() > 0).all()


def test_engine_hours_cut_at_failure():
    shifts = pd.DataFrame(
        {
            "shift_id": ["A", "B"],
            "machine_id": ["M01", "M01"],
            "start_time": ["2026-08-01T00:30:00Z", "2026-08-02T00:30:00Z"],
            "end_time": ["2026-08-01T08:30:00Z", "2026-08-02T08:30:00Z"],
        }
    )
    machines = pd.DataFrame({"machine_id": ["M01"], "total_engine_hours": [100.0]})
    mlog = pd.DataFrame(
        {
            "machine_id": ["M01"],
            "event_date": ["2026-08-01T02:30:00Z"],
            "event_type": ["failure"],
            "component": ["engine"],
            "engine_hours_at_event": [90.0],
        }
    )
    she = M.shift_engine_hours(shifts, machines, mlog).set_index("shift_id")
    assert she.loc["A", "eh_end"] - she.loc["A", "eh_start"] == pytest.approx(2.0)
    assert she.loc["B", "eh_end"] == pytest.approx(100.0)


def test_feature_columns(feats):
    assert list(feats.columns) == INPUT
    assert not {"anomaly_label", "anomaly_type"} & set(M.FEATURE_COLUMNS)
    assert feats.machine_id.nunique() == 2
    assert (
        feats.groupby("machine_id")
        .engine_h.apply(lambda s: s.is_monotonic_increasing)
        .all()
    )
    assert feats.hours_since_service.ge(0).all()
    assert (
        feats[[f"machine_type_{t}" for t in M.MACHINE_TYPES]].sum(axis=1) == 1
    ).all()


def test_features_only_use_the_past(raw, feats):
    """A row computed on telemetry truncated at its own ts equals the row from the full day."""
    row = feats[feats.machine_id == "M01"].iloc[
        len(feats[feats.machine_id == "M01"]) // 2
    ]
    tel = raw["tel"]
    cut = _build(raw, tel[pd.to_datetime(tel.ts, utc=True) <= row.ts])
    again = cut[(cut.machine_id == "M01") & (cut.ts == row.ts)].iloc[0]
    cols = [
        c
        for c in M.SPEC_FEATURES
        if c not in ("anomaly_score_mean24", "anomaly_count24")
    ]
    pd.testing.assert_series_equal(
        again[cols].astype(float), row[cols].astype(float), check_names=False
    )


def test_glitch_does_not_move_hourly_mean(raw, feats):
    tel = raw["tel"].copy()
    rows = tel.index[(tel.machine_id == "M01")][100]
    tel.loc[rows, "battery_voltage"] = 0.0
    spiked = _build(raw, tel)
    diff = (spiked.battery_voltage_mean24 - feats.battery_voltage_mean24).abs()
    # a 0 V minute among ~60 would pull the hourly mean ~0.45 V down
    assert diff.max() < 0.05


def test_fault_code_episodes_counted_once():
    ts = pd.date_range("2026-08-01 01:00", periods=8, freq="1min", tz="UTC")
    code = [None, "E-110", "E-110", "E-110", None, "E-110", "E-410", "E-410"]
    s = M._run_starts(
        pd.Series(["M01"] * 8), pd.Series(ts), pd.Series(code).notna(), pd.Series(code)
    )
    assert s.sum() == 3


# ---------------------------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------------------------
def test_label_hours_horizon_downtime_and_censoring():
    feats = pd.DataFrame(
        {
            "machine_id": "M01",
            "ts": pd.to_datetime(
                [
                    "2026-08-01 01:00",
                    "2026-08-03 01:00",
                    "2026-08-05 01:00",
                    "2026-08-05 03:00",
                    "2026-08-05 09:00",
                    "2026-08-20 01:00",
                ],
                utc=True,
            ),
            "engine_h": [30.0, 60.0, 99.0, 100.5, 100.5, 180.0],
        }
    )
    mlog = pd.DataFrame(
        {
            "machine_id": ["M01", "M01"],
            "event_date": ["2026-08-05T02:00:00Z", "2026-08-05T08:00:00Z"],
            "event_type": ["failure", "repair"],
            "component": ["cooling", "cooling"],
            "engine_hours_at_event": [100.0, 100.0],
        }
    )
    lab = M.label_hours(feats, mlog, {"M01": 200.0})
    assert lab.label.tolist() == [0, 1, 1, 0, 0, 0]
    assert lab.hours_to_failure.iloc[1] == pytest.approx(40.0)
    assert lab.next_component.iloc[2] == "cooling"
    # failure -> repair dropped; last row: no failure ahead and < 48 h of data left
    assert lab.keep.tolist() == [True, True, True, False, True, False]


# ---------------------------------------------------------------------------------------------
# Component rule, baseline, inference
# ---------------------------------------------------------------------------------------------
def test_likely_component_follows_worst_signal():
    df = _synthetic_rows(200)
    scales = M.fit_scales(df)
    row = df.iloc[[0, 1, 2]].copy()
    for c in df.columns:
        if c.endswith(("_dev24", "_slope72")):
            row[c] = 0.0
    row.iloc[0, row.columns.get_loc("battery_voltage_dev24")] = -10.0
    row.iloc[1, row.columns.get_loc("oil_pressure_kpa_dev24")] = -10.0
    row.iloc[2, row.columns.get_loc("hydraulic_oil_temp_c_slope72")] = 10.0
    assert M.likely_component(row, scales).likely_component.tolist() == [
        "electrical",
        "engine",
        "hydraulics",
    ]


def test_baseline_probability_rule():
    df = _synthetic_rows(200)
    scales = M.fit_scales(df)
    row = df.iloc[[0, 1]].copy()
    for c in df.columns:
        if c.endswith("_slope72"):
            row[c] = 0.0
    row["service_ratio"] = [0.2, 1.1]
    p = M.baseline_probability(row, scales)
    assert p[0] < 0.5 <= p[1]


def test_output_matches_contract(tiny_artifacts):
    df = _synthetic_rows(5, seed=3)
    out = M.predict_failure(df.iloc[0][INPUT].to_dict())
    assert set(out) == OUTPUT_KEYS
    assert out["machine_id"] in {"M01", "M06"} and out["horizon_hours"] == 48
    assert 0.0 <= out["failure_probability"] <= 1.0
    assert out["risk_band"] == M.risk_band(out["failure_probability"])
    assert out["likely_component"] in M.COMPONENTS
    assert 1 <= len(out["top_factors"]) <= M.TOP_N
    for f in out["top_factors"]:
        assert set(f) == {"feature", "shap"} and f["feature"] in M.FEATURE_COLUMNS
    json.dumps(out)  # plain JSON types only
    many = M.predict_failure(df[INPUT])
    assert isinstance(many, list) and len(many) == 5


def test_high_risk_row_is_explained_by_battery(tiny_artifacts):
    df = _synthetic_rows(1, seed=4)
    df["battery_voltage_dev24"] = -3.0
    out = M.predict_failure(df[INPUT].iloc[0].to_dict())
    assert out["failure_probability"] > 0.5
    assert any(
        "battery" in f["feature"] or f["feature"].startswith("worst")
        for f in out["top_factors"]
    )


def test_risk_bands():
    assert [M.risk_band(p) for p in (0.0, 0.29, 0.3, 0.6, 0.61, 1.0)] == [
        "low",
        "low",
        "medium",
        "high",
        "high",
        "high",
    ]


def test_ignores_ground_truth_columns(tiny_artifacts):
    row = _synthetic_rows(1, seed=5)[INPUT].iloc[0].to_dict()
    base = M.predict_failure(row)
    assert (
        M.predict_failure({**row, "anomaly_label": True, "anomaly_type": "overheating"})
        == base
    )


def test_missing_columns_raise(tiny_artifacts):
    row = _synthetic_rows(1)[INPUT].iloc[0].to_dict()
    row.pop("hours_since_service")
    with pytest.raises(ValueError, match="hours_since_service"):
        M.predict_failure(row)


@pytest.mark.skipif(
    not (M.ARTIFACT_DIR / "config.json").exists(),
    reason="run ml/04_predictive_maintenance.ipynb first",
)
def test_real_artifacts_load_and_predict(feats):
    M.load_artifacts.cache_clear()
    out = M.predict_failure(feats[INPUT])
    assert len(out) == len(feats)
    assert all(set(o) == OUTPUT_KEYS for o in out)
    # A normal sample day: no machine should be high risk.
    assert max(o["failure_probability"] for o in out) < 0.6


def test_safety_floor_raises_to_medium_and_names_signal(tiny_artifacts):
    row = _synthetic_rows(1, seed=6)
    row["battery_voltage_dev24"] = 1.0  # the tiny model's only real signal says "fine"
    base = M.predict_failure(row[INPUT].iloc[0].to_dict())
    assert base["failure_probability"] < M.FLOOR_P
    row["coolant_temp_c_dev24"] = 20.0  # ~20 std hotter than this machine's normal
    out = M.predict_failure(row[INPUT].iloc[0].to_dict())
    assert out["failure_probability"] == pytest.approx(M.FLOOR_P)
    assert out["risk_band"] == "medium"
    assert out["top_factors"][0]["feature"] == "coolant_temp_c_devz24"
    assert len(out["top_factors"]) <= M.TOP_N


def test_safety_floor_needs_six_sigma_the_wrong_way():
    df = _synthetic_rows(200)
    scales = M.fit_scales(df)
    rows = df.iloc[[0, 1, 2]].copy()
    for c in df.columns:
        if c.endswith("_dev24"):
            rows[c] = 0.0
    rows.iloc[0, rows.columns.get_loc("coolant_temp_c_dev24")] = 3.0  # < 6 std
    rows.iloc[1, rows.columns.get_loc("battery_voltage_dev24")] = (
        20.0  # higher voltage: fine
    )
    rows.iloc[2, rows.columns.get_loc("battery_voltage_dev24")] = (
        -20.0
    )  # sagging: floor
    floor, signal = M.deviation_floor(rows, scales)
    assert floor.tolist() == [0.0, 0.0, M.FLOOR_P]
    assert signal.tolist() == [None, None, "battery_voltage"]
    assert M.apply_floor(np.array([0.1, 0.1, 0.9]), rows, scales).tolist() == [
        0.1,
        0.1,
        0.9,
    ]

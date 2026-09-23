"""Tests for ml/inference/task_time.py.

Small throwaway models are fitted on the committed 1-day sample (data/output/sample/), so the
tests run on a fresh clone. The last test also checks the real artifacts when they exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

import ml.inference.task_time as T

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "output" / "sample"
OUTPUT_KEYS = {
    "task_id",
    "p10_min",
    "p50_min",
    "p90_min",
    "factors",
    "operator_avg_min",
    "expected_efficiency",
}
UTC = "UTC"


def _read(name: str, dates: list[str]) -> pd.DataFrame:
    return pd.read_csv(SAMPLE / f"{name}.csv", parse_dates=dates)


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    return {
        "tasks": _read("tasks", ["scheduled_start", "actual_start", "actual_end"]),
        "weather": _read("weather", ["ts"]),
        "operators": _read("operators", []).drop(columns=["personality"]),
        "machines": _read("machines", []),
        "shifts": _read("shifts", ["start_time", "end_time"]),
        "fatigue_log": _read("fatigue_log", ["ts"]),
        "maintenance_log": _read("maintenance_log", ["event_date"]),
    }


@pytest.fixture(scope="module")
def features(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    done = tables["tasks"][tables["tasks"].actual_duration_min.notna()]
    return T.build_feature_table(**tables, reference=T.fit_reference(done))


@pytest.fixture()
def tiny_artifacts(
    tables: dict[str, pd.DataFrame],
    features: pd.DataFrame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    done = tables["tasks"][tables["tasks"].actual_duration_min.notna()]
    train = features[features.actual_duration_min.notna()]
    cats = {c: sorted(train[c].dropna().astype(str).unique()) for c in T.CATEGORICAL}
    X, y = T.to_model_frame(train, cats), np.log1p(train.actual_duration_min.to_numpy())
    for q in T.QUANTILES:
        m = lgb.LGBMRegressor(
            objective="quantile",
            alpha=q,
            n_estimators=30,
            min_child_samples=2,
            min_data_in_bin=1,
            random_state=42,
            verbose=-1,
        ).fit(X, y)
        joblib.dump(m, tmp_path / f"lgbm_p{round(q * 100)}.joblib")
    (tmp_path / "feature_list.json").write_text(json.dumps(T.FEATURE_COLUMNS))
    enc = {"categories": cats, "baseline": T.fit_baseline(done), "reference": T.fit_reference(done)}
    (tmp_path / "encoders.json").write_text(json.dumps(enc))
    (tmp_path / "config.json").write_text(json.dumps({"interval_offset_log": 0.05}))
    monkeypatch.setattr(T, "ARTIFACT_DIR", tmp_path)
    _clear_caches()
    yield tmp_path
    _clear_caches()


def _clear_caches() -> None:
    T.load_artifacts.cache_clear()
    if hasattr(T._explainer, "cache"):
        del T._explainer.cache


# ---------------------------------------------------------------------------------------------
# Feature table
# ---------------------------------------------------------------------------------------------
def test_feature_table_has_every_feature(features: pd.DataFrame, tables) -> None:
    assert set(T.FEATURE_COLUMNS) <= set(features.columns)
    assert len(features) == len(tables["tasks"])
    assert features.health_score.between(0.3, 1.0).all()
    assert (features.hours_into_shift >= 0).all()
    assert "personality" not in features.columns


def test_weather_is_the_hour_containing_the_start(features: pd.DataFrame, tables) -> None:
    w = tables["weather"]
    for r in features.itertuples():
        row = w[(w.site_id == r.site_id) & (w.ts <= r.scheduled_start)].sort_values("ts").iloc[-1]
        assert row.rain_mm == pytest.approx(r.rain_mm)
        assert r.scheduled_start - row.ts < pd.Timedelta(hours=1)


def _task(op: str, start: str, end: str | None, dur: float | None, tt: str = "dig") -> dict:
    return {
        "operator_id": op,
        "task_type": tt,
        "quantity": 10.0,
        "haul_distance_m": np.nan,
        "scheduled_start": pd.Timestamp(start, tz=UTC),
        "actual_end": pd.Timestamp(end, tz=UTC) if end else pd.NaT,
        "actual_duration_min": dur,
    }


def test_operator_ratio_uses_only_the_past() -> None:
    ref = {"dig": 1.0, "haul": 1.0}  # reference minutes = quantity (10)
    hist = pd.DataFrame(
        [
            _task("OP01", "2026-06-01 01:00", "2026-06-01 02:00", 20.0),  # ratio 2
            _task("OP01", "2026-06-02 01:00", "2026-06-02 02:00", 10.0),  # ratio 1
            _task("OP01", "2026-06-03 01:00", "2026-06-03 02:00", 5.0),  # after target
            _task("OP02", "2026-06-01 01:00", "2026-06-01 02:00", 40.0),  # other operator
            _task("OP01", "2026-06-01 03:00", "2026-06-01 04:00", 99.0, tt="haul"),  # other type
            _task("OP01", "2026-04-01 01:00", "2026-04-01 02:00", 90.0),  # older than 30 days
        ]
    )
    targets = pd.DataFrame(
        [
            _task("OP01", "2026-06-02 02:30", None, None),  # sees ratios 2 and 1
            _task("OP01", "2026-06-02 01:30", None, None),  # task 2 not finished yet: sees 2
            _task("OP03", "2026-06-02 02:30", None, None),  # no history
        ]
    )
    got = T.operator_time_ratio(hist, targets, ref)
    assert got.iloc[0] == pytest.approx(1.5)
    assert got.iloc[1] == pytest.approx(2.0)
    assert np.isnan(got.iloc[2])


def test_haul_reference_scales_with_distance() -> None:
    df = pd.DataFrame(
        {
            "task_type": ["haul", "haul", "dig"],
            "quantity": [100.0] * 3,
            "haul_distance_m": [500.0, 2000.0, np.nan],
        }
    )
    mins = T.reference_minutes(df, {"haul": 1.0, "dig": 0.5}).to_numpy()
    assert mins == pytest.approx([50.0, 200.0, 50.0])


def test_machine_health_resets_after_service() -> None:
    shifts = pd.DataFrame(
        {
            "shift_id": ["A", "B", "C"],
            "machine_id": ["M01"] * 3,
            "start_time": pd.to_datetime(
                ["2026-06-01 00:30", "2026-06-02 00:30", "2026-06-03 00:30"], utc=True
            ),
            "end_time": pd.to_datetime(
                ["2026-06-01 08:30", "2026-06-02 08:30", "2026-06-03 08:30"], utc=True
            ),
        }
    )
    machines = pd.DataFrame(
        {"machine_id": ["M01"], "total_engine_hours": [1024.0], "service_interval_hours": [100.0]}
    )
    log = pd.DataFrame(
        {
            "machine_id": ["M01", "M01"],
            "event_date": pd.to_datetime(["2026-05-20 00:00", "2026-06-02 09:00"], utc=True),
            "engine_hours_at_event": [950.0, 1016.0],
            "event_type": ["scheduled_service", "scheduled_service"],
        }
    )
    h = T.machine_health_at_shift_start(shifts, machines, log)
    # eh at starts: 1000, 1008, 1016 -> hss 50, 58, 0
    assert h["A"] == pytest.approx(1 - 0.2 * 50 / 100)
    assert h["B"] == pytest.approx(1 - 0.2 * 58 / 100)
    assert h["C"] == pytest.approx(1.0)


def test_calibrate_orders_and_widens() -> None:
    raw = np.array([[3.0, 2.0, 4.0], [1.0, 1.1, 1.2]])
    q = T.calibrate(raw, 0.1)
    assert (q[:, 0] <= q[:, 1]).all() and (q[:, 1] <= q[:, 2]).all()
    assert q[0] == pytest.approx([1.9, 3.0, 4.1])


# ---------------------------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------------------------
def test_output_matches_contract(features: pd.DataFrame, tiny_artifacts: Path) -> None:
    out = T.predict_task_time(features[T.INPUT_COLUMNS])
    assert len(out) == len(features)
    for p, tid in zip(out, features.task_id):
        assert set(p) == OUTPUT_KEYS and p["task_id"] == tid
        assert 0 < p["p10_min"] <= p["p50_min"] <= p["p90_min"]
        assert 1 <= len(p["factors"]) <= T.TOP_N
        for f in p["factors"]:
            assert set(f) == {"feature", "label", "impact_min"}
            assert f["feature"] not in T.NOT_A_FACTOR
        if p["operator_avg_min"] is not None:
            assert p["expected_efficiency"] == pytest.approx(
                p["p50_min"] / p["operator_avg_min"], abs=0.01
            )
    json.dumps(out)  # plain JSON types only


def test_no_history_gives_null_usual_time(features: pd.DataFrame, tiny_artifacts: Path) -> None:
    df = features[T.INPUT_COLUMNS].head(2).copy()
    df["operator_avg_time_ratio"] = np.nan
    for p in T.predict_task_time(df, explain_factors=False):
        assert p["operator_avg_min"] is None and p["expected_efficiency"] is None
        assert p["factors"] == []


def test_categorical_labels_and_unseen_level(features: pd.DataFrame, tiny_artifacts: Path) -> None:
    assert T.factor_name("material_type", "rock") == ("material_type=rock", "Material: rock")
    assert T.factor_name("shift_type", "night") == ("shift_type=night", "Night shift")
    assert T.factor_name("rain_mm", 3.0) == ("rain_mm", "Rain")
    df = features[T.INPUT_COLUMNS].head(1).copy()
    df["material_type"] = "marble"  # never seen in training -> NaN, still predicts
    assert T.predict_task_time(df)[0]["p50_min"] > 0


def test_selects_columns_explicitly(features: pd.DataFrame, tiny_artifacts: Path) -> None:
    df = features[T.INPUT_COLUMNS].head(3).copy()
    base = T.predict_task_time(df, explain_factors=False)
    df["actual_duration_min"] = 1e6  # extra columns must not change anything
    df["personality"] = "novice"
    assert T.predict_task_time(df, explain_factors=False) == base
    with pytest.raises(KeyError):
        T.predict_task_time(df.drop(columns=["rain_mm"]))


def test_feature_list_mismatch_is_rejected(tiny_artifacts: Path) -> None:
    (tiny_artifacts / "feature_list.json").write_text(json.dumps(T.FEATURE_COLUMNS[::-1]))
    T.load_artifacts.cache_clear()
    with pytest.raises(ValueError):
        T.load_artifacts()


@pytest.mark.skipif(
    not (T.ARTIFACT_DIR / "config.json").exists(), reason="run ml/02_task_time.ipynb first"
)
def test_real_artifacts_load_and_predict(features: pd.DataFrame) -> None:
    _clear_caches()
    art = T.load_artifacts()
    assert art["interval_offset"] >= 0
    out = T.predict_task_time(features[T.INPUT_COLUMNS])
    assert all(0 < p["p10_min"] <= p["p50_min"] <= p["p90_min"] for p in out)
    done = features.actual_duration_min.notna().to_numpy()
    p50 = np.array([p["p50_min"] for p in out])[done]
    act = features.actual_duration_min.to_numpy()[done]
    assert np.mean(np.abs(p50 - act) / act) < 0.35  # sample day is in the training period
    _clear_caches()

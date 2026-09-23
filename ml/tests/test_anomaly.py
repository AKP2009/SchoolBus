"""Tests for ml/inference/anomaly.py.

Small throwaway artifacts are fitted on the committed 1-day sample (data/output/sample/), so the
tests run on a fresh clone. The last test also checks the real artifacts when they exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

import ml.inference.anomaly as A

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "output" / "sample" / "telemetry.csv"
OUTPUT_KEYS = {"machine_id", "ts", "anomaly_score", "is_anomaly", "kind", "top_signals"}


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE, parse_dates=["ts"])
    return df.sort_values(["machine_id", "ts"]).reset_index(drop=True)


@pytest.fixture()
def tiny_artifacts(sample: pd.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    machine_map = {"M01": "excavator", "M06": "articulated_truck"}
    feats, _ = A.build_features(sample)
    feats["machine_type"] = feats.machine_id.map(machine_map)
    scale = {}
    for (mt, st), g in feats.groupby(["machine_type", "state"]):
        key = A.model_key(mt, st)
        X = g[A.FEATURE_COLUMNS].to_numpy(float)
        sc = StandardScaler().fit(X)
        m = IsolationForest(n_estimators=50, contamination=0.03, random_state=42).fit(
            sc.transform(X)
        )
        raw = -m.score_samples(sc.transform(X))
        scale[key] = {
            "min": float(raw.min()),
            "max": float(raw.max()),
            "threshold": float(-m.offset_),
        }
        joblib.dump(m, tmp_path / f"iforest_{key}.joblib")
        joblib.dump(sc, tmp_path / f"scaler_{key}.joblib")
    (tmp_path / "feature_list.json").write_text(json.dumps(A.FEATURE_COLUMNS))
    cfg = {"model_keys": sorted(scale), "machine_map": machine_map, "score_scale": scale}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(A, "ARTIFACT_DIR", tmp_path)
    A.load_artifacts.cache_clear()
    yield tmp_path
    A.load_artifacts.cache_clear()


def _window(sample: pd.DataFrame, machine: str = "M01", minutes: int = 60) -> pd.DataFrame:
    """Trailing window ending at the 15th minute of the machine's longest working stretch."""
    g = sample[sample.machine_id == machine]
    working = ~g.is_idle.astype(bool)
    run = (working != working.shift()).cumsum()
    longest = run[working].value_counts().idxmax()
    end = g.ts[run == longest].iloc[15]
    return g[(g.ts > end - pd.Timedelta(minutes=minutes)) & (g.ts <= end)].copy()


def test_output_matches_contract(sample, tiny_artifacts):
    out = A.score_anomaly(_window(sample))
    assert set(out) == OUTPUT_KEYS
    assert out["machine_id"] == "M01"
    assert out["ts"].endswith("Z")
    assert 0.0 <= out["anomaly_score"] <= 1.0
    assert isinstance(out["is_anomaly"], bool)
    assert out["kind"] in {"normal", "machine_fault", "sensor_glitch"}
    assert len(out["top_signals"]) == A.TOP_N
    for s in out["top_signals"]:
        assert set(s) == {"feature", "z"} and s["feature"] in A.FEATURE_COLUMNS
    json.dumps(out)  # plain JSON types only


def test_single_minute_spike_is_sensor_glitch(sample, tiny_artifacts):
    w = _window(sample)
    w.loc[w.index[-1], "coolant_temp_c"] = 150.0
    out = A.score_anomaly(w)
    assert out["kind"] == "sensor_glitch" and out["is_anomaly"]
    assert out["top_signals"][0]["feature"] == "coolant_temp_c"


def test_glitch_does_not_leak_into_following_features(sample):
    w = _window(sample)
    spiked = w.copy()
    spiked.loc[spiked.index[-5], "battery_voltage"] = 0.0
    f_clean, _ = A.build_features(w)
    f_spiked, glitch = A.build_features(spiked)
    assert glitch.iloc[-5] == "battery_voltage"
    # The spike is replaced by the previous reading: features move by sensor noise, not by the
    # ~5.6 V a 0 V reading would pull the 5-min mean down.
    diff = (f_clean["battery_voltage_mean5"] - f_spiked["battery_voltage_mean5"]).abs()
    assert diff.max() < 0.2
    assert glitch.drop(glitch.index[-5]).isna().all()


def test_fault_fires_only_after_persistence(sample, tiny_artifacts):
    # Broad engine fault starting 6 minutes before the window end, 10+ minutes into working.
    w = _window(sample)
    fault = ["coolant_temp_c", "hydraulic_oil_temp_c", "oil_pressure_kpa", "battery_voltage"]
    w.loc[w.index[-6:], [*fault, "vibration_rms_g"]] = [112.0, 95.0, 120.0, 22.5, 1.5]
    # 2 flagged minutes: not yet; 3rd consecutive flagged minute: machine_fault
    assert A.score_anomaly(w.iloc[:-4])["kind"] == "normal"
    out = A.score_anomaly(w.iloc[:-3])
    assert out["kind"] == "machine_fault" and out["is_anomaly"]
    top = " ".join(s["feature"] for s in out["top_signals"])
    assert any(sig in top for sig in [*fault, "vibration_rms_g"])


def test_classify_settle_and_persistence():
    ts = pd.Series(pd.date_range("2026-08-20 01:00", periods=12, freq="1min", tz="UTC"))
    feats = pd.DataFrame(
        {"machine_id": "M01", "ts": ts, "state_age_min": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]}
    )
    flagged = np.array([1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 0], dtype=bool)
    glitch = pd.Series([None] * 11 + ["coolant_temp_c"], dtype=object)
    kind = A.classify(feats, flagged, glitch)
    # flags at age < 5 don't count; minutes 5-7 are the first 3 settled flags in a row
    assert list(kind[:7]) == ["normal"] * 7
    assert kind[7] == "machine_fault"
    assert list(kind[8:11]) == ["normal"] * 3  # run broken at minute 8, only 2 flags after it
    assert kind[11] == "sensor_glitch"  # glitches fire at once


def test_ignores_ground_truth_columns(sample, tiny_artifacts):
    w = _window(sample)
    base = A.score_anomaly(w)
    poisoned = w.assign(anomaly_label=True, anomaly_type="overheating")
    assert A.score_anomaly(poisoned) == base
    assert A.score_anomaly(w.drop(columns=["anomaly_label", "anomaly_type"])) == base


def test_rejects_multiple_machines(sample, tiny_artifacts):
    with pytest.raises(ValueError, match="one machine"):
        A.score_anomaly(sample)


@pytest.mark.skipif(
    not (A.ARTIFACT_DIR / "config.json").exists(), reason="run ml/01_anomaly.ipynb first"
)
def test_real_artifacts_load_and_score(sample):
    A.load_artifacts.cache_clear()
    out = A.score_anomaly(_window(sample, "M06"))
    assert set(out) == OUTPUT_KEYS and out["machine_id"] == "M06"

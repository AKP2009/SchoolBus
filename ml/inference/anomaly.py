"""Unusual behaviour / anomaly detection (models.md §1).

`score_anomaly(window_df)` scores the last minute of one machine's trailing telemetry window and
returns the output JSON from models.md §1. Feature code lives here so the training notebook
(`ml/01_anomaly.ipynb`) and the backend compute exactly the same features.

Never reads `anomaly_label` / `anomaly_type`: every function selects columns explicitly.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "anomaly"

SIGNALS = [
    "engine_rpm",
    "engine_load_pct",
    "coolant_temp_c",
    "oil_pressure_kpa",
    "hydraulic_pressure_bar",
    "hydraulic_oil_temp_c",
    "fuel_rate_lph",
    "battery_voltage",
    "vibration_rms_g",
]
INPUT_COLUMNS = ["ts", "machine_id", *SIGNALS, "is_idle"]

FEATURE_COLUMNS = [
    *[f"{s}_{agg}" for s in SIGNALS for agg in ("mean5", "std5", "slope15")],
    "idle_pct30",
    "rpm_per_load",
    "fuel_per_load",
    "coolant_minus_hyd_oil_c",
]

# Sensor validity ranges (what a telematics unit accepts as a physically possible reading).
# A reading outside its range cannot be real; oil pressure only counts while the engine runs.
PLAUSIBLE_RANGE: dict[str, tuple[float, float]] = {
    "engine_rpm": (0, 3000),
    "engine_load_pct": (-10, 110),
    "coolant_temp_c": (10, 130),
    "oil_pressure_kpa": (20, 800),
    "hydraulic_pressure_bar": (-10, 1000),
    "hydraulic_oil_temp_c": (10, 120),
    "fuel_rate_lph": (0, 80),
    "battery_voltage": (18, 32),
    "vibration_rms_g": (0, 5),
}
ENGINE_RUNNING_RPM = 500
PERSIST_MIN = 3  # a real fault persists >= 3 minutes ...
MULTI_SIGNAL_Z = 3.0  # ... or moves >= 2 signals with |z| above this
TOP_N = 3


# ---------------------------------------------------------------------------------------------
# Sensor glitch handling
# ---------------------------------------------------------------------------------------------
def implausible(df: pd.DataFrame) -> pd.DataFrame:
    """Boolean frame (rows x SIGNALS): reading is outside its sensor validity range."""
    out = pd.DataFrame(index=df.index)
    for s in SIGNALS:
        lo, hi = PLAUSIBLE_RANGE[s]
        x = df[s].astype(float)
        out[s] = (x < lo) | (x > hi) | x.isna()
    out["oil_pressure_kpa"] &= df["engine_rpm"].astype(float) > ENGINE_RUNNING_RPM
    return out


def mark_glitches(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Flag single-minute sensor glitches and replace the glitched value by the previous reading.

    Glitch at minute t: exactly one signal is implausible, and that signal was plausible in the
    previous minute of the same machine (<= 2 min earlier). Causal: only uses t-1 and t.
    Returns (cleaned copy of df, Series with the glitched signal name or None per row).
    `df` must be sorted by (machine_id, ts).
    """
    bad = implausible(df)
    same_machine = df["machine_id"].eq(df["machine_id"].shift())
    contiguous = same_machine & (df["ts"] - df["ts"].shift() <= pd.Timedelta(minutes=2))
    prev_bad = bad.shift(fill_value=False).astype(bool)
    single = bad.sum(axis=1) == 1
    newly = bad & ~prev_bad & contiguous.to_numpy()[:, None]
    is_glitch = single & newly.any(axis=1)

    glitch_signal = pd.Series(None, index=df.index, dtype=object)
    clean = df.copy()
    for s in SIGNALS:
        rows = is_glitch & bad[s]
        if rows.any():
            glitch_signal[rows] = s
            clean[s] = clean[s].astype(float).mask(rows)
            clean[s] = clean.groupby("machine_id", sort=False)[s].ffill()
    return clean, glitch_signal


# ---------------------------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------------------------
def _machine_features(g: pd.DataFrame) -> pd.DataFrame:
    g = g.set_index("ts")
    t = (g.index - g.index[0]).total_seconds().to_numpy() / 60.0  # minutes
    out = pd.DataFrame(index=g.index)
    t_s = pd.Series(t, index=g.index)
    r15_n = t_s.rolling("15min").count()
    r15_t = t_s.rolling("15min").sum()
    r15_tt = (t_s * t_s).rolling("15min").sum()
    denom = r15_n * r15_tt - r15_t**2
    for s in SIGNALS:
        x = g[s].astype(float)
        r5 = x.rolling("5min")
        out[f"{s}_mean5"] = r5.mean()
        out[f"{s}_std5"] = r5.std(ddof=0)
        r15_x = x.rolling("15min").sum()
        r15_tx = (t_s * x).rolling("15min").sum()
        slope = (r15_n * r15_tx - r15_t * r15_x) / denom.where(denom > 1e-9)
        out[f"{s}_slope15"] = slope.fillna(0.0)
    out["idle_pct30"] = g["is_idle"].astype(float).rolling("30min").mean() * 100
    load = g["engine_load_pct"].clip(lower=0.0) + 1  # the sensor reads slightly below 0 at idle
    out["rpm_per_load"] = g["engine_rpm"] / load
    out["fuel_per_load"] = g["fuel_rate_lph"] / load
    out["coolant_minus_hyd_oil_c"] = g["coolant_temp_c"] - g["hydraulic_oil_temp_c"]
    return out.fillna(0.0).reset_index(drop=True)


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Rolling-window features per machine, one row per telemetry minute.

    Returns (features aligned to the sorted input, glitch Series). Input columns used: INPUT_COLUMNS.
    Windows are trailing and time-based (5 min, 15 min, 30 min), so gaps between shifts are fine.
    """
    df = df.loc[:, INPUT_COLUMNS].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)
    clean, glitch = mark_glitches(df)
    parts = [_machine_features(g) for _, g in clean.groupby("machine_id", sort=False)]
    feats = pd.concat(parts, ignore_index=True)[FEATURE_COLUMNS]
    feats.index = df.index
    feats.insert(0, "ts", df["ts"])
    feats.insert(0, "machine_id", df["machine_id"])
    return feats, glitch


# ---------------------------------------------------------------------------------------------
# Decision logic (shared by the notebook evaluation and live scoring)
# ---------------------------------------------------------------------------------------------
def classify(
    machine_id: pd.Series,
    ts: pd.Series,
    flagged: np.ndarray,
    z: np.ndarray,
    glitch: pd.Series,
) -> np.ndarray:
    """kind per row: 'sensor_glitch', 'machine_fault' or 'normal'.

    machine_fault = model flag that has persisted >= PERSIST_MIN consecutive minutes, or a flag
    where >= 2 distinct raw signals have a feature with |z| >= MULTI_SIGNAL_Z.
    Rows must be sorted by (machine_id, ts).
    """
    flagged = np.asarray(flagged, dtype=bool)
    same_run = (
        machine_id.eq(machine_id.shift()) & (ts.diff() <= pd.Timedelta(minutes=2))
    ).to_numpy()
    run_id = np.cumsum(~same_run | ~flagged)
    run_len = pd.Series(flagged.astype(int)).groupby(run_id).cumsum().to_numpy()

    big = np.abs(z) >= MULTI_SIGNAL_Z
    sig_of = np.array([_signal_of(f) for f in FEATURE_COLUMNS])
    n_signals = np.zeros(len(flagged), dtype=int)
    for s in np.unique(sig_of):
        n_signals += big[:, sig_of == s].any(axis=1)

    fault = flagged & ((run_len >= PERSIST_MIN) | (n_signals >= 2))
    kind = np.where(fault, "machine_fault", "normal").astype(object)
    kind[glitch.notna().to_numpy()] = "sensor_glitch"
    return kind


def _signal_of(feature: str) -> str:
    for s in SIGNALS:
        if feature.startswith(s):
            return s
    return {
        "idle_pct30": "is_idle",
        "rpm_per_load": "engine_rpm",
        "fuel_per_load": "fuel_rate_lph",
        "coolant_minus_hyd_oil_c": "coolant_temp_c",
    }[feature]


# ---------------------------------------------------------------------------------------------
# Live scoring
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_artifacts(artifact_dir: str | None = None) -> dict[str, Any]:
    d = Path(artifact_dir) if artifact_dir else ARTIFACT_DIR
    features = json.loads((d / "feature_list.json").read_text())
    if features != FEATURE_COLUMNS:
        raise RuntimeError("feature_list.json does not match FEATURE_COLUMNS; retrain the model")
    cfg = json.loads((d / "config.json").read_text())
    models = {
        mt: {
            "model": joblib.load(d / f"iforest_{mt}.joblib"),
            "scaler": joblib.load(d / f"scaler_{mt}.joblib"),
        }
        for mt in cfg["machine_types"]
    }
    return {"features": features, "config": cfg, "models": models}


def score_frame(
    feats: pd.DataFrame, machine_type: str, art: dict[str, Any]
) -> dict[str, np.ndarray]:
    """Raw model outputs for a feature frame of one machine type."""
    m = art["models"][machine_type]
    X = feats.loc[:, art["features"]].to_numpy(dtype=float)
    z = m["scaler"].transform(X)
    raw = -m["model"].score_samples(z)  # higher = more anomalous
    c = art["config"]["score_scale"][machine_type]
    score = np.clip((raw - c["min"]) / (c["max"] - c["min"]), 0.0, 1.0)
    flagged = raw > c["threshold"]
    return {"z": z, "raw": raw, "score": score, "flagged": flagged}


def score_anomaly(window_df: pd.DataFrame, machine_type: str | None = None) -> dict[str, Any]:
    """Score the latest minute of one machine's trailing telemetry window.

    `window_df`: telemetry rows of one machine (columns INPUT_COLUMNS, extra columns ignored),
    ideally the last >= 30 minutes. `machine_type` defaults to a `machine_type` column in the
    frame, else the machine map saved at training time.
    """
    art = load_artifacts()
    if window_df.empty:
        raise ValueError("window_df is empty")
    machine_ids = window_df["machine_id"].unique()
    if len(machine_ids) != 1:
        raise ValueError(f"window_df must hold one machine, got {list(machine_ids)}")
    machine_id = str(machine_ids[0])
    if machine_type is None:
        if "machine_type" in window_df.columns:
            machine_type = str(window_df["machine_type"].iloc[-1])
        else:
            machine_type = art["config"]["machine_map"][machine_id]

    feats, glitch = build_features(window_df)
    out = score_frame(feats, machine_type, art)
    kind = classify(feats["machine_id"], feats["ts"], out["flagged"], out["z"], glitch)

    i = len(feats) - 1
    k = str(kind[i])
    z_last = out["z"][i]
    top = np.argsort(-np.abs(z_last))[:TOP_N]
    top_signals = [{"feature": art["features"][j], "z": round(float(z_last[j]), 2)} for j in top]
    if k == "sensor_glitch":
        # The glitched reading was replaced before feature building; report its own z instead,
        # standardised like that signal's 5-min mean.
        s = str(glitch.iloc[i])
        sc = art["models"][machine_type]["scaler"]
        j = art["features"].index(f"{s}_mean5")
        raw_value = float(window_df.sort_values("ts", kind="stable")[s].iloc[-1])
        z_raw = (raw_value - sc.mean_[j]) / sc.scale_[j]
        top_signals = [{"feature": s, "z": round(float(z_raw), 2)}] + top_signals[: TOP_N - 1]
    return {
        "machine_id": machine_id,
        "ts": feats["ts"].iloc[i].isoformat().replace("+00:00", "Z"),
        "anomaly_score": round(float(out["score"][i]), 3),
        "is_anomaly": k != "normal",
        "kind": k,
        "top_signals": top_signals,
    }

"""Predictive maintenance (models.md §3).

`predict_failure(features)` takes one machine-hour feature row (or a frame of them) as built by
`build_hourly_features` and returns the output JSON from models.md §3: the probability of a
failure within the next 48 engine hours, the likely component and the top SHAP factors.

Feature code lives here so the training notebook (`ml/04_predictive_maintenance.ipynb`) and the
backend compute exactly the same thing:

* `engine_hours` rebuilds engine hours per telemetry minute from `shifts` (a shift adds its full
  length, cut at a logged failure) anchored to the end-of-run `machines.total_engine_hours`,
  as in `ml/01_anomaly.ipynb` but with failures from `maintenance_log`, not truth files.
* `score_minutes` runs the anomaly detector (`ml.inference.anomaly`) on every minute.
* `build_hourly_features` aggregates minutes into engine-hour bins and computes trailing-window
  features in engine hours (24 h / 72 h), plus calendar 7 days for the fault-code count.

Columns are selected explicitly. `anomaly_label`, `anomaly_type` and `data/output/truth/` are
never read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "maintenance"

HORIZON_H = 48.0  # engine hours
WINDOWS_H = (24, 72)  # trailing windows in engine hours
FAULT_WINDOW_DAYS = 7
HIGH_LOAD_PCT = 80.0
TRAVEL_KMH = (
    2.0  # "travelling" for the undercarriage vibration trend (component head only)
)
MACHINE_TYPES = ("excavator", "wheel_loader", "dozer", "articulated_truck")
TOP_N = 3

TELEMETRY_COLUMNS = [
    "ts",
    "machine_id",
    "shift_id",
    "engine_load_pct",
    "coolant_temp_c",
    "oil_pressure_kpa",
    "hydraulic_pressure_bar",
    "hydraulic_oil_temp_c",
    "battery_voltage",
    "vibration_rms_g",
    "ground_speed_kmh",
    "engine_rpm",
    "fuel_rate_lph",
    "fault_code",
]
# Hourly aggregate -> how it is made from the minute rows
HOURLY_SIGNALS = {
    "coolant_temp_c": ("coolant_temp_c", "mean"),
    "oil_pressure_kpa": ("oil_pressure_kpa", "mean"),
    "hydraulic_oil_temp_c": ("hydraulic_oil_temp_c", "mean"),
    "hydraulic_pressure_bar_std": ("hydraulic_pressure_bar", "std"),
    "vibration_rms_g": ("vibration_rms_g", "mean"),
    "battery_voltage": ("battery_voltage", "mean"),
}
TREND_SIGNALS = list(HOURLY_SIGNALS)
# Direction in which a signal gets worse before a failure (+1 rising, -1 falling)
BAD_DIRECTION = {
    "coolant_temp_c": 1,
    "oil_pressure_kpa": -1,
    "hydraulic_oil_temp_c": 1,
    "hydraulic_pressure_bar_std": 1,
    "vibration_rms_g": 1,
    "battery_voltage": -1,
    "vibration_travel_g": 1,
}

# models.md §3 features as specified (v1)
SPEC_FEATURES = [
    "hours_since_service",
    "service_ratio",
    *[
        f"{s}_{agg}{w}"
        for s in TREND_SIGNALS
        for w in WINDOWS_H
        for agg in ("mean", "slope")
    ],
    "anomaly_count24",
    "anomaly_score_mean24",
    "fault_code_count24",
    "fault_code_count7d",
    "high_load_hours_since_service",
    *[f"machine_type_{t}" for t in MACHINE_TYPES],
    "age_years",
]
# Tuning round (v2): each signal against the machine's own baseline, and component-agnostic
# "worst signal" summaries, so a failure type seen once or twice in training is still caught.
DEV_SIGNALS = [*TREND_SIGNALS, "vibration_travel_g"]
BASELINE_H = (
    72,
    336,
)  # baseline = engine hours (t-336, t-72], before most of the drift
RAW_EXTRA_COLUMNS = [
    "vibration_travel_g_mean24",
    "vibration_travel_g_slope72",
    *[f"{s}_dev24" for s in DEV_SIGNALS],
]
DERIVED_FEATURES = [
    "vibration_travel_g_mean24",
    "vibration_travel_g_slope72",
    *[f"{s}_devz24" for s in DEV_SIGNALS],
    "worst_dev_z24",
    "worst_trend_z72",
]
FEATURE_COLUMNS = [*SPEC_FEATURES, *DERIVED_FEATURES]
KEY_COLUMNS = ["machine_id", "machine_type", "ts", "engine_h"]

# Signal -> subsystem (models.md §11); undercarriage = vibration while travelling
SUBSYSTEM_SIGNALS = {
    "engine": ["oil_pressure_kpa", "vibration_rms_g"],
    "cooling": ["coolant_temp_c"],
    "hydraulics": ["hydraulic_oil_temp_c", "hydraulic_pressure_bar_std"],
    "electrical": ["battery_voltage"],
    "undercarriage": ["vibration_travel_g"],
}
COMPONENTS = list(SUBSYSTEM_SIGNALS)

RISK_BANDS = (
    (0.3, "low"),
    (0.6, "medium"),
    (1.01, "high"),
)  # < 0.3 low, 0.3-0.6, > 0.6 high


# ---------------------------------------------------------------------------------------------
# Engine hours
# ---------------------------------------------------------------------------------------------
def _failures(maintenance_log: pd.DataFrame) -> pd.DataFrame:
    """Logged failures with their repair time: machine_id, failure_ts, repair_ts, component,
    engine_hours_at_event."""
    m = maintenance_log[
        ["machine_id", "event_date", "event_type", "component", "engine_hours_at_event"]
    ].copy()
    m["event_date"] = pd.to_datetime(m["event_date"], utc=True)
    f = m[m.event_type == "failure"].sort_values("event_date")
    r = m[m.event_type == "repair"].sort_values("event_date")
    f = pd.merge_asof(
        f.rename(columns={"event_date": "failure_ts"}),
        r[["machine_id", "event_date"]].rename(columns={"event_date": "repair_ts"}),
        left_on="failure_ts",
        right_on="repair_ts",
        by="machine_id",
        direction="forward",
    )
    return f[
        ["machine_id", "failure_ts", "repair_ts", "component", "engine_hours_at_event"]
    ].reset_index(drop=True)


def shift_engine_hours(
    shifts: pd.DataFrame, machines: pd.DataFrame, maintenance_log: pd.DataFrame
) -> pd.DataFrame:
    """Engine hours at each shift start: shift_id, machine_id, start_time, eh_start, eh_end.

    A shift adds its full length (engine hours don't stop when telemetry does), cut at a logged
    failure inside it. Anchored so that the last shift ends at `machines.total_engine_hours`.
    """
    sh = shifts[["shift_id", "machine_id", "start_time", "end_time"]].copy()
    sh["start_time"] = pd.to_datetime(sh["start_time"], utc=True)
    sh["end_time"] = pd.to_datetime(sh["end_time"], utc=True)
    fails = _failures(maintenance_log)
    cut = sh.merge(fails[["machine_id", "failure_ts"]], on="machine_id", how="left")
    cut = cut[(cut.failure_ts >= cut.start_time) & (cut.failure_ts < cut.end_time)]
    end = sh.shift_id.map(cut.groupby("shift_id").failure_ts.min()).fillna(sh.end_time)
    sh["hours"] = (
        pd.to_datetime(end, utc=True) - sh.start_time
    ).dt.total_seconds() / 3600
    sh = sh.sort_values(["machine_id", "start_time"], kind="stable")
    total_end = sh.machine_id.map(machines.set_index("machine_id").total_engine_hours)
    from_here = sh.groupby("machine_id").hours.transform(
        lambda x: x[::-1].cumsum()[::-1]
    )
    sh["eh_start"] = total_end - from_here
    sh["eh_end"] = sh.eh_start + sh.hours
    return sh[
        ["shift_id", "machine_id", "start_time", "eh_start", "eh_end"]
    ].reset_index(drop=True)


def engine_hours(telemetry: pd.DataFrame, shift_eh: pd.DataFrame) -> np.ndarray:
    """Engine hours of each telemetry minute = shift start engine hours + minutes into shift."""
    t = telemetry[["shift_id", "ts"]].merge(
        shift_eh[["shift_id", "start_time", "eh_start"]], on="shift_id", how="left"
    )
    ts = pd.to_datetime(t.ts, utc=True)
    return (t.eh_start + (ts - t.start_time).dt.total_seconds() / 3600).to_numpy()


# ---------------------------------------------------------------------------------------------
# Minute-level inputs
# ---------------------------------------------------------------------------------------------
def score_minutes(
    telemetry: pd.DataFrame, machine_type: Mapping[str, str]
) -> pd.DataFrame:
    """Anomaly detector output per telemetry minute (models.md §1): machine_id, ts,
    anomaly_score, kind. Also returns the glitch-cleaned signals used for the hourly means.
    """
    from ml.inference import anomaly as A

    feats, glitch = A.build_features(telemetry[A.INPUT_COLUMNS])
    art = A.load_artifacts()
    score = np.zeros(len(feats))
    flagged = np.zeros(len(feats), dtype=bool)
    for mt, idx in feats.groupby(feats.machine_id.map(machine_type)).groups.items():
        out = A.score_frame(feats.loc[idx], mt, art)
        rows = feats.index.get_indexer(idx)
        score[rows], flagged[rows] = out["score"], out["flagged"]
    return pd.DataFrame(
        {
            "machine_id": feats.machine_id.to_numpy(),
            "ts": feats.ts.to_numpy(),
            "anomaly_score": score,
            "kind": A.classify(feats, flagged, glitch),
        }
    )


def _clean_glitches(df: pd.DataFrame) -> pd.DataFrame:
    """Replace single-minute sensor glitches by the previous reading (same rule as model 1), so a
    coolant reading of 150 or a battery reading of 0 V doesn't shift an hourly mean.
    `df` must be sorted by (machine_id, ts)."""
    from ml.inference import anomaly as A

    clean, _ = A.mark_glitches(df)
    return clean


# ---------------------------------------------------------------------------------------------
# Hourly features
# ---------------------------------------------------------------------------------------------
def _run_starts(
    machine_id: pd.Series, ts: pd.Series, active: pd.Series, value=None
) -> pd.Series:
    """True on the first minute of each run of `active` minutes (a gap > 2 min or, if given, a
    change of `value` starts a new run)."""
    same = machine_id.eq(machine_id.shift()) & (ts.diff() <= pd.Timedelta(minutes=2))
    prev = active.shift(fill_value=False) & same
    if value is not None:
        prev &= value.eq(value.shift()).fillna(False)
    return active & ~prev


def _rolling_slope(t: pd.Series, x: pd.Series, window: str) -> pd.Series:
    """Least-squares slope of x on t (units of x per engine hour) over a trailing window."""
    ok = x.notna().astype(float)
    tt = t.where(x.notna(), 0.0)
    xx = x.fillna(0.0)

    def r(s: pd.Series) -> pd.Series:
        return s.rolling(window).sum()

    n, st, stt, sx, stx = r(ok), r(tt), r(tt * tt), r(xx), r(tt * xx)
    den = n * stt - st**2
    return ((n * stx - st * sx) / den.where(den > 1e-9)).where(n >= 3)


def hourly_aggregates(minutes: pd.DataFrame) -> pd.DataFrame:
    """One row per machine x engine-hour bin (floor of engine_h) with telemetry.

    `minutes` needs TELEMETRY_COLUMNS + engine_h + anomaly_score + kind, sorted by
    (machine_id, ts). Aggregates are glitch-cleaned.
    """
    m = minutes.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)
    m["ts"] = pd.to_datetime(m.ts, utc=True)
    m["bin"] = np.floor(m.engine_h).astype(int)
    m["fault_start"] = _run_starts(
        m.machine_id, m.ts, m.kind.eq("machine_fault")
    ).astype(int)
    m["code_start"] = _run_starts(
        m.machine_id, m.ts, m.fault_code.notna(), m.fault_code
    ).astype(int)
    m["high_load"] = (m.engine_load_pct > HIGH_LOAD_PCT).astype(int)
    m["vibration_travel_g"] = m.vibration_rms_g.where(m.ground_speed_kmh > TRAVEL_KMH)
    for c, f in HOURLY_SIGNALS.values():
        if f == "std":
            m[f"_{c}_sq"] = m[c].astype(float) ** 2
    agg = {
        "ts": ("ts", "last"),
        "engine_h": ("engine_h", "last"),
        "n_min": ("ts", "size"),
        **{k: (c, "mean") for k, (c, f) in HOURLY_SIGNALS.items() if f == "mean"},
        **{
            f"_{c}_sq": (f"_{c}_sq", "mean")
            for c, f in HOURLY_SIGNALS.values()
            if f == "std"
        },
        **{f"_{c}_m": (c, "mean") for c, f in HOURLY_SIGNALS.values() if f == "std"},
        "vibration_travel_g": ("vibration_travel_g", "mean"),
        "anomaly_score_sum": ("anomaly_score", "sum"),
        "anomaly_count": ("fault_start", "sum"),
        "fault_code_count": ("code_start", "sum"),
        "high_load_min": ("high_load", "sum"),
    }
    h = m.groupby(["machine_id", "bin"], sort=True).agg(**agg).reset_index()
    for k, (c, f) in HOURLY_SIGNALS.items():
        if f == "std":  # population std of the minutes in the bin
            h[k] = np.sqrt((h[f"_{c}_sq"] - h[f"_{c}_m"] ** 2).clip(lower=0))
    return h.drop(columns=[c for c in h if c.startswith("_")])


def build_hourly_features(
    telemetry: pd.DataFrame,
    minute_scores: pd.DataFrame,
    shifts: pd.DataFrame,
    machines: pd.DataFrame,
    maintenance_log: pd.DataFrame,
) -> pd.DataFrame:
    """Feature rows per machine per engine hour (models.md §3).

    Returns KEY_COLUMNS + SPEC_FEATURES + RAW_EXTRA_COLUMNS. `ts` / `engine_h` are those of
    the last telemetry minute in the bin: the moment the prediction is made. Every window is
    trailing, so a row only uses telemetry up to its own `ts`.
    `minute_scores` = `score_minutes(telemetry, ...)` (machine_id, ts, anomaly_score, kind).
    """
    tel = telemetry[TELEMETRY_COLUMNS].copy()
    tel["ts"] = pd.to_datetime(tel.ts, utc=True)
    tel = tel.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)
    tel = _clean_glitches(tel)
    sc = minute_scores[["machine_id", "ts", "anomaly_score", "kind"]].copy()
    sc["ts"] = pd.to_datetime(sc.ts, utc=True)
    tel = tel.merge(sc, on=["machine_id", "ts"], how="left")
    tel["anomaly_score"] = tel.anomaly_score.fillna(0.0)
    tel["kind"] = tel.kind.fillna("normal")
    shift_eh = shift_engine_hours(shifts, machines, maintenance_log)
    tel["engine_h"] = engine_hours(tel, shift_eh)

    # High-load minutes since the last scheduled service (by wall-clock time of the service)
    mlog = maintenance_log[
        ["machine_id", "event_date", "event_type", "engine_hours_at_event"]
    ].copy()
    mlog["event_date"] = pd.to_datetime(mlog.event_date, utc=True)
    services = mlog[mlog.event_type == "scheduled_service"].sort_values("event_date")
    h = hourly_aggregates(tel)

    parts = []
    for mid, g in h.groupby("machine_id", sort=False):
        g = g.reset_index(drop=True)
        idx = pd.Timestamp("2000-01-01", tz="UTC") + pd.to_timedelta(g.bin, unit="h")
        gi = g.set_index(idx)
        t = pd.Series(gi.bin.to_numpy() - gi.bin.iloc[0], index=idx, dtype=float)
        out = pd.DataFrame(index=idx)
        for w in WINDOWS_H:
            win = f"{w}h"
            n = gi.n_min.rolling(win).sum()
            for s in DEV_SIGNALS:
                x = gi[s]
                wsum = (x * gi.n_min).rolling(win).sum()
                wn = gi.n_min.where(x.notna()).rolling(win).sum()
                out[f"{s}_mean{w}"] = wsum / wn
                out[f"{s}_slope{w}"] = _rolling_slope(t, x, win)
            if w == 24:
                out["anomaly_count24"] = gi.anomaly_count.rolling(win).sum()
                out["anomaly_score_mean24"] = (
                    gi.anomaly_score_sum.rolling(win).sum() / n
                )
                out["fault_code_count24"] = gi.fault_code_count.rolling(win).sum()
        lo, hi = BASELINE_H
        for s in DEV_SIGNALS:
            x = gi[s]
            wx = (x * gi.n_min).fillna(0.0)
            wn = gi.n_min.where(x.notna()).fillna(0.0)
            bsum = wx.rolling(f"{hi}h").sum() - wx.rolling(f"{lo}h").sum()
            bn = wn.rolling(f"{hi}h").sum() - wn.rolling(f"{lo}h").sum()
            base = (bsum / bn.where(bn >= 60)).to_numpy()  # >= 1 h of baseline minutes
            out[f"{s}_dev24"] = out[f"{s}_mean24"].to_numpy() - base
        by_ts = g.set_index("ts")
        out["fault_code_count7d"] = (
            by_ts.fault_code_count.rolling(f"{FAULT_WINDOW_DAYS}D").sum().to_numpy()
        )
        out = out.reset_index(drop=True)
        out["machine_id"] = mid
        out["ts"] = g.ts
        out["engine_h"] = g.engine_h
        out["_high_load_min"] = g.high_load_min
        parts.append(out)
    f = pd.concat(parts, ignore_index=True)

    # Service: last scheduled service logged at or before the prediction time
    f = f.sort_values("ts", kind="stable")
    f = pd.merge_asof(
        f,
        services[["machine_id", "event_date", "engine_hours_at_event"]],
        left_on="ts",
        right_on="event_date",
        by="machine_id",
        direction="backward",
    )
    f = f.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)
    mi = machines.set_index("machine_id")
    interval = f.machine_id.map(mi.service_interval_hours)
    f["hours_since_service"] = (f.engine_h - f.engine_hours_at_event).clip(lower=0)
    f["service_ratio"] = f.hours_since_service / interval
    period = f.event_date.astype("int64").fillna(0)
    f["high_load_hours_since_service"] = (
        f.groupby([f.machine_id, period])._high_load_min.cumsum() / 60.0
    )
    f["machine_type"] = f.machine_id.map(mi.machine_type)
    for t in MACHINE_TYPES:
        f[f"machine_type_{t}"] = (f.machine_type == t).astype(int)
    built = pd.to_datetime(
        f.machine_id.map(mi.year).astype(int).astype(str) + "-01-01", utc=True
    )
    f["age_years"] = (f.ts - built).dt.days / 365.25
    return f[[*KEY_COLUMNS, *SPEC_FEATURES, *RAW_EXTRA_COLUMNS]]


# ---------------------------------------------------------------------------------------------
# Labels (training / evaluation only)
# ---------------------------------------------------------------------------------------------
def label_hours(
    feats: pd.DataFrame,
    maintenance_log: pd.DataFrame,
    run_end_eh: Mapping[str, float],
    horizon_h: float = HORIZON_H,
) -> pd.DataFrame:
    """Label each feature row from `maintenance_log` failures.

    Adds: `hours_to_failure` (engine hours to the next logged failure on the machine, NaN if
    none), `next_failure_ts`, `next_component`, `label` (1 if the failure is within `horizon_h`),
    and `keep`: False for rows between a failure and its repair, and for censored rows (no
    failure ahead and fewer than `horizon_h` engine hours of data left, so the label is unknown).
    """
    f = _failures(maintenance_log).sort_values("failure_ts")
    d = feats[["machine_id", "ts", "engine_h"]].copy()
    d["_row"] = np.arange(len(d))
    d = pd.merge_asof(
        d.sort_values("ts"),
        f.rename(columns={"failure_ts": "next_failure_ts"})[
            ["machine_id", "next_failure_ts", "component", "engine_hours_at_event"]
        ],
        left_on="ts",
        right_on="next_failure_ts",
        by="machine_id",
        direction="forward",
        allow_exact_matches=False,
    )
    d = pd.merge_asof(
        d.sort_values("ts"),
        f[["machine_id", "failure_ts", "repair_ts"]],
        left_on="ts",
        right_on="failure_ts",
        by="machine_id",
        direction="backward",
    ).sort_values("_row")
    out = pd.DataFrame(index=feats.index)
    out["hours_to_failure"] = (d.engine_hours_at_event - d.engine_h).to_numpy()
    out["next_failure_ts"] = d.next_failure_ts.to_numpy()
    out["next_component"] = d.component.to_numpy()
    out["label"] = (
        out.hours_to_failure.gt(0) & out.hours_to_failure.le(horizon_h)
    ).astype(int)
    down = (d.ts >= d.failure_ts) & (d.ts <= d.repair_ts)
    left = d.machine_id.map(run_end_eh) - d.engine_h
    censored = d.next_failure_ts.isna() & (left < horizon_h)
    out["keep"] = (~down & ~censored).to_numpy()
    return out


# ---------------------------------------------------------------------------------------------
# Standardised deviations, worst-signal summaries, likely component, baseline rule
# ---------------------------------------------------------------------------------------------
def fit_scales(normal_rows: pd.DataFrame) -> dict[str, Any]:
    """Per machine type and signal: mean / std of the 72 h slope ("trend") and of the 24 h
    deviation from the machine's baseline ("dev"). Fit on training rows far from any failure.
    """
    out: dict[str, Any] = {"trend": {}, "dev": {}}
    for mt, g in normal_rows.groupby("machine_type"):
        out["trend"][mt], out["dev"][mt] = {}, {}
        for sig in DEV_SIGNALS:
            for kind, col in (("trend", f"{sig}_slope72"), ("dev", f"{sig}_dev24")):
                x = g[col].dropna()
                if len(x) > 10 and x.std() > 1e-9:
                    out[kind][mt][sig] = {
                        "mean": float(x.mean()),
                        "std": float(x.std()),
                    }
    return out


def _signed_z(
    feats: pd.DataFrame, col: str, sig: str, scale: Mapping[str, Any]
) -> np.ndarray:
    vals = np.full(len(feats), np.nan)
    x = feats[col].to_numpy(dtype=float)
    mt = feats["machine_type"].astype(str).to_numpy()
    for t in np.unique(mt):
        p = scale.get(t, {}).get(sig)
        if p:
            rows = mt == t
            vals[rows] = (x[rows] - p["mean"]) / p["std"]
    return BAD_DIRECTION[sig] * vals


def trend_z(feats: pd.DataFrame, scales: Mapping[str, Any]) -> pd.DataFrame:
    """72 h slope per signal, signed so positive = getting worse, in training std units."""
    return pd.DataFrame(
        {s: _signed_z(feats, f"{s}_slope72", s, scales["trend"]) for s in DEV_SIGNALS},
        index=feats.index,
    )


def dev_z(feats: pd.DataFrame, scales: Mapping[str, Any]) -> pd.DataFrame:
    """24 h mean minus the machine's own baseline, signed so positive = worse, in std units."""
    return pd.DataFrame(
        {s: _signed_z(feats, f"{s}_dev24", s, scales["dev"]) for s in DEV_SIGNALS},
        index=feats.index,
    )


def add_derived(feats: pd.DataFrame, scales: Mapping[str, Any]) -> pd.DataFrame:
    """Add DERIVED_FEATURES (v2 tuning round) to rows from `build_hourly_features`."""
    out = feats.copy()
    dz = dev_z(feats, scales)
    for s in DEV_SIGNALS:
        out[f"{s}_devz24"] = dz[s].to_numpy()
    out["worst_dev_z24"] = dz.max(axis=1, skipna=True).to_numpy()
    out["worst_trend_z72"] = (
        trend_z(feats, scales)[TREND_SIGNALS].max(axis=1).to_numpy()
    )
    return out


def likely_component(feats: pd.DataFrame, scales: Mapping[str, Any]) -> pd.DataFrame:
    """Subsystem whose signals moved furthest the wrong way (models.md §3, rule option).

    Score per subsystem = max signed z of its signals, taking the larger of the level deviation
    (24 h vs the machine's baseline) and the 72 h trend. Returns `score_<component>` columns and
    `likely_component` (None when no signal has a score)."""
    w = np.fmax(dev_z(feats, scales).to_numpy(), trend_z(feats, scales).to_numpy())
    w = pd.DataFrame(w, columns=DEV_SIGNALS, index=feats.index)
    # Engine wear raises vibration everywhere; undercarriage wear only while travelling. So
    # undercarriage counts only the travelling vibration beyond the overall vibration change.
    w["vibration_travel_g"] = w["vibration_travel_g"] - w["vibration_rms_g"].clip(
        lower=0
    )
    scores = pd.DataFrame(
        {c: w[sigs].max(axis=1, skipna=True) for c, sigs in SUBSYSTEM_SIGNALS.items()},
        index=feats.index,
    )
    best = scores.fillna(-np.inf).idxmax(axis=1).astype(object)
    best[scores.isna().all(axis=1)] = None
    out = scores.add_prefix("score_")
    out["likely_component"] = best
    return out


def baseline_probability(feats: pd.DataFrame, scales: Mapping[str, Any]) -> np.ndarray:
    """Baseline rule (nothing learned): the worst 72 h trend z of the six §3 signals, scaled so
    z = 3 gives 0.5 and z = 6 gives 1.0; an overdue service (service_ratio >= 1) gives >= 0.5.
    """
    worst = trend_z(feats, scales)[TREND_SIGNALS].max(axis=1).fillna(0.0).to_numpy()
    p = np.clip(worst / 6.0, 0.0, 1.0)
    overdue = feats["service_ratio"].to_numpy(dtype=float) >= 1.0
    return np.where(overdue, np.maximum(p, 0.5), p)


# ---------------------------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_artifacts(artifact_dir: str | None = None) -> dict[str, Any]:
    d = Path(artifact_dir) if artifact_dir else ARTIFACT_DIR
    features = json.loads((d / "feature_list.json").read_text())
    if features != FEATURE_COLUMNS:
        raise RuntimeError(
            "feature_list.json does not match FEATURE_COLUMNS; retrain the model"
        )
    cfg = json.loads((d / "config.json").read_text())
    model = joblib.load(d / "xgb_failure.joblib")
    return {"features": features, "config": cfg, "model": model}


def risk_band(p: float) -> str:
    return next(name for limit, name in RISK_BANDS if p < limit)


def _as_frame(features: pd.DataFrame | Mapping[str, Any]) -> pd.DataFrame:
    if isinstance(features, pd.DataFrame):
        return features.reset_index(drop=True)
    return pd.DataFrame([dict(features)])


def shap_values(X: pd.DataFrame, model: Any) -> np.ndarray:
    """Exact TreeSHAP from XGBoost (log-odds units); last column is the bias."""
    import xgboost as xgb

    dm = xgb.DMatrix(X.to_numpy(dtype=float), feature_names=list(X.columns))
    return model.get_booster().predict(dm, pred_contribs=True)


def predict_failure(
    features: pd.DataFrame | Mapping[str, Any], top_n: int = TOP_N
) -> dict[str, Any] | list[dict[str, Any]]:
    """Failure probability within the next 48 engine hours (models.md §3 output).

    `features`: one row (mapping) or a frame of rows from `build_hourly_features` (`machine_id`,
    `machine_type`, SPEC_FEATURES, RAW_EXTRA_COLUMNS). The derived columns are added here with
    the saved scales. NaN (e.g. no baseline yet) is allowed: XGBoost treats it as missing.
    A mapping returns one dict, a frame a list.
    `top_factors` = the features with the largest SHAP values pushing the probability up
    (log-odds), or the largest |SHAP| when nothing pushes it up.
    """
    art = load_artifacts()
    df = _as_frame(features)
    need = ("machine_id", "machine_type", *SPEC_FEATURES, *RAW_EXTRA_COLUMNS)
    missing = [c for c in need if c not in df]
    if missing:
        raise ValueError(f"missing feature columns: {missing}")
    scales = art["config"]["scales"]
    df = add_derived(df, scales)
    X = df.loc[:, art["features"]].astype(float)
    proba = art["model"].predict_proba(X.to_numpy(dtype=float))[:, 1]
    contrib = shap_values(X, art["model"])[:, :-1]
    comp = likely_component(df, scales)

    results = []
    for i in range(len(df)):
        c = contrib[i]
        order = np.argsort(-c) if (c > 0).any() else np.argsort(-np.abs(c))
        top = [
            {"feature": art["features"][j], "shap": round(float(c[j]), 3)}
            for j in order[:top_n]
            if (c[j] > 0) or not (c > 0).any()
        ]
        p = float(proba[i])
        results.append(
            {
                "machine_id": str(df.machine_id.iloc[i]),
                "horizon_hours": int(HORIZON_H),
                "failure_probability": round(p, 3),
                "risk_band": risk_band(p),
                "likely_component": comp.likely_component.iloc[i],
                "top_factors": top,
            }
        )
    return results[0] if isinstance(features, Mapping) else results

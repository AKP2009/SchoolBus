"""Fleet clustering, efficiency ranking and outliers (models.md §4).

`cluster_week(metrics_df)` takes weekly metric rows (one per entity × week × machine_type, as
built by `build_weekly_segments`) and returns one row per entity × week with the
`fleet_metrics_weekly` columns (OUTPUT_COLUMNS): cluster, efficiency index, rank in site and
outlier flag with a plain-language reason.

Every metric is compared within its machine type: each segment is standardised with the mean /
std of its (entity_type, machine_type) group on the training weeks. Operators drive all four
machine types, so an operator's week is the productive-hours-weighted mean of their per-type
z-scores. The fitting helpers live here too, so the notebook (`ml/03_clustering.ipynb`) and the
backend compute exactly the same thing. Columns are selected explicitly; hidden generator fields
(`operators.personality`, `anomaly_label`, `anomaly_type`, `data/output/truth/`) are never read.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "clustering"

ENTITY_TYPES = ("operator", "machine")
MACHINE_TYPES = ("excavator", "wheel_loader", "dozer", "articulated_truck")
KEY_COLUMNS = ["entity_type", "entity_id", "site_id", "week_start"]
METRIC_COLUMNS = [
    "productive_hours",
    "fuel_per_productive_hour",
    "idle_pct",
    "productivity_per_hour",
    "time_ratio",
    "anomaly_count",
    "safety_event_count",
]
INPUT_COLUMNS = [*KEY_COLUMNS, "machine_type", *METRIC_COLUMNS]
FEATURE_COLUMNS = [
    "fuel_per_productive_hour",
    "idle_pct",
    "productivity_per_hour",
    "time_ratio",
    "anomaly_per_10h",
    "safety_per_10h",
]
Z_COLUMNS = [f"z_{f}" for f in FEATURE_COLUMNS]
OUTPUT_COLUMNS = [
    *KEY_COLUMNS,
    *METRIC_COLUMNS,
    "efficiency_index",
    "cluster_id",
    "cluster_label",
    "is_outlier",
    "outlier_reason",
    "rank_in_site",
]

# models.md §4: 0.35*z(productivity) - 0.25*z(fuel_per_hour) - 0.2*z(idle_pct) - 0.2*z(time_ratio)
EFFICIENCY_WEIGHTS = {
    "productivity_per_hour": 0.35,
    "fuel_per_productive_hour": -0.25,
    "idle_pct": -0.20,
    "time_ratio": -0.20,
}
Z_OUTLIER = 2.5
DBSCAN_MIN_SAMPLES = 5
K_RANGE = (3, 4, 5)
MIN_FIT_HOURS = 4.0  # entity-weeks with fewer productive hours are too noisy to fit on
# Roster-driven (night shifts), not operating behaviour: not counted as a safety event here.
EXCLUDED_SAFETY_EVENTS = ("fatigue_high",)
# Schema numeric scales (001_init.sql)
ROUNDING = {
    "productive_hours": 2,
    "fuel_per_productive_hour": 2,
    "idle_pct": 2,
    "productivity_per_hour": 2,
    "time_ratio": 3,
    "efficiency_index": 3,
}

LABELS = {
    "fuel_per_productive_hour": "Fuel per productive hour",
    "idle_pct": "Idle time",
    "productivity_per_hour": "Output per productive hour",
    "time_ratio": "Task time vs estimate",
    "anomaly_per_10h": "Machine anomalies per 10 h",
    "safety_per_10h": "Safety events per 10 h",
}
UNITS = {
    "fuel_per_productive_hour": " L/h",
    "idle_pct": "%",
    "productivity_per_hour": "/h",
    "time_ratio": "×",
    "anomaly_per_10h": "",
    "safety_per_10h": "",
}
# (feature, direction) -> descriptive cluster name, used after the named rules in name_clusters
TRAIT_NAMES = {
    ("fuel_per_productive_hour", 1): "high fuel use",
    ("fuel_per_productive_hour", -1): "fuel-saving",
    ("idle_pct", 1): "idle-heavy",
    ("idle_pct", -1): "low idle",
    ("productivity_per_hour", 1): "high output",
    ("productivity_per_hour", -1): "low output",
    ("time_ratio", 1): "slow pace",
    ("time_ratio", -1): "fast pace",
    ("anomaly_per_10h", 1): "anomaly-prone",
    ("anomaly_per_10h", -1): "few anomalies",
    ("safety_per_10h", 1): "needs safety coaching",
    ("safety_per_10h", -1): "few safety events",
}
TRAIT_MIN_Z = (
    0.5  # a centroid must be at least this far from the mean to earn a trait name
)


# ---------------------------------------------------------------------------------------------
# Weekly metrics from raw tables
# ---------------------------------------------------------------------------------------------
def week_start(dates: pd.Series) -> pd.Series:
    """Monday of the week (dates are local shift dates)."""
    d = pd.to_datetime(dates).dt.normalize()
    return (d - pd.to_timedelta(d.dt.dayofweek, unit="D")).dt.date


def _events_to_shift(events: pd.DataFrame, shifts: pd.DataFrame) -> pd.Series:
    """shift_id of the shift on the same machine whose [start_time, end_time) holds each event."""
    e = events[["machine_id", "ts"]].copy()
    e["ts"] = pd.to_datetime(e["ts"], utc=True)
    e["_row"] = np.arange(len(e))
    s = shifts[["shift_id", "machine_id", "start_time", "end_time"]].copy()
    s["start_time"] = pd.to_datetime(s["start_time"], utc=True)
    s["end_time"] = pd.to_datetime(s["end_time"], utc=True)
    m = pd.merge_asof(
        e.sort_values("ts"),
        s.sort_values("start_time").rename(columns={"start_time": "ts"}),
        on="ts",
        by="machine_id",
        direction="backward",
    )
    m.loc[~(m["ts"] < m["end_time"]), "shift_id"] = np.nan
    return (
        m.set_index("_row")["shift_id"]
        .reindex(np.arange(len(e)))
        .set_axis(events.index)
    )


def anomaly_event_starts(
    ts: pd.Series, machine_id: pd.Series, kind: pd.Series
) -> pd.DataFrame:
    """One row per machine_fault event (a run of consecutive fault minutes) from the anomaly
    detector's per-minute `kind` (ml.inference.anomaly.classify). Sensor glitches don't count:
    they are the sensor, not the machine or the operator."""
    d = pd.DataFrame(
        {"machine_id": machine_id.to_numpy(), "ts": pd.to_datetime(ts, utc=True)}
    )
    d["fault"] = kind.to_numpy() == "machine_fault"
    d = d.sort_values(["machine_id", "ts"], kind="stable")
    prev_fault = d.groupby("machine_id")["fault"].shift(fill_value=False)
    gap = d.groupby("machine_id")["ts"].diff() > pd.Timedelta(minutes=2)
    start = d["fault"] & (~prev_fault | gap)
    return d.loc[start, ["machine_id", "ts"]].reset_index(drop=True)


def build_weekly_segments(
    telemetry: pd.DataFrame,
    tasks: pd.DataFrame,
    shifts: pd.DataFrame,
    machines: pd.DataFrame,
    safety_events: pd.DataFrame,
    anomaly_events: pd.DataFrame,
    p50_column: str = "fleet_p50_min",
) -> pd.DataFrame:
    """Weekly metrics per entity × week × machine_type (INPUT_COLUMNS).

    telemetry: shift_id, fuel_rate_lph, is_idle (one row = one engine-on minute).
    tasks: shift_id, status, quantity, actual_duration_min and `p50_column` (the p50 minutes from
    ml.inference.task_time the actual time is compared with; default the fleet yardstick from
    `fleet_p50`, not the planning p50 in `tasks.predicted_p50_min`). shifts: shift_id, site_id,
    operator_id, machine_id, shift_date, start_time, end_time. safety_events: ts, machine_id, event_type. anomaly_events: machine_id,
    ts (from `anomaly_event_starts`). Weeks start on Monday of the shift date.

    productive_hours = non-idle engine-on hours; fuel_per_productive_hour = all fuel burnt ÷
    productive hours; idle_pct = idle ÷ engine-on minutes; productivity_per_hour = quantity of
    completed tasks ÷ productive hours (m³, or tons for haul); time_ratio = Σ actual ÷ Σ p50 over
    completed tasks.
    """
    sh = shifts[
        ["shift_id", "site_id", "operator_id", "machine_id", "shift_date"]
    ].copy()
    sh = sh.merge(machines[["machine_id", "machine_type"]], on="machine_id", how="left")
    sh["week_start"] = week_start(sh["shift_date"])

    tel = telemetry[["shift_id", "fuel_rate_lph", "is_idle"]]
    per = tel.groupby("shift_id").agg(
        engine_min=("is_idle", "size"),
        idle_min=("is_idle", "sum"),
        fuel_lph_sum=("fuel_rate_lph", "sum"),
    )
    per["fuel_l"] = per.pop("fuel_lph_sum") / 60

    t = tasks[["shift_id", "status", "quantity", "actual_duration_min", p50_column]]
    t = t.rename(columns={p50_column: "p50_min"})
    t = t[(t["status"] == "completed") & t["actual_duration_min"].notna()]
    both = t["p50_min"].notna()
    per = per.join(
        t.groupby("shift_id")["quantity"].sum().rename("quantity"), how="outer"
    )
    per = per.join(
        t[both].groupby("shift_id")[["actual_duration_min", "p50_min"]].sum(),
        how="outer",
    )

    se = safety_events[["ts", "machine_id", "event_type"]]
    se = se[~se["event_type"].isin(EXCLUDED_SAFETY_EVENTS)]
    for name, ev in (
        ("safety_n", se),
        ("anomaly_n", anomaly_events[["machine_id", "ts"]]),
    ):
        sid = _events_to_shift(ev, shifts) if len(ev) else pd.Series(dtype=object)
        per = per.join(sid.dropna().value_counts().rename(name), how="outer")

    sums = [
        "engine_min",
        "idle_min",
        "fuel_l",
        "quantity",
        "actual_duration_min",
        "p50_min",
        "safety_n",
        "anomaly_n",
    ]
    per = sh.merge(
        per.reindex(columns=sums).fillna(0.0), left_on="shift_id", right_index=True
    )

    frames = []
    for entity_type, id_col in (("operator", "operator_id"), ("machine", "machine_id")):
        g = per.groupby(
            [id_col, "site_id", "week_start", "machine_type"], as_index=False
        )[sums]
        a = g.sum().rename(columns={id_col: "entity_id"})
        a.insert(0, "entity_type", entity_type)
        frames.append(a)
    a = pd.concat(frames, ignore_index=True)
    a = a[a["engine_min"] > 0]

    prod_h = (a["engine_min"] - a["idle_min"]) / 60
    safe_h = prod_h.where(prod_h > 0)
    out = a[[*KEY_COLUMNS, "machine_type"]].copy()
    out["productive_hours"] = prod_h
    out["fuel_per_productive_hour"] = a["fuel_l"] / safe_h
    out["idle_pct"] = 100 * a["idle_min"] / a["engine_min"]
    out["productivity_per_hour"] = a["quantity"] / safe_h
    out["time_ratio"] = a["actual_duration_min"] / a["p50_min"].where(a["p50_min"] > 0)
    out["anomaly_count"] = a["anomaly_n"].astype(int)
    out["safety_event_count"] = a["safety_n"].astype(int)
    return out.sort_values(INPUT_COLUMNS[:5]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# Features and within-machine-type standardisation
# ---------------------------------------------------------------------------------------------
def segment_features(df: pd.DataFrame) -> pd.DataFrame:
    """FEATURE_COLUMNS from the metric columns. Event counts become rates per 10 engine-on
    hours; engine hours = productive_hours / (1 - idle_pct / 100)."""
    ph = df["productive_hours"].astype(float)
    idle = df["idle_pct"].astype(float)
    engine_h = ph / (1 - idle / 100).clip(lower=1e-6)
    engine_h = engine_h.where(engine_h > 0)
    f = pd.DataFrame(index=df.index)
    for c in (
        "fuel_per_productive_hour",
        "idle_pct",
        "productivity_per_hour",
        "time_ratio",
    ):
        f[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    f["anomaly_per_10h"] = 10 * df["anomaly_count"].astype(float) / engine_h
    f["safety_per_10h"] = 10 * df["safety_event_count"].astype(float) / engine_h
    return f[FEATURE_COLUMNS]


def fit_type_scalers(
    segments: pd.DataFrame,
) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """Mean / std of each feature per entity_type × machine_type (fit on training weeks only).

    Returns {entity_type: {machine_type: {"mean": {feature: v}, "std": {feature: v}}}}.
    Segments with fewer than MIN_FIT_HOURS productive hours are left out.
    """
    s = segments[segments["productive_hours"] >= MIN_FIT_HOURS]
    f = segment_features(s)
    out: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for (et, mt), idx in s.groupby(["entity_type", "machine_type"]).groups.items():
        g = f.loc[idx]
        out.setdefault(str(et), {})[str(mt)] = {
            "mean": {c: float(g[c].mean()) for c in FEATURE_COLUMNS},
            "std": {c: float(g[c].std(ddof=0)) or 1.0 for c in FEATURE_COLUMNS},
        }
    return out


def standardise(segments: pd.DataFrame, scalers: dict[str, Any]) -> pd.DataFrame:
    """Z_COLUMNS per segment, against the segment's entity_type × machine_type group."""
    f = segment_features(segments)
    z = pd.DataFrame(np.nan, index=segments.index, columns=Z_COLUMNS)
    for (et, mt), idx in segments.groupby(
        ["entity_type", "machine_type"]
    ).groups.items():
        try:
            sc = scalers[str(et)][str(mt)]
        except KeyError as e:
            raise KeyError(f"no scaler for {et} / {mt}") from e
        mean = pd.Series(sc["mean"])[FEATURE_COLUMNS].to_numpy()
        std = pd.Series(sc["std"])[FEATURE_COLUMNS].to_numpy()
        z.loc[idx] = (f.loc[idx, FEATURE_COLUMNS].to_numpy() - mean) / std
    return z


def _wmean(values: pd.Series, weights: pd.Series) -> float:
    ok = values.notna() & weights.notna() & (weights > 0)
    if not ok.any():
        return float("nan")
    return float(np.average(values[ok], weights=weights[ok]))


def entity_weeks(segments: pd.DataFrame, scalers: dict[str, Any]) -> pd.DataFrame:
    """One row per entity × week: summed / re-derived METRIC_COLUMNS, Z_COLUMNS (the
    productive-hours-weighted mean of the per-machine-type z) and `machine_type` (for machines
    their type; for operators the type they drove most, used only for wording)."""
    seg = segments[INPUT_COLUMNS].copy()
    z = standardise(seg, scalers)
    ph = seg["productive_hours"].astype(float)
    eh = ph / (1 - seg["idle_pct"].astype(float) / 100).clip(lower=1e-6)
    w = seg.assign(
        _ph=ph,
        _eh=eh,
        _fuel=seg["fuel_per_productive_hour"].astype(float) * ph,
        _qty=seg["productivity_per_hour"].astype(float) * ph,
        **{c: z[c] for c in Z_COLUMNS},
    )
    rows = []
    for key, g in w.groupby(KEY_COLUMNS, sort=True):
        tph, teh = g["_ph"].sum(), g["_eh"].sum()
        row: dict[str, Any] = dict(zip(KEY_COLUMNS, key))
        row["machine_type"] = (
            str(g.loc[g["_ph"].idxmax(), "machine_type"])
            if tph > 0
            else str(g["machine_type"].iloc[0])
        )
        row["n_machine_types"] = int(g["machine_type"].nunique())
        row["productive_hours"] = tph
        row["fuel_per_productive_hour"] = g["_fuel"].sum() / tph if tph > 0 else np.nan
        row["idle_pct"] = 100 * (1 - tph / teh) if teh > 0 else np.nan
        row["productivity_per_hour"] = g["_qty"].sum() / tph if tph > 0 else np.nan
        row["time_ratio"] = _wmean(g["time_ratio"].astype(float), g["_ph"])
        row["anomaly_count"] = int(g["anomaly_count"].sum())
        row["safety_event_count"] = int(g["safety_event_count"].sum())
        for c in Z_COLUMNS:
            row[c] = _wmean(g[c], g["_ph"])
        rows.append(row)
    return pd.DataFrame(rows)


def efficiency_index(z: pd.DataFrame) -> pd.Series:
    """models.md §4 formula on the within-machine-type z-scores (missing z counts as 0)."""
    zz = z.fillna(0.0)
    return sum(w * zz[f"z_{f}"] for f, w in EFFICIENCY_WEIGHTS.items())


def cluster_matrix(ew: pd.DataFrame) -> np.ndarray:
    """Z_COLUMNS as a float matrix; a missing z (no completed task that week) is the type mean."""
    return ew[Z_COLUMNS].fillna(0.0).to_numpy(dtype=float)


def cluster_space(ew: pd.DataFrame, model: dict[str, Any]) -> np.ndarray:
    """The space KMeans, DBSCAN and PCA work in: StandardScaler, then per-feature weights."""
    return model["scaler"].transform(cluster_matrix(ew)) * np.asarray(model["weights"])


# ---------------------------------------------------------------------------------------------
# Fitting helpers (used by the notebook)
# ---------------------------------------------------------------------------------------------
def reliability(ew: pd.DataFrame) -> dict[str, float]:
    """Week-to-week reliability per Z column: ICC(1) = between-entity share of the variance,
    from a one-way ANOVA over entities (label-free). A feature that doesn't repeat for the same
    entity from week to week can't describe how that entity behaves."""
    out = {}
    for c in Z_COLUMNS:
        g = ew[["entity_id", c]].dropna().groupby("entity_id")[c]
        within = float(g.var(ddof=1).mean())
        n = float(g.size().mean())
        between = max(0.0, float(g.mean().var(ddof=1)) - within / n)
        out[c] = between / (between + within) if between + within > 0 else 0.0
    return out


def fit_entity_model(
    ew_train: pd.DataFrame, weights: np.ndarray | None = None
) -> dict[str, Any]:
    """StandardScaler → weights → KMeans (k by silhouette) + PCA(2) + DBSCAN core points, fitted
    on the training entity-weeks of one entity_type (rows with ≥ MIN_FIT_HOURS)."""
    from sklearn.cluster import DBSCAN, KMeans
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    fit = ew_train[ew_train["productive_hours"] >= MIN_FIT_HOURS]
    w = np.ones(len(Z_COLUMNS)) if weights is None else np.asarray(weights, dtype=float)
    scaler = StandardScaler().fit(cluster_matrix(fit))
    X = scaler.transform(cluster_matrix(fit)) * w
    k, silhouettes = select_k(X)
    kmeans = KMeans(n_clusters=k, n_init=10, random_state=42).fit(X)
    eps, kdist = k_distance_eps(X)
    db = DBSCAN(eps=eps, min_samples=DBSCAN_MIN_SAMPLES).fit(X)
    centroids = pd.DataFrame(
        scaler.inverse_transform(kmeans.cluster_centers_ / np.where(w > 0, w, 1)),
        columns=FEATURE_COLUMNS,
    )
    return {
        "scaler": scaler,
        "weights": w,
        "kmeans": kmeans,
        "pca": PCA(n_components=2, random_state=42).fit(X),
        "core_points": X[db.core_sample_indices_],
        "eps": eps,
        "k": k,
        "silhouette": silhouettes,
        "centroids_z": centroids,
        "cluster_labels": name_clusters(centroids),
        "k_distances": kdist,
        "train_noise_share": float(np.mean(db.labels_ == -1)),
        "n_fit": len(fit),
    }


def fleet_p50(feature_table: pd.DataFrame) -> pd.Series:
    """p50 minutes from ml.inference.task_time for the *fleet* yardstick: the same model with the
    operator's own history (`operator_avg_time_ratio`) left unknown. The planning p50 includes the
    operator's usual pace, so actual ÷ planning p50 hides exactly the differences between
    operators we want to compare. Returns a Series indexed by task_id."""
    from ml.inference import task_time as T

    ft = feature_table[T.INPUT_COLUMNS].copy()
    ft["operator_avg_time_ratio"] = np.nan
    pred = T.predict_task_time(ft, explain_factors=False)
    return pd.Series(
        [p["p50_min"] for p in pred], index=[p["task_id"] for p in pred], dtype=float
    )


def select_k(
    X: np.ndarray, k_range: tuple[int, ...] = K_RANGE
) -> tuple[int, dict[int, float]]:
    """k with the best silhouette score (StandardScaler already applied to X)."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    scores = {}
    for k in k_range:
        labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
        scores[k] = float(silhouette_score(X, labels))
    return max(scores, key=lambda k: scores[k]), scores


def k_distance_eps(
    X: np.ndarray, min_samples: int = DBSCAN_MIN_SAMPLES
) -> tuple[float, np.ndarray]:
    """eps at the knee of the sorted k-distance curve (k = min_samples, the point itself
    included, as DBSCAN counts it): the point furthest below the chord from the first to the last
    point. Returns (eps, sorted k-distances)."""
    from sklearn.neighbors import NearestNeighbors

    d, _ = NearestNeighbors(n_neighbors=min_samples).fit(X).kneighbors(X)
    kd = np.sort(d[:, -1])
    x = np.linspace(0, 1, len(kd))
    y = (kd - kd[0]) / (kd[-1] - kd[0])
    return float(kd[int(np.argmax(x - y))]), kd


def name_clusters(centroids_z: pd.DataFrame) -> dict[int, str]:
    """Cluster names from centroids in within-type z units (index = cluster_id, FEATURE_COLUMNS).

    In order, each name to one cluster at most: "needs safety coaching" (most safety events per
    10 h, z ≥ TRAIT_MIN_Z), "idle-heavy" (highest idle_pct, z ≥ TRAIT_MIN_Z), "efficient" (lowest
    time_ratio + fuel, i.e. highest -z(time_ratio) - z(fuel), > 0). The rest are named after their
    strongest remaining trait (TRAIT_NAMES), or "average" when no feature reaches TRAIT_MIN_Z.
    """
    c = centroids_z[FEATURE_COLUMNS]
    names: dict[int, str] = {}

    def take(score: pd.Series, name: str, minimum: float) -> None:
        free = score.drop(index=list(names))
        if len(free) and free.max() >= minimum:
            names[int(free.idxmax())] = name

    take(c["safety_per_10h"], "needs safety coaching", TRAIT_MIN_Z)
    take(c["idle_pct"], "idle-heavy", TRAIT_MIN_Z)
    take(-c["time_ratio"] - c["fuel_per_productive_hour"], "efficient", 1e-9)
    used = set(names.values())
    for cid in c.index:
        if int(cid) in names:
            continue
        row = c.loc[cid]
        name = "average"
        for f in row.abs().sort_values(ascending=False).index:
            if abs(row[f]) < TRAIT_MIN_Z:
                break
            cand = TRAIT_NAMES[(f, int(np.sign(row[f])))]
            if cand not in used:
                name = cand
                break
        if name in used:
            name = f"{name} {sum(v.startswith(name) for v in used) + 1}"
        names[int(cid)] = name
        used.add(name)
    return dict(sorted(names.items()))


# ---------------------------------------------------------------------------------------------
# Outlier reasons
# ---------------------------------------------------------------------------------------------
def _fmt(feature: str, value: float) -> str:
    if feature in ("time_ratio", "anomaly_per_10h"):
        return f"{value:.2f}{UNITS[feature]}"
    return f"{value:.1f}{UNITS[feature]}"


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def outlier_reason(
    row: pd.Series, feats: pd.Series, scalers: dict[str, Any], noise: bool
) -> str | None:
    """Plain-language reason, naming the feature(s). None when the row is not an outlier."""
    z = row[Z_COLUMNS].astype(float)
    parts = []
    for f in FEATURE_COLUMNS:
        zf = z[f"z_{f}"]
        if np.isfinite(zf) and abs(zf) > Z_OUTLIER:
            side = "above" if zf > 0 else "below"
            value = _fmt(f, float(feats[f])) if np.isfinite(feats[f]) else "n/a"
            if row["entity_type"] == "machine":
                mean = scalers["machine"][row["machine_type"]]["mean"][f]
                mt = str(row["machine_type"]).replace("_", " ")
                parts.append(
                    f"{LABELS[f]} {value} is far {side} normal for {_article(mt)} {mt} "
                    f"(typical {_fmt(f, mean)}, z {zf:+.1f})"
                )
            else:
                parts.append(
                    f"{LABELS[f]} {value} is far {side} normal for the machines driven "
                    f"(z {zf:+.1f})"
                )
    if parts:
        return "; ".join(parts) + "."
    if noise:
        top = z.abs().sort_values(ascending=False).index[:2]
        traits = " and ".join(
            f"{'high' if z[t] > 0 else 'low'} {LABELS[t[2:]].lower()} (z {z[t]:+.1f})"
            for t in top
        )
        return f"Unusual combination this week: {traits}, unlike any usual pattern."
    return None


# ---------------------------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_artifacts() -> dict[str, Any]:
    """Type scalers, cluster names and per-entity-type models (scaler, weights, kmeans, pca,
    DBSCAN core points, eps), loaded once per process."""
    feats = json.loads((ARTIFACT_DIR / "feature_list.json").read_text())
    if feats != FEATURE_COLUMNS:
        raise ValueError(
            "feature_list.json does not match FEATURE_COLUMNS in clustering.py"
        )
    cfg = json.loads((ARTIFACT_DIR / "config.json").read_text(encoding="utf-8"))
    models = {
        et: joblib.load(ARTIFACT_DIR / f"model_{et}.joblib") for et in ENTITY_TYPES
    }
    return {"config": cfg, "scalers": cfg["type_scalers"], "models": models}


def is_dbscan_noise(Xs: np.ndarray, core_points: np.ndarray, eps: float) -> np.ndarray:
    """A point is DBSCAN noise when no core point of the training fit lies within eps."""
    if len(Xs) == 0:
        return np.zeros(0, dtype=bool)
    d = np.sqrt(((Xs[:, None, :] - core_points[None, :, :]) ** 2).sum(axis=2))
    return ~(d <= eps).any(axis=1)


def project_2d(ew: pd.DataFrame, entity_type: str) -> np.ndarray:
    """(n, 2) PCA coordinates for the manager dashboard scatter (rows of one entity_type)."""
    m = load_artifacts()["models"][entity_type]
    return m["pca"].transform(cluster_space(ew, m))


def cluster_week(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Clusters, ranks and flags weekly metrics; returns fleet_metrics_weekly rows.

    metrics_df: INPUT_COLUMNS, one row per entity × week × machine_type (a machine has one row
    per week; an operator one per machine type driven). Missing columns raise KeyError. Returns
    OUTPUT_COLUMNS, one row per entity × week, rounded to the schema's numeric scales.
    rank_in_site: 1 = highest efficiency_index among the same entity_type, site and week.
    """
    missing = [c for c in INPUT_COLUMNS if c not in metrics_df]
    if missing:
        raise KeyError(f"missing columns: {missing}")
    bad = set(metrics_df["entity_type"].unique()) - set(ENTITY_TYPES)
    if bad:
        raise ValueError(f"unknown entity_type: {sorted(bad)}")
    if metrics_df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    art = load_artifacts()
    scalers = art["scalers"]
    ew = entity_weeks(metrics_df, scalers)
    ew["efficiency_index"] = efficiency_index(ew[Z_COLUMNS])
    feats = segment_features(ew)
    ew["cluster_id"] = -1
    ew["cluster_label"] = None
    ew["is_outlier"] = False
    ew["outlier_reason"] = None
    for et, idx in ew.groupby("entity_type").groups.items():
        m = art["models"][str(et)]
        labels = art["config"]["entities"][str(et)]["cluster_labels"]
        Xs = cluster_space(ew.loc[idx], m)
        cid = m["kmeans"].predict(Xs)
        ew.loc[idx, "cluster_id"] = cid
        ew.loc[idx, "cluster_label"] = [labels[str(c)] for c in cid]
        noise = is_dbscan_noise(Xs, m["core_points"], m["eps"])
        reasons = [
            outlier_reason(ew.loc[i], feats.loc[i], scalers, bool(n))
            for i, n in zip(idx, noise)
        ]
        ew.loc[idx, "outlier_reason"] = reasons
        ew.loc[idx, "is_outlier"] = [r is not None for r in reasons]

    ew["rank_in_site"] = (
        ew.groupby(["entity_type", "site_id", "week_start"])["efficiency_index"]
        .rank(ascending=False, method="min")
        .astype(int)
    )
    ew["cluster_id"] = ew["cluster_id"].astype(int)
    ew["is_outlier"] = ew["is_outlier"].astype(bool)
    for c, nd in ROUNDING.items():
        ew[c] = ew[c].astype(float).round(nd)
    return ew[OUTPUT_COLUMNS].reset_index(drop=True)

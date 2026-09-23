"""Tests for ml/inference/clustering.py.

Weekly metrics are built from the committed 1-day sample (data/output/sample/); cluster_week runs
against small throwaway artifacts fitted on synthetic entity-weeks, so the tests run on a fresh
clone. The last tests also check the real artifacts when they exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

import ml.inference.clustering as C

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "output" / "sample"
MIGRATION = ROOT / "supabase" / "migrations" / "001_init.sql"


def _read(name: str, dates: list[str]) -> pd.DataFrame:
    return pd.read_csv(SAMPLE / f"{name}.csv", parse_dates=dates)


# ---------------------------------------------------------------------------------------------
# Weekly metrics from the sample
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sample() -> dict[str, pd.DataFrame]:
    tasks = _read("tasks", ["scheduled_start", "actual_start", "actual_end"])
    tasks["fleet_p50_min"] = tasks["actual_duration_min"] / 1.25  # every ratio = 1.25
    return {
        "telemetry": _read("telemetry", ["ts"]),
        "tasks": tasks,
        "shifts": _read("shifts", ["start_time", "end_time"]),
        "machines": _read("machines", []),
        "safety_events": _read("safety_events", ["ts"]),
    }


@pytest.fixture(scope="module")
def segments(sample: dict[str, pd.DataFrame]) -> pd.DataFrame:
    tel = sample["telemetry"]
    one = tel.iloc[[100]]  # one fault event on a telemetry minute
    anomaly_events = pd.DataFrame({"machine_id": one.machine_id, "ts": one.ts})
    return C.build_weekly_segments(
        tel,
        sample["tasks"],
        sample["shifts"],
        sample["machines"],
        sample["safety_events"],
        anomaly_events,
    )


def test_segments_rebuild_raw_totals(segments: pd.DataFrame, sample) -> None:
    tel, se = sample["telemetry"], sample["safety_events"]
    assert list(segments.columns) == C.INPUT_COLUMNS
    for et in C.ENTITY_TYPES:
        s = segments[segments.entity_type == et]
        engine_h = s.productive_hours / (1 - s.idle_pct / 100)
        assert engine_h.sum() == pytest.approx(len(tel) / 60)
        fuel = (s.fuel_per_productive_hour * s.productive_hours).sum()
        assert fuel == pytest.approx(tel.fuel_rate_lph.sum() / 60)
        assert s.anomaly_count.sum() == 1
        assert (
            s.safety_event_count.sum()
            == (~se.event_type.isin(C.EXCLUDED_SAFETY_EVENTS)).sum()
        )
    machines = segments[segments.entity_type == "machine"]
    assert set(machines.entity_id) == set(tel.machine_id)
    assert machines.groupby(["entity_id", "week_start"]).size().eq(1).all()
    assert segments.time_ratio.dropna().to_numpy() == pytest.approx(1.25)
    assert str(segments.week_start.iloc[0]) == "2026-06-01"  # a Monday


def test_fatigue_high_is_not_a_safety_event(sample) -> None:
    se = sample["safety_events"].iloc[:1].copy()
    se["event_type"] = "fatigue_high"
    seg = C.build_weekly_segments(
        sample["telemetry"],
        sample["tasks"],
        sample["shifts"],
        sample["machines"],
        se,
        pd.DataFrame(columns=["machine_id", "ts"]),
    )
    assert seg.safety_event_count.sum() == 0


def test_anomaly_event_starts_counts_runs() -> None:
    ts = pd.Series(pd.date_range("2026-06-01 00:00", periods=10, freq="min", tz="UTC"))
    ts = pd.concat(
        [ts, pd.Series([pd.Timestamp("2026-06-01 03:00", tz="UTC")])], ignore_index=True
    )
    kind = pd.Series(
        [
            "normal",
            "machine_fault",
            "machine_fault",
            "normal",
            "sensor_glitch",
            "machine_fault",
            "machine_fault",
            "machine_fault",
            "normal",
            "normal",
            "machine_fault",
        ]
    )
    ev = C.anomaly_event_starts(ts, pd.Series(["M01"] * len(ts)), kind)
    assert len(ev) == 3  # minute 1, minute 5 and after the gap; glitch does not count
    assert list(ev.ts.dt.minute) == [1, 5, 0]


# ---------------------------------------------------------------------------------------------
# Standardisation and aggregation
# ---------------------------------------------------------------------------------------------
def _scalers(entity_type: str = "operator") -> dict:
    mean = {
        "fuel_per_productive_hour": 20.0,
        "idle_pct": 15.0,
        "productivity_per_hour": 100.0,
        "time_ratio": 1.0,
        "anomaly_per_10h": 0.1,
        "safety_per_10h": 2.0,
    }
    std = dict.fromkeys(C.FEATURE_COLUMNS, 1.0) | {
        "idle_pct": 5.0,
        "productivity_per_hour": 10.0,
    }
    return {entity_type: {mt: {"mean": mean, "std": std} for mt in C.MACHINE_TYPES}}


def _segment(**kw) -> dict:
    row = {
        "entity_type": "operator",
        "entity_id": "OP01",
        "site_id": "S1",
        "week_start": "2026-06-01",
        "machine_type": "excavator",
        "productive_hours": 30.0,
        "fuel_per_productive_hour": 20.0,
        "idle_pct": 15.0,
        "productivity_per_hour": 100.0,
        "time_ratio": 1.0,
        "anomaly_count": 0,
        "safety_event_count": 7,
    }
    row.update(kw)
    return row


def test_segment_rates_use_engine_hours() -> None:
    df = pd.DataFrame(
        [
            _segment(
                productive_hours=30.0,
                idle_pct=25.0,
                anomaly_count=2,
                safety_event_count=8,
            )
        ]
    )
    f = C.segment_features(df)
    assert f.anomaly_per_10h.iloc[0] == pytest.approx(10 * 2 / 40)
    assert f.safety_per_10h.iloc[0] == pytest.approx(10 * 8 / 40)


def test_operator_week_is_hours_weighted_across_machine_types() -> None:
    seg = pd.DataFrame(
        [
            _segment(machine_type="excavator", productive_hours=30.0, idle_pct=10.0),
            _segment(
                machine_type="dozer",
                productive_hours=10.0,
                idle_pct=30.0,
                time_ratio=np.nan,
            ),
        ]
    )
    ew = C.entity_weeks(seg, _scalers())
    assert len(ew) == 1
    r = ew.iloc[0]
    assert r.z_idle_pct == pytest.approx((30 * -1.0 + 10 * 3.0) / 40)
    assert r.z_time_ratio == pytest.approx(0.0)  # NaN segment left out of the mean
    engine = 30 / 0.9 + 10 / 0.7
    assert r.idle_pct == pytest.approx(100 * (1 - 40 / engine))
    assert r.productive_hours == pytest.approx(40)
    assert r.machine_type == "excavator"


def test_missing_scaler_group_raises() -> None:
    with pytest.raises(KeyError, match="no scaler"):
        C.standardise(
            pd.DataFrame([_segment(entity_type="machine")]), _scalers("operator")
        )


def test_efficiency_index_formula() -> None:
    z = pd.DataFrame(
        [
            dict.fromkeys(C.Z_COLUMNS, 0.0)
            | {
                "z_productivity_per_hour": 1.0,
                "z_fuel_per_productive_hour": 1.0,
                "z_idle_pct": -1.0,
                "z_time_ratio": np.nan,
            }
        ]
    )
    assert C.efficiency_index(z).iloc[0] == pytest.approx(0.35 - 0.25 + 0.2)


def test_reliability_separates_trait_from_noise() -> None:
    rng = np.random.default_rng(42)
    ids = np.repeat([f"OP{i:02d}" for i in range(20)], 10)
    trait = np.repeat(rng.normal(0, 1, 20), 10)
    ew = pd.DataFrame(
        {"entity_id": ids} | {c: rng.normal(0, 1, len(ids)) for c in C.Z_COLUMNS}
    )
    ew["z_idle_pct"] = trait + rng.normal(0, 0.1, len(ids))
    rel = C.reliability(ew)
    assert rel["z_idle_pct"] > 0.9
    assert rel["z_anomaly_per_10h"] < 0.2


# ---------------------------------------------------------------------------------------------
# Names and reasons
# ---------------------------------------------------------------------------------------------
def test_name_clusters_from_centroids() -> None:
    cent = pd.DataFrame(
        [
            [0.1, 2.0, 0.0, 0.2, 0.0, -0.1],  # idle-heavy
            [-0.8, -0.7, 0.3, -0.5, 0.0, -0.2],  # efficient
            [0.5, -0.5, 0.0, -0.3, 0.2, 1.1],  # needs safety coaching
            [0.0, 0.2, -1.2, 0.9, 0.0, 0.1],  # strongest trait: low output
            [0.1, 0.0, 0.1, 0.0, 0.1, 0.0],  # nothing stands out
        ],
        columns=C.FEATURE_COLUMNS,
    )
    assert C.name_clusters(cent) == {
        0: "idle-heavy",
        1: "efficient",
        2: "needs safety coaching",
        3: "low output",
        4: "average",
    }


def test_name_clusters_keeps_names_unique() -> None:
    cent = pd.DataFrame([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]] * 3, columns=C.FEATURE_COLUMNS)
    cent.iloc[0, 3] = -1.0  # efficient
    names = C.name_clusters(cent)
    assert len(set(names.values())) == 3


def test_outlier_reason_names_the_feature() -> None:
    row = pd.Series(
        {"entity_type": "machine", "machine_type": "excavator"}
        | dict.fromkeys(C.Z_COLUMNS, 0.0)
        | {"z_idle_pct": 3.2}
    )
    feats = pd.Series(dict.fromkeys(C.FEATURE_COLUMNS, 1.0) | {"idle_pct": 31.0})
    reason = C.outlier_reason(row, feats, _scalers("machine"), noise=False)
    assert reason is not None
    assert "Idle time 31.0% is far above normal for an excavator" in reason
    assert "typical 15.0%" in reason and "z +3.2" in reason
    assert (
        C.outlier_reason(row.replace(3.2, 1.0), feats, _scalers("machine"), noise=False)
        is None
    )
    noise = C.outlier_reason(
        row.replace(3.2, 1.9), feats, _scalers("machine"), noise=True
    )
    assert (
        noise is not None
        and noise.startswith("Unusual combination")
        and "idle time" in noise
    )


def test_dbscan_noise_is_distance_to_core_points() -> None:
    core = np.array([[0.0, 0.0], [1.0, 0.0]])
    got = C.is_dbscan_noise(np.array([[0.5, 0.2], [5.0, 5.0]]), core, eps=0.6)
    assert list(got) == [False, True]


# ---------------------------------------------------------------------------------------------
# cluster_week against tiny artifacts
# ---------------------------------------------------------------------------------------------
def _synthetic_segments(n_weeks: int = 6) -> pd.DataFrame:
    """Two sites, 3 operators each driving two machine types, plus 4 machines, n_weeks weeks."""
    rng = np.random.default_rng(42)
    rows = []
    weeks = pd.date_range("2026-06-01", periods=n_weeks, freq="7D").date
    for w in weeks:
        for i in range(6):
            idle = [8.0, 15.0, 35.0][i % 3] + rng.normal(0, 1)
            for mt in ("excavator", "dozer"):
                rows.append(
                    _segment(
                        entity_id=f"OP{i:02d}",
                        site_id=f"S{i // 3 + 1}",
                        week_start=w,
                        machine_type=mt,
                        productive_hours=float(rng.uniform(10, 20)),
                        idle_pct=idle,
                        fuel_per_productive_hour=20 + idle / 5 + rng.normal(0, 0.5),
                        productivity_per_hour=100 + rng.normal(0, 10),
                        time_ratio=1 + rng.normal(0, 0.05),
                        anomaly_count=int(rng.poisson(0.5)),
                        safety_event_count=int(rng.poisson(5)),
                    )
                )
        for j, mt in enumerate(("excavator", "excavator", "dozer", "dozer")):
            rows.append(
                _segment(
                    entity_type="machine",
                    entity_id=f"M{j + 1:02d}",
                    site_id=f"S{j % 2 + 1}",
                    week_start=w,
                    machine_type=mt,
                    productive_hours=float(rng.uniform(40, 60)),
                    idle_pct=15 + rng.normal(0, 4),
                    fuel_per_productive_hour=20 + rng.normal(0, 1),
                    productivity_per_hour=100 + rng.normal(0, 10),
                    time_ratio=1 + rng.normal(0, 0.05),
                    anomaly_count=int(rng.poisson(0.5)),
                    safety_event_count=int(rng.poisson(10)),
                )
            )
    return pd.DataFrame(rows)[C.INPUT_COLUMNS]


@pytest.fixture()
def tiny_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    seg = _synthetic_segments()
    scalers = C.fit_type_scalers(seg)
    ew = C.entity_weeks(seg, scalers)
    cfg = {"type_scalers": scalers, "entities": {}}
    for et in C.ENTITY_TYPES:
        m = C.fit_entity_model(ew[ew.entity_type == et])
        keep = {
            k: m[k]
            for k in ("scaler", "weights", "kmeans", "pca", "core_points", "eps")
        }
        joblib.dump(keep, tmp_path / f"model_{et}.joblib")
        cfg["entities"][et] = {
            "cluster_labels": {str(k): v for k, v in m["cluster_labels"].items()}
        }
    (tmp_path / "feature_list.json").write_text(json.dumps(C.FEATURE_COLUMNS))
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(C, "ARTIFACT_DIR", tmp_path)
    C.load_artifacts.cache_clear()
    yield tmp_path
    C.load_artifacts.cache_clear()


def _check_output(out: pd.DataFrame) -> None:
    assert list(out.columns) == C.OUTPUT_COLUMNS
    assert not out.duplicated(["entity_type", "entity_id", "week_start"]).any()
    assert out.cluster_label.notna().all()
    assert (out.is_outlier == out.outlier_reason.notna()).all()
    for _, g in out.groupby(["entity_type", "site_id", "week_start"]):
        best = g.sort_values("efficiency_index", ascending=False)
        assert best.rank_in_site.iloc[0] == 1
        assert best.rank_in_site.is_monotonic_increasing
        assert g.rank_in_site.max() <= len(g)


def test_cluster_week_returns_schema_rows(tiny_artifacts: Path) -> None:
    seg = _synthetic_segments()
    out = C.cluster_week(seg)
    _check_output(out)
    assert len(out) == 6 * (6 + 4)  # weeks × (operators + machines)
    assert out.productive_hours.sum() == pytest.approx(
        seg.productive_hours.sum(), abs=0.1
    )


def test_cluster_week_ignores_hidden_columns(tiny_artifacts: Path) -> None:
    seg = _synthetic_segments()
    base = C.cluster_week(seg)
    noisy = seg.assign(
        personality="idler",
        anomaly_label=True,
        anomaly_type="overheating",
        idle_pct_extra=99.0,
    )
    pd.testing.assert_frame_equal(C.cluster_week(noisy), base)


def test_cluster_week_is_row_independent(tiny_artifacts: Path) -> None:
    """A single operator's week gets the same cluster whatever else is in the batch."""
    seg = _synthetic_segments()
    full = C.cluster_week(seg)
    one = seg[(seg.entity_id == "OP02") & (seg.week_start == seg.week_start.iloc[0])]
    alone = C.cluster_week(one)
    ref = full[(full.entity_id == "OP02") & (full.week_start == seg.week_start.iloc[0])]
    assert alone.cluster_id.iloc[0] == ref.cluster_id.iloc[0]
    assert alone.efficiency_index.iloc[0] == ref.efficiency_index.iloc[0]
    assert alone.rank_in_site.iloc[0] == 1


def test_cluster_week_validates_input(tiny_artifacts: Path) -> None:
    seg = _synthetic_segments(1)
    with pytest.raises(KeyError, match="idle_pct"):
        C.cluster_week(seg.drop(columns=["idle_pct"]))
    with pytest.raises(ValueError, match="entity_type"):
        C.cluster_week(seg.assign(entity_type="site"))
    assert list(C.cluster_week(seg.iloc[:0]).columns) == C.OUTPUT_COLUMNS


# ---------------------------------------------------------------------------------------------
# Schema and real artifacts
# ---------------------------------------------------------------------------------------------
def test_output_matches_fleet_metrics_weekly_schema() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    body = re.search(r"create table fleet_metrics_weekly \((.*?)\n\);", sql, re.DOTALL)
    assert body is not None
    cols = [
        ln.split()[0]
        for ln in body.group(1).splitlines()
        if re.match(r"\s+[a-z_]+\s", ln)
    ]
    cols = [c for c in cols if c not in {"id", "verified_by", "verified_at", "unique"}]
    assert set(cols) == set(C.OUTPUT_COLUMNS)


REAL = C.ARTIFACT_DIR / "model_operator.joblib"


@pytest.mark.skipif(not REAL.exists(), reason="real artifacts not trained yet")
def test_real_artifacts_load_and_score() -> None:
    C.load_artifacts.cache_clear()
    art = C.load_artifacts()
    labels = {
        v
        for et in C.ENTITY_TYPES
        for v in art["config"]["entities"][et]["cluster_labels"].values()
    }
    assert {"efficient", "idle-heavy", "needs safety coaching"} <= labels
    seg = _synthetic_segments(2)
    _check_output(C.cluster_week(seg))


@pytest.mark.skipif(not REAL.exists(), reason="real artifacts not trained yet")
def test_real_idle_heavy_week_is_named_idle_heavy() -> None:
    C.load_artifacts.cache_clear()
    sc = C.load_artifacts()["scalers"]["operator"]
    rows = []
    for mt in ("excavator", "wheel_loader"):
        m = sc[mt]["mean"]
        rows.append(
            _segment(
                machine_type=mt,
                idle_pct=38.0,
                fuel_per_productive_hour=m["fuel_per_productive_hour"] * 1.15,
                productivity_per_hour=m["productivity_per_hour"],
                time_ratio=m["time_ratio"],
                safety_event_count=3,
            )
        )
    out = C.cluster_week(pd.DataFrame(rows))
    assert out.cluster_label.iloc[0] == "idle-heavy"

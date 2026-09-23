"""Tests for ml/inference/task_time.py — quantile order, efficiency math, triggers."""

import pandas as pd
import pytest

from ml.inference import task_time as tt
from ml.inference.schemas import (
    EfficiencyRow,
    Recommendation,
    TaskTimeContext,
    TaskTimePrediction,
)

DATES = pd.date_range("2026-06-01", periods=100).strftime("%Y-%m-%d").tolist()


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def _eff_row(op, tt_, date, site, actual, std, delay=None, status="completed"):
    return {
        "operator_id": op,
        "task_type": tt_,
        "site_id": site,
        "task_date": date,
        "actual_duration_min": actual,
        "standard_min": std,
        "delay_reason": delay,
        "status": status,
    }


def _real_frames():
    """Real generator frames, injected into the singleton (models load from artifacts)."""

    def rd(name):
        df = pd.read_csv(f"data/output/{name}.csv")
        if name == "weather":
            df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
        if name == "shifts":
            df["start_time"] = pd.to_datetime(df["start_time"], utc=True, format="ISO8601")
        if name == "tasks":
            df["scheduled_start"] = pd.to_datetime(
                df["scheduled_start"], utc=True, format="ISO8601"
            )
        return df

    frames = {
        n: rd(n)
        for n in ("tasks", "shifts", "operators", "machines", "weather", "machine_health_daily")
    }
    tt.reload_state(
        "data/output",
        tasks=frames["tasks"],
        shifts=frames["shifts"],
        operators=frames["operators"],
        machines=frames["machines"],
        weather=frames["weather"],
        machine_health_daily=frames["machine_health_daily"],
    )
    return frames


def _context(frames):
    return TaskTimeContext(
        shifts=frames["shifts"],
        operators=frames["operators"],
        machines=frames["machines"],
        weather=frames["weather"],
        machine_health_daily=frames["machine_health_daily"],
    )


def _rec(module_id, status="pending"):
    return Recommendation(
        operator_id="OP01",
        task_type="dig",
        module_id=module_id,
        reason="x",
        trigger_metric="efficiency",
        trigger_value=0.8,
        status=status,
    )


# ---------------------------------------------------------------------------------------------
# predict() against the real artifacts
# ---------------------------------------------------------------------------------------------
def test_predict_quantile_order_and_payload():
    frames = _real_frames()
    upcoming = frames["tasks"][frames["tasks"]["status"] == "scheduled"].copy()
    assert len(upcoming) > 0
    preds = tt.predict(upcoming, _context(frames))
    assert len(preds) == len(upcoming)
    assert [p.task_id for p in preds] == upcoming["task_id"].tolist()
    for p in preds:
        assert isinstance(p, TaskTimePrediction)
        assert 0 < p.p10_min <= p.p50_min <= p.p90_min
        assert p.standard_min > 0
        assert p.expected_efficiency == pytest.approx(p.standard_min / p.p50_min, rel=1e-9)
        assert len(p.factors) == 3
        assert all(f.label for f in p.factors)


def test_efficiency_summary_on_real_data():
    _real_frames()
    rows = tt.efficiency_summary("OP01")
    assert rows, "OP01 has completed tasks"
    assert {r.task_type for r in rows} <= {"dig", "trench", "load", "haul", "grade", "backfill"}
    for r in rows:
        assert r.n_tasks >= 3 or r.avg_efficiency is None
        if r.avg_efficiency is not None and r.prev_efficiency is not None:
            assert r.trend in ("up", "down", "flat")


# ---------------------------------------------------------------------------------------------
# Pure efficiency math on hand-built frames
# ---------------------------------------------------------------------------------------------
def _hand_eff():
    """as_of = 2026-08-30. OP01 dig: 3 tasks in the avg window, 3 in prev, one delayed
    (excluded), one before the prev window. OP02 dig: 3 tasks in window for the site
    median (S1). OP03 dig (S9): only 2 tasks."""
    rows = [
        _eff_row("OP01", "dig", "2026-07-01", "S1", 60.0, 60.0),  # day -60: outside prev
        _eff_row("OP01", "dig", "2026-07-05", "S1", 60.0, 60.0),  # prev e = 1.0
        _eff_row("OP01", "dig", "2026-07-10", "S1", 60.0, 60.0),
        _eff_row("OP01", "dig", "2026-07-15", "S1", 60.0, 60.0),
        _eff_row("OP01", "dig", "2026-08-01", "S1", 60.0, 60.0),  # avg e = 1.0
        _eff_row("OP01", "dig", "2026-08-10", "S1", 75.0, 60.0),  # 0.8
        _eff_row("OP01", "dig", "2026-08-15", "S1", 100.0, 60.0),  # 0.6, most recent
        _eff_row("OP01", "dig", "2026-08-20", "S1", 90.0, 60.0, delay="refuelling"),
        _eff_row("OP02", "dig", "2026-08-03", "S1", 100.0, 90.0),  # 0.9
        _eff_row("OP02", "dig", "2026-08-08", "S1", 90.0, 90.0),  # 1.0
        _eff_row("OP02", "dig", "2026-08-25", "S1", 80.0, 90.0),  # 1.125
        _eff_row("OP03", "dig", "2026-08-05", "S9", 60.0, 60.0),
        _eff_row("OP03", "dig", "2026-08-06", "S9", 60.0, 60.0),
    ]
    tasks = pd.DataFrame(rows)
    return tt._efficiency_rows(tasks, tasks["standard_min"])


def test_efficiency_math_on_hand_built_frame():
    rows = {r.task_type: r for r in tt._summary_from(_hand_eff(), "OP01", "S1", None, "2026-08-30")}
    r = rows["dig"]
    # avg = duration-weighted = sum(std) / sum(actual) = 180/235 over 3 non-delayed tasks
    assert r.avg_efficiency == pytest.approx(180.0 / 235.0)
    assert r.n_tasks == 3
    assert r.prev_efficiency == pytest.approx(1.0)
    assert r.last_task_efficiency == pytest.approx(0.6)
    assert r.trend == "down"  # 0.766 <= 0.97 * 1.0
    # site median over the avg window: {1.0, 0.8, 0.6, 0.9, 1.0, 1.125}
    assert r.fleet_median == pytest.approx(0.95)
    single = tt._summary_from(_hand_eff(), "OP01", "S1", "dig", "2026-08-30")
    assert len(single) == 1 and single[0].task_type == "dig"


def test_delayed_tasks_excluded_from_efficiency():
    tasks = pd.DataFrame(
        [
            _eff_row("OP01", "dig", "2026-08-01", "S1", 60.0, 60.0),
            _eff_row("OP01", "dig", "2026-08-05", "S1", 90.0, 60.0, delay="refuelling"),
            _eff_row("OP01", "dig", "2026-08-10", "S1", 70.0, 60.0),
            _eff_row("OP01", "dig", "2026-08-15", "S1", 70.0, 60.0),
        ]
    )
    eff = tt._efficiency_rows(tasks, tasks["standard_min"])
    assert len(tasks[tasks["delay_reason"] == "refuelling"]) == 1  # present in the source
    assert len(eff) == 3 and "delay_reason" not in eff.columns  # excluded from the table
    row = tt._summary_from(eff, "OP01", "S1", "dig", "2026-08-30")[0]
    assert row.n_tasks == 3  # the delayed task is not counted
    assert row.avg_efficiency == pytest.approx(180.0 / 200.0)


def test_n_below_3_nulls():
    rows = {r.task_type: r for r in tt._summary_from(_hand_eff(), "OP03", "S9", None, "2026-08-30")}
    r = rows["dig"]
    assert r.n_tasks == 2
    assert r.avg_efficiency is None and r.prev_efficiency is None
    assert r.trend is None  # trend needs both windows
    assert r.last_task_efficiency is not None  # last task exists regardless of window size
    # fleet median also needs >= 3 site rows in the window; OP03's site has only 2
    assert r.fleet_median is None


def test_trend_thresholds():
    """up at >= 1.03x prev, down at <= 0.97x prev, flat in between."""
    for a, expected in [(58.0, "up"), (62.0, "down"), (60.0, "flat")]:
        rows = pd.DataFrame(
            [
                _eff_row("OP05", "dig", "2026-07-05", "S1", 60.0, 60.0),
                _eff_row("OP05", "dig", "2026-07-10", "S1", 60.0, 60.0),
                _eff_row("OP05", "dig", "2026-07-15", "S1", 60.0, 60.0),  # prev = 1.0
                _eff_row("OP05", "dig", "2026-08-05", "S1", a, 60.0),
                _eff_row("OP05", "dig", "2026-08-10", "S1", a, 60.0),
                _eff_row("OP05", "dig", "2026-08-15", "S1", a, 60.0),
            ]
        )
        eff = tt._efficiency_rows(rows, rows["standard_min"])
        row = tt._summary_from(eff, "OP05", "S1", "dig", "2026-08-30")[0]
        assert row.trend == expected, f"actual {a} min"


# ---------------------------------------------------------------------------------------------
# recommend()
# ---------------------------------------------------------------------------------------------
def _summary_rows():
    """dig gap 0.25 (n=10), trench gap 0.22 (n=12), haul gap 0.20 (n=6), grade triggers via
    the dropping rule; load healthy; backfill under n."""
    return [
        EfficiencyRow(
            task_type="dig",
            avg_efficiency=0.75,
            prev_efficiency=0.80,
            fleet_median=1.00,
            n_tasks=10,
        ),
        EfficiencyRow(
            task_type="trench",
            avg_efficiency=0.78,
            prev_efficiency=1.00,
            trend="down",
            fleet_median=1.00,
            n_tasks=12,
        ),
        EfficiencyRow(
            task_type="haul",
            avg_efficiency=0.80,
            prev_efficiency=0.95,
            fleet_median=1.00,
            n_tasks=6,
        ),
        EfficiencyRow(
            task_type="load",
            avg_efficiency=0.95,
            prev_efficiency=0.96,
            fleet_median=1.00,
            n_tasks=20,
        ),
        EfficiencyRow(
            task_type="grade",
            avg_efficiency=0.85,
            prev_efficiency=0.99,
            fleet_median=1.00,
            n_tasks=9,
        ),
        EfficiencyRow(
            task_type="backfill",
            avg_efficiency=0.70,
            prev_efficiency=None,
            fleet_median=1.00,
            n_tasks=4,
        ),
    ]


def test_recommend_max_two_open_no_duplicates_worst_first():
    recs = tt.recommend_from(_summary_rows(), "OP01", open_recs=[])
    assert len(recs) == 2
    assert [r.task_type for r in recs] == ["dig", "trench"]  # worst gap first
    assert recs[0].module_id == "TM-TECH-DIG-01"
    assert recs[1].module_id == "TM-TECH-TRENCH-01"
    assert recs[1].reason == "Trench: efficiency 0.78 vs site 1.00 over 12 tasks"
    assert recs[1].trigger_metric == "efficiency"
    assert recs[1].trigger_value == pytest.approx(0.78)
    assert recs[1].sim_module_id is None

    open_one = [_rec("TM-TECH-DIG-01")]
    recs2 = tt.recommend_from(_summary_rows(), "OP01", open_recs=open_one)
    assert len(recs2) == 1 and recs2[0].task_type == "trench"  # dig open: slot used, no dup

    open_two = [_rec("TM-TECH-DIG-01"), _rec("TM-TECH-TRENCH-01", status="accepted")]
    assert tt.recommend_from(_summary_rows(), "OP01", open_recs=open_two) == []

    done = [
        _rec("TM-TECH-DIG-01", status="completed"),
        _rec("TM-TECH-TRENCH-01", status="dismissed"),
    ]
    recs_done = tt.recommend_from(_summary_rows(), "OP01", open_recs=done)
    # completed/dismissed modules are never re-recommended; next two by gap
    assert [r.task_type for r in recs_done] == ["haul", "grade"]


def test_recommend_grade_via_dropping_rule():
    recs = tt.recommend_from(_summary_rows(), "OP01", open_recs=[])
    # only 2 slots; grade (dropping rule) is behind dig/trench on gap and must be cut
    assert all(r.task_type != "grade" for r in recs)
    rows = [
        EfficiencyRow(
            task_type="load",
            avg_efficiency=0.85,
            prev_efficiency=1.00,
            fleet_median=0.90,
            n_tasks=9,
        )
    ]
    rec = tt.recommend_from(rows, "OP07", open_recs=[])[0]
    assert rec.task_type == "load" and rec.module_id == "TM-TECH-LOAD-01"
    assert rec.reason == "Load: efficiency 0.85 vs site 0.90 over 9 tasks"


def test_recommend_public_path_smoke():
    _real_frames()
    recs = tt.recommend("OP01", open_recs=[])
    assert isinstance(recs, list)
    for r in recs:
        assert r.module_id.startswith("TM-TECH-")
        assert r.trigger_metric == "efficiency" and len(r.reason) > 0


def test_as_of_default_is_latest_completed():
    """as_of must default to the latest task_date with a completed task, not today."""
    _real_frames()
    explicit = tt.efficiency_summary("OP01", as_of="2026-08-29")
    default = tt.efficiency_summary("OP01")
    assert default == explicit  # synthetic data ends before today; latest completed = 2026-08-29
    early = tt.efficiency_summary("OP01", as_of="2026-06-10")
    n_default = {r.task_type: r.n_tasks for r in explicit}
    for r in early:
        assert r.n_tasks < n_default[r.task_type] or r.n_tasks == 0

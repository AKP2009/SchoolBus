"""Tests for ml/inference/plan.py (models.md §12).

Scheduler tests use a fake predictor (p50 = base minutes × (1 + 0.1 × rain_mm)), so they are exact.
The last test runs the real task-time model on the committed 1-day sample.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import ml.inference.plan as P

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "output" / "sample"
API_KEYS = {"shift_id", "fits", "moved_to_next_shift", "new_order", "explanation"}
SHIFT = "SH-2026-08-21-M07-D"
START = pd.Timestamp("2026-08-21 00:30", tz="UTC")  # 06:00 IST
END = pd.Timestamp("2026-08-21 08:30", tz="UTC")  # 14:00 IST


def tid(n: int) -> str:
    return f"T-{SHIFT}-{n}"


def make_tasks(*specs: tuple[int, int, float], **extra: object) -> pd.DataFrame:
    """specs: (sequence_no, priority, base minutes). All scheduled, dry."""
    df = pd.DataFrame(
        [
            {
                "task_id": tid(n),
                "sequence_no": n,
                "priority": pr,
                "status": "scheduled",
                "base_min": m,
                "rain_mm": 0.0,
                "actual_start": pd.NaT,
                "hours_into_shift": 0.0,
            }
            for n, pr, m in specs
        ]
    )
    if len(df):
        df["actual_start"] = pd.to_datetime(df["actual_start"], utc=True)
    for k, v in extra.items():
        df[k] = v
    return df


def fake_predictor(df: pd.DataFrame) -> list[dict]:
    return [
        {"task_id": r.task_id, "p50_min": r.base_min * (1 + 0.1 * r.rain_mm)}
        for r in df.itertuples()
    ]


def plan(tasks: pd.DataFrame, now: pd.Timestamp, **kw: object) -> dict:
    return P.re_evaluate_plan(
        SHIFT, tasks, now, END, START, predictor=kw.pop("predictor", fake_predictor), **kw
    )


# ---------------------------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------------------------
def test_triggers_each_reason() -> None:
    now = START + pd.Timedelta(hours=2)
    running = {"actual_start": now - pd.Timedelta(minutes=61), "predicted_p90_min": 60}
    assert P.detect_triggers(now=now, running_task=running) == ["task_overrun"]
    assert P.detect_triggers(rain_mm_h=2.5) == ["rain"]
    assert P.detect_triggers(health_overall=0.59) == ["low_health"]
    assert P.detect_triggers(fatigue_level="HIGH") == ["fatigue_high"]
    assert P.detect_triggers(manager_edit=True) == ["manager_edit"]
    assert P.detect_triggers(
        now=now,
        running_task=running,
        rain_mm_h=5,
        health_overall=0.3,
        fatigue_level="high",
        manager_edit=True,
    ) == list(P.TRIGGERS)


def test_triggers_edges_and_missing_inputs() -> None:
    now = START + pd.Timedelta(hours=2)
    within = {"actual_start": now - pd.Timedelta(minutes=60), "predicted_p90_min": 60}
    assert P.detect_triggers(now=now, running_task=within) == []  # exactly at p90: not past it
    assert P.detect_triggers(rain_mm_h=2.0, health_overall=0.6, fatigue_level="medium") == []
    assert P.detect_triggers() == []
    assert (
        P.detect_triggers(
            now=now,
            running_task={"actual_start": None, "predicted_p90_min": None},
            rain_mm_h=float("nan"),
            health_overall="n/a",  # type: ignore[arg-type]
            fatigue_level=None,
        )
        == []
    )
    assert P.detect_triggers(running_task=within) == []  # no `now`


# ---------------------------------------------------------------------------------------------
# Re-planning
# ---------------------------------------------------------------------------------------------
def test_no_tasks_left() -> None:
    now = START + pd.Timedelta(hours=6)
    for tasks in (
        make_tasks(),
        make_tasks((1, 2, 60), (2, 2, 60), status="completed"),
        make_tasks((1, 2, 60), status="cancelled"),
        None,
    ):
        out = plan(tasks, now, reason="rain")  # type: ignore[arg-type]
        assert API_KEYS <= set(out)
        assert out["fits"] == out["moved_to_next_shift"] == out["new_order"] == []
        assert out["explanation"] == "Rain started. No tasks left in this shift."


def test_all_tasks_fit_keep_order() -> None:
    now = START + pd.Timedelta(hours=1)
    out = plan(make_tasks((1, 1, 60), (2, 2, 60), (3, 3, 60)), now)
    assert out["fits"] == out["new_order"] == [tid(1), tid(2), tid(3)]
    assert out["moved_to_next_shift"] == []
    assert out["explanation"] == "All remaining tasks still fit before 14:00."
    assert out["available_min"] == pytest.approx(7 * 60 - 10)
    sched = out["schedule"]
    assert (
        sched[0]["start"] == "2026-08-21T01:30:00Z" and sched[-1]["end"] == "2026-08-21T04:30:00Z"
    )


def test_sorted_by_priority_then_p50() -> None:
    now = START + pd.Timedelta(hours=1)
    out = plan(make_tasks((1, 3, 30), (2, 2, 90), (3, 1, 80), (4, 2, 40), (5, 1, 20)), now)
    assert out["new_order"] == [tid(5), tid(3), tid(4), tid(2), tid(1)]
    assert out["fits"] == [tid(1), tid(2), tid(3), tid(4), tid(5)]  # original order
    assert "New order: task 5, task 3, task 4, task 2, task 1" in out["explanation"]


def test_greedy_skips_what_does_not_fit_and_tries_the_next() -> None:
    now = END - pd.Timedelta(minutes=130)  # 120 min available after the buffer
    out = plan(make_tasks((1, 1, 70), (2, 2, 60), (3, 2, 45)), now)
    assert out["new_order"] == [tid(1), tid(3)]  # 70 + 45 = 115; task 2 (60) doesn't fit
    assert out["moved_to_next_shift"] == [tid(2)]
    assert out["explanation"].startswith(
        "Task 2 no longer fits before 14:00; it moves to the next shift"
    )


def test_buffer_is_respected() -> None:
    now = END - pd.Timedelta(minutes=70)
    assert plan(make_tasks((1, 2, 60)), now)["moved_to_next_shift"] == []  # 60 <= 70 - 10
    assert plan(make_tasks((1, 2, 60.1)), now)["moved_to_next_shift"] == [tid(1)]
    assert plan(make_tasks((1, 2, 60)), now, buffer_min=15)["moved_to_next_shift"] == [tid(1)]


def test_rain_conditions_move_a_task() -> None:
    now = END - pd.Timedelta(minutes=190)  # 180 min available
    tasks = make_tasks((1, 1, 80), (2, 2, 50), (3, 3, 40))
    dry = plan(tasks, now, conditions={"rain_mm": 0.0})
    wet = plan(tasks, now, conditions={"rain_mm": 3.0}, reason=P.detect_triggers(rain_mm_h=3.0))
    assert dry["moved_to_next_shift"] == []
    assert wet["new_order"] == [tid(1), tid(2)]  # 104 + 65 = 169; +52 would not fit
    assert wet["moved_to_next_shift"] == [tid(3)]
    assert wet["triggers"] == ["rain"]
    assert wet["explanation"] == (
        "Rain started. Task 3 no longer fits before 14:00; it moves to the next shift."
    )


def test_running_task_stays_first_with_time_left() -> None:
    now = START + pd.Timedelta(hours=5)  # 170 min available
    tasks = make_tasks((1, 3, 90), (2, 1, 60), (3, 2, 100))
    tasks.loc[0, ["status", "actual_start"]] = ["in_progress", now - pd.Timedelta(minutes=30)]
    out = plan(tasks, now)
    assert out["new_order"][0] == tid(1)  # running, priority 3, still first
    assert out["new_order"] == [tid(1), tid(2)]  # 60 left + 60 = 120; +100 won't fit
    first = out["schedule"][0]
    assert pd.Timestamp(first["end"]) - pd.Timestamp(first["start"]) == pd.Timedelta(minutes=60)


def test_overrunning_task_gets_minimum_time_left() -> None:
    now = START + pd.Timedelta(hours=2)
    tasks = make_tasks((1, 2, 40))
    tasks.loc[0, ["status", "actual_start"]] = ["in_progress", now - pd.Timedelta(minutes=90)]
    s = plan(tasks, now, reason="task_overrun")["schedule"][0]
    assert pd.Timestamp(s["end"]) - pd.Timestamp(s["start"]) == pd.Timedelta(minutes=P.MIN_LEFT_MIN)


def test_past_shift_end_moves_everything() -> None:
    out = plan(make_tasks((1, 2, 30), (2, 1, 20)), END + pd.Timedelta(minutes=5))
    assert out["new_order"] == [] and out["fits"] == []
    assert out["moved_to_next_shift"] == [tid(1), tid(2)]
    assert out["available_min"] == 0
    assert "Task 1 and task 2 no longer fit" in out["explanation"]
    assert out["explanation"].endswith("Nothing fits in the time left.")


def test_model_failure_falls_back_to_stored_p50() -> None:
    def broken(df: pd.DataFrame) -> list[dict]:
        raise KeyError("missing columns: ['rain_mm']")

    now = END - pd.Timedelta(minutes=100)
    tasks = make_tasks((1, 1, 0), (2, 2, 0), (3, 2, 0))
    tasks["predicted_p50_min"] = [50.0, 30.0, None]
    out = plan(tasks, now, predictor=broken)
    assert out["new_order"] == [tid(1), tid(2)]
    assert out["moved_to_next_shift"] == [tid(3)]  # no estimate at all: not scheduled blind
    assert "No time estimate for task 3" in out["explanation"]


def test_missing_priority_conditions_and_sequence() -> None:
    now = START + pd.Timedelta(hours=1)
    tasks = make_tasks((1, 1, 60), (2, 1, 30))
    tasks["priority"] = [None, 1]  # missing -> schema default 2
    tasks["rain_mm"] = [1.0, 1.0]
    out = plan(tasks, now, conditions={"rain_mm": None, "temp_c": float("nan")})
    assert out["new_order"] == [tid(2), tid(1)]
    assert out["schedule"][1]["p50_min"] == pytest.approx(66.0)  # task's own rain kept
    tasks = tasks.drop(columns=["sequence_no", "status"])
    out = plan(tasks, now)
    assert set(out["new_order"]) == {tid(1), tid(2)}
    assert "task 1" in out["explanation"]  # label from the task id


def test_current_features_overrides() -> None:
    now = START + pd.Timedelta(hours=3, minutes=30)
    t = make_tasks((1, 2, 60), health_score=0.9, visibility_m=8000.0)
    X = P.current_features(
        t, now, START, {"rain_mm": 6.0, "health_score": 0.55, "visibility_m": None}
    )
    assert X.rain_mm.iloc[0] == 6.0 and X.health_score.iloc[0] == 0.55
    assert X.visibility_m.iloc[0] == 8000.0
    assert X.hours_into_shift.iloc[0] == pytest.approx(3.5)
    assert t.rain_mm.iloc[0] == 0.0  # input untouched


@pytest.mark.skipif(
    not (ROOT / "ml" / "artifacts" / "task_time" / "config.json").exists(),
    reason="task_time artifacts not built",
)
def test_real_model_on_sample_shift() -> None:
    import ml.inference.task_time as T

    def read(name: str, dates: list[str]) -> pd.DataFrame:
        return pd.read_csv(SAMPLE / f"{name}.csv", parse_dates=dates)

    tasks = read("tasks", ["scheduled_start", "actual_start", "actual_end"])
    shifts = read("shifts", ["start_time", "end_time"])
    F = T.build_feature_table(
        tasks,
        read("weather", ["ts"]),
        read("operators", []).drop(columns=["personality"]),
        read("machines", []),
        shifts,
        read("fatigue_log", ["ts"]),
        read("maintenance_log", ["event_date"]),
        T.load_artifacts()["reference"],
    )
    F = F.merge(tasks[["task_id", "priority", "sequence_no", "actual_start"]], on="task_id")
    sid = F.shift_id.iloc[0]
    sh = shifts.set_index("shift_id").loc[sid]
    t = F[F.shift_id == sid].assign(status="scheduled", actual_start=pd.NaT)
    now = sh.start_time + pd.Timedelta(hours=2)
    dry = P.re_evaluate_plan(sid, t, now, sh.end_time, sh.start_time, {"rain_mm": 0.0})
    wet = P.re_evaluate_plan(
        sid,
        t,
        now,
        sh.end_time,
        sh.start_time,
        {"rain_mm": 8.0, "visibility_m": 1500.0},
        reason="rain",
    )
    for out in (dry, wet):
        assert API_KEYS <= set(out)
        assert sorted(out["new_order"] + out["moved_to_next_shift"]) == sorted(t.task_id)
        assert all(s["p50_min"] and s["p50_min"] > 0 for s in out["schedule"])
    total = lambda o: sum(s["p50_min"] for s in o["schedule"])  # noqa: E731
    assert total(wet) > total(dry)  # heavy rain and poor visibility slow the work down
    assert len(wet["moved_to_next_shift"]) >= len(dry["moved_to_next_shift"])
    assert wet["explanation"].startswith("Rain started.")


def test_fatigue_adds_break_and_refits() -> None:
    now = END - pd.Timedelta(minutes=110)  # 100 min available
    tasks = make_tasks((1, 1, 50), (2, 2, 40))
    rested = plan(tasks, now)
    assert rested["moved_to_next_shift"] == [] and rested["break"] is None
    tired = plan(tasks, now, reason=P.detect_triggers(fatigue_level="high"))
    assert tired["break"] == {
        "start": "2026-08-21T06:40:00Z",
        "end": "2026-08-21T06:55:00Z",
        "minutes": P.BREAK_MIN,
    }
    assert tired["new_order"] == [tid(1)]  # 15 + 50 + 40 = 105 > 100
    assert tired["moved_to_next_shift"] == [tid(2)]
    assert tired["schedule"][0]["start"] == "2026-08-21T06:55:00Z"  # after the break
    assert tired["explanation"] == (
        "Break added because fatigue is high (15 min before the next task). "
        "Task 2 no longer fits before 14:00; it moves to the next shift."
    )


def test_fatigue_break_goes_after_the_running_task() -> None:
    now = START + pd.Timedelta(hours=2)
    tasks = make_tasks((1, 2, 60), (2, 1, 30))
    tasks.loc[0, ["status", "actual_start"]] = ["in_progress", now - pd.Timedelta(minutes=20)]
    out = plan(tasks, now, reason=["rain", "fatigue_high"])
    run, nxt = out["schedule"]
    assert out["break"]["start"] == run["end"]  # running task finishes first (40 min left)
    assert nxt["start"] == out["break"]["end"]
    assert out["explanation"].startswith("Rain started; break added because fatigue is high")


def test_fatigue_without_waiting_tasks_adds_no_break() -> None:
    now = START + pd.Timedelta(hours=2)
    tasks = make_tasks((1, 2, 60))
    tasks.loc[0, ["status", "actual_start"]] = ["in_progress", now - pd.Timedelta(minutes=20)]
    out = plan(tasks, now, reason="fatigue_high")
    assert out["break"] is None
    assert out["explanation"].startswith("Operator fatigue is high.")
    assert plan(make_tasks(), now, reason="fatigue_high")["break"] is None

"""POST /predict/task-time, /plan/re-evaluate and /plan/accept on the sample data with the real
task-time model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

SHIFT = "SH-2026-06-01-M06-D"  # OP05 on M06, 00:30-08:30 UTC, tasks 1-6
T = [f"T-{SHIFT}-{i}" for i in range(1, 7)]


def test_predict_writes_tasks(client, repo, headers):
    resp = client.post(
        "/predict/task-time", json={"task_ids": [T[2], T[1]]}, headers=headers("op05")
    )
    assert resp.status_code == 200, resp.text
    preds = resp.json()["predictions"]
    assert [p["task_id"] for p in preds] == [T[2], T[1]]  # request order
    for p in preds:
        assert 0 < p["p10_min"] <= p["p50_min"] <= p["p90_min"]
        assert 1 <= len(p["factors"]) <= 3
        assert {"feature", "label", "impact_min"} <= set(p["factors"][0])
        if p["operator_avg_min"] is not None:  # OP05's earlier same-type task that day
            assert p["expected_efficiency"] == round(p["p50_min"] / p["operator_avg_min"], 2)
        row = repo.tasks(task_ids=[p["task_id"]]).iloc[0]
        assert row.predicted_p50_min == p["p50_min"] and row.predicted_p90_min == p["p90_min"]
    updated = dict(repo.task_updates)
    assert updated[T[1]]["prediction_factors"] == preds[1]["factors"]


def test_predict_uses_live_health(repo, engine):
    from app.services.tasks import feature_table, live_health

    tasks = repo.tasks(task_ids=[T[1]])
    repo.health["M06"] = {"machine_id": "M06", "overall_score": 0.45}
    assert live_health(repo, ["M06"], engine) == {"M06": 0.45}
    engine.health["M06"] = {"overall": 0.52}  # the running replay wins over the snapshot
    health = live_health(repo, ["M06"], engine)
    assert health == {"M06": 0.52}
    assert feature_table(repo, tasks, health).health_score.tolist() == [0.52]
    # without a live value: service-based health at shift start, as in training
    fallback = feature_table(repo, tasks, {}).health_score.iloc[0]
    assert 0.3 <= fallback <= 1.0


def test_predict_errors(client, headers):
    resp = client.post(
        "/predict/task-time", json={"task_ids": ["T-nope"]}, headers=headers("manager")
    )
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_FOUND"
    resp = client.post(
        "/predict/task-time", json={"task_ids": [T[1]]}, headers=headers("operator")
    )  # Ravi (OP03) -> OP05's task
    assert resp.status_code == 403


def test_predict_model_missing_is_503(client, headers, monkeypatch):
    from ml.inference import task_time

    def boom():
        raise FileNotFoundError("lgbm_p10.joblib")

    monkeypatch.setattr(task_time, "load_artifacts", boom)
    resp = client.post("/predict/task-time", json={"task_ids": [T[1]]}, headers=headers("manager"))
    assert resp.status_code == 503
    assert resp.json()["error"] == {
        "code": "MODEL_NOT_LOADED",
        "message": "Task time model is not loaded. Run ml/02_task_time.ipynb and restart.",
    }


# ---------------------------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def running_shift(repo):
    """Tasks 2-6 not done yet; task 2 running since 03:48."""
    t = repo.t_tasks
    idx = t.task_id.isin(T[1:])
    t.loc[idx, "actual_end"] = pd.NaT
    t.loc[idx, "actual_duration_min"] = float("nan")
    t["delay_reason"] = t["delay_reason"].astype(object)
    t.loc[idx, "delay_reason"] = None
    t["status"] = t["status"].astype(object)
    t.loc[idx, "status"] = "scheduled"
    t.loc[t.task_id == T[1], "status"] = "in_progress"
    t.loc[~t.task_id.isin([T[1]]) & idx, "actual_start"] = pd.NaT
    return repo


def reeval(client, headers, now, reason="rain", who="op05"):
    return client.post(
        "/plan/re-evaluate",
        json={"shift_id": SHIFT, "reason": reason, "now": now},
        headers=headers(who),
    )


def test_re_evaluate_plans_the_rest_of_the_shift(client, running_shift, headers):
    resp = reeval(client, headers, "2026-06-01T04:00:00Z", reason="manager_edit")
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["shift_id"] == SHIFT and out["now"] == "2026-06-01T04:00:00Z"
    assert out["new_order"][0] == T[1]  # the running task stays first
    assert set(out["fits"]) | set(out["moved_to_next_shift"]) == set(T[1:])
    assert out["triggers"][0] == "manager_edit"
    assert out["available_min"] == pytest.approx(4.5 * 60 - 10)
    assert out["explanation"].startswith("The manager changed the plan")
    assert {s["task_id"] for s in out["schedule"]} == set(T[1:])
    assert out["break"] is None


def test_late_in_shift_tasks_move(client, running_shift, headers):
    out = reeval(client, headers, "2026-06-01T07:40:00Z").json()
    assert out["moved_to_next_shift"], out
    assert "next shift" in out["explanation"]
    moved = [s for s in out["schedule"] if not s["fits"]]
    assert all(s["start"] is None for s in moved)


def test_fatigue_trigger_reads_latest_fatigue_level(client, running_shift, headers, repo):
    live = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    repo.insert_fatigue(
        {
            "ts": live,
            "operator_id": "OP05",
            "shift_id": SHIFT,
            "fatigue_score": 0.72,
            "fatigue_level": "high",
        }
    )
    out = reeval(client, headers, "2026-06-01T04:00:00Z", reason="task_overrun").json()
    assert "fatigue_high" in out["triggers"]
    assert out["break"] is not None and out["break"]["minutes"] == 15
    assert out["fatigue"]["fatigue_level"] == "high"
    assert "break added because fatigue is high" in out["explanation"]


def test_rain_trigger_from_weather(client, running_shift, headers, repo):
    # weather rows are hourly at :30 UTC (IST hours); 03:30 is the hour holding 04:10
    repo.t_weather.loc[repo.t_weather.ts == pd.Timestamp("2026-06-01T03:30Z"), "rain_mm"] = 6.0
    out = reeval(client, headers, "2026-06-01T04:10:00Z", reason="manager_edit").json()
    assert out["triggers"] == ["manager_edit", "rain"]


def test_accept_applies_the_plan(client, running_shift, headers, repo):
    out = reeval(client, headers, "2026-06-01T07:40:00Z").json()
    resp = client.post("/plan/accept", json={"shift_id": SHIFT}, headers=headers("op05"))
    assert resp.status_code == 200 and resp.json() == {"shift_id": SHIFT, "applied": True}
    tasks = repo.tasks(shift_ids=[SHIFT]).set_index("task_id")
    order = [*out["new_order"], *out["moved_to_next_shift"]]
    assert tasks.loc[order, "sequence_no"].tolist() == list(range(2, 2 + len(order)))
    for tid in out["moved_to_next_shift"]:
        assert tasks.loc[tid, "status"] == "delayed"
        assert tasks.loc[tid, "delay_reason"] == "moved_to_next_shift"
    assert tasks.loc[T[1], "status"] == "in_progress"
    # moved tasks stay out of the next re-evaluation
    again = reeval(client, headers, "2026-06-01T07:45:00Z").json()
    assert not set(again["fits"]) & set(out["moved_to_next_shift"])
    assert not set(again["moved_to_next_shift"]) & set(out["moved_to_next_shift"])


def test_accept_needs_a_pending_plan(client, headers, running_shift):
    resp = client.post("/plan/accept", json={"shift_id": SHIFT}, headers=headers("op05"))
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NO_PENDING_PLAN"
    reeval(client, headers, "2026-06-01T04:00:00Z")
    assert (
        client.post(
            "/plan/accept", json={"shift_id": SHIFT}, headers=headers("manager")
        ).status_code
        == 200
    )
    resp = client.post("/plan/accept", json={"shift_id": SHIFT}, headers=headers("op05"))
    assert resp.status_code == 409  # used up


def test_plan_permissions_and_404(client, headers):
    assert reeval(client, headers, "2026-06-01T04:00:00Z", who="operator").status_code == 403
    resp = client.post(
        "/plan/re-evaluate",
        json={"shift_id": "SH-nope", "reason": "rain"},
        headers=headers("manager"),
    )
    assert resp.status_code == 404


def test_plan_clock_follows_the_replay(engine):
    from app.services.tasks import plan_clock

    shift = {"machine_id": "M06"}
    fixed = datetime(2026, 6, 1, 5, tzinfo=UTC)
    assert plan_clock(shift, engine, fixed) == fixed
    engine.running, engine.replay_ts, engine.streams = True, fixed, {"M06": object()}
    assert plan_clock(shift, engine, None) == fixed
    assert plan_clock({"machine_id": "M01"}, engine, None) > fixed  # not replayed: wall clock

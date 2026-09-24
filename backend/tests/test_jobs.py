"""Maintenance scoring job, POST /analytics/cluster and the APScheduler setup (sample data,
real models)."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeTelemetry, sample_telemetry

from app.replay.source import normalize

START = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)


@dataclass
class Stream:
    machine_type: str
    replayed: deque = field(default_factory=deque)


class FakeEngine:
    def __init__(self) -> None:
        self.running = True
        self.from_ts = START
        self.replay_ts = START
        self.streams = {"M06": Stream("articulated_truck")}
        self.maintenance: dict[str, dict[str, Any]] = {}
        self._rows = [
            normalize(r) for r in sample_telemetry().query("machine_id == 'M06'").to_dict("records")
        ]

    def advance(self, minutes: int) -> None:
        """Move the replay clock and 'emit' the recorded minutes up to it."""
        self.replay_ts += timedelta(minutes=minutes)
        s = self.streams["M06"]
        s.replayed.clear()
        s.replayed.extend(r for r in self._rows if self.from_ts <= r["ts"] <= self.replay_ts)

    def set_maintenance(self, machine_id: str, row: dict[str, Any]) -> None:
        self.maintenance[machine_id] = row


@pytest.fixture
def scorer(repo):
    from app.jobs.maintenance import MaintenanceScorer

    eng = FakeEngine()
    return MaintenanceScorer(repo, eng, FakeTelemetry()), eng


def test_scores_every_10_replay_minutes(scorer, repo):
    sc, eng = scorer
    assert eng.streams["M06"].machine_type == repo.machine("M06")["machine_type"]
    eng.advance(1)
    assert asyncio.run(sc.tick()) == 1
    (row,) = repo.predictions
    assert row["machine_id"] == "M06" and row["horizon_hours"] == 48
    assert 0 <= row["failure_probability"] <= 1
    assert row["likely_component"] in (
        "engine",
        "cooling",
        "hydraulics",
        "electrical",
        "undercarriage",
    )
    assert row["model_version"] == "v2+floor"
    assert row["predicted_at"] == "2026-06-01T04:01:00Z"  # replay time, not wall clock
    assert 1 <= len(row["top_factors"]) <= 3 and {"feature", "shap"} == set(row["top_factors"][0])
    # handed to the replay so the next health minute uses it
    assert eng.maintenance["M06"]["failure_probability"] == row["failure_probability"]

    eng.advance(9)  # 10 replay minutes since the start, 9 since the last score
    assert asyncio.run(sc.tick()) == 0
    eng.advance(1)
    assert asyncio.run(sc.tick()) == 1
    assert repo.predictions[-1]["predicted_at"] == "2026-06-01T04:11:00Z"
    assert len(sc._cache) == 1  # history scored once per replay


def test_restarted_replay_scores_again(scorer, repo):
    sc, eng = scorer
    eng.advance(1)
    asyncio.run(sc.tick())
    eng.from_ts = eng.replay_ts = START - timedelta(hours=1)  # restart earlier
    eng.advance(1)
    assert asyncio.run(sc.tick()) == 1


def test_idle_without_replay(scorer, repo):
    sc, eng = scorer
    eng.running = False
    assert asyncio.run(sc.tick()) == 0 and repo.predictions == []


def test_health_uses_the_prediction():
    """The engine's health minute turns the prediction into failure_probability."""
    from ml.inference import health as H

    pred = {"machine_id": "M06", "failure_probability": 0.72, "likely_component": "hydraulics"}
    out = H.compute_health(
        "M06", START, rule_states=[], anomaly=None, maintenance=pred, travelling=False
    )
    assert out["failure_probability"] == 0.72 and out["likely_component"] == "hydraulics"


# ---------------------------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------------------------
def test_cluster_week_writes_fleet_metrics(client, repo, headers):
    resp = client.post("/analytics/cluster?week_start=2026-06-01", headers=headers("manager"))
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["week_start"] == "2026-06-01" and out["rows_written"] == len(repo.fleet) > 0
    assert out["telemetry_source"] == "parquet"
    kinds = {k[0] for k in repo.fleet}
    assert kinds == {"operator", "machine"}
    assert {"M01", "M06"} <= {k[1] for k in repo.fleet if k[0] == "machine"}
    for row in repo.fleet.values():
        assert row["week_start"] == "2026-06-01"
        assert isinstance(row["cluster_id"], int) and row["cluster_label"]
        assert isinstance(row["rank_in_site"], int) and isinstance(row["is_outlier"], bool)
        assert "verified_by" not in row  # a manager's verification survives the upsert
    assert "operators (" in out["summary"] and "machines (" in out["summary"]


def test_cluster_week_errors(client, headers):
    resp = client.post("/analytics/cluster?week_start=2026-06-02", headers=headers("manager"))
    assert resp.status_code == 400 and "Monday" in resp.json()["error"]["message"]
    resp = client.post("/analytics/cluster?week_start=2026-06-08", headers=headers("manager"))
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_FOUND"
    resp = client.post("/analytics/cluster?week_start=junk", headers=headers("manager"))
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_latest_week(repo):
    from app.services.analytics import latest_week

    assert str(latest_week(repo)) == "2026-06-01"


# ---------------------------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------------------------
def test_scheduler_jobs():
    from app.jobs.scheduler import build_scheduler

    jobs = {j.id: j for j in build_scheduler().get_jobs()}
    assert set(jobs) == {
        "maintenance_scoring",
        "vision_alert_expiry",
        "fleet_clustering",
        "handover_summary",
        "training_recommendations",
    }
    assert jobs["maintenance_scoring"].trigger.interval == timedelta(seconds=5)
    assert jobs["vision_alert_expiry"].trigger.interval == timedelta(seconds=10)
    assert "hour='1'" in str(jobs["fleet_clustering"].trigger)
    assert jobs["handover_summary"].trigger.interval == timedelta(seconds=30)
    assert "minute='30'" in str(jobs["training_recommendations"].trigger)
    assert all(j.max_instances == 1 for j in jobs.values())

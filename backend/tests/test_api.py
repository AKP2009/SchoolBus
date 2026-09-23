"""API tests on CsvRepo (FastAPI TestClient, data/output CSVs)."""

import pandas as pd
import pytest
from app import REPO_ROOT
from app.config import Settings, get_settings
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def settings():
    """Point the cached settings at the repo's CSVs (dev bypass on)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _future_task_ids(n: int = 3) -> list[str]:
    tasks = pd.read_csv(REPO_ROOT / "data/output/tasks.csv")
    return tasks.loc[tasks["status"] == "scheduled", "task_id"].head(n).tolist()


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["models"]["task_time"] == "v1"


def test_predict_shape_and_ordering():
    ids = _future_task_ids(3)
    res = client.post("/predict/task-time", json={"task_ids": ids})
    assert res.status_code == 200, res.text
    preds = res.json()["predictions"]
    assert [p["task_id"] for p in preds] == ids
    for p in preds:
        assert 0 < p["p10_min"] <= p["p50_min"] <= p["p90_min"]
        assert p["standard_min"] > 0
        assert p["expected_efficiency"] == pytest.approx(p["standard_min"] / p["p50_min"], rel=1e-9)
        assert len(p["factors"]) == 3
        assert all(f["label"] for f in p["factors"])
        assert {"operator_avg_min", "operator_avg_efficiency", "operator_prev_efficiency"} <= set(p)


def test_predict_unknown_task_id():
    res = client.post("/predict/task-time", json={"task_ids": ["T-NOPE"]})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_predict_requires_task_ids():
    res = client.post("/predict/task-time", json={"task_ids": []})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_efficiency_endpoint_shape():
    res = client.get("/operators/OP01/efficiency")
    assert res.status_code == 200
    body = res.json()
    assert body["operator_id"] == "OP01"
    assert body["as_of"] == "2026-08-29"  # latest completed task_date, not today
    assert body["summary"]
    row = body["summary"][0]
    assert {
        "task_type",
        "avg_efficiency",
        "prev_efficiency",
        "last_task_efficiency",
        "trend",
        "fleet_median",
        "n_tasks",
    } <= set(row)
    for rec in body["recommendations"]:
        assert rec["module_id"].startswith("TM-TECH-")
        assert rec["trigger_metric"] == "efficiency"


def test_efficiency_unknown_operator():
    res = client.get("/operators/OP99/efficiency")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "OPERATOR_NOT_FOUND"


def test_auth_required_when_bypass_off():
    app.dependency_overrides[get_settings] = lambda: Settings(
        data_source="csv",
        dev_auth_bypass=False,
        supabase_url="http://127.0.0.1:1",
        supabase_service_role_key="stub",
    )
    try:
        res = client.post("/predict/task-time", json={"task_ids": [_future_task_ids(1)[0]]})
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "UNAUTHORIZED"
    finally:
        app.dependency_overrides.pop(get_settings)

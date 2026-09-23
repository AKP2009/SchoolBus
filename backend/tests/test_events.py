"""POST /events: every event type, validation, coalescing, expiry, WebSocket forwarding."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

NOW = "2026-06-01T02:00:00Z"
SHIFT = "SH-2026-06-01-M06-D"  # OP05 on M06 in the sample

PROXIMITY = {
    "type": "proximity_breach",
    "machine_id": "M06",
    "operator_id": "OP05",
    "ts": NOW,
    "severity": "critical",
    "distance_m": 2.4,
    "sector": "front",
    "approaching": True,
    "details": {"track_id": 7, "class": "person", "conf": 0.83},
}
SAMPLE = {
    "type": "fatigue_sample",
    "operator_id": "OP05",
    "shift_id": SHIFT,
    "ts": NOW,
    "ear_avg": 0.27,
    "perclos_60s": 0.08,
    "yawn_count": 0,
    "head_down_events": 0,
    "phone_detected": False,
    "fatigue_score": 0.31,
    "fatigue_level": "low",
}


def post(client, body, who="vision"):
    from conftest import VISION_TOKEN, bearer, make_token

    token = VISION_TOKEN if who == "vision" else make_token(who)
    return client.post("/events", json=body, headers=bearer(token))


def test_proximity_breach_writes_alert_and_event(client, repo, pusher):
    resp = post(client, PROXIMITY)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["stored"] is True and out["alert_id"] > 0 and out["event_id"] > 0

    alert = repo.alerts[out["alert_id"]]
    assert alert["alert_code"] == "PROXIMITY_RED"
    assert (alert["severity"], alert["category"], alert["source"]) == (
        "critical",
        "safety",
        "vision",
    )
    assert alert["site_id"] == "S1" and alert["stage"] == "warn"
    assert alert["title"] == "Person 2.4 m in front of the machine"
    assert alert["evidence"]["count"] == 1

    (ev,) = repo.safety_rows
    assert ev["alert_id"] == out["alert_id"]
    assert (ev["event_type"], ev["distance_m"], ev["sector"], ev["approaching"]) == (
        "proximity_breach",
        2.4,
        "front",
        True,
    )
    assert ev["details"]["track_id"] == 7

    # safety goes out before the database write, the alert after it with the row id
    assert pusher.kinds("M06") == ["safety", "alert"]
    assert pusher.sent[0][2] == {
        "type": "proximity_breach",
        "severity": "critical",
        "distance_m": 2.4,
        "sector": "front",
        "approaching": True,
    }
    assert pusher.sent[1][2]["id"] == out["alert_id"]


def test_blindspot_uses_its_own_code(client, repo):
    body = {
        **PROXIMITY,
        "type": "blindspot_intrusion",
        "sector": "rear",
        "severity": "warning",
        "distance_m": 5.1,
        "approaching": False,
    }
    out = post(client, body).json()
    alert = repo.alerts[out["alert_id"]]
    assert alert["alert_code"] == "BLINDSPOT_ORANGE"
    assert alert["title"] == "Person 5.1 m behind the machine — blind spot"


def test_repeats_coalesce_into_one_alert(client, repo, pusher):
    first = post(client, {**PROXIMITY, "severity": "warning", "distance_m": 4.0}).json()
    second = post(client, {**PROXIMITY, "severity": "warning", "distance_m": 3.5}).json()
    third = post(client, {**PROXIMITY, "severity": "critical", "distance_m": 2.2}).json()
    assert first["alert_id"] == second["alert_id"] == third["alert_id"]
    assert len(repo.alerts) == 1 and len(repo.safety_rows) == 3
    alert = repo.alerts[first["alert_id"]]
    assert alert["alert_code"] == "PROXIMITY_RED" and alert["severity"] == "critical"
    assert alert["evidence"]["count"] == 3 and alert["evidence"]["min_distance_m"] == 2.2
    # alert messages: on open and on the severity rise, not on the plain repeat
    assert [k for k in pusher.kinds("M06")] == ["safety", "alert", "safety", "safety", "alert"]


def test_expire_resolves_quiet_alerts_but_not_sos(client, repo, pusher):
    prox = post(client, PROXIMITY).json()
    sos = post(
        client, {"type": "sos", "machine_id": "M06", "operator_id": "OP05", "ts": NOW}, who="u-op05"
    ).json()
    assert asyncio.run(client.service.expire(older_than_s=-1)) == 1
    assert repo.alerts[prox["alert_id"]]["stage"] == "resolved"
    assert repo.alerts[prox["alert_id"]]["resolved_at"]
    assert repo.alerts[sos["alert_id"]].get("resolved_at") is None
    last = pusher.sent[-1]
    assert last[1] == "alert" and last[2]["stage"] == "resolved"
    # a new event after expiry opens a new alert
    again = post(client, PROXIMITY).json()
    assert again["alert_id"] != prox["alert_id"]


def test_sos_is_an_emergency(client, repo, pusher):
    body = {
        "type": "sos",
        "operator_id": "OP05",
        "machine_id": "M06",
        "ts": NOW,
        "severity": "warning",
        "details": {"message": "Stuck in trench"},
    }
    out = post(client, body, who="u-op05").json()
    alert = repo.alerts[out["alert_id"]]
    assert (alert["severity"], alert["category"], alert["stage"]) == (
        "emergency",
        "emergency",
        "escalated",
    )
    assert alert["source"] == "operator" and alert["message"] == "Stuck in trench"
    assert repo.safety_rows[0]["severity"] == "emergency"
    assert pusher.sent[-1][2]["severity"] == "emergency"


def test_sos_without_machine_uses_operator_site(client, repo, pusher):
    out = post(client, {"type": "sos", "operator_id": "OP05", "ts": NOW}, who="u-op05").json()
    assert repo.alerts[out["alert_id"]]["site_id"] == "S1"
    assert pusher.sent == []  # no machine, no WebSocket


def test_fatigue_high_eyes_closed_is_critical(client, repo, pusher):
    body = {
        "type": "fatigue_high",
        "machine_id": "M06",
        "operator_id": "OP05",
        "ts": NOW,
        "severity": "critical",
        "sector": "cab",
        "details": {
            "shift_id": SHIFT,
            "reason": "eyes_closed",
            "eyes_closed_s": 2.07,
            "machine_moving": True,
            "fatigue_score": 0.41,
            "fatigue_level": "medium",
        },
    }
    out = post(client, body).json()
    alert = repo.alerts[out["alert_id"]]
    assert alert["alert_code"] == "EYES_CLOSED" and alert["severity"] == "critical"
    assert alert["title"] == "Eyes closed for 2.1 s while moving"
    assert pusher.kinds("M06") == ["safety", "fatigue", "alert"]
    assert pusher.sent[1][2] == {"fatigue_level": "medium", "fatigue_score": 0.41}
    # the level-high warning is a separate episode from eyes closed
    warn = {**body, "severity": "warning", "details": {"shift_id": SHIFT}}
    out2 = post(client, warn).json()
    assert out2["alert_id"] != out["alert_id"]
    assert repo.alerts[out2["alert_id"]]["alert_code"] == "FATIGUE_HIGH"


def test_phone_use(client, repo):
    body = {
        "type": "phone_use",
        "machine_id": "M06",
        "operator_id": "OP05",
        "ts": NOW,
        "severity": "warning",
        "details": {"shift_id": SHIFT, "class": "cell phone"},
    }
    out = post(client, body).json()
    alert = repo.alerts[out["alert_id"]]
    assert (alert["alert_code"], alert["category"]) == ("PHONE_USE", "behaviour")
    assert repo.safety_rows[0]["sector"] == "cab"  # default for cab-camera events


def test_fatigue_sample_writes_fatigue_log(client, repo, pusher):
    out = post(client, SAMPLE).json()
    assert out == {"stored": True, "alert_id": None, "event_id": out["event_id"]}
    (row,) = repo.fatigue_rows
    assert row["shift_id"] == SHIFT and row["fatigue_level"] == "low" and row["ear_avg"] == 0.27
    assert repo.alerts == {} and repo.safety_rows == []
    assert pusher.sent == [("M06", "fatigue", {"fatigue_level": "low", "fatigue_score": 0.31})]


def test_fatigue_sample_unknown_shift_is_kept(client, repo, pusher):
    body = {**SAMPLE, "shift_id": "SH-2026-09-24-M04-D"}  # vision's "today" id, not loaded
    assert post(client, body).status_code == 200
    assert repo.fatigue_rows[0]["shift_id"] is None
    assert pusher.sent[0][0] == "M04"  # machine from the shift id


@pytest.mark.parametrize(
    "body",
    [
        {**PROXIMITY, "type": "harsh_maneuver"},  # not accepted from vision
        {k: v for k, v in PROXIMITY.items() if k != "distance_m"},
        {k: v for k, v in PROXIMITY.items() if k != "sector"},
        {**PROXIMITY, "sector": "cab"},
        {**PROXIMITY, "severity": "emergency"},
        {**PROXIMITY, "severity": "info"},
        {**PROXIMITY, "distance_m": -1},
        {**PROXIMITY, "ts": "2026-06-01T02:00:00"},  # no timezone
        {k: v for k, v in PROXIMITY.items() if k != "machine_id"},
        {"type": "fatigue_high", "machine_id": "M06", "ts": NOW, "severity": "warning"},
        {"type": "phone_use", "machine_id": "M06", "operator_id": "OP05", "ts": NOW},
        {"type": "sos", "ts": NOW},
        {**SAMPLE, "fatigue_score": 1.5},
        {**SAMPLE, "fatigue_level": "extreme"},
        {k: v for k, v in SAMPLE.items() if k != "shift_id"},
        {"machine_id": "M06"},
    ],
)
def test_invalid_events_are_400(client, repo, body):
    resp = post(client, body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert repo.alerts == {} and repo.safety_rows == [] and repo.fatigue_rows == []


def test_unknown_machine_or_operator_is_404(client):
    resp = post(client, {**PROXIMITY, "machine_id": "M99"})
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "UNKNOWN_MACHINE"
    resp = post(client, {**SAMPLE, "operator_id": "OP99"})
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "UNKNOWN_OPERATOR"


def test_operator_posts_only_for_themselves(client, repo):
    ok = post(client, {"type": "sos", "operator_id": "OP05", "ts": NOW}, who="u-op05")
    assert ok.status_code == 200
    resp = post(client, {"type": "sos", "operator_id": "OP03", "ts": NOW}, who="u-op05")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "FORBIDDEN"
    assert post(client, {**SAMPLE}, who="u-priya").status_code == 200  # manager: anyone


# ---------------------------------------------------------------------------------------------
# GET /operator/{id}/fatigue
# ---------------------------------------------------------------------------------------------
def test_operator_fatigue_latest_row(client, repo, headers):
    resp = client.get("/operator/OP05/fatigue", headers=headers("vision"))
    assert resp.status_code == 200, resp.text
    old = resp.json()
    assert old["stale"] is True and old["operator_id"] == "OP05"  # sample rows are from June

    now = datetime.now(UTC).replace(microsecond=0)
    body = {**SAMPLE, "ts": now.isoformat(), "fatigue_score": 0.66, "fatigue_level": "high"}
    assert post(client, body).status_code == 200
    live = client.get("/operator/OP05/fatigue", headers=headers("op05")).json()
    assert (live["fatigue_level"], live["fatigue_score"], live["stale"]) == ("high", 0.66, False)
    assert live["shift_id"] == SHIFT and live["age_min"] < 1


def test_operator_fatigue_permissions_and_404(client, headers):
    resp = client.get("/operator/OP05/fatigue", headers=headers("operator"))  # Ravi is OP03
    assert resp.status_code == 403
    assert client.get("/operator/OP05/fatigue", headers=headers("manager")).status_code == 200
    resp = client.get("/operator/OP99/fatigue", headers=headers("manager"))
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_FOUND"


def test_plan_fatigue_prefers_live_rows(repo):
    from app.services.events import fatigue_for_plan

    plan_now = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
    replayed = fatigue_for_plan(repo, "OP05", plan_now)
    assert replayed is not None and replayed["ts"] <= "2026-06-01T04:00:00Z"
    live_ts = datetime.now(UTC) - timedelta(minutes=5)
    repo.insert_fatigue(
        {
            "ts": live_ts.isoformat(),
            "operator_id": "OP05",
            "shift_id": None,
            "fatigue_score": 0.7,
            "fatigue_level": "high",
        }
    )
    assert fatigue_for_plan(repo, "OP05", plan_now)["fatigue_level"] == "high"

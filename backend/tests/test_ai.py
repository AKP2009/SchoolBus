"""RAG chat, handover summary, incident drafts, training recommender and their jobs, with an
in-memory AiRepo and a scripted LLM (no network). The live LLM is exercised by tests/rag_eval.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fakes import FakeLLM, FakeTelemetry, MemoryAiRepo

from app.repo import SAFETY_COLUMNS, frame

SHIFT = "SH-2026-08-19-M05-N"
END = datetime(2026, 8, 19, 20, 30, tzinfo=UTC)


def chunk(doc_id: int, index: int, title: str, heading: str, text: str) -> dict[str, Any]:
    return {
        "id": doc_id * 100 + index,
        "document_id": doc_id,
        "content": f"# {title}\n## {heading}\n\n{text}",
        "metadata": {"title": title, "heading": heading, "chunk_index": index},
        "similarity": 0.6,
    }


E360 = chunk(1, 4, "Fault codes", "What does fault code E-360 mean?", "Low hydraulic oil level.")
LEAK = chunk(2, 6, "Troubleshooting FAQ", "How do I look for a hydraulic leak?", "Use cardboard.")


@pytest.fixture
def ai() -> MemoryAiRepo:
    return MemoryAiRepo()


@pytest.fixture
def api(client, ai):
    """The API client with the AI repo and a scripted LLM (set `api.llm`)."""
    from app.ai_repo import get_ai_repo
    from app.llm import get_llm

    client.llm = FakeLLM()
    client.app.dependency_overrides[get_ai_repo] = lambda: ai
    client.app.dependency_overrides[get_llm] = lambda: client.llm
    return client


@pytest.fixture(autouse=True)
def no_embedding_model(monkeypatch):
    from app.services import kb

    monkeypatch.setattr(kb, "embed_query", lambda text: [0.0] * kb.EMBED_DIM)


# ---------------------------------------------------------------------------------------------
# knowledge base
# ---------------------------------------------------------------------------------------------
def test_kb_chunks_one_per_section_with_heading():
    from app.services.kb import MAX_CHARS, load_kb

    docs = {d.file: d for d in load_kb()}
    assert set(docs) == {
        "fault_codes.md",
        "troubleshooting_faq.md",
        "safety_rules.md",
        "operating_tips.md",
        "training_modules.md",
    }
    fc = docs["fault_codes.md"]
    assert fc.title == "Fault codes" and fc.doc_type == "fault_codes"
    assert fc.source == "backend/kb/fault_codes.md"
    e365 = next(c for c in fc.chunks if "E-365" in c.heading)
    assert e365.content.startswith("# Fault codes\n## What does fault code E-365 mean?")
    assert [c.chunk_index for c in fc.chunks] == list(range(len(fc.chunks)))
    assert all(len(c.content) <= MAX_CHARS for d in docs.values() for c in d.chunks)


def test_kb_splits_a_long_section_with_overlap():
    from app.services.kb import MAX_CHARS, parse_markdown

    paras = "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(8))
    doc = parse_markdown("x.md", f"# Title\n\n## Long question?\n\n{paras}\n\n## Short?\n\nYes.")
    long = [c for c in doc.chunks if c.heading == "Long question?"]
    assert len(long) > 1 and all(len(c.content) <= MAX_CHARS + 100 for c in long)
    assert long[0].content[-300:] in long[1].content  # ~100 tokens carried over
    assert doc.chunks[-1].heading == "Short?"


# ---------------------------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------------------------
def test_split_sources_maps_numbers_and_dedupes():
    from app.services.chat import split_sources

    text, src = split_sources("Stop. (Source: Fault codes)\nSOURCES: 1, 1, 9", [E360, LEAK])
    assert text == "Stop. (Source: Fault codes)"
    assert src == [{"document_id": 1, "title": "Fault codes", "chunk_index": 4}]
    _, none = split_sources("Not in my manuals.\nSOURCES: none", [E360])
    assert none == []
    # cut-off reply without the SOURCES line: fall back to the titles cited in the text
    _, cited = split_sources("Use cardboard (Source: Troubleshooting FAQ)", [E360, LEAK])
    assert [s["document_id"] for s in cited] == [2]


def test_chat_answers_from_context_and_saves_both_turns(api, ai, headers):
    ai.chunks = [E360, LEAK]
    api.llm.replies = ["Lower the attachment and stop. (Source: Fault codes)\nSOURCES: 1"]
    sid = str(uuid4())
    r = api.post(
        "/chat",
        json={"session_id": sid, "operator_id": "OP03", "message": "What does E-360 mean?"},
        headers=headers("operator"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"].startswith("Lower the attachment")
    assert body["sources"] == [{"document_id": 1, "title": "Fault codes", "chunk_index": 4}]
    call = api.llm.calls[0]
    assert call["temperature"] == 0.2 and "English" in call["system"]
    assert "[1] Title: Fault codes" in call["prompt"] and "E-360" in call["prompt"]
    user, bot = ai.chat_rows
    assert (user["role"], bot["role"]) == ("user", "assistant")
    assert user["session_id"] == bot["session_id"] == sid
    assert user["content"] == "What does E-360 mean?" and bot["sources"] == body["sources"]
    assert user["created_at"] < bot["created_at"]


def test_chat_hindi_is_translated_then_answered_in_hindi(api, ai, headers):
    ai.chunks = [E360]
    api.llm.replies = ["What does E-365 mean?", "अटैचमेंट नीचे करें। (Source: Fault codes)\nSOURCES: 1"]
    r = api.post(
        "/chat",
        json={"session_id": str(uuid4()), "operator_id": "OP03", "message": "E-365 का क्या मतलब है?"},
        headers=headers("operator"),
    )
    assert r.status_code == 200, r.text
    translate, reply = api.llm.calls
    assert translate["temperature"] == 0.0 and "English" in translate["system"]
    assert "Hindi" in reply["system"] and "(In English: What does E-365 mean?)" in reply["prompt"]


def test_chat_with_nothing_retrieved_still_asks_the_llm(api, ai, headers):
    api.llm.replies = ["That is not in my manuals. Please ask your supervisor.\nSOURCES: none"]
    r = api.post(
        "/chat",
        json={"session_id": str(uuid4()), "operator_id": "OP03", "message": "Torque for pins?"},
        headers=headers("operator"),
    )
    assert r.json() == {
        "answer": "That is not in my manuals. Please ask your supervisor.",
        "sources": [],
    }
    assert "nothing relevant was found" in api.llm.calls[0]["prompt"]


def test_chat_only_as_yourself(api, headers):
    r = api.post(
        "/chat",
        json={"session_id": str(uuid4()), "operator_id": "OP05", "message": "hi"},
        headers=headers("operator"),
    )
    assert r.status_code == 403


def test_llm_features_503_without_key_but_server_runs(client, ai, headers, monkeypatch):
    from app.ai_repo import get_ai_repo
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_api_key", None)
    client.app.dependency_overrides[get_ai_repo] = lambda: ai
    body = {"session_id": str(uuid4()), "operator_id": "OP03", "message": "hi"}
    r = client.post("/chat", json=body, headers=headers("operator"))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert "LLM_API_KEY" in r.json()["error"]["message"]
    r = client.post(
        "/incidents/transcribe",
        json={"transcript": "x", "operator_id": "OP03"},
        headers=headers("operator"),
    )
    assert r.status_code == 503
    health = client.get("/health").json()
    assert health["models"]["llm"] == "not_configured"


# ---------------------------------------------------------------------------------------------
# handover
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def shift(ai) -> dict[str, Any]:
    s = {
        "shift_id": SHIFT,
        "site_id": "S1",
        "operator_id": "OP02",
        "machine_id": "M05",
        "shift_type": "night",
        "start_time": "2026-08-19T12:30:00+00:00",
        "end_time": "2026-08-19T20:30:00+00:00",
        "fuel_start_pct": 93.41,
        "fuel_end_pct": 60.48,
        "handover_notes": "Hydraulic oil ran hot around 23:29. Task 5 not finished, 19.2 m3 left.",
        "issues_reported": ["hydraulic_temp_high", "unfinished_task"],
        "handover_summary": None,
    }
    ai.shifts[SHIFT] = s
    ai.alerts = [
        {
            "ts": "2026-08-19T17:59:00+00:00",
            "machine_id": "M05",
            "alert_code": "HYD_OIL_HIGH",
            "title": "Hydraulic oil hot — 94 °C",
            "severity": "warning",
            "stage": "warn",
            "recommended_action": "Switch to economy mode",
        }
    ]
    ai.events = [
        {"machine_id": "M05", "event_type": "blindspot_intrusion", "severity": "critical"},
        {"machine_id": "M05", "event_type": "blindspot_intrusion", "severity": "warning"},
    ]
    ai.tasks = [
        {"shift_id": SHIFT, "sequence_no": 4, "task_type": "grade", "material_type": "gravel",
         "quantity": 40, "unit": "m3", "status": "completed", "delay_reason": None},
        {"shift_id": SHIFT, "sequence_no": 5, "task_type": "grade", "material_type": "gravel",
         "quantity": 60, "unit": "m3", "status": "delayed", "delay_reason": "hydraulic_temp"},
    ]  # fmt: skip
    return s


def test_handover_facts(ai, shift):
    from app.services.handover import gather

    f = gather(ai, SHIFT)
    assert f["machine_id"] == "M05" and f["fuel_end_pct"] == 60.48
    assert f["alerts_open_at_shift_end"][0]["code"] == "HYD_OIL_HIGH"
    assert f["alerts_open_at_shift_end"][0]["raised"] == "19 Aug 23:29"  # Asia/Kolkata
    assert f["safety_events_during_shift"] == [
        {"type": "blindspot_intrusion", "count": 2, "critical_or_worse": 1}
    ]
    assert [t["task"] for t in f["tasks_not_completed"]] == [5]
    assert f["latest_maintenance_prediction"] is None


def test_handover_endpoint_keeps_5_lines_and_saves(api, ai, shift, headers):
    api.llm.replies = [
        "**Machine:** OK\n- Open issues: hydraulic oil hot\n\nCheck before starting: oil level\n"
        "Unfinished work: task 5\nFuel: 60%\nExtra line the model should not add"
    ]
    r = api.post(f"/handover/{SHIFT}", headers=headers("manager"))
    assert r.status_code == 200, r.text
    lines = r.json()["summary"].split("\n")
    assert lines == [
        "Machine: OK",
        "Open issues: hydraulic oil hot",
        "Check before starting: oil level",
        "Unfinished work: task 5",
        "Fuel: 60%",
    ]
    assert ai.handovers[SHIFT][0] == r.json()["summary"]
    call = api.llm.calls[0]
    assert call["temperature"] == 0.3 and "HYD_OIL_HIGH" in call["prompt"]


def test_handover_unknown_shift_is_404(api, headers):
    assert api.post("/handover/SH-NOPE", headers=headers("manager")).status_code == 404


def test_handover_other_site_operator_is_403(api, ai, shift, headers):
    shift["site_id"] = "S2"
    assert api.post(f"/handover/{SHIFT}", headers=headers("operator")).status_code == 403


# ---------------------------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------------------------
def test_incident_draft_from_transcript(api, headers):
    api.llm.replies = [
        {
            "incident_type": "near_miss",
            "severity": "warning",
            "description": "A worker walked behind M05 while reversing; the operator stopped.",
            "injury": False,
        }
    ]
    r = api.post(
        "/incidents/transcribe",
        json={"transcript": "a guy walked behind me", "operator_id": "OP03", "machine_id": "M05"},
        headers=headers("operator"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["incident_type"] == "near_miss" and r.json()["injury"] is False
    assert api.llm.calls[0]["temperature"] == 0.0
    assert "Machine: M05" in api.llm.calls[0]["prompt"]


def test_incident_injury_raises_severity(api, headers):
    api.llm.replies = [
        {"incident_type": "injury", "severity": "info", "description": "Cut hand.", "injury": False}
    ]
    r = api.post(
        "/incidents/transcribe",
        json={"transcript": "cut my hand on the bucket", "operator_id": "OP03"},
        headers=headers("operator"),
    )
    assert r.json()["injury"] is True and r.json()["severity"] == "critical"


def test_incident_empty_transcript_is_400(api, headers):
    r = api.post(
        "/incidents/transcribe",
        json={"transcript": "  ", "operator_id": "OP03"},
        headers=headers("operator"),
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------------------------
# training recommender
# ---------------------------------------------------------------------------------------------
def events(operator_id: str, at: datetime, **counts: int) -> Any:
    rows = [
        {"ts": at - timedelta(hours=1), "site_id": "S1", "machine_id": "M06",
         "operator_id": operator_id, "event_type": t, "severity": "warning"}
        for t, n in counts.items()
        for _ in range(n)
    ]  # fmt: skip
    return frame(rows, SAFETY_COLUMNS)


@pytest.fixture
def rec(repo, ai, monkeypatch):
    """recommend() on the sample data, with scripted safety events and time ratios."""
    from app.services import recommender

    monkeypatch.setattr(recommender, "_time_ratios", lambda repo, tasks: {})
    as_of = recommender.default_as_of(repo)
    op = repo.shifts().operator_id.iloc[0]

    def run(**counts: int) -> dict[str, Any]:
        monkeypatch.setattr(repo, "safety_events", lambda s, e: events(op, as_of, **counts))
        return recommender.recommend(repo, ai, FakeTelemetry(), op, as_of)

    run.op = op  # type: ignore[attr-defined]
    return run


def test_recommender_safety_first_and_max_two(rec, ai):
    out = rec(tip_risk=1, seatbelt_unfastened=1, proximity_breach=1, blindspot_intrusion=1,
              harsh_maneuver=3)  # fmt: skip
    fired = [t["metric"] for t in out["triggers"]]
    assert fired[:4] == ["tip_risk", "seatbelt_unfastened", "proximity_breach", "harsh_maneuver"]
    assert [r["module_id"] for r in out["created"]] == ["TM-SAFE-03", "TM-SAFE-02"]
    assert all(r["status"] == "pending" for r in ai.recs)
    assert out["created"][0]["reason"].startswith("The tilt warning (tip risk) came on 1 time ")
    assert out["created"][0]["trigger_metric"] == "tip_risk"
    # both slots are taken: nothing new until one is completed or dismissed
    assert rec(tip_risk=2, harsh_maneuver=5)["created"] == []


def test_recommender_skips_open_and_recently_dismissed(rec, ai):
    now = datetime.now(UTC).isoformat()
    ai.recs = [
        {"id": 90, "operator_id": rec.op, "module_id": "TM-SAFE-03", "status": "dismissed",
         "created_at": now},
        {"id": 91, "operator_id": rec.op, "module_id": "TM-SAFE-01", "status": "pending",
         "created_at": now},
    ]  # fmt: skip
    out = rec(tip_risk=1, proximity_breach=2, harsh_maneuver=3)
    assert [r["module_id"] for r in out["created"]] == ["TM-SMTH-01"]  # one slot left
    assert "3 harsh movements" in out["created"][0]["reason"]


def test_recommender_below_thresholds_writes_nothing(rec, ai):
    out = rec(proximity_breach=1, harsh_maneuver=2)
    assert out["created"] == [] and ai.recs == []


def test_recommender_cluster_and_time_ratio(rec, ai, monkeypatch):
    from app.services import recommender

    ai.metrics = [{"entity_id": rec.op, "cluster_label": "needs safety coaching"}]
    monkeypatch.setattr(recommender, "_time_ratios", lambda repo, tasks: {"load": (1.31, 4)})
    out = rec()
    assert [r["module_id"] for r in out["created"]] == ["TM-SIM-01", "TM-CYC-01"]
    assert "load tasks took 31% longer than expected" in out["created"][1]["reason"]


def test_recommender_real_time_ratio_path_runs(repo, ai):
    """No mocks: the task-time model on the sample day (just must not fail)."""
    from app.services import recommender

    op = repo.shifts().operator_id.iloc[0]
    out = recommender.recommend(repo, ai, FakeTelemetry(), op)
    assert out["as_of"] is not None and len(out["created"]) <= 2


def test_recommendations_endpoint_permissions(client, ai, headers):
    from app.ai_repo import get_ai_repo

    client.app.dependency_overrides[get_ai_repo] = lambda: ai
    assert (
        client.post("/training/recommendations", json={}, headers=headers("operator")).status_code
        == 403
    )
    r = client.post(
        "/training/recommendations", json={"operator_id": "OP05"}, headers=headers("operator")
    )
    assert r.status_code == 403
    r = client.post(
        "/training/recommendations", json={"operator_id": "OP03"}, headers=headers("operator")
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["operator_id"] == "OP03"


# ---------------------------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------------------------
class Engine:
    def __init__(self, running: bool, replay_ts: datetime | None, machines: list[str]) -> None:
        self.running = running
        self.replay_ts = replay_ts
        self.streams = dict.fromkeys(machines)


def test_scheduler_has_the_ai_jobs():
    from app.jobs.scheduler import build_scheduler

    ids = {j.id for j in build_scheduler().get_jobs()}
    assert {"handover_summary", "training_recommendations"} <= ids


def test_handover_job_uses_the_replay_clock(ai, shift, monkeypatch):
    from app.jobs import ai as jobs

    llm = FakeLLM("Machine: fine\nFuel: 60%")
    monkeypatch.setattr(jobs, "get_llm", lambda: llm)
    wall = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    before_end = jobs.HandoverJob(ai, Engine(True, END - timedelta(minutes=5), ["M05"]))
    assert asyncio.run(before_end.tick(wall)) is None  # replay hasn't reached the shift end
    after_end = jobs.HandoverJob(ai, Engine(True, END + timedelta(minutes=1), ["M05"]))
    assert asyncio.run(after_end.tick(wall)) == SHIFT
    assert ai.handovers[SHIFT][0] == "Machine: fine\nFuel: 60%"
    assert asyncio.run(after_end.tick(wall)) is None  # done: summary set


def test_handover_job_wall_clock_and_retry(ai, shift, monkeypatch):
    from app.core.errors import ApiError
    from app.jobs import ai as jobs

    class Failing(FakeLLM):
        def text(self, *a: Any, **k: Any) -> str:
            raise ApiError(503, "LLM_ERROR", "down")

    monkeypatch.setattr(jobs, "get_llm", lambda: Failing())
    job = jobs.HandoverJob(ai, Engine(False, None, []))
    now = END + timedelta(minutes=2)  # wall clock just after the shift end
    assert asyncio.run(job.tick(now)) is None and SHIFT in job.failed
    assert job.candidates(now + timedelta(minutes=5)) == []  # waits RETRY_AFTER
    assert [s["shift_id"] for s in job.candidates(now + timedelta(minutes=11))] == [SHIFT]


def test_handover_job_idle_without_key(ai, shift, monkeypatch):
    from app.core.config import get_settings
    from app.jobs import ai as jobs

    monkeypatch.setattr(get_settings(), "llm_api_key", None)
    job = jobs.HandoverJob(ai, Engine(False, None, []))
    assert asyncio.run(job.tick(END + timedelta(minutes=1))) is None
    assert ai.handovers == {}


def test_recommend_all_covers_every_operator(repo, ai, monkeypatch):
    from app.jobs.ai import recommend_all
    from app.services import recommender

    seen = []
    monkeypatch.setattr(
        recommender, "recommend", lambda *a, **k: seen.append(a[3]) or {"created": [1]}
    )
    monkeypatch.setattr("app.jobs.ai.recommend", recommender.recommend)
    assert recommend_all(repo, ai, FakeTelemetry()) == 2
    assert seen == ["OP01", "OP05"]

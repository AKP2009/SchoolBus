"""Shift handover summary (`POST /handover/{shift_id}`, job at shift end; models.md §9).

The summary of a shift is written for the next operator on the same machine and stored in that
shift's `handover_summary` (the web app reads the machine's latest shift). Inputs: the
operator's `handover_notes` and `issues_reported`, alerts still open at the shift end, the
shift's safety events, tasks not completed, fuel, and the latest maintenance prediction.
Output: at most 5 lines — machine condition, open issues, check before starting, unfinished
work, fuel. `temperature=0.3`.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.ai_repo import AiRepo
from app.core.errors import ApiError
from app.llm import LLM

IST = ZoneInfo("Asia/Kolkata")
TEMPERATURE = 0.3
MAX_TOKENS = 350
LABELS = ("Machine", "Open issues", "Check before starting", "Unfinished work", "Fuel")

SYSTEM_PROMPT = """You write the shift handover for the next operator of a construction machine.
Use ONLY the facts given. Do not invent problems, numbers or causes.
Write exactly 5 short lines, in this order, each starting with its label:
Machine: overall condition in a few words (mention a maintenance risk only if it is given).
Open issues: warnings still open and anything the operator reported; "None" if nothing.
Check before starting: 1-3 concrete checks that follow from the issues (safety checks first).
Unfinished work: tasks not completed with what is left; "None" if all done.
Fuel: level at the end of the shift, and "refuel before starting" if it is below 25%.
Plain words, no markdown, no bullet symbols, at most about 20 words per line.
Times are Asia/Kolkata local time."""


def _dt(v: Any) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _local(v: Any) -> str:
    return _dt(v).astimezone(IST).strftime("%d %b %H:%M")


def gather(repo: AiRepo, shift_id: str) -> dict[str, Any]:
    shift = repo.shift_full(shift_id)
    if shift is None:
        raise ApiError(404, "NOT_FOUND", f"Shift {shift_id} not found.")
    mid = shift["machine_id"]
    start, end = _dt(shift["start_time"]), _dt(shift["end_time"])

    alerts = [
        {
            "raised": _local(a["ts"]),
            "code": a["alert_code"],
            "title": a["title"],
            "severity": a["severity"],
            "stage": a["stage"],
            "recommended_action": a.get("recommended_action"),
        }
        for a in repo.open_alerts_at(mid, end)
    ]
    events = repo.machine_safety_events(mid, start, end)
    counts = Counter(e["event_type"] for e in events)
    worst = Counter(e["event_type"] for e in events if e["severity"] in ("critical", "emergency"))
    safety = [
        {"type": t, "count": n, "critical_or_worse": worst.get(t, 0)}
        for t, n in counts.most_common()
    ]
    unfinished = [
        {
            "task": t["sequence_no"],
            "type": t["task_type"],
            "material": t["material_type"],
            "planned_quantity": f"{t['quantity']} {t['unit']}",
            "status": t["status"],
            "delay_reason": t.get("delay_reason"),
        }
        for t in repo.shift_tasks(shift_id)
        if t["status"] != "completed"
    ]
    mp = repo.latest_maintenance(mid, end)
    maintenance = (
        {
            "failure_probability_48h": round(float(mp["failure_probability"]), 2),
            "likely_component": mp.get("likely_component"),
            "predicted": _local(mp["predicted_at"]),
        }
        if mp
        else None
    )
    return {
        "shift_id": shift_id,
        "machine_id": mid,
        "shift": f"{shift['shift_type']} shift, {_local(start)} to {_local(end)}",
        "operator_notes": shift.get("handover_notes"),
        "issues_reported": shift.get("issues_reported") or [],
        "alerts_open_at_shift_end": alerts,
        "safety_events_during_shift": safety,
        "tasks_not_completed": unfinished,
        "fuel_start_pct": shift.get("fuel_start_pct"),
        "fuel_end_pct": shift.get("fuel_end_pct"),
        "latest_maintenance_prediction": maintenance,
    }


def clean(text: str) -> str:
    """At most 5 non-empty lines, markdown bullets and bold stripped."""
    lines = []
    for ln in text.splitlines():
        ln = ln.strip().lstrip("-*• ").replace("**", "").strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines[: len(LABELS)])


def summarise(llm: LLM, facts: dict[str, Any]) -> str:
    prompt = "Facts about the shift that just ended (JSON):\n" + json.dumps(
        facts, ensure_ascii=False, indent=1, default=str
    )
    return clean(llm.text(SYSTEM_PROMPT, prompt, temperature=TEMPERATURE, max_tokens=MAX_TOKENS))


def generate(repo: AiRepo, llm: LLM, shift_id: str) -> str:
    """Build the summary and save it to shifts.handover_summary."""
    summary = summarise(llm, gather(repo, shift_id))
    if not summary:
        raise ApiError(503, "LLM_ERROR", "The language model returned an empty handover.")
    repo.save_handover(shift_id, summary, datetime.now(UTC))
    return summary

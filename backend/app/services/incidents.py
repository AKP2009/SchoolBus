"""Voice transcript -> structured incident draft (`POST /incidents/transcribe`).

The LLM fills `IncidentDraft` (type, severity, description, injury) with `temperature=0`; the
operator confirms or edits it before the web app saves it to `incidents`. Guard rails after the
model: anyone hurt means severity at least `critical`; an `injury` type means `injury=true`.
"""

from __future__ import annotations

from app.core.errors import ApiError
from app.llm import LLM
from app.schemas.common import IncidentType, Severity
from app.schemas.incidents import IncidentDraft

MAX_TOKENS = 400

SYSTEM_PROMPT = """You turn a construction machine operator's spoken incident report into a \
draft incident record. The transcript may be in English, Hindi or Tamil and may be messy.

Fields:
- incident_type: near_miss (nobody hurt, nothing damaged, but it could have been), collision \
(machine hit something or someone), injury (a person was hurt), equipment_damage (machine or \
property damaged), spill_leak (oil, fuel or hydraulic fluid leak or spill), other.
- severity: info (minor, no risk now), warning (could have caused harm or damage), critical \
(someone hurt, serious damage, or a danger that is still there), emergency (serious injury, \
fire, rollover, power line contact, life at risk).
- description: 1-3 plain English sentences for the site manager: what happened, where, which \
machine, what was done. Use only facts from the transcript; don't add causes or details.
- injury: true only if the transcript says a person was hurt.
If unsure between two severities, choose the higher one."""


def draft(llm: LLM, transcript: str, machine_id: str | None) -> IncidentDraft:
    text = transcript.strip()
    if not text:
        raise ApiError(400, "VALIDATION_ERROR", "The transcript is empty.")
    prompt = f"Machine: {machine_id or 'not given'}\nTranscript:\n{text}"
    d = llm.json(SYSTEM_PROMPT, prompt, IncidentDraft, temperature=0.0, max_tokens=MAX_TOKENS)
    if d.incident_type == IncidentType.injury:
        d.injury = True
    if d.injury and d.severity in (Severity.info, Severity.warning):
        d.severity = Severity.critical
    return d

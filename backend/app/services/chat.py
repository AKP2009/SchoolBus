"""RAG training chatbot (`POST /chat`, models.md §7).

question (translated to English when it isn't) -> bge embedding -> match_document_chunks
(top 4, similarity >= 0.3) -> LLM with the context and the §7 rules -> answer in the operator's
language + the sources it used. Both turns are saved in chat_messages.

The model ends its reply with `SOURCES: 1, 3` (or `SOURCES: none`); that line is stripped and
mapped to the retrieved chunks, so `sources` lists only what the answer used.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai_repo import AiRepo
from app.core.errors import ApiError
from app.llm import BUSY_CODES, LLM
from app.repo import iso
from app.services import kb
from app.services.chat_cache import ChatCache

log = logging.getLogger(__name__)

MATCH_COUNT = 4
MIN_SIMILARITY = 0.3
TEMPERATURE = 0.2
MAX_TOKENS = 500
CHAT_ATTEMPTS = 2  # someone is waiting at the screen: one quick retry, then cache or "busy"
CHAT_MAX_DELAY_S = 5.0
BUSY_MESSAGE = "Chatbot busy, try again in a minute."

LANGUAGES = {"en": "English", "hi": "Hindi", "ta": "Tamil"}

SYSTEM_PROMPT = """You are the Smart Operator Assistant. You help operators of construction \
machines (excavators, wheel loaders, dozers, articulated trucks) on a job site. They read your \
answer on a screen in a vibrating cab.

Rules:
1. Answer ONLY from the CONTEXT sections in the message. Do not use outside knowledge. Never \
invent numbers, limits, torque values, part numbers or procedures.
2. If the CONTEXT does not answer the question, say plainly that this is not in your manuals, \
suggest asking the supervisor (or maintenance / the machine's service manual for technical \
specifications), and end with SOURCES: none. Do not guess.
3. Safety first, always, in every language: when the question touches a fault code, warning, \
alarm, hazard, damage or any safety topic (even "what does X mean?" or "at what angle...?"), \
the FIRST sentence is the first step of the section's "What to do now" (an action such as: \
reduce the load, lower the attachment, stop, stay in the cab, keep the seatbelt on). Do not \
start with the meaning, a definition or a "don't touch" warning; they come after the steps. \
Keep the steps in the order the CONTEXT gives them.
4. Never suggest touching hot or pressurised parts, opening a hot radiator cap, bypassing or \
silencing a safety system, working without the seatbelt, or carrying on while a critical \
warning is active, unless the CONTEXT says exactly that.
5. Be complete but plain: use the section that answers the question and keep EVERY action \
step, limit, "call maintenance / supervisor when..." condition and "never do..." warning it \
gives. Short sentences or bullet points, at most about 180 words.
6. When you give a temperature, pressure, voltage, angle, distance or time limit, say it is the \
demo's assumed limit (as the CONTEXT says), not a Caterpillar specification.
7. Cite the sources you used by title in the answer, like "(Source: Fault codes)".
8. Write the whole answer in {language}. Keep fault codes (E-365) and alert names as they are.
9. The very last line is SOURCES: followed by the numbers of the CONTEXT sections you used, \
e.g. "SOURCES: 1, 3", or "SOURCES: none"."""

TRANSLATE_PROMPT = (
    "Translate the machine operator's question into English. Keep fault codes, numbers and "
    "machine terms unchanged. Reply with the English question only."
)

_SOURCES_LINE = re.compile(r"^\s*\**SOURCES\**\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)


@dataclass
class ChatResult:
    answer: str
    sources: list[dict[str, Any]]  # [{document_id, title, chunk_index}]
    question_en: str
    retrieved: list[dict[str, Any]]


def detect_language(message: str, requested: str) -> str:
    """The requested language, unless the text is plainly in Devanagari or Tamil script."""
    if re.search(r"[஀-௿]", message):
        return "ta"
    if re.search(r"[ऀ-ॿ]", message):
        return "hi"
    return requested or "en"


def to_english(llm: LLM, message: str, language: str, **retry: Any) -> str:
    if language == "en" and message.isascii():
        return message
    return llm.text(TRANSLATE_PROMPT, message, temperature=0.0, max_tokens=200, **retry) or message


def build_context(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "CONTEXT: (nothing relevant was found in the manuals)"
    parts = []
    for i, c in enumerate(chunks, 1):
        title = (c.get("metadata") or {}).get("title", "Unknown")
        parts.append(f"[{i}] Title: {title}\n{c['content']}")
    return "CONTEXT:\n\n" + "\n\n---\n\n".join(parts)


def split_sources(reply: str, chunks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Strip the SOURCES line and map its numbers to chunks (deduplicated, in order)."""
    matches = list(_SOURCES_LINE.finditer(reply))
    used: list[int] = []
    if matches:
        m = matches[-1]
        used = [int(n) for n in re.findall(r"\d+", m.group(1))]
        reply = (reply[: m.start()] + reply[m.end() :]).strip()
    else:  # no SOURCES line (e.g. cut off): fall back to the titles cited in the text
        used = [
            i
            for i, c in enumerate(chunks, 1)
            if (c.get("metadata") or {}).get("title", "\0") in reply
        ]
    sources: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for i in used:
        if not 1 <= i <= len(chunks):
            continue
        c = chunks[i - 1]
        meta = c.get("metadata") or {}
        key = (int(c["document_id"]), int(meta.get("chunk_index", -1)))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "document_id": key[0],
                "title": str(meta.get("title", "")),
                "chunk_index": key[1],
            }
        )
    return reply, sources


def answer(
    repo: AiRepo,
    llm: LLM,
    message: str,
    language: str = "en",
    embed: Callable[[str], list[float]] | None = None,
    **retry: Any,
) -> ChatResult:
    """`retry`: `max_attempts` / `max_delay_s` for the LLM calls (the live endpoint keeps them
    short; the eval uses the defaults)."""
    embed = embed or kb.embed_query
    lang = detect_language(message, language)
    question_en = to_english(llm, message, lang, **retry)
    chunks = repo.match_chunks(embed(question_en), MATCH_COUNT, MIN_SIMILARITY)
    prompt = f"{build_context(chunks)}\n\nQUESTION: {message}"
    if question_en != message:
        prompt += f"\n(In English: {question_en})"
    system = SYSTEM_PROMPT.format(language=LANGUAGES.get(lang, lang))
    reply = llm.text(system, prompt, temperature=TEMPERATURE, max_tokens=MAX_TOKENS, **retry)
    text, sources = split_sources(reply, chunks)
    if not text:
        text = "Sorry, I couldn't answer that. Please ask your supervisor."
    return ChatResult(text, sources, question_en, chunks)


def answer_or_cache(
    repo: AiRepo,
    cache: ChatCache,
    llm: Callable[[], LLM],
    message: str,
    language: str = "en",
) -> tuple[ChatResult, bool]:
    """The cached answer if there is one (no LLM call), else a live one, which is then cached.
    Live calls get CHAT_ATTEMPTS quick tries; if the LLM is rate limited or overloaded the cache
    is checked again, else 503 CHAT_BUSY. Returns (result, served_from_cache)."""
    lang = detect_language(message, language)
    if (hit := cache.get(message, lang)) is not None:
        return ChatResult(hit["answer"], hit["sources"], message, []), True
    try:
        result = answer(
            repo, llm(), message, lang, max_attempts=CHAT_ATTEMPTS, max_delay_s=CHAT_MAX_DELAY_S
        )
    except ApiError as e:
        if e.code not in BUSY_CODES:
            raise
        if (hit := cache.get(message, lang)) is not None:
            return ChatResult(hit["answer"], hit["sources"], message, []), True
        log.warning("chat busy (%s) for %r", e.code, message)
        raise ApiError(503, "CHAT_BUSY", BUSY_MESSAGE) from e
    cache.put(message, lang, result.answer, result.sources)
    return result, False


def save_turns(
    repo: AiRepo,
    session_id: str,
    operator_id: str,
    message: str,
    result: ChatResult,
    asked_at: datetime,
) -> None:
    """Explicit created_at: one insert gives both rows the same now(), and the session is read
    ordered by created_at."""
    answered_at = max(datetime.now(UTC), asked_at + timedelta(milliseconds=1))
    repo.insert_chat_messages(
        [
            {
                "session_id": session_id,
                "operator_id": operator_id,
                "role": "user",
                "content": message,
                "sources": None,
                "created_at": iso(asked_at),
            },
            {
                "session_id": session_id,
                "operator_id": operator_id,
                "role": "assistant",
                "content": result.answer,
                "sources": result.sources,
                "created_at": iso(answered_at),
            },
        ]
    )

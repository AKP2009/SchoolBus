"""One LLM interface for chat (RAG), handover summaries and incident drafts (models.md §7, §9).

Provider and model come from backend/.env: `LLM_PROVIDER` (only "gemini", via google-genai),
`LLM_MODEL`, `LLM_API_KEY`. A missing key doesn't stop the server: `get_llm()` raises a 503
`LLM_UNAVAILABLE` when one of these features is used, and the app logs a warning at startup.

The free tier is rate limited, so 429 (and 500/503) responses are retried with exponential
backoff, honouring the server's retry hint. A daily quota that is used up is not retried.
All calls are sync: run them in the threadpool.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROVIDERS = ("gemini",)
RETRY_STATUS = {429, 500, 503}
MAX_ATTEMPTS = 6
BASE_DELAY_S = 2.0
MAX_DELAY_S = 60.0


def config_error() -> str | None:
    """Why the LLM can't be used, or None when it is configured."""
    s = get_settings()
    if s.llm_provider not in PROVIDERS:
        return f"LLM_PROVIDER={s.llm_provider!r} is not supported (use one of {PROVIDERS})."
    if s.llm_api_key is None or not s.llm_api_key.get_secret_value().strip():
        return "LLM_API_KEY is not set in backend/.env."
    if not s.llm_model.strip():
        return "LLM_MODEL is not set in backend/.env."
    return None


def llm_unavailable(reason: str) -> ApiError:
    return ApiError(
        503, "LLM_UNAVAILABLE", f"The assistant's language model is unavailable: {reason}"
    )


def _retry_delay(e: Exception, attempt: int) -> float:
    """The server's RetryInfo hint ("retryDelay": "31s") if any, else exponential backoff."""
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(e))
    backoff = BASE_DELAY_S * 2**attempt + random.uniform(0, 1)
    hint = float(m.group(1)) + 1 if m else 0.0
    return min(max(hint, backoff), MAX_DELAY_S)


def _daily_quota(e: Exception) -> bool:
    text = str(e)
    return "PerDay" in text or "per day" in text.lower()


class LLM:
    """Gemini through google-genai. `text()` for prose, `json()` for a pydantic-shaped reply."""

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        # lowest thinking level the model accepts; some reject "minimal" (-> "low")
        self.thinking_level = "minimal"

    def _config(
        self, system: str, temperature: float, max_tokens: int, schema: type[BaseModel] | None
    ) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        # Thinking tokens count against max_output_tokens (a 500-token answer could come back
        # empty), so keep thinking to the minimum: these are short, grounded answers.
        if self.model.startswith("gemini-2.5-flash"):
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        elif not self.model.startswith("gemini-2"):
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=types.ThinkingLevel(self.thinking_level.upper())
            )
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        return types.GenerateContentConfig(**kwargs)

    def _call(self, prompt: str, config: Callable[[], Any]) -> Any:
        from google.genai import errors

        for attempt in range(MAX_ATTEMPTS):
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=prompt, config=config()
                )
            except errors.APIError as e:
                if e.code == 400 and "thinking level" in str(e).lower():
                    if self.thinking_level == "minimal":
                        log.info("%s rejects minimal thinking; using low", self.model)
                        self.thinking_level = "low"
                        continue
                last = attempt == MAX_ATTEMPTS - 1
                if e.code not in RETRY_STATUS or last or (e.code == 429 and _daily_quota(e)):
                    log.error("LLM call failed (%s): %s", e.code, e)
                    code = "LLM_RATE_LIMITED" if e.code == 429 else "LLM_ERROR"
                    raise ApiError(503, code, f"The language model call failed ({e.code}).") from e
                delay = _retry_delay(e, attempt)
                log.warning("LLM %s, retry %d in %.1f s", e.code, attempt + 1, delay)
                time.sleep(delay)
        raise AssertionError("unreachable")

    def text(self, system: str, prompt: str, *, temperature: float, max_tokens: int) -> str:
        resp = self._call(prompt, lambda: self._config(system, temperature, max_tokens, None))
        return (resp.text or "").strip()

    def json(
        self, system: str, prompt: str, schema: type[T], *, temperature: float, max_tokens: int
    ) -> T:
        resp = self._call(prompt, lambda: self._config(system, temperature, max_tokens, schema))
        if isinstance(resp.parsed, schema):
            return resp.parsed
        try:
            return schema.model_validate_json(resp.text or "")
        except ValueError as e:
            log.error("LLM reply is not valid %s: %r", schema.__name__, resp.text)
            raise ApiError(503, "LLM_ERROR", "The language model gave an unreadable reply.") from e


@lru_cache
def _build() -> LLM:
    from google import genai

    s = get_settings()
    assert s.llm_api_key is not None
    client = genai.Client(api_key=s.llm_api_key.get_secret_value())
    return LLM(client, s.llm_model)


def get_llm() -> LLM:
    """The configured LLM (FastAPI dependency). 503 LLM_UNAVAILABLE when it isn't configured."""
    reason = config_error()
    if reason is not None:
        raise llm_unavailable(reason)
    try:
        return _build()
    except ImportError as e:
        raise llm_unavailable(
            "google-genai is not installed (pip install -r requirements.txt)."
        ) from e

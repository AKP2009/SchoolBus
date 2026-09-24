"""Supabase client (service role) whose HTTP calls retry transient connection errors.

postgrest-py only retries GET requests answered with 503/520; a dropped or refused connection
raised straight through to the route. Every Supabase call (PostgREST, Auth, Storage) goes
through `RetryTransport`: up to `ATTEMPTS` tries with exponential backoff, then
`DatabaseUnavailable`, which the API returns as 503 `DB_UNAVAILABLE` (app/core/errors.py).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import lru_cache

import httpx
from supabase import Client, ClientOptions, create_client

from app.core.config import get_settings

log = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF_S = 0.25  # 0.25 s, 0.5 s between the attempts
TIMEOUT_S = 120  # postgrest-py's default

# The request never reached the server: safe to resend whatever the method.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.WriteError)
# The connection broke after the request went out: resend only if repeating it is harmless.
BROKEN = (httpx.RemoteProtocolError, httpx.ReadError)
IDEMPOTENT = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}


class DatabaseUnavailable(Exception):
    """Supabase stayed unreachable after the retries."""


def _retryable(request: httpx.Request, e: Exception) -> bool:
    return isinstance(e, NOT_SENT) or (isinstance(e, BROKEN) and request.method in IDEMPOTENT)


class RetryTransport(httpx.BaseTransport):
    def __init__(
        self,
        inner: httpx.BaseTransport,
        attempts: int = ATTEMPTS,
        backoff_s: float = BACKOFF_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.attempts = attempts
        self.backoff_s = backoff_s
        self.sleep = sleep

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(1, self.attempts + 1):
            try:
                return self.inner.handle_request(request)
            except (*NOT_SENT, *BROKEN) as e:
                what = f"{request.method} {request.url.path}"
                if not _retryable(request, e):
                    raise DatabaseUnavailable(f"Supabase connection broke on {what}: {e!r}") from e
                if attempt == self.attempts:
                    raise DatabaseUnavailable(
                        f"Supabase unreachable after {attempt} attempts ({what}): {e!r}"
                    ) from e
                delay = self.backoff_s * 2 ** (attempt - 1)
                log.warning("%s failed (%r), retry %d in %.2f s", what, e, attempt, delay)
                self.sleep(delay)
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.inner.close()


def http_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """The httpx client postgrest-py would build (HTTP/2, redirects), with retries."""
    inner = transport or httpx.HTTPTransport(http2=True)
    return httpx.Client(transport=RetryTransport(inner), timeout=TIMEOUT_S, follow_redirects=True)


@lru_cache
def get_supabase() -> Client:
    """Supabase client with the service-role key. Bypasses RLS: backend use only."""
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key.get_secret_value(),
        options=ClientOptions(httpx_client=http_client()),
    )

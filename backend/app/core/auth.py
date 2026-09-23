"""Bearer auth for every endpoint except `GET /health` (docs/api_contract.md "Auth").

Two kinds of caller:
* People: `Authorization: Bearer <Supabase access token>`. The token is verified locally when
  possible (HS256 with `SUPABASE_JWT_SECRET`, or ES256/RS256 against the project's JWKS);
  otherwise Supabase checks it (`GET /auth/v1/user`). Role, operator_id and site_id then come from
  `profiles`, never from the token's `user_metadata`, which the user can edit.
* The vision service: `Authorization: Bearer <VISION_API_TOKEN>` from backend/.env. It may call
  only the endpoints it needs (`POST /events`, `GET /machine/{id}/state`,
  `GET /operator/{id}/fatigue`).

Rules mirror RLS (docs/supabase.md §5): an operator acts only on their own rows, managers and
admins on everything.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request, WebSocket
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import ApiError

log = logging.getLogger(__name__)

PROFILE_TTL_S = 60.0
REMOTE_TTL_S = 60.0
AUDIENCE = "authenticated"


@dataclass(frozen=True)
class Principal:
    """Who is calling. `role` is the profiles.role, or "service" for the vision token."""

    role: str  # operator | manager | admin | service
    user_id: str | None = None
    operator_id: str | None = None
    site_id: str | None = None

    @property
    def is_manager(self) -> bool:
        return self.role in ("manager", "admin")

    @property
    def is_service(self) -> bool:
        return self.role == "service"

    def can_act_for(self, operator_id: str | None) -> bool:
        """Operator's own rows, or anything for managers and the vision service."""
        if self.is_manager or self.is_service:
            return True
        return operator_id is not None and operator_id == self.operator_id


SERVICE = Principal(role="service")


def unauthorized(message: str = "Sign in again: missing or invalid access token.") -> ApiError:
    return ApiError(401, "UNAUTHORIZED", message)


def forbidden(message: str) -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


# ---------------------------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------------------------
class TokenVerifier:
    """Supabase access token -> user id (sub). Sync; call it in the threadpool."""

    def __init__(self) -> None:
        self._jwks: jwt.PyJWKClient | None = None
        self._remote: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def _jwks_client(self) -> jwt.PyJWKClient:
        if self._jwks is None:
            url = get_settings().supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
            self._jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=600)
        return self._jwks

    def verify(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise unauthorized() from e
        alg = header.get("alg")
        secret = get_settings().supabase_jwt_secret
        secret_value = secret.get_secret_value() if secret else ""
        try:
            if alg == "HS256" and secret_value:
                claims = jwt.decode(token, secret_value, algorithms=["HS256"], audience=AUDIENCE)
            elif alg in ("ES256", "RS256"):
                key = self._jwks_client().get_signing_key_from_jwt(token)
                claims = jwt.decode(token, key.key, algorithms=[alg], audience=AUDIENCE)
            else:
                return self._verify_remote(token)
        except jwt.ExpiredSignatureError as e:
            raise unauthorized("Access token expired: sign in again.") from e
        except jwt.PyJWKClientError:
            return self._verify_remote(token)  # JWKS unreachable: let Supabase decide
        except jwt.PyJWTError as e:
            raise unauthorized() from e
        sub = claims.get("sub")
        if not sub:
            raise unauthorized()
        return str(sub)

    def _verify_remote(self, token: str) -> str:
        """Ask Supabase Auth (GET /auth/v1/user). Cached briefly per token."""
        now = time.monotonic()
        with self._lock:
            hit = self._remote.get(token)
            if hit and now - hit[0] < REMOTE_TTL_S:
                return hit[1]
        from app.db import get_supabase

        try:
            res = get_supabase().auth.get_user(token)
        except Exception as e:  # noqa: BLE001 - gotrue raises its own error types
            raise unauthorized() from e
        user = getattr(res, "user", None)
        if user is None:
            raise unauthorized()
        with self._lock:
            if len(self._remote) > 1000:
                self._remote.clear()
            self._remote[token] = (now, str(user.id))
        return str(user.id)


class ProfileCache:
    """profiles row per user id, cached for PROFILE_TTL_S (role changes apply within a minute)."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            hit = self._rows.get(user_id)
            if hit and now - hit[0] < PROFILE_TTL_S:
                return hit[1]
        from app.db import get_supabase

        data = (
            get_supabase()
            .table("profiles")
            .select("id,role,operator_id,site_id")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
        )
        row = data[0] if data else None
        with self._lock:
            self._rows[user_id] = (now, row)
        return row


_verifier = TokenVerifier()
_profiles = ProfileCache()


def _bearer(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _is_service_token(token: str) -> bool:
    configured = get_settings().vision_api_token
    value = configured.get_secret_value() if configured else ""
    return bool(value) and hmac.compare_digest(token.encode(), value.encode())


def _user_principal(token: str) -> Principal:
    user_id = _verifier.verify(token)
    try:
        profile = _profiles.get(user_id)
    except Exception as e:  # noqa: BLE001
        log.error("profile lookup failed for %s: %s", user_id, e)
        raise ApiError(503, "AUTH_UNAVAILABLE", "Could not load your profile. Try again.") from e
    if profile is None:
        raise forbidden("This account has no profile. Ask an admin to set it up.")
    return Principal(
        role=str(profile.get("role") or "operator"),
        user_id=user_id,
        operator_id=profile.get("operator_id"),
        site_id=profile.get("site_id"),
    )


async def authenticate(token: str | None, allow_service: bool) -> Principal:
    if token is None:
        raise unauthorized("Missing bearer token.")
    if _is_service_token(token):
        if not allow_service:
            raise forbidden("The vision service token can't call this endpoint.")
        return SERVICE
    return await run_in_threadpool(_user_principal, token)


# ---------------------------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------------------------
async def current_user(request: Request) -> Principal:
    """A signed-in person (Supabase JWT)."""
    return await authenticate(_bearer(request.headers.get("authorization")), allow_service=False)


async def user_or_service(request: Request) -> Principal:
    """A signed-in person or the vision service."""
    return await authenticate(_bearer(request.headers.get("authorization")), allow_service=True)


async def manager(user: Annotated[Principal, Depends(current_user)]) -> Principal:
    if not user.is_manager:
        raise forbidden("Managers only.")
    return user


async def websocket_user(websocket: WebSocket) -> Principal:
    """WebSockets can't send headers from the browser: the token comes as `?token=` (or a
    bearer header from non-browser clients)."""
    token = websocket.query_params.get("token") or _bearer(websocket.headers.get("authorization"))
    return await authenticate(token, allow_service=True)


CurrentUser = Annotated[Principal, Depends(current_user)]
UserOrService = Annotated[Principal, Depends(user_or_service)]
Manager = Annotated[Principal, Depends(manager)]

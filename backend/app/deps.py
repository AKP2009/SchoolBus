"""Auth dependency: `Authorization: Bearer <supabase JWT>`.

DEV_AUTH_BYPASS=true accepts any token (local dev without a Supabase project). Otherwise
the token is validated with `supabase.auth.get_user`; failures are 401 in the contract
error format.
"""

from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.errors import ApiError

_bearer = HTTPBearer(auto_error=False)


class User(BaseModel):
    id: str
    email: str | None = None


def auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if settings.dev_auth_bypass:
        return User(id="dev-bypass")
    if credentials is None or not credentials.credentials:
        raise ApiError(401, "UNAUTHORIZED", "Missing bearer token")
    try:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        res: Any = client.auth.get_user(credentials.credentials)
        return User(id=res.user.id, email=res.user.email)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(401, "UNAUTHORIZED", "Invalid or expired token") from exc

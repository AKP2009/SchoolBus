from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Backend settings, read from backend/.env (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str
    supabase_service_role_key: SecretStr
    database_url: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    cors_origins: str = "http://localhost:5173"
    # Shared bearer token of the vision service (POST /events and the reads it polls).
    vision_api_token: SecretStr | None = None
    # Legacy HS256 JWT secret (Dashboard -> Settings -> API -> JWT secret). Optional: without it,
    # asymmetric tokens are checked against the project's JWKS and HS256 ones via GET /auth/v1/user.
    supabase_jwt_secret: SecretStr | None = None
    # APScheduler jobs (maintenance scoring, daily clustering, vision alert expiry). Off in tests.
    scheduler_enabled: bool = True
    # Replay falls back to this file when Supabase telemetry is unreachable or empty.
    telemetry_parquet: str = str(REPO_ROOT / "data" / "output" / "telemetry.parquet")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment

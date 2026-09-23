"""Settings (pydantic-settings, from backend/.env). Never commit backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    data_source: Literal["csv", "supabase"] = "csv"
    data_dir: Path = REPO_ROOT / "data/output"
    artifact_dir: Path = REPO_ROOT / "ml/artifacts/task_time/v1"
    dev_auth_bypass: bool = True

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolve_paths(self) -> "Settings":
        """Relative paths are resolved against backend/ (uvicorn's usual cwd)."""
        if not self.data_dir.is_absolute():
            self.data_dir = (BACKEND_DIR / self.data_dir).resolve()
        if not self.artifact_dir.is_absolute():
            self.artifact_dir = (BACKEND_DIR / self.artifact_dir).resolve()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings().resolve_paths()

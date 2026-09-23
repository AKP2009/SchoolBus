"""GET /health — liveness plus per-model artifact status (docs/api_contract.md)."""

from fastapi import APIRouter

from app.config import get_settings
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])

MODEL_ARTIFACTS = ("standard.joblib", "p10.joblib", "p50.joblib", "p90.joblib")


def _task_time_status() -> str:
    settings = get_settings()
    if all((settings.artifact_dir / name).is_file() for name in MODEL_ARTIFACTS):
        return "v1"
    return "not_loaded"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", models={"task_time": _task_time_status()})

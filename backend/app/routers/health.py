from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

MODEL_NAMES = ("anomaly", "task_time", "maintenance", "clustering")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # Models are not loaded yet; report their status so the UI can show it.
    return HealthResponse(models={name: "not_loaded" for name in MODEL_NAMES})

from fastapi import APIRouter

from app.core.config import get_settings
from app.llm import config_error
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

MODEL_NAMES = ("anomaly", "task_time", "maintenance", "clustering")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # Models are not loaded yet; report their status so the UI can show it.
    models = {name: "not_loaded" for name in MODEL_NAMES}
    # chat / handover / incident drafts: the LLM model name, or "not_configured"
    models["llm"] = "not_configured" if config_error() else get_settings().llm_model
    return HealthResponse(models=models)

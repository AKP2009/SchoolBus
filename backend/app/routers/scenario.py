from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.scenario import ScenarioName, ScenarioRequest, ScenarioResponse

router = APIRouter(prefix="/scenario", tags=["scenario"])


@router.post("/{name}", response_model=ScenarioResponse)
def trigger(name: ScenarioName, body: ScenarioRequest) -> ScenarioResponse:
    raise not_implemented(f"Scenario '{name}'")

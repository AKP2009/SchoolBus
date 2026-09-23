from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.machine import MachineHealthResponse

router = APIRouter(prefix="/machine", tags=["machine"])


@router.get("/{machine_id}/health", response_model=MachineHealthResponse)
def machine_health(machine_id: str) -> MachineHealthResponse:
    raise not_implemented("Machine health")

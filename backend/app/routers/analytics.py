from datetime import date

from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.analytics import ClusterRunResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/cluster", response_model=ClusterRunResponse)
def cluster(week_start: date) -> ClusterRunResponse:
    raise not_implemented("Fleet clustering")

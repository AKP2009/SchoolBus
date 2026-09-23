from fastapi import APIRouter

from app.core.errors import not_implemented
from app.schemas.events import Event, EventResponse

router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventResponse)
def post_event(body: Event) -> EventResponse:
    raise not_implemented("Event ingestion")
